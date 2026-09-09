"""
群聊消息总线
- 管理房间内消息历史（L0 群聊上下文，内存 FIFO + 落盘持久化）
- 负责把用户消息推送给房间内所有 Agent 实例
- 广播 Agent 回复给前端

D 阶段：消息落盘持久化（进程重启不丢失，支撑历史加载/恢复）。
落盘策略：
  - 每收到一条消息异步写盘（单文件 JSON，room_id -> [ChatMessage]）。
  - 写盘失败静默降级（不影响主流程，符合项目"失败降级"原则）。
  - 内存仍是最新事实源；落盘仅用于重启后恢复。
"""
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
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

    @classmethod
    def from_dict(cls, d: dict) -> "ChatMessage":
        return cls(
            message_id=d.get("message_id", ""),
            room_id=d.get("room_id", ""),
            sender_role=d.get("sender_role", ""),
            sender_user=d.get("sender_user", ""),
            content=d.get("content", ""),
            msg_type=d.get("msg_type", "text"),
            is_user=bool(d.get("is_user", True)),
            timestamp=d.get("timestamp", 0.0),
        )


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# 前端广播回调：async (room_id, data_dict) -> None
BroadcastCallback = Callable[[str, dict], Awaitable[None]]


class MessageBus:
    """群聊消息总线（单例）"""

    _instance: Optional["MessageBus"] = None
    _instance_init: Optional[str] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, history_file: Optional[str] = None):
        # 单例：仅首次调用执行初始化；历史文件允许注入（测试隔离），但同进程只初始化一次
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.message_histories: Dict[str, List[ChatMessage]] = {}
        self._broadcast_callback: Optional[BroadcastCallback] = None
        self.max_history_per_room = 200
        # D 阶段：落盘持久化文件（项目根/rooms_history.json）
        if history_file is None:
            try:
                from core.config import PROJECT_ROOT
                history_file = str(Path(PROJECT_ROOT) / "rooms_history.json")
            except Exception:
                history_file = "rooms_history.json"
        self.history_file = history_file
        self._load_history()

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

    async def broadcast_event(self, room_id: str, data: dict):
        """广播一个不入 L0 历史的事件（如 room_turn 打字指示器），仅推送到前端。"""
        await self._send_to_frontend(room_id, data)

    # ===================== D: 落盘持久化 =====================

    def _load_history(self):
        """启动时从 disk 恢复历史（失败静默，保持空历史）。"""
        try:
            if not self.history_file or not Path(self.history_file).exists():
                return
            with open(self.history_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return
            for room_id, msgs in raw.items():
                if not isinstance(msgs, list):
                    continue
                loaded = []
                for m in msgs:
                    try:
                        if isinstance(m, dict):
                            loaded.append(ChatMessage.from_dict(m))
                    except Exception:
                        continue
                if loaded:
                    self.message_histories[room_id] = loaded[-self.max_history_per_room:]
        except Exception:
            pass  # 恢复失败静默降级

    def _flush_history(self):
        """把当前所有房间历史异步写盘（内存仍为最新事实源）。"""
        try:
            if not self.history_file:
                return
            data = {}
            for room_id, msgs in self.message_histories.items():
                data[room_id] = [m.to_dict() for m in msgs[-self.max_history_per_room:]]
            Path(self.history_file).parent.mkdir(parents=True, exist_ok=True)
            # 原子写：先写临时文件再替换，避免写一半损坏
            tmp = self.history_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.history_file)
        except Exception:
            pass  # 落盘失败静默降级，不影响主流程

    # ===================== L0 群聊上下文 =====================

    def _store_message(self, room_id: str, message: ChatMessage):
        hist = self.message_histories.setdefault(room_id, [])
        hist.append(message)
        if len(hist) > self.max_history_per_room:
            self.message_histories[room_id] = hist[-self.max_history_per_room:]
        self._flush_history()

    def get_recent_messages(self, room_id: str, n: int = 30) -> List[ChatMessage]:
        return self.message_histories.get(room_id, [])[-n:]

    def get_formatted_context(
        self, room_id: str, n: int = 20,
        exclude_role: Optional[str] = None, max_chars: Optional[int] = None,
    ) -> str:
        """格式化最近 n 条消息为注入 prompt 的文本。

        - exclude_role: 若指定，跳过该角色自己的发言（避免角色跟自己对话）。
        - max_chars: 若指定，对输出做"系统公告 > 近期对话 > 旧对话"的自适应
          裁剪，防止 system prompt 膨胀（B 阶段优化）。
        """
        msgs = self.get_recent_messages(room_id, n)
        if exclude_role:
            msgs = [m for m in msgs if m.sender_role != exclude_role]

        def fmt(m: ChatMessage) -> str:
            if m.msg_type == "system":
                return f"[系统]: {m.content}"
            if m.is_user:
                return f"[{m.sender_role}(用户)]: {m.content}"
            return f"[{m.sender_role}]: {m.content}"

        if max_chars is None or max_chars <= 0:
            return "\n".join(fmt(m) for m in msgs)

        # 自适应裁剪：先保留系统公告与最近 1/3，其余按从新到旧填充，直到达到上限
        head = [m for m in msgs if m.msg_type == "system"]
        tail = msgs[max(0, len(msgs) - max(3, len(msgs) // 3)):]
        keep = head + tail
        body = [m for m in msgs if m not in keep]
        keep_text = "\n".join(fmt(m) for m in keep)
        lines = keep_text.splitlines() if keep_text else []
        chars = sum(len(ln) + 1 for ln in lines)
        for m in reversed(body):  # 从最新往前补
            line = fmt(m)
            if chars + len(line) + 1 > max_chars:
                break
            lines.insert(0, line)
            chars += len(line) + 1
        return "\n".join(lines)

    # ===================== 前端 =====================

    async def _send_to_frontend(self, room_id: str, data: dict):
        if self._broadcast_callback:
            try:
                await self._broadcast_callback(room_id, data)
            except Exception:
                pass
