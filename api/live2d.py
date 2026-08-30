"""
Live2D 桌面宠物后端
- 提供模型列表 API：/api/live2d/models
- 提供模型静态资源挂载：/live2d-models/<型号相对路径>（含 model.json/.moc/纹理/动作）
- 提供独立 Live2D 角色页：GET /live2d
- 提供 Live2D 运行时静态资源（本地优先，前端负责 CDN 回退）

设计要点（为后续优化留后门）：
- 模型发现为纯目录扫描：LIVE2D_MODEL_ROOT 下每个含 model.json 的目录即一个模型，
  新模型丢进目录即可，无需改代码。
- 渲染器解耦：LIVE2D_RENDERER 指定运行时；当前仅 live2d-widget.js（Cubism2 .moc）。
  未来接入 pixi / 自研渲染器只要改配置并提供对应前端适配层。
- 所有开关都是配置项，可在 .env 调整。
"""
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from core.config import (
    LIVE2D_MODEL_ROOT,
    LIVE2D_DEFAULT_MODEL,
    LIVE2D_ENABLED,
    LIVE2D_RENDERER,
    PROJECT_ROOT,
)

# 运行时本地目录（static/live2d/runtime）
RUNTIME_DIR = Path(PROJECT_ROOT) / "static" / "live2d" / "runtime"
RUNTIME_FILES = ("L2Dwidget.min.js", "L2Dwidget.0.min.js")

# CDN 回退地址（本地运行时缺失时由前端加载；版本可在此集中维护）
CDN_BASE = "https://cdn.jsdelivr.net/npm/live2d-widget@3.1.4/lib"
CDN_FALLBACK = {
    "runtime": f"{CDN_BASE}/L2Dwidget.min.js",
    "chunk": f"{CDN_BASE}/L2Dwidget.0.min.js",
}

router = APIRouter()


def _model_root() -> Path:
    return Path(LIVE2D_MODEL_ROOT)


def _scan_models() -> list:
    """扫描模型根目录，返回每个含 model.json 的模型信息。"""
    root = _model_root()
    models = []
    if not root.exists():
        return models
    # 模型 = 根目录下任一含 model.json 的目录（支持多层：角色名/服装）
    for p in sorted(root.rglob("model.json")):
        folder = p.parent
        rel = folder.relative_to(root).as_posix()
        # 展示名：优先顶层角色目录（Toyama Kasumi），其次 model.json 的 name，最后兜底目录名
        top_level = folder.relative_to(root).parts[0] if folder != root else folder.name
        display = top_level
        iface = "cubism2"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("name"):
                display = data["name"]
            iface = data.get("version", "cubism2") if data.get("version") else "cubism2"
        except Exception:
            pass
        models.append({
            "id": rel,
            "name": display,
            "path": rel,
            # 供前端 /live2d-page.js 直接加载
            "model_url": f"/live2d-models/{quote(rel, safe='/')}/model.json",
            "root_url": f"/live2d-models/{quote(rel, safe='/')}",
            "runtime": LIVE2D_RENDERER,
            "interface": iface,
        })
    return models


def _default_model() -> str:
    """返回默认模型 id（目录相对路径）。"""
    if LIVE2D_DEFAULT_MODEL:
        return LIVE2D_DEFAULT_MODEL
    models = _scan_models()
    return models[0]["id"] if models else ""


def setup_live2d(app):
    # 挂载模型静态资源：/live2d-models/<...> -> LIVE2D_MODEL_ROOT
    root = _model_root()
    root.mkdir(parents=True, exist_ok=True)
    if LIVE2D_ENABLED:
        app.mount(
            "/live2d-models",
            StaticFiles(directory=str(root)),
            name="live2d-models",
        )

    @router.get("/live2d", response_class=HTMLResponse)
    async def live2d_page():
        return FileResponse(str(PROJECT_ROOT / "templates" / "live2d.html"))

    @router.get("/live2d-chat", response_class=HTMLResponse)
    async def live2d_chat_page():
        """双窗口模式下的独立对话窗页面（sender）"""
        return FileResponse(str(PROJECT_ROOT / "templates" / "live2d-chat.html"))

    @router.get("/api/live2d/models")
    async def list_models():
        if not LIVE2D_ENABLED:
            return {"code": -1, "message": "Live2D 已禁用"}
        models = _scan_models()
        return {
            "code": 0,
            "data": {
                "models": models,
                "default": _default_model(),
                "renderer": LIVE2D_RENDERER,
                "enabled": LIVE2D_ENABLED,
                "runtime_local": [{
                    "name": f,
                    "url": f"/static/live2d/runtime/{f}",
                    "present": (RUNTIME_DIR / f).exists(),
                } for f in RUNTIME_FILES],
                "runtime_cdn": CDN_FALLBACK,
            },
        }

    @router.get("/api/live2d/config")
    async def live2d_config():
        """返回前端初始化所需的渲染环境信息。"""
        return {
            "code": 0,
            "data": {
                "renderer": LIVE2D_RENDERER,
                "enabled": LIVE2D_ENABLED,
                "default_model": _default_model(),
                "runtime_dir": "/static/live2d/runtime",
                "runtime_local": [{
                    "name": f,
                    "url": f"/static/live2d/runtime/{f}",
                    "present": (RUNTIME_DIR / f).exists(),
                } for f in RUNTIME_FILES],
                "runtime_cdn": CDN_FALLBACK,
            },
        }

    return router
