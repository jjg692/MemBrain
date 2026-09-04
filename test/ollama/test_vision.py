"""
单元测试：本地视觉感知服务（core/vision）
================================================
覆盖：
  - 未启用（VISION_ENABLED=false）时 describe_image / describe_screen 返回空串（不伪造、零副作用）
  - _to_b64 把 base64 字符串 / 文件路径 / bytes 统一转成 base64
  - 启用时 describe_image 成功返回模型文本（mock ollama client）
  - 模型调用失败/不可达时返回空串（静默降级）
  - 结果带短缓存
"""
import base64

import core.vision as V


def _reset_cache_and_svc():
    V._cache.clear()
    V._service = None


def _png_b64() -> str:
    return base64.b64encode(b"\x89PNG\r\n\x1a\nFAKEDATA").decode("ascii")


def test_to_b64_from_b64string():
    b = _png_b64()
    assert V.VisionService._to_b64(b) == b
    # data URI
    assert V.VisionService._to_b64("data:image/png;base64," + b) == b
    # base64, 前缀
    assert V.VisionService._to_b64("base64," + b) == b


def test_to_b64_from_bytes():
    b = base64.b64encode(b"bytesdata").decode("ascii")
    assert V.VisionService._to_b64(b"bytesdata") == b


def test_to_b64_from_path(tmp_path):
    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNG-data")
    b = base64.b64encode(b"\x89PNG-data").decode("ascii")
    assert V.VisionService._to_b64(str(p)) == b


def test_to_b64_none_empty():
    assert V.VisionService._to_b64(None) == ""
    assert V.VisionService._to_b64("") == ""


def test_disabled_returns_empty(monkeypatch):
    """未启用时返回空串，且不真正调模型。"""
    _reset_cache_and_svc()
    called = []
    svc = V.VisionService(model="m", enabled=False)  # 关
    svc._ensure_client = lambda: called.append(1) or object()
    svc._describe = lambda images, origin: called.append("describe") or "不应被调"
    assert svc.describe_image("aGk=") == ""
    assert svc.describe_screen() == ""
    assert called == []


def test_describe_image_success_mock(monkeypatch):
    """启用且模型可用时，返回模型文本，且结果被缓存。"""
    _reset_cache_and_svc()
    class FakeClient:
        def chat(self, **kw):
            assert kw["images"]  # 图片确实传入
            return {"message": {"content": "一只猫在窗台上晒太阳"}}
    svc = V.VisionService(model="vl-model", enabled=True)
    svc._ensure_client = lambda: FakeClient()
    out = svc.describe_image(_png_b64())
    assert "猫" in out
    # 缓存命中：二次调用不再调模型
    calls = []
    svc._ensure_client = lambda: calls.append(1) or FakeClient()
    out2 = svc.describe_image(_png_b64())
    assert out2 == out
    assert calls == []  # 命中缓存，未再 ensure_client


def test_model_failure_returns_empty(monkeypatch):
    """模型调用异常时返回空串（静默降级，不伪造）。"""
    _reset_cache_and_svc()
    class Boom:
        def chat(self, **kw):
            raise RuntimeError("model down")
    svc = V.VisionService(model="vl", enabled=True)
    svc._ensure_client = lambda: Boom()
    assert svc.describe_image(_png_b64()) == ""
    # describe_screen 的失败降级由下一用例覆盖


def test_screen_falls_back_when_no_pillow(monkeypatch):
    """截屏失败（模拟无 Pillow）时 describe_screen 返回空。"""
    _reset_cache_and_svc()
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name == "PIL.ImageGrab":
            raise ImportError("no pillow")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    svc = V.VisionService(model="vl", enabled=True)
    assert svc.describe_screen() == ""
