"""
WebSocket 连接管理器
管理所有房间的 WebSocket 连接，提供广播功能
"""
from typing import Dict, Set
from fastapi import WebSocket
import json


class WebSocketConnectionManager:
    """管理所有房间的 WebSocket 连接"""
    
    def __init__(self):
        self._rooms: Dict[str, Set[WebSocket]] = {}
    
    def add_connection(self, room_id: str, ws: WebSocket):
        if room_id not in self._rooms:
            self._rooms[room_id] = set()
        self._rooms[room_id].add(ws)
    
    def remove_connection(self, room_id: str, ws: WebSocket):
        if room_id in self._rooms:
            self._rooms[room_id].discard(ws)
            # 如果房间为空，删除该房间的 key
            if not self._rooms[room_id]:
                del self._rooms[room_id]
    
    def get_connections(self, room_id: str) -> Set[WebSocket]:
        return self._rooms.get(room_id, set())
    
    def get_room_count(self, room_id: str) -> int:
        return len(self._rooms.get(room_id, set()))
    
    async def broadcast(self, room_id: str, data: dict):
        """向房间内所有连接广播消息"""
        if room_id not in self._rooms:
            return
        dead = set()
        for ws in self._rooms[room_id]:
            try:
                await ws.send_text(json.dumps(data, ensure_ascii=False))
            except Exception:
                dead.add(ws)
        self._rooms[room_id] -= dead

# 全局单例
ws_manager = WebSocketConnectionManager()


class SimpleConnectionManager:
    def __init__(self):
        self._connections: Set[WebSocket] = set()
    
    def add(self, ws: WebSocket):
        self._connections.add(ws)
    
    def remove(self, ws: WebSocket):
        self._connections.discard(ws)
    
    def get_all(self) -> Set[WebSocket]:
        return self._connections

# 全局单例
single_ws_manager = SimpleConnectionManager()