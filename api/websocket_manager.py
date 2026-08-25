"""
WebSocket 连接管理器
管理私聊 / 群聊的前端连接，并提供一个群聊广播回调
"""
import asyncio
from typing import Dict, List, Set

from fastapi import WebSocket


class SingleConnectionManager:
    """私聊连接：每个 user_id 一个 WebSocket"""

    def __init__(self):
        self._connections: Dict[str, WebSocket] = {}

    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        # 同 user 旧连接关闭
        old = self._connections.get(user_id)
        if old:
            try:
                await old.close()
            except Exception:
                pass
        self._connections[user_id] = ws

    def disconnect(self, user_id: str):
        self._connections.pop(user_id, None)

    def get(self, user_id: str) -> WebSocket | None:
        return self._connections.get(user_id)

    def get_all(self) -> List[WebSocket]:
        return list(self._connections.values())

    async def push_to_user(self, user_id: str, data: dict):
        """向在线用户主动推送（用于 L3/定时主动消息）"""
        ws = self._connections.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception:
            return False

    def user_ids(self) -> List[str]:
        return list(self._connections.keys())


class RoomConnectionManager:
    """群聊连接：room_id -> set[WebSocket]"""

    def __init__(self):
        self._rooms: Dict[str, Set[WebSocket]] = {}

    async def connect(self, room_id: str, ws: WebSocket):
        await ws.accept()
        self._rooms.setdefault(room_id, set()).add(ws)

    def disconnect(self, room_id: str, ws: WebSocket):
        room = self._rooms.get(room_id)
        if room:
            room.discard(ws)
            if not room:
                self._rooms.pop(room_id, None)

    async def broadcast_to_room(self, room_id: str, data: dict):
        """向某个房间的所有前端发送"""
        room = self._rooms.get(room_id)
        if not room:
            return
        dead = []
        for ws in list(room):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            room.discard(ws)

    def count(self) -> int:
        return sum(len(s) for s in self._rooms.values())


# 单例
single_ws_manager = SingleConnectionManager()
room_ws_manager = RoomConnectionManager()
