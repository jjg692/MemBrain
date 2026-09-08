"""
MemBrain (Refactor) — GPT-SoVITS TTS 客户端

把 LLM 生成的回复文本交给本地 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)
的 HTTP API（`api_v2.py` 自带的 FastAPI 服务，默认端口 9880）合成成人声并播放。

设计原则（与项目内其它扩展一致）：
- **默认关闭**：`TTS_ENABLED` 为 false 时本模块完全不工作，不影响主流程与既有行为。
- **纯隔离**：不 import 任何 `core.proactivity*`，不注册任何 MCP/工具，不写系统核心状态。
- **失败静默**：GPT-SoVITS 未启动 / 网络异常 / 合成失败 / 播放失败，全部捕获并仅记日志，
  绝不让 TTS 异常影响 LLM 回复的生成与推送。
- 把 GPT-SoVITS 视为**外部服务**（如同 Ollama），而非 MCP 工具：它跑在独立进程/端口，
  与 MemBrain 后端解耦，单独启停。

典型用法：
    from core.tts_client import speak_reply, tts_enabled
    if tts_enabled():
        speak_reply("你好呀，今天想聊什么？")   # 后台线程合成并播放，失败静默
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import core.config as _cfg

logger = logging.getLogger("membrain.tts")


def _live(key, default=None):
    """动态读取配置：优先 os.environ（后台 update_config 已写入），否则模块默认值。

    后台 TTS 管理与配置管理共用 update_config → 会同时写 .env 与 os.environ，
    因此这里不缓存模块 import 时的常量，让开关/参数改动即时生效（无需重启后端）。
    """
    val = os.environ.get(key)
    if val is None:
        return getattr(_cfg, key, default)
    return val


def _live_bool(key, default=False) -> bool:
    return str(_live(key, default)).strip().lower() in ("1", "true", "yes", "on")


class TTSClient:
    """GPT-SoVITS `/tts` 接口的轻量封装。

    所有网络/合成/播放异常都只记日志并吞掉，保证调用方（LLM 回复链路）零风险。
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        ref_audio_path: str = None,
        prompt_text: str = None,
        text_lang: str = None,
        prompt_lang: str = None,
        media_type: str = None,
        speed_factor: float = None,
    ):
        import core.config as C

        # 显式传入的实参优先；否则动态读取 os.environ（后台改动即时生效）/模块默认值。
        def _vi(arg, key, default):
            if arg not in (None, ""):
                return arg
            v = _live(key, default)
            return v if v not in (None, "") else default

        self.base_url = (
            f"http://{_vi(host, 'TTS_HOST', C.TTS_HOST)}:"
            f"{int(float(_vi(port, 'TTS_PORT', C.TTS_PORT)))}"
        )
        self.ref_audio_path = _vi(ref_audio_path, "TTS_REF_AUDIO_PATH", C.TTS_REF_AUDIO_PATH) or ""
        self.prompt_text = _vi(prompt_text, "TTS_PROMPT_TEXT", C.TTS_PROMPT_TEXT) or ""
        self.text_lang = _vi(text_lang, "TTS_TEXT_LANG", C.TTS_TEXT_LANG) or "zh"
        self.prompt_lang = _vi(prompt_lang, "TTS_PROMPT_LANG", C.TTS_PROMPT_LANG) or "zh"
        self.media_type = _vi(media_type, "TTS_MEDIA_TYPE", C.TTS_MEDIA_TYPE) or "wav"
        self.speed_factor = float(
            _vi(speed_factor, "TTS_SPEED_FACTOR", C.TTS_SPEED_FACTOR) or 1.0
        )

    # ---- 合成 ----
    def synthesize(self, text: str, timeout: float = 30.0) -> Optional[bytes]:
        """调用 `/tts` 合成语音，返回音频字节（wav/ogg/...）；失败返回 None。

        GPT-SoVITS 的 ref_audio_path / prompt_text 相对其服务运行时的工作目录解析，
        因此这里传入的参照音频路径是 GPT-SoVITS 侧能看到的路径（通常是绝对路径，
        或相对其启动目录的路径），由 `core.config.TTS_REF_AUDIO_PATH` 提供。
        """
        if not text or not text.strip():
            logger.warning("[tts] 空文本，跳过合成")
            return None

        params = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_audio_path,
            "prompt_lang": self.prompt_lang,
            "prompt_text": self.prompt_text,
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": self.media_type,
            # 流式模式 3：更快/质量略低，适合会话级"说完立即放"；v2 用默认 0 更稳
            "streaming_mode": 0,
            "speed_factor": self.speed_factor,
        }
        try:
            import requests
            resp = requests.post(
                f"{self.base_url}/tts",
                json=params,
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.warning("[tts] 合成失败 status=%s body=%r", resp.status_code, resp.text[:300])
                return None
            return resp.content
        except Exception as exc:  # 网络异常 / 服务未启动等，全部静默
            logger.warning("[tts] 合成异常: %s", exc)
            return None

    # ---- 播放 ----
    @staticmethod
    def play(audio: bytes) -> bool:
        """播放音频字节；跨平台（winsound Windows；否则退化到静默跳过）。失败返回 False。"""
        if not audio:
            return False
        import tempfile
        import os
        suf = ".wav"
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=suf)
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(audio)
            # Windows 用 winsound 播放 WAV；非 Windows 平台暂不支持 → 静默跳过
            try:
                import winsound
                winsound.PlaySound(tmp_path, winsound.SND_FILENAME)
                return True
            except ImportError:
                logger.info("[tts] 当前平台不支持本地播放（仅 Windows winsound），跳过播放")
                return False
        except Exception as exc:
            logger.warning("[tts] 播放异常: %s", exc)
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    # ---- 合成 + 播放 ----
    def speak(self, text: str, timeout: float = 3.0) -> bool:
        """合成并播放一段语音。返回是否成功；任何失败静默返回 False。"""
        audio = self.synthesize(text, timeout=timeout)
        if audio is None:
            return False
        return self.play(audio)

    # ---- 服务健康检查（可选，供管理页 / 测试） ----
    def health(self, timeout: float = 3.0) -> bool:
        """尝试读 /tts 是否能连上（GET 空文本验证端口存活，忽略 4xx/网络异常外的错误）。"""
        try:
            import requests
            resp = requests.get(f"{self.base_url}/tts", params={"text": ""}, timeout=timeout)
            # 能收到响应（无论 200 还是参数校验类 4xx）都说明服务在线
            return resp.status_code < 500
        except Exception:
            return False


