"""
HTTP API 路由
- /api/contacts           联系人列表（角色列表）
- /api/history            历史消息（按 user_id + role_id）
- /api/rooms              群聊列表
- /api/rooms/create       创建群聊
- /api/health            健康检查
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from core.initializer import AppInitializer

router = APIRouter()


def setup_routes(initializer: AppInitializer):
    app = initializer

    @router.get("/", response_class=HTMLResponse)
    async def index():
        return FileResponse(str(Path(__file__).parent.parent / "templates" / "chat.html"))

    @router.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        return FileResponse(str(Path(__file__).parent.parent / "templates" / "admin.html"))

    @router.get("/health")
    async def health():
        return {
            "status": "alive",
            "time": datetime.now().isoformat(),
            "version": "v2.0-membrain-refactor",
        }

    # ===================== 联系人 =====================

    @router.get("/api/contacts")
    async def contacts():
        return {"code": 0, "data": app.get_contact_info()}

    # ===================== 历史消息 =====================

    @router.get("/api/history")
    async def history(
        user_id: str = Query(default="default_user"),
        role_id: str = Query(default=""),
        n: int = Query(default=30),
    ):
        """返回 (user_id, role_id) 的 L1 + L2 历史"""
        if not role_id:
            role_id = app.role_manager.get_default_role() or "kasumi"
        # L2 短期记忆（持久）
        results = app.memory.get(
            where=app.memory._build_where(
                {"user_id": user_id}, {"role_id": role_id}, {"type": "short_term"}
            ),
            limit=n,
        )
        items = []
        for item in results["results"]:
            doc = item["document"]
            user_part, _, asst_part = doc.partition("\n助手：")
            items.append({
                "role": "user",
                "content": user_part.replace("用户：", "").strip(),
            })
            if asst_part:
                items.append({"role": "assistant", "content": asst_part.strip()})
        return {"code": 0, "data": items}

    # ===================== 用户资料（昵称） =====================

    @router.get("/api/profile")
    async def get_profile(user_id: str = Query(default="default_user")):
        """获取用户资料（昵称）"""
        return {"code": 0, "data": app.user_profile.get_profile(user_id)}

    @router.post("/api/profile")
    async def set_profile(request: Request):
        """设置用户资料（昵称）。body: {user_id?, nickname}"""
        body = await request.json()
        user_id = body.get("user_id") or "default_user"
        nickname = body.get("nickname") or ""
        saved = app.user_profile.set_nickname(user_id, nickname)
        return {"code": 0, "data": {"user_id": user_id, "nickname": saved}, "message": "已保存"}

    # ===================== 群聊房间 =====================

    @router.get("/api/rooms")
    async def list_rooms():
        return {"code": 0, "data": app.room_manager.list_room_summaries()}

    @router.post("/api/rooms/create")
    async def create_room(request: Request):
        body = await request.json()
        room_id = (body.get("room_id") or "").strip()
        topic = body.get("topic", "")
        if not room_id:
            return {"code": -1, "message": "room_id 不能为空"}
        room = app.room_manager.create_room(room_id, topic)
        if not room:
            return {"code": -1, "message": f"房间 {room_id} 已存在"}
        return {"code": 0, "data": room.get_room_summary(), "message": "创建成功"}

    @router.get("/api/rooms/{room_id}")
    async def get_room(room_id: str):
        room = app.room_manager.get_room(room_id)
        if not room:
            return {"code": -1, "message": "房间不存在"}
        return {"code": 0, "data": room.get_room_summary()}

    @router.post("/api/rooms/{room_id}/join")
    async def join_room(room_id: str, request: Request):
        body = await request.json()
        role_id = body.get("role_id", "").strip()
        if not role_id:
            return {"code": -1, "message": "role_id 不能为空"}
        room = app.room_manager.get_room(room_id)
        if not room:
            return {"code": -1, "message": "房间不存在"}
        agent = app.agent_factory.get_role_agent(role_id)
        if not app.room_manager.add_agent_to_room(room_id, role_id, agent):
            return {"code": -1, "message": "加入失败"}
        await app.message_bus.send_system_message(room_id, f"角色 {role_id} 加入了房间")
        return {"code": 0, "message": "加入成功"}

    @router.post("/api/rooms/{room_id}/leave")
    async def leave_room(room_id: str, request: Request):
        body = await request.json()
        role_id = body.get("role_id", "").strip()
        if app.room_manager.remove_agent_from_room(room_id, role_id):
            await app.message_bus.send_system_message(room_id, f"角色 {role_id} 离开了房间")
            return {"code": 0, "message": "已离开"}
        return {"code": -1, "message": "角色不在房间中"}

    @router.get("/api/rooms/{room_id}/messages")
    async def room_messages(room_id: str, n: int = Query(default=30)):
        msgs = app.message_bus.get_recent_messages(room_id, n)
        return {"code": 0, "data": [m.to_dict() for m in msgs]}

    return router
