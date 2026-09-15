"""
WebSocket 连接管理器
管理私聊 / 群聊的前端连接，并提供一个群聊广播回调

私聊支持"双窗口"：同一个 user_id 同时可有多个连接
- sender（模型窗口）：可发送输入触发 agent 回复
- watcher（对话窗口）：只读，接收与 sender 同样的 thinking/reply/推送事件，
  用于在原聊天界面显示完整对话，但不触发回复
"""
import asyncio
from typing import Dict, List, Set, Tuple

from fastapi import WebSocket


class _PrivateConn:
    """一条私聊连接：ws + 是否 sender"""
    __slots__ = ("ws", "is_sender")

    def __init__(self, ws: WebSocket, is_sender: bool):
        self.ws = ws
        self.is_sender = is_sender


class SingleConnectionManager:
    """私聊连接：每个 user_id 可能有多个 WebSocket（sender + watcher）"""

    def __init__(self):
        self._connections: Dict[str, List[_PrivateConn]] = {}

    async def connect(self, user_id: str, ws: WebSocket, is_sender: bool = True):
        await ws.accept()
        conns = self._connections.setdefault(user_id, [])
        # 允许多个 sender 共存（如：主聊天窗 + 双击角色弹出的对话框）。
        # 每个 sender 发消息都会触发回复，回复通过 broadcast_to_user 广播给
        # 该 user 的**所有**连接（多个 sender + watcher），从而多窗内容完全同步，
        # 而不是让新 sender 顶掉旧的（旧行为会导致双击对话框与聊天窗互相挤断、不同步）。
        conns.append(_PrivateConn(ws, is_sender))

    def disconnect(self, user_id: str, ws: WebSocket = None):
        conns = self._connections.get(user_id)
        if not conns:
            return
        if ws is None:
            self._connections.pop(user_id, None)
            return
        conns[:] = [c for c in conns if c.ws is not ws]
        if not conns:
            self._connections.pop(user_id, None)

    async def broadcast_to_user(self, user_id: str, data: dict):
        """向某个 user 的所有连接发送（sender + watcher）"""
        conns = self._connections.get(user_id)
        if not conns:
            return False
        dead = []
        for c in list(conns):
            try:
                await c.ws.send_json(data)
            except Exception:
                dead.append(c.ws)
        for w in dead:
            self.disconnect(user_id, w)
        return True

    def get_sender(self, user_id: str) -> WebSocket | None:
        """返回该 user 的某个 sender 连接（用于反向单发）。

        允许多个 sender 共存后，此方法返回第一个 sender（弱路由）。
        业务侧广播一律用 broadcast_to_user（发给所有连接）；仅测试/特定场景用此方法。
        """
        for c in self._connections.get(user_id, []):
            if c.is_sender:
                return c.ws
        return None

    def get_all(self) -> List[WebSocket]:
        return [c.ws for conns in self._connections.values() for c in conns]

    async def push_to_user(self, user_id: str, data: dict):
        """向在线用户主动推送（L3/定时主动消息）——广播给所有连接"""
        return await self.broadcast_to_user(user_id, data)

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
