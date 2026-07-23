"""
MemBrain Web 入口
组装所有模块，启动服务
"""
import os
import threading
import uvicorn
import asyncio
import time
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from core.config import HOST, PORT, API_BASE, L3_PUSH_INTERVAL  # 新增 L3_PUSH_INTERVAL
from core.initializer import AppInitializer
from core.websocket_manager import ws_manager, single_ws_manager  # 新增 single_ws_manager
from api.routes import setup_routes
from api.websocket import setup_websocket

def load_system_prompt() -> str:
    prompt_file = Path(__file__).parent / "role_prompt.txt"
    if prompt_file.exists():
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "你是一个有帮助、友好的助手。"


# 1. 初始化
system_prompt = load_system_prompt()
initializer = AppInitializer(system_prompt, API_BASE)

# 2. 创建 FastAPI app
app = FastAPI(title="MemBrain", version="v0.0.1-缝合版")

# ================== 后台管理模块 ==================
from api import admin
app.include_router(admin.router)

# 注入依赖（从 initializer 中获取）
admin.memory = initializer.memory
admin.agent_factory = initializer.agent_factory
admin.room_manager = initializer.room_manager
admin.role_pool = initializer.role_pool
admin.l3_manager = initializer.l3_manager

# 3. 挂载静态文件
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 4. 注册路由
app.include_router(setup_routes(initializer))

# 5. 注册 WebSocket 端点
app = setup_websocket(app, initializer)

# 6. 注册启动事件（消息总线广播回调）
@app.on_event("startup")
async def startup():
    initializer.message_bus.set_broadcast_callback(ws_manager.broadcast)
    print("[启动] 消息总线广播回调已注册")


def l3_active_push_task():
    """主动推送 L3 信息给在线单聊用户（群聊不触发）"""
    import json
    import asyncio

    while True:
        time.sleep(L3_PUSH_INTERVAL)
        try:
            conns = single_ws_manager.get_all()
            if not conns:
                print("[L3 推送] 没有在线单聊连接，跳过")
                continue

            async def push_to_connections():
                user_ids = ["web_user", "default_user"]
                pushed = 0
                for user_id in user_ids:
                    l3_items = initializer.l3_manager.get_pending_shares(user_id, n=3)
                    if not l3_items:
                        continue
                    
                    lines = ["【B站热门视频】"]
                    for item in l3_items:
                        info = json.loads(item["content"])
                        title = info.get('title', '')
                        url = info.get('url', '')  # 获取 URL
                        
                        if url:
                            lines.append(f"• {title}\n  🔗 {url}")  # 带链接
                        else:
                            lines.append(f"• {title}")
                    
                    menu_msg = "\n".join(lines)

                    for item in l3_items:
                        initializer.l3_manager.mark_shared(user_id, item["id"])

                    for ws in conns:
                        try:
                            await ws.send_text(json.dumps({
                                "type": "system",
                                "content": menu_msg,
                                "from_l3": True
                            }))
                            pushed += 1
                        except Exception as e:
                            print(f"[L3 推送] 发送失败: {e}")
                
                if pushed > 0:
                    print(f"[L3 推送] 已向 {pushed} 个连接推送")

            asyncio.run(push_to_connections())

        except Exception as e:
            print(f"[L3 推送] 异常: {e}")

# 7. 启动时初始化角色事实和 L3
def init_on_startup():
    initializer.init_role_facts()
    initializer.start_l3_scheduler()
    
    # 延迟启动主动推送任务（30秒后开始）
    def start_active_push():
        time.sleep(30)
        l3_active_push_task()
    threading.Thread(target=start_active_push, daemon=True).start()


if __name__ == "__main__":
    init_on_startup()
    
    import webbrowser
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")