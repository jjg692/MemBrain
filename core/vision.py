"""
视觉感知服务（本地 Ollama 多模态模型 → 把图转成文本，供纯文本主模型"看图"）
================================================
主模型无视觉模态，故用一个本地 Ollama 视觉模型把图片转成中文文本描述，
主模型再基于转述理解。两个场景：

- describe_image : 对话中识别用户发的图片（VISION_IN_CHAT）
- describe_screen: 桌面窗口识别，作为感知层"电脑当前活跃"的补充（VISION_SCREEN_ON_DEMAND）

设计原则（与感知层一致）：
- 全部默认关（VISION_ENABLED=false），不影响既有行为；
- 失败/超时/未装模型/未启用 → 返回空串，绝不伪造"看到了"；
- 复用 OllamaAdapter 的 images 机制（Ollama chat 的 messages[].images 字段）；
- 带短缓存 + 冷却，避免高频重复识别；
- describe_screen 依赖 Pillow(PIL.ImageGrab)，无则静默降级为空。
"""

import base64
import threading
import time
from pathlib import Path

try:
    import ollama
    from ollama import Client as OllamaClient
except Exception:  # pragma: no cover
    ollama = None
    OllamaClient = None

from core.config import (
    OLLAMA_HOST,
    VISION_ENABLED,
    VISION_MODEL,
    VISION_IN_CHAT,
    VISION_SCREEN_ON_DEMAND,
    VISION_TIMEOUT,
    PROJECT_ROOT,
)

_CACHE_TTL = 3.0
_cache = {}       # key -> [ts, text]
_lock = threading.RLock()
_service = None


def get_vision_service() -> "VisionService":
    """单例：返回全局视觉服务（按配置惰性构造）。"""
    global _service
    if _service is None:
        _service = VisionService(model=VISION_MODEL, host=OLLAMA_HOST, enabled=VISION_ENABLED)
    return _service


class VisionService:
    """本地 Ollama 视觉模型封装：图 → 中文文本描述。"""

    def __init__(self, model: str = "", host: str = "", enabled: bool = False):
        self.model = model or ""
        self.host = host or ""
        self.enabled = bool(enabled)
        self._client = None

    # ---------- 内部 ----------
    def _ensure_client(self):
        if self._client is None:
            if OllamaClient is None:
                return None
            self._client = OllamaClient(host=self.host) if self.host else ollama
        return self._client

    def _enabled_for(self, feature: str) -> bool:
        """feature: 'chat' | 'screen'。总开关 + 分场景开关都要满足。"""
        if not self.enabled:
            return False
        if feature == "chat":
            return bool(VISION_IN_CHAT)
        if feature == "screen":
            return bool(VISION_SCREEN_ON_DEMAND)
        return False

    # ---------- 对外接口 ----------
    def describe_image(self, image, feature: str = "chat") -> str:
        """识别一张图片（path / bytes / base64）→ 中文描述。失败返回空串。"""
        if not self._enabled_for(feature):
            return ""
        b64 = self._to_b64(image)
        if not b64:
            return ""
        return self._describe([b64], origin="image")

    def describe_screen(self, region=None, feature: str = "screen") -> str:
        """截取当前屏幕（或指定区域）→ 中文描述。无 Pillow/截屏失败 → 空串。"""
        if not self._enabled_for(feature):
            return ""
        b64 = self._capture_screen_b64(region)
        if not b64:
            return ""
        return self._describe([b64], origin="screen")

    # ---------- 图片编码 ----------
    @staticmethod
    def _to_b64(image) -> str:
        """把 path / bytes / base64 字符串统一转成 base64（不带 data URI 前缀）。"""
        try:
            if image is None:
                return ""
            if isinstance(image, str):
                s = image.strip()
                if s.startswith("data:"):
                    # data:image/png;base64,xxxx
                    if "," in s:
                        s = s.split(",", 1)[1]
                if s.startswith(("base64,", "Base64,")):
                    s = s.split(",", 1)[1]
                # 若是文件路径且存在
                p = Path(s)
                if p.exists() and p.is_file():
                    return base64.b64encode(p.read_bytes()).decode("ascii")
                # 否则视为已是 base64
                if s:
                    return s
                return ""
            if isinstance(image, (bytes, bytearray)):
                return base64.b64encode(bytes(image)).decode("ascii")
            return ""
        except Exception:  # pragma: no cover
            return ""

    @staticmethod
    def _capture_screen_b64(region=None) -> str:
        """Pillow 截屏 → base64。无 Pillow 或失败返回空串。"""
        try:
            from PIL import ImageGrab
            import io as _io
            img = ImageGrab.grab(bbox=region, all_screens=True) if region else ImageGrab.grab(all_screens=True)
            buf = _io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:  # pragma: no cover
            return ""

    # ---------- LLM 调用 ----------
    def _describe(self, images, origin: str) -> str:
        if not images or not images[0]:
            return ""
        key = origin + ":" + images[0][:64]
        now = time.time()
        with _lock:
            cached = _cache.get(key)
            if cached and now - cached[0] < _CACHE_TTL:
                return cached[1]
        client = self._ensure_client()
        if client is None:
            return ""
        try:
            prompt = "请用中文简要描述这张图片里的主体、内容与场景（两三句话即可）。如果看不清就说看不清。"
            resp = client.chat(
                model=self.model or VISION_MODEL,
                messages=[{"role": "user", "content": prompt}],
                images=images,
                stream=False,
                options={"temperature": 0.2},
            )
            text = ((resp.get("message", {}) or {}).get("content", "") or "").strip()
            with _lock:
                _cache[key] = [now, text]
            return text
        except Exception:  # pragma: no cover
            with _lock:
                _cache[key] = [now, ""]
            return ""
