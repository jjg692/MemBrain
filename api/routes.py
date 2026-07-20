"""
HTTP API 路由
"""
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path
from datetime import datetime

from core.room.role_pool import RoleConfig
from core.initializer import AppInitializer
from core.config import PORT

router = APIRouter()


def setup_routes(initializer: AppInitializer):
    """将路由绑定到 initializer 实例"""
    
    @router.get("/", response_class=HTMLResponse)
    async def index():
        return FileResponse(str(Path(__file__).parent.parent / "templates" / "chat.html"))
    
    @router.get("/api/history")
    async def get_history(user_id: str = Query(default="default_user"), n: int = Query(default=20)):
        try:
            recent = initializer.memory.get_recent(user_id=user_id, n=n)
            return {"code": 0, "data": recent, "message": "ok"}
        except Exception as e:
            return {"code": -1, "data": [], "message": str(e)}
    
    @router.get("/api/search")
    async def search_memory(user_id: str = Query(default="default_user"), query: str = Query(default="")):
        try:
            results = initializer.memory.search(query=query, user_id=user_id, threshold=0.5, n_results=5)
            return {"code": 0, "data": results.get("results", []), "message": "ok"}
        except Exception as e:
            return {"code": -1, "data": [], "message": str(e)}
    
    @router.get("/health")
    async def health():
        return {"status": "alive", "time": datetime.now().isoformat(), "version": "v0.0.1-缝合版"}
    
    # ===== 群聊 Room API =====
    @router.get("/api/rooms")
    async def list_rooms():
        rooms = initializer.room_manager.list_rooms()
        return {"code": 0, "data": [initializer.room_manager.get_room_context(rid) for rid in rooms if initializer.room_manager.get_room_context(rid)]}
    
    @router.post("/api/rooms/create")
    async def create_room_api(request: Request):
        body = await request.json()
        room_id = body.get("room_id", "").strip()
        topic = body.get("topic", "")
        if not room_id:
            return {"code": -1, "message": "room_id 不能为空"}
        room = initializer.room_manager.create_room(room_id, topic)
        if not room:
            return {"code": -1, "message": f"房间 {room_id} 已存在"}
        return {"code": 0, "data": room.get_room_summary(), "message": f"房间 {room_id} 创建成功"}
    
    @router.get("/api/rooms/{room_id}")
    async def get_room_info(room_id: str):
        room = initializer.room_manager.get_room(room_id)
        if not room:
            return {"code": -1, "message": f"房间 {room_id} 不存在"}
        return {"code": 0, "data": room.get_room_summary()}
    
    @router.post("/api/rooms/{room_id}/join")
    async def join_room_api(room_id: str, request: Request):
        body = await request.json()
        role_id = body.get("role_id", "").strip()
        user_id = body.get("user_id", role_id)
        display_name = body.get("display_name", role_id)
        if not role_id:
            return {"code": -1, "message": "role_id 不能为空"}
        room = initializer.room_manager.get_room(room_id)
        if not room:
            return {"code": -1, "message": f"房间 {room_id} 不存在"}
        config = initializer.role_pool.get_role_config(role_id)
        if not config:
            initializer.role_pool.register_role(RoleConfig(
                role_id=role_id,
                display_name=display_name,
                role_prompt=f"你是{display_name}，一个友好的角色。请在群聊中根据你的人设发言。",
            ))
        def agent_factory_fn(config, rid):
            return initializer.agent_factory.get_agent(rid)
        agent_instance = initializer.role_pool.get_agent(role_id, agent_factory_fn)
        if not agent_instance:
            return {"code": -1, "message": f"无法创建角色 {role_id} 的 Agent 实例"}
        success = initializer.room_manager.add_agent_to_room(room_id, role_id, user_id, agent_instance)
        if not success:
            return {"code": -1, "message": f"角色 {role_id} 加入房间失败"}
        await initializer.message_bus.notify_agent_join(room_id, role_id)
        return {"code": 0, "message": f"{role_id} 加入房间 {room_id}"}
    
    @router.post("/api/rooms/{room_id}/leave")
    async def leave_room_api(room_id: str, request: Request):
        body = await request.json()
        role_id = body.get("role_id", "").strip()
        if not role_id:
            return {"code": -1, "message": "role_id 不能为空"}
        await initializer.message_bus.notify_agent_leave(room_id, role_id)
        success = initializer.room_manager.remove_agent_from_room(room_id, role_id)
        if not success:
            return {"code": -1, "message": f"角色 {role_id} 未在房间 {room_id} 中"}
        return {"code": 0, "message": f"{role_id} 离开房间 {room_id}"}
    
    @router.get("/api/rooms/{room_id}/messages")
    async def get_room_messages(room_id: str, n: int = Query(default=30)):
        messages = initializer.message_bus.get_recent_messages(room_id, n)
        return {"code": 0, "data": [msg.to_dict() for msg in messages]}
    
    return router