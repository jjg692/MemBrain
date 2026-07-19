"""
消息总线
群聊消息的分发和广播，负责：
- 将用户消息推送给房间内所有 Agent
- 将 Agent 回复广播给前端
- 管理 WebSocket 连接与房间的映射
"""

import asyncio
import json
import time
from typing import Dict, List, Optional, Set, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime

from core.logger import log_debug, log_router


@dataclass
class ChatMessage:
    """群聊中的一条消息"""
    message_id: str = ""
    room_id: str = ""
    sender_role: str = ""       # 发送者角色 ID
    sender_user: str = ""       # 发送者 user_id（如果是用户发的）
    content: str = ""
    msg_type: str = "text"      # text, system, action
    is_user: bool = True        # True=用户消息, False=Agent消息
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


# ==================== 消息 ID 生成 ====================
import uuid


def _new_msg_id() -> str:
    return uuid.uuid4().hex[:12]


# ==================== 前端 WebSocket 回调类型 ====================
# 外部注册的回调：发送数据到指定房间的前端
WebSocketBroadcast = Callable[[str, dict], Awaitable[None]]


class MessageBus:
    """
    消息总线（单例）
    负责消息路由和分发
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
        # 房间内的消息历史（L0 群聊记忆的核心存储）
        self.message_histories: Dict[str, List[ChatMessage]] = {}
        # 前端广播回调（由 web_app 注册）
        self._broadcast_callback: Optional[WebSocketBroadcast] = None
        # 每个房间的消息上限
        self.max_history_per_room = 200

    # ==================== 外部注册 ====================

    def set_broadcast_callback(self, callback: WebSocketBroadcast):
        """注册前端广播回调（由 web_app 调用）"""
        self._broadcast_callback = callback

    # ==================== 消息发送 ====================

    async def broadcast(self, room_id: str, message: ChatMessage):
        """
        广播消息给房间内的所有 Agent 和前端
        
        1. 存储到 L0 群聊记忆
        2. 推送给前端
        """
        # 保存到历史
        self._store_message(room_id, message)

        # 推送给前端
        await self._send_to_frontend(room_id, {
            "type": "chat_message",
            "data": message.to_dict()
        })

        log_debug("MessageBus", f"[{room_id}] {message.sender_role}: {message.content[:60]}...")

    async def send_agent_message(self, room_id: str, role_id: str, content: str):
        """Agent 发送一条消息到房间"""
        msg = ChatMessage(
            message_id=_new_msg_id(),
            room_id=room_id,
            sender_role=role_id,
            content=content,
            is_user=False,
            msg_type="text",
        )
        await self.broadcast(room_id, msg)

    async def send_user_message(self, room_id: str, user_id: str, role_id: str, content: str):
        """用户发送一条消息到房间"""
        msg = ChatMessage(
            message_id=_new_msg_id(),
            room_id=room_id,
            sender_role=role_id,
            sender_user=user_id,
            content=content,
            is_user=True,
            msg_type="text",
        )
        await self.broadcast(room_id, msg)

    async def send_system_message(self, room_id: str, content: str):
        """发送系统通知"""
        msg = ChatMessage(
            message_id=_new_msg_id(),
            room_id=room_id,
            sender_role="system",
            content=content,
            is_user=False,
            msg_type="system",
        )
        await self.broadcast(room_id, msg)

    async def send_action_message(self, room_id: str, role_id: str, content: str):
        """发送动作描述（如：香澄拍了拍你）"""
        msg = ChatMessage(
            message_id=_new_msg_id(),
            room_id=room_id,
            sender_role=role_id,
            content=content,
            is_user=False,
            msg_type="action",
        )
        await self.broadcast(room_id, msg)

    # ==================== L0 群聊记忆 ====================

    def _store_message(self, room_id: str, message: ChatMessage):
        """存储消息到历史"""
        if room_id not in self.message_histories:
            self.message_histories[room_id] = []
        history = self.message_histories[room_id]
        history.append(message)
        # FIFO 淘汰
        if len(history) > self.max_history_per_room:
            self.message_histories[room_id] = history[-self.max_history_per_room:]

    def get_recent_messages(self, room_id: str, n: int = 20) -> List[ChatMessage]:
        """获取房间最近的 N 条消息（L0 群聊上下文）"""
        history = self.message_histories.get(room_id, [])
        return history[-n:]

    def get_formatted_context(self, room_id: str, n: int = 20) -> str:
        """
        获取格式化的群聊上下文（供 Agent system prompt 使用）
        返回格式：
        [香澄]: 明天去哪里玩？
        [有咲]: 去新开的咖啡馆怎么样
        """
        history = self.get_recent_messages(room_id, n)
        lines = []
        for msg in history:
            if msg.msg_type == "system":
                lines.append(f"[系统]: {msg.content}")
            elif msg.is_user:
                lines.append(f"[{msg.sender_role}(用户)]: {msg.content}")
            else:
                if msg.msg_type == "action":
                    lines.append(f"*{msg.sender_role} {msg.content}*")
                else:
                    lines.append(f"[{msg.sender_role}]: {msg.content}")
        return "\n".join(lines)

    def clear_room_history(self, room_id: str):
        """清空房间消息历史"""
        if room_id in self.message_histories:
            self.message_histories[room_id] = []

    # ==================== 前端推送 ====================

    async def _send_to_frontend(self, room_id: str, data: dict):
        """通过 WebSocket 推送数据到前端"""
        if self._broadcast_callback:
            try:
                await self._broadcast_callback(room_id, data)
            except Exception as e:
                log_debug("MessageBus", f"前端推送失败 [{room_id}]: {e}")

    # ==================== 内部 ====================

    async def notify_agent_join(self, room_id: str, role_id: str):
        """通知 Agent 加入房间"""
        await self.send_system_message(
            room_id,
            f"{role_id} 进入了房间。"
        )

    async def notify_agent_leave(self, room_id: str, role_id: str):
        """通知 Agent 离开房间"""
        await self.send_system_message(
            room_id,
            f"{role_id} 离开了房间。"
        )
