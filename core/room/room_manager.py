"""
房间管理器
- 创建/销毁/查询房间
- 管理房间内的成员（Agent 角色实例）
- 群聊角色发言调度（简化版：成员依次发言）
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Room:
    room_id: str
    topic: str = ""
    created_at: float = field(default_factory=time.time)
    members: Dict[str, object] = field(default_factory=dict)  # role_id -> agent instance

    def get_room_summary(self) -> dict:
        return {
            "room_id": self.room_id,
            "topic": self.topic,
            "created_at": self.created_at,
            "members": list(self.members.keys()),
        }


class RoomManager:
    def __init__(self):
        self._rooms: Dict[str, Room] = {}

    def create_room(self, room_id: str, topic: str = "") -> Optional[Room]:
        room_id = room_id.strip()
        if not room_id or room_id in self._rooms:
            return None
        room = Room(room_id=room_id, topic=topic)
        self._rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def list_rooms(self) -> List[str]:
        return list(self._rooms.keys())

    def list_room_summaries(self) -> List[dict]:
        return [r.get_room_summary() for r in self._rooms.values()]

    def add_agent_to_room(self, room_id: str, role_id: str, agent) -> bool:
        room = self._rooms.get(room_id)
        if not room:
            return False
        room.members[role_id] = agent
        return True

    def remove_agent_from_room(self, room_id: str, role_id: str) -> bool:
        room = self._rooms.get(room_id)
        if not room:
            return False
        if role_id in room.members:
            del room.members[role_id]
            return True
        return False

    def get_member_agents(self, room_id: str) -> Dict[str, object]:
        room = self._rooms.get(room_id)
        return room.members if room else {}

    def delete_room(self, room_id: str) -> bool:
        if room_id in self._rooms:
            del self._rooms[room_id]
            return True
        return False