# 全局可复用的单例（懒加载，避免 import 即建连接）
_client: Optional[TTSClient] = None
_client_lock = threading.Lock()


def get_client() -> Optional[TTSClient]:
    """返回全局 TTS 客户端实例；TTS 关闭时返回 None。"""
    global _client
    if not tts_enabled():
        return None
    with _client_lock:
        if _client is None:
            _client = TTSClient()
    return _client


def tts_enabled() -> bool:
    """TTS 总开关是否开启（动态读取，后台改动即时生效）。"""
    try:
        return _live_bool("TTS_ENABLED", False)
    except Exception:
        return False


def status() -> dict:
    """返回 TTS 当前状态与配置快照（供后台 TTS 管理页展示）。

    动态读取，无需重启后端即可反映开关/参数改动。
    """
    cfg = {}
    for key in ("TTS_ENABLED", "TTS_HOST", "TTS_PORT", "TTS_REF_AUDIO_PATH",
                "TTS_PROMPT_TEXT", "TTS_TEXT_LANG", "TTS_PROMPT_LANG",
                "TTS_MEDIA_TYPE", "TTS_SPEED_FACTOR"):
        cfg[key] = _live(key)
    return {
        "enabled": _live_bool("TTS_ENABLED", False),
        "config": cfg,
    }


def speak_reply(text: str, timeout: float = 3.0, role_id: Optional[str] = None) -> bool:
    """在**后台线程**合成并播放一段回复语音；失败静默。

    若传入 role_id 且该角色配置了**每角色独立 TTS**（enabled + 已启动 + 权重/参照音频齐全），
    则优先用该角色服务；否则回退到全局单端口模式（由 TTS_ENABLED 控制）。
    供 LLM 回复 hook 调用 —— 不阻塞回复生成/推送，也不因 TTS 异常影响主流程。
    返回 True 表示已发起（不代表播放成功）。
    """
    # 每角色独立 TTS 优先
    if role_id:
        try:
            from core import tts_roles
            cfg = tts_roles.get_role_config(role_id)
            if cfg.get("enabled") and tts_roles.role_running(role_id):
                logger.info("[tts] 每角色 TTS 生效（role=%s）", role_id)
                tts_roles.speak_for_role(role_id, text, timeout=timeout)
                return True
        except Exception as exc:
            logger.warning("[tts] 每角色 TTS 失败，回退全局: %s", exc)

    client = get_client()
    if client is None or not (text and text.strip()):
        return False

    def _run():
        try:
            client.speak(text, timeout=timeout)
        except Exception as exc:  # 兜底：绝不让 TTS 异常逃逸到主线程
            logger.warning("[tts] speak_reply 异常: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return True
