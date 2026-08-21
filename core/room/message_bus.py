"""
群聊消息总线
- 管理房间内消息历史（L0 群聊上下文，内存 FIFO）
- 负责把用户消息推送给房间内所有 Agent 实例
- 广播 Agent 回复给前端
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable


@dataclass
class ChatMessage:
    message_id: str = ""
    room_id: str = ""
    sender_role: str = ""
    sender_user: str = ""
    content: str = ""
    msg_type: str = "text"
    is_user: bool = True
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "room_id": self.room_id,
            "sender_role": self.sender_role,
            "sender_user": self.sender_user,
            "content": self.content,
            "msg_type": self.msg_type,
            "is_user": self.is_user,
            "timestamp": self.timestamp,
        }


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# 前端广播回调：async (room_id, data_dict) -> None
BroadcastCallback = Callable[[str, dict], Awaitable[None]]


class MessageBus:
    """群聊消息总线（单例）"""

    _instance: Optional["MessageBus"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.message_histories: Dict[str, List[ChatMessage]] = {}
        self._broadcast_callback: Optional[BroadcastCallback] = None
        self.max_history_per_room = 200

    def set_broadcast_callback(self, cb: BroadcastCallback):
        self._broadcast_callback = cb

    # ===================== 分发 =====================

    async def broadcast(self, room_id: str, message: ChatMessage):
        self._store_message(room_id, message)
        await self._send_to_frontend(room_id, {"type": "chat_message", "data": message.to_dict()})

    async def send_agent_message(self, room_id: str, role_id: str, content: str):
        msg = ChatMessage(
            message_id=_new_id(), room_id=room_id, sender_role=role_id,
            content=content, is_user=False, msg_type="text",
        )
        await self.broadcast(room_id, msg)

    async def send_user_message(self, room_id: str, user_id: str, role_id: str, content: str):
        msg = ChatMessage(
            message_id=_new_id(), room_id=room_id, sender_role=role_id,
            sender_user=user_id, content=content, is_user=True, msg_type="text",
        )
        await self.broadcast(room_id, msg)

    async def send_system_message(self, room_id: str, content: str):
        msg = ChatMessage(
            message_id=_new_id(), room_id=room_id, sender_role="系统",
            content=content, is_user=False, msg_type="system",
        )
        await self.broadcast(room_id, msg)

    # ===================== L0 群聊上下文 =====================

    def _store_message(self, room_id: str, message: ChatMessage):
        hist = self.message_histories.setdefault(room_id, [])
        hist.append(message)
        if len(hist) > self.max_history_per_room:
            self.message_histories[room_id] = hist[-self.max_history_per_room:]

    def get_recent_messages(self, room_id: str, n: int = 30) -> List[ChatMessage]:
        return self.message_histories.get(room_id, [])[-n:]

    def get_formatted_context(self, room_id: str, n: int = 20) -> str:
        lines = []
        for msg in self.get_recent_messages(room_id, n):
            if msg.msg_type == "system":
                lines.append(f"[系统]: {msg.content}")
            elif msg.is_user:
                lines.append(f"[{msg.sender_role}(用户)]: {msg.content}")
            else:
                lines.append(f"[{msg.sender_role}]: {msg.content}")
        return "\n".join(lines)

    # ===================== 前端 =====================

    async def _send_to_frontend(self, room_id: str, data: dict):
        if self._broadcast_callback:
            try:
                await self._broadcast_callback(room_id, data)
            except Exception:
                pass
