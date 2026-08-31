"""
WebSocket 端点
- /ws/chat：私聊（携带 user_id + role_id）
- /ws/room/{room_id}：群聊（携带 room_id + role_id + content）

群聊采用"接力对话"调度：
1. 用户消息进 L0 并广播
2. 首轮：所有成员并行对用户消息各回一句
3. 接力轮：基于"包含所有人最新发言"的最新中国群聊上下文，让所有成员
   "看到别人刚说的话"并再次自然接话，循环 ROOM_RELAY_ROUNDS 次，
   形成角色之间互相搭话的多轮连续对话。
"""
import asyncio
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.initializer import AppInitializer
from core.logger import log_error
from api.websocket_manager import single_ws_manager, room_ws_manager


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# 接力对话轮数（用户发言后的额外"角色互相搭话"轮次），可被环境变量覆盖
ROOM_RELAY_ROUNDS = _int(os.getenv("ROOM_RELAY_ROUNDS", ""), 2)


def setup_websocket(app) -> AppInitializer:
    return app


def register(app, initializer: AppInitializer):
    router = APIRouter()

    # ===================== 私聊 =====================

    @router.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket, user_id: str = "default_user", role_id: str = "", mode: str = "sender"):
        """
        私聊。
        - mode=sender（默认，模型窗口）：接收用户输入，触发 agent 回复。
        - mode=watcher（对话窗口）：只读，只接收转发的事件（thinking/reply/推送），
          用于双窗口下其他窗口同步展示对话，不触发回复。
        同一 user_id 可同时有一个 sender 和多个 watcher，事件会广播给所有人的连接。
        """
        if not role_id:
            role_id = initializer.role_manager.get_default_role() or "kasumi"
        is_sender = mode != "watcher"
        await single_ws_manager.connect(user_id, ws, is_sender=is_sender)
        try:
            await ws.send_json({"type": "connected", "user_id": user_id, "role_id": role_id, "mode": mode})
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"content": raw}
                content = (data.get("content") or "").strip()
                new_role = data.get("role_id") or role_id
                image = data.get("image")
                # watcher 只接收、不发送输入
                if not is_sender:
                    continue
                if not content:
                    continue

                agent = initializer.agent_factory.get_agent(user_id, new_role)
                # 通知前端开始处理（广播给该 user 的所有窗口）
                await single_ws_manager.broadcast_to_user(user_id, {"type": "thinking", "role_id": new_role})
                reply = await asyncio.get_event_loop().run_in_executor(
                    None, agent.chat, user_id, content, image
                )
                await single_ws_manager.broadcast_to_user(user_id, {
                    "type": "reply",
                    "role_id": new_role,
                    "content": reply,
                })
                # 行为事件（契约 §3.2）：由内核推导的表情/口型/动作，随 reply 一并下发。
                # 向后兼容：若 agent 未产出 behavior（旧实现/异常），静默跳过，壳不受影响。
                behavior = getattr(agent, "last_behavior", lambda: None)()
                if behavior:
                    await single_ws_manager.broadcast_to_user(user_id, {
                        "type": "behavior",
                        "role_id": new_role,
                        **behavior,
                    })
        except WebSocketDisconnect:
            single_ws_manager.disconnect(user_id, ws)
        except Exception as e:
            log_error("WS", f"私聊异常: {e}")
            try:
                await ws.close()
            except Exception:
                pass
            single_ws_manager.disconnect(user_id, ws)

    # ===================== 群聊 =====================

    def _room_user(room_id: str) -> str:
        return "_room_" + room_id

    async def _speak_all(room_id: str, members: dict, prompt: str) -> None:
        """
        让所有成员依次发言（顺序轮流，非并行）。

        为什么顺序执行：
        - 每个 agent.chat 都会触发主模型生成（Ollama/远程 LLM）。Ollama 对并发
          请求会排队甚至卡死（曾出现整个 gather 挂起），顺序执行可避免并发打爆。
        - 顺序轮流也更契合"接力对话"：每次发言前都取最新 L0 上下文，后面的角色
          能看到前面角色刚说的话，形成自然的多轮互相搭话。

        情感说明：群聊接力传 persist_emotion=False。角色之间的对话不属于"用户对
        角色"的情感信号，不应更新对用户的感情/好感度，也避免每角色每轮重复做
        情感分析（省调用、防误染私聊维度）。
        """
        for role, agent in members.items():
            # 每次发言前都取最新 L0 上下文，保证彼此能看到最新发言（接力关键）
            ctx = initializer.message_bus.get_formatted_context(room_id, n=30)
            try:
                reply = await asyncio.get_event_loop().run_in_executor(
                    None, agent.chat, _room_user(room_id), prompt, None, ctx, False
                )
            except Exception as e:
                log_error("Room", f"{role} 发言失败: {e}")
                reply = ""
            if reply and reply.strip():
                await initializer.message_bus.send_agent_message(room_id, role, reply)

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

                # 第 1 轮：所有成员依次对用户消息各回一句
                await _speak_all(room_id, members, content)

                # 接力轮：角色们互相搭话多轮，形成连续对话
                for i in range(ROOM_RELAY_ROUNDS):
                    await _speak_all(
                        room_id, members,
                        "（群聊接力：上面是群里最新对话。请以你自己的身份自然接一句话，"
                        "回应/调侃/接续别人刚说的话，或补充一个观点。保持角色性格，简短自然，"
                        "不要重复已经说过的话，不要一次说太多。）"
                    )

        except WebSocketDisconnect:
            room_ws_manager.disconnect(room_id, ws)
        except Exception as e:
            log_error("WS", f"群聊异常: {e}")
            room_ws_manager.disconnect(room_id, ws)

    app.include_router(router)
    return app
