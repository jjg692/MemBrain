"""
WebSocket 端点
- /ws/chat：私聊（携带 user_id + role_id）
- /ws/room/{room_id}：群聊（携带 room_id + role_id + content）
"""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.initializer import AppInitializer
from core.logger import log_error
from api.websocket_manager import single_ws_manager, room_ws_manager


def setup_websocket(app) -> AppInitializer:
    return app


def register(app, initializer: AppInitializer):
    router = APIRouter()

    # ===================== 私聊 =====================

    @router.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket, user_id: str = "default_user", role_id: str = ""):
        if not role_id:
            role_id = initializer.role_manager.get_default_role() or "kasumi"
        await single_ws_manager.connect(user_id, ws)
        try:
            await ws.send_json({"type": "connected", "user_id": user_id, "role_id": role_id})
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"content": raw}
                content = (data.get("content") or "").strip()
                new_role = data.get("role_id") or role_id
                image = data.get("image")
                if not content:
                    continue

                agent = initializer.agent_factory.get_agent(user_id, new_role)
                # 通知前端开始处理
                await ws.send_json({"type": "thinking", "role_id": new_role})
                reply = await asyncio.get_event_loop().run_in_executor(
                    None, agent.chat, user_id, content, image
                )
                await ws.send_json({
                    "type": "reply",
                    "role_id": new_role,
                    "content": reply,
                })
        except WebSocketDisconnect:
            single_ws_manager.disconnect(user_id)
        except Exception as e:
            log_error("WS", f"私聊异常: {e}")
            try:
                await ws.close()
            except Exception:
                pass
            single_ws_manager.disconnect(user_id)

    # ===================== 群聊 =====================

    @router.websocket("/ws/room/{room_id}")
    async def ws_room(ws: WebSocket, room_id: str, role_id: str = ""):
        await room_ws_manager.connect(room_id, ws)
        try:
            await ws.send_json({"type": "connected", "room_id": room_id, "role_id": role_id})
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"content": raw}
                content = (data.get("content") or "").strip()
                sender_role = data.get("role_id") or role_id or "web_user"
                user_id = data.get("user_id") or "room_user"
                if not content:
                    continue

                # 用户消息进 L0 并广播（供所有成员 Agent 看到）
                await initializer.message_bus.send_user_message(room_id, user_id, sender_role, content)

                members = initializer.room_manager.get_member_agents(room_id)
                if not members:
                    await initializer.message_bus.send_system_message(room_id, "房间还没有成员，快去邀请角色吧～")
                    continue

                room_context = initializer.message_bus.get_formatted_context(room_id, n=20)

                # 并行让每个成员 Agent 回复
                async def run_member(role, agent):
                    try:
                        reply = await asyncio.get_event_loop().run_in_executor(
                            None, agent.chat, "_room_" + room_id, content, None, room_context
                        )
                        return role, reply
                    except Exception as e:
                        log_error("Room", f"{role} 回复失败: {e}")
                        return role, f"（{role} 暂时无法回复）"

                results = await asyncio.gather(*(run_member(r, a) for r, a in members.items()))
                for role, reply in results:
                    if reply and reply.strip():
                        await initializer.message_bus.send_agent_message(room_id, role, reply)

        except WebSocketDisconnect:
            room_ws_manager.disconnect(room_id, ws)
        except Exception as e:
            log_error("WS", f"群聊异常: {e}")
            room_ws_manager.disconnect(room_id, ws)

    app.include_router(router)
    return app
