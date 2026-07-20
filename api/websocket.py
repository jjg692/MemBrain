"""
WebSocket 端点：单聊 + 群聊
"""
import json
import time
import base64
import asyncio
from fastapi import WebSocket, WebSocketDisconnect

from core.websocket_manager import ws_manager
from core.initializer import AppInitializer
from core.logger import log_time, log_debug
from tts_client import text_to_speech
from core.room.role_pool import RoleConfig


def setup_websocket(app, initializer: AppInitializer):
    """注册 WebSocket 端点到 app"""
    
    @app.websocket("/ws/chat")
    async def websocket_chat(websocket: WebSocket):
        await websocket.accept()

        from core.websocket_manager import single_ws_manager
        single_ws_manager.add(websocket) 
        print("[WebSocket] 新连接已建立")
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                    user_id = data.get("user_id", "default_user")
                    user_message = data.get("message", "").strip()
                    image_base64 = data.get("image", None)
                    tts_enabled = data.get("tts", True)
                    if not user_message:
                        await websocket.send_text(json.dumps({"type": "error", "content": "消息不能为空！"}))
                        continue
                    agent = initializer.agent_factory.get_agent(user_id)
                    reply = agent.chat(user_id, user_message, image=image_base64)
                    audio_data = None
                    if tts_enabled:
                        try:
                            audio_data = text_to_speech(reply, language="ja", translate=True)
                        except Exception as e:
                            print(f"[TTS] 异常: {e}")
                    response = {"type": "message", "content": reply}
                    if audio_data:
                        response["audio"] = base64.b64encode(audio_data).decode('utf-8')
                    await websocket.send_text(json.dumps(response))
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({"type": "error", "content": "格式错误"}))
        except WebSocketDisconnect:
            print("[WebSocket] 断开")
        finally:
            single_ws_manager.remove(websocket)  # 新增
            try:
                await websocket.close()
            except:
                pass
    
    @app.websocket("/ws/room/{room_id}")
    async def websocket_room(websocket: WebSocket, room_id: str):
        await websocket.accept()
        print(f"[WebSocket] 房间 [{room_id}] 新连接已建立")
        ws_manager.add_connection(room_id, websocket)
        room = initializer.room_manager.get_room(room_id)
        if room:
            await websocket.send_text(json.dumps({
                "type": "room_info",
                "data": room.get_room_summary()
            }, ensure_ascii=False))
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                    action = data.get("action", "message")
                    if action == "message":
                        user_id = data.get("user_id", "anonymous")
                        role_id = data.get("role_id", "user")
                        content = data.get("content", "").strip()
                        if not content:
                            continue
                        await initializer.message_bus.send_user_message(room_id, user_id, role_id, content)
                        asyncio.create_task(process_room_message(initializer, room_id, user_id, role_id, content))
                    elif action == "join":
                        role_id = data.get("role_id", "").strip()
                        display_name = data.get("display_name", role_id)
                        user_id = data.get("user_id", role_id)
                        if role_id:
                            config = initializer.role_pool.get_role_config(role_id)
                            if not config:
                                initializer.role_pool.register_role(RoleConfig(
                                    role_id=role_id,
                                    display_name=display_name,
                                    role_prompt=f"你是{display_name}，一个友好的角色。"
                                ))
                            def agent_factory_fn(config, rid):
                                return initializer.agent_factory.get_agent(rid)
                            agent_instance = initializer.role_pool.get_agent(role_id, agent_factory_fn)
                            if agent_instance:
                                initializer.room_manager.add_agent_to_room(room_id, role_id, user_id, agent_instance)
                                await initializer.message_bus.notify_agent_join(room_id, role_id)
                                await websocket.send_text(json.dumps({
                                    "type": "join_ok",
                                    "role_id": role_id,
                                    "message": f"你已作为 {display_name} 加入房间"
                                }, ensure_ascii=False))
                    elif action == "leave":
                        role_id = data.get("role_id", "").strip()
                        if role_id:
                            await initializer.message_bus.notify_agent_leave(room_id, role_id)
                            initializer.room_manager.remove_agent_from_room(room_id, role_id)
                    elif action == "get_history":
                        n = data.get("n", 30)
                        messages = initializer.message_bus.get_recent_messages(room_id, n)
                        await websocket.send_text(json.dumps({
                            "type": "history",
                            "data": [msg.to_dict() for msg in messages]
                        }, ensure_ascii=False))
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({"type": "error", "content": "消息格式错误"}))
        except WebSocketDisconnect:
            print(f"[WebSocket] 房间 [{room_id}] 断开")
        finally:
            ws_manager.remove_connection(room_id, websocket)
    
    return app


async def process_room_message(initializer: AppInitializer, room_id: str, user_id: str, sender_role: str, content: str):
    """处理房间消息：让 Agent 回复"""
    room = initializer.room_manager.get_room(room_id)
    if not room:
        return
    agents = room.get_online_agent_list()
    if not agents:
        return
    for role_id in agents:
        agent = room.get_agent(role_id)
        if not agent or not hasattr(agent, 'chat'):
            continue
        try:
            reply = agent.chat(user_id, content)
            if reply:
                await initializer.message_bus.send_agent_message(room_id, role_id, reply)
                await asyncio.sleep(0.5)
        except Exception as e:
            log_debug("群聊", f"Agent [{role_id}] 回复失败: {e}")