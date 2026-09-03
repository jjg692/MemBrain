"""
MemBrain (Refactor) Web 入口
组装所有模块，启动服务
"""
import os
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.config import HOST, PORT
from core.initializer import AppInitializer
from core.logger import log_info
from api.websocket_manager import room_ws_manager
from api import admin

# 全局初始化器（供路由/WS 注入）
initializer = AppInitializer()

app = FastAPI(title="MemBrain (Refactor)", version="v2.0.0")

# ===================== 浏览器自动打开（仅本地启动脚本） =====================


def _open_browser():
    import webbrowser
    webbrowser.open(f"http://localhost:{PORT}")


# ===================== 挂载路由 =====================

# HTTP 路由
from api.routes import setup_routes
app.include_router(setup_routes(initializer))

# Live2D 桌面宠物（模型列表 API + 独立角色页 + 模型静态资源）
from api.live2d import setup_live2d
app.include_router(setup_live2d(app))

# 后台管理
app.include_router(admin.setup_admin(initializer))

# WebSocket
from api.websocket import register
register(app, initializer)

# 静态文件
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ===================== 启动事件 =====================

@app.on_event("startup")
async def startup():
    # 消息总线广播回调 -> 群聊前端
    initializer.message_bus.set_broadcast_callback(room_ws_manager.broadcast_to_room)
    # 启动时一次性加载所有角色 L5 事实
    def load_facts():
        initializer.load_all_role_facts()
    threading.Thread(target=load_facts, daemon=True).start()
    # 启动 L3 主动信息池（外部信息采集 + 主动推送）
    initializer.start_l3_loops()
    # 启动日程/提醒引擎（到点主动提醒 → WS 推送）
    initializer.start_reminder_scheduler()
    # 启动星露谷状态轮询（可选扩展；开关关闭时为空操作）
    initializer.start_stardew_poller()
    log_info("Startup", "启动完成")


if __name__ == "__main__":
    log_info("Startup", f"MemBrain 启动于 http://localhost:{PORT}")
    # 桌面宠物壳启动时设置 MEMBRAIN_NO_BROWSER=1（已有独立窗口，不再弹浏览器）
    if not os.getenv("MEMBRAIN_NO_BROWSER"):
        threading.Timer(1.5, _open_browser).start()
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
