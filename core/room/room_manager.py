"""
房间管理器
负责创建/销毁/查询房间，管理房间内 Agent 和用户的关系
"""

from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentRef:
    """房间内 Agent 的引用信息"""
    role_id: str
    user_id: str
    instance: object  # LangGraphMemoryAgent 实例
    is_online: bool = True
    joined_at: datetime = field(default_factory=datetime.now)


@dataclass
class Room:
    """群聊房间"""
    room_id: str
    topic: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    agents: Dict[str, AgentRef] = field(default_factory=dict)   # role_id → AgentRef
    online_users: Set[str] = field(default_factory=set)         # 在线 user_id
    max_agents: int = 10

    def add_agent(self, role_id: str, user_id: str, agent_instance) -> bool:
        """添加一个 Agent 到房间"""
        if len(self.agents) >= self.max_agents:
            return False
        if role_id in self.agents:
            return False
        self.agents[role_id] = AgentRef(
            role_id=role_id,
            user_id=user_id,
            instance=agent_instance
        )
        return True

    def remove_agent(self, role_id: str) -> bool:
        """从房间移除一个 Agent"""
        if role_id not in self.agents:
            return False
        del self.agents[role_id]
        return True

    def get_agent(self, role_id: str) -> Optional[object]:
        """获取房间内某个 Agent 的实例"""
        ref = self.agents.get(role_id)
        return ref.instance if ref else None

    def get_online_agent_list(self) -> List[str]:
        """获取在线 Agent 的角色 ID 列表"""
        return [r.role_id for r in self.agents.values() if r.is_online]

    def get_room_summary(self) -> dict:
        """获取房间摘要信息"""
        return {
            "room_id": self.room_id,
            "topic": self.topic,
            "agent_count": len(self.agents),
            "agents": [
                {
                    "role_id": ref.role_id,
                    "user_id": ref.user_id,
                    "is_online": ref.is_online,
                }
                for ref in self.agents.values()
            ],
            "created_at": self.created_at.isoformat(),
        }


class RoomManager:
    """
    全局房间管理器（单例）
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.rooms: Dict[str, Room] = {}
        # role_id → (room_id, role_prompt_path) 的映射，用于跨房间复用角色
        self._role_registry: Dict[str, str] = {}

    # ==================== 房间 CRUD ====================

    def create_room(self, room_id: str, topic: str = "") -> Optional[Room]:
        """创建一个新房间"""
        if room_id in self.rooms:
            return None
        room = Room(room_id=room_id, topic=topic)
        self.rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Optional[Room]:
        """获取房间"""
        return self.rooms.get(room_id)

    def delete_room(self, room_id: str) -> bool:
        """删除房间"""
        if room_id not in self.rooms:
            return False
        del self.rooms[room_id]
        return True

    def list_rooms(self) -> List[str]:
        """列出所有房间 ID"""
        return list(self.rooms.keys())

    # ==================== Agent 管理 ====================

    def add_agent_to_room(self, room_id: str, role_id: str, user_id: str, agent_instance) -> bool:
        """将 Agent 加入房间"""
        room = self.get_room(room_id)
        if not room:
            return False
        success = room.add_agent(role_id, user_id, agent_instance)
        if success:
            self._role_registry[role_id] = room_id
        return success

    def remove_agent_from_room(self, room_id: str, role_id: str) -> bool:
        """从房间移除 Agent"""
        room = self.get_room(room_id)
        if not room:
            return False
        success = room.remove_agent(role_id)
        if success and self._role_registry.get(role_id) == room_id:
            del self._role_registry[role_id]
        return success

    def set_agent_online(self, room_id: str, role_id: str, online: bool = True) -> bool:
        """设置 Agent 在线状态"""
        room = self.get_room(room_id)
        if not room:
            return False
        ref = room.agents.get(role_id)
        if not ref:
            return False
        ref.is_online = online
        if online:
            room.online_users.add(ref.user_id)
        else:
            room.online_users.discard(ref.user_id)
        return True

    # ==================== 查询 ====================

    def get_room_context(self, room_id: str) -> Optional[dict]:
        """获取房间上下文（供 LangGraph 使用）"""
        room = self.get_room(room_id)
        if not room:
            return None
        return room.get_room_summary()

    def find_room_by_agent(self, role_id: str) -> Optional[str]:
        """查找某个 Agent 所在的房间 ID"""
        return self._role_registry.get(role_id)

    def get_agents_in_room(self, room_id: str) -> List[str]:
        """获取房间内所有 Agent 的角色 ID"""
        room = self.get_room(room_id)
        if not room:
            return []
        return list(room.agents.keys())
