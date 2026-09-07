"""
core/tts_client 单元测试。

覆盖：
- tts_enabled() / get_client() 尊重 TTS_ENABLED（默认关 → 返回 None / False）
- speak_reply 在关闭时返回 False 且不发起线程
- 服务不可达时 synthesize 返回 None（失败静默）
- 通过 monkeypatch 假 requests，验证 POST /tts 的入参正确（text/ref_audio_path 等）
- 播放与非 wav 平台静默降级
全部通过 monkeypatch 注入，不依赖真实 GPT-SoVITS 服务 / 真实音频设备 / 线程。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.tts_client as TTS


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """每用例后重置模块级单例，并确保测试环境 TTS_ENABLED 处于已知默认（关）。"""
    monkeypatch.delenv("TTS_ENABLED", raising=False)
    yield
    TTS._client = None


def test_disabled_semantics():
    # 默认 TTS_ENABLED=false（未设置环境变量）
    assert TTS.tts_enabled() is False
    assert TTS.get_client() is None
    assert TTS.speak_reply("你好") is False


def test_enabled_requires_switch(monkeypatch):
    monkeypatch.setenv("TTS_ENABLED", "true")
    assert TTS.tts_enabled() is True
    client = TTS.get_client()
    assert client is not None
    assert TTS.get_client() is client  # 单例复用


def test_synthesize_server_down_returns_none(monkeypatch):
    """服务未启动（连接异常）→ 返回 None，绝不抛异常。"""
    def boom(*a, **k):
        raise ConnectionError("service down")

    monkeypatch.setattr("requests.post", boom)
    client = TTS.TTSClient()
    assert client.synthesize("你好") is None


def test_synthesize_params_sent(monkeypatch):
    """验证 POST /tts 的请求体符合 GPT-SoVITS 契约。"""
    captured = {}

    class FakeResp:
        status_code = 200
        content = b"RIFF....fake-wav"

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("requests.post", fake_post)
    client = TTS.TTSClient(
        ref_audio_path="D:/voices/ref.wav",
        prompt_text="参照转写",
        text_lang="zh",
        prompt_lang="zh",
        media_type="wav",
    )
    audio = client.synthesize("今天天气真好")
    assert audio == b"RIFF....fake-wav"
    assert captured["url"].endswith("/tts")
    body = captured["json"]
    assert body["text"] == "今天天气真好"
    assert body["text_lang"] == "zh"
    assert body["ref_audio_path"] == "D:/voices/ref.wav"
    assert body["prompt_text"] == "参照转写"
    assert body["prompt_lang"] == "zh"
    assert body["media_type"] == "wav"
    assert body["text_split_method"] == "cut5"


def test_synthesize_http_error_returns_none(monkeypatch):
    class FakeResp:
        status_code = 400
        text = '{"detail": "params error"}'
        content = b""

    def fake_post(url, json=None, timeout=None):
        return FakeResp()

    monkeypatch.setattr("requests.post", fake_post)
    client = TTS.TTSClient()
    assert client.synthesize("你好") is None


def test_speak_empty(monkeypatch):
    client = TTS.TTSClient()
    assert client.speak("   ") is False
    assert client.speak("") is False


def test_speak_synthesize_failure_returns_false(monkeypatch):
    client = TTS.TTSClient()

    # 直接 stub synthesize 返回 None → speak False
    def fake_synth(text, timeout=None):
        return None
    client.synthesize = fake_synth
    assert client.speak("你好") is False


def test_after_reply_hook_importable():
    """_after_reply 中引入 speak_reply 在 TTS 关闭时应无副作用、不报错。"""
    assert hasattr(TTS, "speak_reply")
    assert TTS.speak_reply("你好") is False


def test_config_hidden_from_snapshot_but_still_editable():
    """TTS 项不出现在「配置管理」页快照，但仍保留在 EDITABLE_KEYS（供 update_config 保存）。"""
    import core.config as C
    snapshot_keys = [s["key"] for s in C.get_config_snapshot()]
    assert not any(k.startswith("TTS_") for k in snapshot_keys), "TTS 项不应出现在配置管理快照"
    # 但仍保留在 EDITABLE_KEYS → update_config 可写（不实际写盘，仅验证成员资格）
    for k in ("TTS_ENABLED", "TTS_HOST", "TTS_PORT", "TTS_REF_AUDIO_PATH",
              "TTS_PROMPT_TEXT", "TTS_TEXT_LANG", "TTS_PROMPT_LANG",
              "TTS_MEDIA_TYPE", "TTS_SPEED_FACTOR"):
        assert k in C.EDITABLE_KEYS, f"{k} 应从 EDITABLE_KEYS 中保留（供 TTS 页保存）"
        assert k in C.CONFIG_HIDDEN_KEYS, f"{k} 应加入 CONFIG_HIDDEN_KEYS"


def test_status_reflects_live_env(monkeypatch):
    """status() 动态读取 os.environ，反映后台改动。"""
    monkeypatch.setenv("TTS_ENABLED", "true")
    monkeypatch.setenv("TTS_HOST", "10.0.0.9")
    st = TTS.status()
    assert st["enabled"] is True
    assert st["config"]["TTS_HOST"] == "10.0.0.9"
