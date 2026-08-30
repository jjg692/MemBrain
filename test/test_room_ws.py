"""
单元测试：群聊承接（房间 / 消息总线 / 轮转） + WS 双窗口管理器
================================================
- MessageBus：存储消息、格式化群聊上下文
- RoomManager：建房 / 加角色 / 取成员 / 删房
- 群聊 send_agent_message 持久化与广播回调
- SingleConnectionManager：sender + watcher 双窗口、广播、断线清理
"""
import asyncio

import pytest


# ===================== 消息总线 =====================

def test_message_bus_store_and_context():
    from core.room.message_bus import MessageBus
    bus = MessageBus.__new__(MessageBus)
    bus._initialized = True
    bus.message_histories = {}
    bus._broadcast_callback = None
    bus.max_history_per_room = 200

    import asyncio
    async def run():
        await bus.send_user_message("room1", "u1", "kasumi", "大家好")
        await bus.send_agent_message("room1", "kokoro", "大家好呀")
    asyncio.run(run())

    ctx = bus.get_formatted_context("room1", 20)
    assert "大家好" in ctx
    assert "kokoro" in ctx
    assert "kasumi(用户)" in ctx or "kasumi" in ctx


def test_message_bus_system_message():
    from core.room.message_bus import MessageBus
    bus = MessageBus.__new__(MessageBus)
    bus._initialized = True
    bus.message_histories = {}
    bus._broadcast_callback = None
    bus.max_history_per_room = 200

    async def run():
        await bus.send_system_message("room1", "房主加入了")
    asyncio.run(run())
    ctx = bus.get_formatted_context("room1")
    assert "房主加入了" in ctx
    assert "[系统]" in ctx


# ===================== 房间管理器 =====================

def test_room_manager_crud():
    from core.room.room_manager import RoomManager
    rm = RoomManager()
    room = rm.create_room("r1", "测试群")
    assert room is not None
    assert rm.get_room("r1") is room
    assert "r1" in rm.list_rooms()

    rm.add_agent_to_room("r1", "kasumi", object())
    rm.add_agent_to_room("r1", "kokoro", object())
    members = rm.get_member_agents("r1")
    assert set(members.keys()) == {"kasumi", "kokoro"}
    assert rm.remove_agent_from_room("r1", "kasumi") is True
    assert "kasumi" not in rm.get_member_agents("r1")

    rm.delete_room("r1")
    assert rm.get_room("r1") is None


def test_room_manager_duplicate_reject():
    from core.room.room_manager import RoomManager
    rm = RoomManager()
    rm.create_room("r1")
    assert rm.create_room("r1") is None   # 重复房间拒绝


# ===================== 群聊：轮转时跳过情感持久化 =====================

def test_group_relay_persist_emotion_false_skips_analysis(agent):
    """
    群聊接力 persist_emotion=False：不应触发情感分析（避免把角色间对话
    误当用户情感信号）。通过检查 tool 适配器（情感分析）是否被调用验证。
    """
    ag, llm, tool, mngr = agent
    # persist_emotion=False：不调用 analyzer。但主模型仍需返回回复
    llm.enqueue_tools("（kasumi 接话）我懂你说的！")
    tool.chat_queue = []  # 不预置情感 JSON，若被调用会走 default 但仍可计数
    before = tool.chat_call_count
    ag.chat("room_u", "有道理", persist_emotion=False)
    # 情感分析走 analyzer -> tool_adapter.chat；persist_emotion=False 应跳过
    # 注意：可能有 L4 事实抽取线程也走 chat，这里仅验证"无前台 analyzer 调用"
    # L4 后台抽取会异步 +0.xx，我们用主流程立即返回后检查计数接近 0
    assert tool.chat_call_count == before


# ===================== WS 双窗口管理器 =====================

class FakeWS:
    """最小 WebSocket 桩，支持 accept/send_json/close."""
    def __init__(self):
        self.accepted = False
        self.sent = []
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _new_manager():
    from api.websocket_manager import SingleConnectionManager
    return SingleConnectionManager()


async def _connect(manager, uid, is_sender):
    ws = FakeWS()
    await manager.connect(uid, ws, is_sender=is_sender)
    return ws


def test_ws_sender_plus_watcher(monkeypatch):
    """同一 user 可同时有 sender + watcher，事件广播给两者。"""
    from api.websocket_manager import SingleConnectionManager
    m = SingleConnectionManager()

    async def run():
        sender = FakeWS(); watcher = FakeWS()
        await m.connect("u1", sender, is_sender=True)
        await m.connect("u1", watcher, is_sender=False)
        assert m.get_sender("u1") is sender
        ok = await m.broadcast_to_user("u1", {"type": "reply", "content": "hi"})
        return ok, sender, watcher

    ok, sender, watcher = asyncio.run(run())
    assert ok is True
    assert sender.sent[-1]["type"] == "reply"
    assert watcher.sent[-1]["type"] == "reply"


def test_ws_duplicate_sender_replaces_old(monkeypatch):
    """重复 sender 时旧 sender 被关闭替换。"""
    m = _new_manager()

    async def run():
        old = FakeWS(); new = FakeWS()
        await m.connect("u1", old, is_sender=True)
        await m.connect("u1", new, is_sender=True)
        return old, new, m.get_sender("u1")

    old, new, current = asyncio.run(run())
    assert old.closed is True
    assert current is new


def test_ws_disconnect_and_watcher_survives(monkeypatch):
    """sender 断开后 watcher 仍保留；全部断开后清理。"""
    m = _new_manager()

    async def run():
        sender = FakeWS(); watcher = FakeWS()
        await m.connect("u1", sender, is_sender=True)
        await m.connect("u1", watcher, is_sender=False)
        # 断开 sender
        m.disconnect("u1", sender)
        assert m.get_sender("u1") is None       # sender 没了
        assert "u1" in m.user_ids()             # watcher 还在
        ok = await m.broadcast_to_user("u1", {"type": "x"})
        assert ok is True
        # 断开 watcher -> 全清
        m.disconnect("u1", watcher)
        assert "u1" not in m.user_ids()

    asyncio.run(run())


def test_ws_push_to_user_broadcasts(monkeypatch):
    """push_to_user 广播给用户的所有连接。"""
    m = _new_manager()

    async def run():
        a = FakeWS(); b = FakeWS()
        await m.connect("u1", a, is_sender=True)
        await m.connect("u1", b, is_sender=False)
        ok = await m.push_to_user("u1", {"type": "proactive"})
        return ok, a, b

    ok, a, b = asyncio.run(run())
    assert ok is True
    assert a.sent[-1]["type"] == "proactive"
    assert b.sent[-1]["type"] == "proactive"
