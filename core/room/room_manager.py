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
    # ===== 群聊配置（C 阶段：可配置化） =====
    enable_relay: bool = True          # 是否启用角色间接力互相搭话
    relay_rounds: Optional[int] = None # 覆盖全局默认接力轮数；None=用全局默认
    relay_style: str = "weighted"      # "all"=每轮全员开口 / "weighted"=按权重挑部分角色
    max_concurrency: int = 2           # 该房间同时进行的 LLM 调用上限
    member_timeout_s: int = 60         # 单角色单轮发言超时（秒）
    # 角色级参与配置：role_id -> {"participate": bool, "weight": float}
    member_config: Dict[str, dict] = field(default_factory=dict)

    def get_room_summary(self) -> dict:
        return {
            "room_id": self.room_id,
            "topic": self.topic,
            "created_at": self.created_at,
            "members": list(self.members.keys()),
            "config": self.get_config(),
        }

    def get_config(self) -> dict:
        return {
            "enable_relay": self.enable_relay,
            "relay_rounds": self.relay_rounds,
            "relay_style": self.relay_style,
            "max_concurrency": self.max_concurrency,
            "member_timeout_s": self.member_timeout_s,
            "member_config": dict(self.member_config),
        }

    def get_member_participate(self, role_id: str) -> bool:
        cfg = self.member_config.get(role_id)
        if cfg is None:
            return True
        return bool(cfg.get("participate", True))

    def get_member_weight(self, role_id: str) -> float:
        cfg = self.member_config.get(role_id)
        if cfg is None:
            return 1.0
        try:
            return max(0.0, float(cfg.get("weight", 1.0)))
        except (TypeError, ValueError):
            return 1.0


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

    def update_room_config(self, room_id: str, cfg: dict) -> bool:
        """更新房间群聊配置（白名单，防止任意字段写入）。

        cfg 支持：
          - enable_relay: bool
          - relay_rounds: int|None
          - relay_style: "all"|"weighted"
          - max_concurrency: int(>=1)
          - member_timeout_s: int(>=1)
          - member_config: {role_id: {"participate": bool, "weight": float}}
        非法字段忽略；返回房间是否存在。
        """
        room = self._rooms.get(room_id)
        if not room:
            return False
        if "enable_relay" in cfg:
            room.enable_relay = bool(cfg["enable_relay"])
        if "relay_rounds" in cfg:
            v = cfg["relay_rounds"]
            room.relay_rounds = None if v is None else max(0, int(v))
        if "relay_style" in cfg and cfg["relay_style"] in ("all", "weighted"):
            room.relay_style = cfg["relay_style"]
        if "max_concurrency" in cfg:
            try:
                room.max_concurrency = max(1, int(cfg["max_concurrency"]))
            except (TypeError, ValueError):
                pass
        if "member_timeout_s" in cfg:
            try:
                room.member_timeout_s = max(1, int(cfg["member_timeout_s"]))
            except (TypeError, ValueError):
                pass
        if "member_config" in cfg and isinstance(cfg["member_config"], dict):
            for role_id, mc in cfg["member_config"].items():
                if not isinstance(mc, dict):
                    continue
                cur = dict(room.member_config.get(role_id) or {})
                if "participate" in mc:
                    cur["participate"] = bool(mc["participate"])
                if "weight" in mc:
                    try:
                        cur["weight"] = max(0.0, float(mc["weight"]))
                    except (TypeError, ValueError):
                        pass
                room.member_config[role_id] = cur
        return True

    def delete_room(self, room_id: str) -> bool:
        if room_id in self._rooms:
            del self._rooms[room_id]
            return True
        return False
