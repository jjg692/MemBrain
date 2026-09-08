"""
core/tts_roles 单元测试（每角色 TTS 独立服务）。

覆盖：
- 配置保存/读取（save_role_config / get_role_config / delete_role_config）
- role_configured / get_all_role_configs
- yaml 生成（权重替换、version 推断）
- _exec_cmd 命令构建
- 进程池 start/stop（用假 Popen 注入，不真启动 GPT-SoVITS）
- speak_for_role 在服务未运行时返回 False（安全降级）
全部通过 monkeypatch 注入，不依赖真实 GPT-SoVITS 服务 / 音频设备。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.tts_roles as TR


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把配置读写/输出重定向到临时目录，避免污染真实 config/tts_roles.json。"""
    monkeypatch.setattr(TR, "TTS_ROLES_FILE", tmp_path / "tts_roles.json")
    monkeypatch.setattr(TR, "TTS_ROLES_CONFIGS_DIR", tmp_path / "cfgs")
    # 清空进程池
    monkeypatch.setattr(TR, "_procs", {})
    yield


def test_save_and_get_roundtrip():
    cfg = TR.save_role_config("alice", {
        "port": 9881, "lang": "ja", "enabled": True,
        "exec_file": r"D:\g\runtime\python.exe",
        "api_file": r"D:\g\api_v2.py",
        "sovits_root": r"D:\g",
        "weights": {"pth": r"D:\g\w\a.pth", "ckpt": r"D:\g\w\a.ckpt"},
    })
    assert cfg["port"] == 9881
    assert cfg["lang"] == "ja"
    assert TR.role_configured("alice") is True
    got = TR.get_role_config("alice")
    assert got["weights"]["pth"] == r"D:\g\w\a.pth"
    assert got["enabled"] is True


def test_delete_role():
    TR.save_role_config("x", {"port": 9883})
    assert TR.role_configured("x") is True
    TR.delete_role_config("x")
    assert TR.role_configured("x") is False


def test_get_all_roles_status():
    TR.save_role_config("a", {"port": 9884, "lang": "zh"})
    TR.save_role_config("b", {"port": 9885, "lang": "ja"})
    lst = TR.get_all_role_configs()
    ids = {c["role_id"] for c in lst}
    assert ids == {"a", "b"}


def test_yaml_generation():
    TR.save_role_config("role1", {
        "sovits_root": r"D:\g",
        "weights": {"pth": r"D:\g\角色\m.pth", "ckpt": r"D:\g\角色\m.ckpt"},
    })
    y = TR.ensure_role_yaml("role1")
    assert y and Path(y).exists()
    text = Path(y).read_text(encoding="utf-8")
    assert "m.pth" in text
    assert "m.ckpt" in text
    # 相对根目录的路径
    assert "角色/m.pth" in text


def test_yaml_needs_weights():
    TR.save_role_config("noweight", {"sovits_root": r"D:\g"})
    assert TR.ensure_role_yaml("noweight") is None


def test_exec_cmd_build():
    TR.save_role_config("c1", {
        "exec_file": r"D:\g\runtime\python.exe",
        "api_file": r"D:\g\api_v2.py",
        "sovits_root": r"D:\g",
        "port": 9901,
        "weights": {"pth": r"D:\g\a.pth", "ckpt": r"D:\g\a.ckpt"},
    })
    cmd = TR._exec_cmd("c1")
    assert cmd is not None
    assert cmd[0] == r"D:\g\runtime\python.exe"
    assert cmd[1] == r"D:\g\api_v2.py"
    assert "-p" in cmd and "9901" in cmd
    assert "-c" in cmd and cmd[-1].endswith(".yaml")


def test_start_stop_with_fake_popen(monkeypatch):
    import subprocess

    class FakeProc:
        def __init__(self): self.pid = 12345
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): pass
        def kill(self): pass

    won = []
    def fake_popen(cmd, **kw):
        won.append(cmd)
        return FakeProc()
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    TR.save_role_config("s1", {
        "exec_file": r"D:\g\py.exe", "api_file": r"D:\g\api_v2.py",
        "sovits_root": r"D:\g", "port": 9910,
        "weights": {"pth": r"D:\g\a.pth", "ckpt": r"D:\g\a.ckpt"},
    })
    res = TR.start_role_tts("s1")
    assert res["running"] is True
    assert won and won[0][0] == r"D:\g\py.exe"
    assert TR.role_running("s1") is True
    stop = TR.stop_role_tts("s1")
    assert stop["running"] is False


def test_start_incomplete_config():
    TR.save_role_config("bad", {"port": 9920})  # 缺 exec/api/weights
    res = TR.start_role_tts("bad")
    assert res["ok"] is False
    assert res["running"] is False


def test_speak_for_role_not_running_returns_false():
    TR.save_role_config("nr", {"port": 9930, "lang": "zh",
                               "ref_audio_path": r"D:\g\ref.mp3"})
    # 服务未运行 -> False
    assert TR.speak_for_role("nr", "你好") is False
