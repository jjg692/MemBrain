"""
WebSocket 端点
- /ws/chat：私聊（携带 user_id + role_id）
- /ws/room/{room_id}：群聊（携带 room_id + role_id + content）

群聊采用"接力对话"调度（A 阶段：交给 core.room.scheduler.RoomTurnScheduler）：
1. 用户消息进 L0 并广播
2. 首轮：成员对用户消息各回一句（有界并发，受信号量约束）
3. 接力轮：基于最新 L0 上下文，让成员互相搭话（按房间 relay_rounds，可配置）
4. 用户新消息会打断当前接力并重启（可中途插话）
调度器负责：有界并发、单角色超时、成员参与度/权重、room_turn 打字指示器、失败降级。
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


# 全局默认接力轮数（用户发言后额外"角色互相搭话"轮次）；可由环境变量覆盖，
# 也可被房间级 relay_rounds 按房间覆盖（C 阶段）。
ROOM_RELAY_ROUNDS = _int(os.getenv("ROOM_RELAY_ROUNDS", ""), 2)
# 全局默认并发上限
ROOM_MAX_CONCURRENCY = _int(os.getenv("ROOM_MAX_CONCURRENCY", ""), 2)

# 共享群聊调度器（单例）：首次 register 时惰性创建，绑定 initializer 的单例 bus + room_manager。
_room_scheduler = None
_scheduler_initializer = None


def _init_scheduler(initializer):
    """用 initializer 的 bus + room_manager 初始化共享调度器（幂等，避免多实例错配）。"""
    global _room_scheduler, _scheduler_initializer
    if _scheduler_initializer is initializer and _room_scheduler is not None:
        return _room_scheduler
    from core.room.scheduler import RoomTurnScheduler
    _room_scheduler = RoomTurnScheduler(
        initializer.message_bus, initializer.room_manager,
        config={"relay_rounds": ROOM_RELAY_ROUNDS, "max_concurrency": ROOM_MAX_CONCURRENCY},
    )
    _scheduler_initializer = initializer
    return _room_scheduler


def get_room_scheduler():
    return _room_scheduler


# 星露谷"游戏行动"关键词闸门与指令投喂已收敛到 stardew.boot.feed_command，
# 主代码（websocket）只做依赖调用，不内嵌星露谷实现逻辑。


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

                # 星露谷扩展归口：若有"游戏行动"意味的指令，喂给自主游玩心跳
                # （逻辑收敛到 stardew.boot.feed_command，插件域，不影响主项目对话）。
                try:
                    from stardew.boot import feed_command
                    feed_command(initializer, content)
                except Exception:
                    pass

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

                # 交给调度器排演一轮接力（有界并发 + 超时 + 可打断 + 打字指示器）
                scheduler = _init_scheduler(initializer)
                scheduler.submit_user_message(room_id)

        except WebSocketDisconnect:
            room_ws_manager.disconnect(room_id, ws)
        except Exception as e:
            log_error("WS", f"群聊异常: {e}")
            room_ws_manager.disconnect(room_id, ws)

    app.include_router(router)
    return app
