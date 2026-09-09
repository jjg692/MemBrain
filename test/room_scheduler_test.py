"""
群聊优化（A/B/C 阶段）测试：
- Room 配置字段 + update_room_config
- MessageBus 上下文自适应裁剪 + 按角色排除 + 落盘
- RoomTurnScheduler：并发限制、@点名、参与度/权重挑选、主调度流程
"""
import asyncio
import json
import os
import tempfile

import pytest

from core.room.message_bus import ChatMessage, MessageBus
from core.room.room_manager import Room, RoomManager
from core.room.scheduler import RoomTurnScheduler


# ===================== 工具：Fake Agent =====================

class FakeAgent:
    """同步 fake agent：记录最后一次调用参数，返回固定文本（可配置延迟/抛错）。"""
    def __init__(self, reply="你好！", delay=0.0, error=None):
        self.reply = reply
        self.delay = delay
        self.error = error
        self.calls = []  # (user_id, prompt, room_context, persist_emotion)

    def chat(self, user_id, user_message, image=None, room_context=None, persist_emotion=True):
        self.calls.append((user_id, user_message, room_context, persist_emotion))
        if self.error:
            raise self.error
        if self.delay:
            import time
            time.sleep(self.delay)
        return self.reply


@pytest.fixture(autouse=True)
def _reset_message_bus_singleton():
    """每个测试前重置 MessageBus 单例，隔离状态 + 关闭落盘（不写真实文件）。"""
    MessageBus._instance = None
    yield
    MessageBus._instance = None


def _fresh_bus(history_file=None):
    """构造一个隔离的 MessageBus（单例已被 fixture 重置）。

    默认写入临时文件，避免污染项目根的 rooms_history.json。
    """
    if history_file is None:
        d = tempfile.mkdtemp()
        history_file = os.path.join(d, "hist.json")
    return MessageBus(history_file=history_file)


def make_room(room_id="g1", role_ids=("kasumi", "sayo", "rimi")):
    room = Room(room_id=room_id)
    for rid in role_ids:
        room.members[rid] = FakeAgent()
    return room


# ===================== C: Room 配置 =====================

def test_room_config_fields_defaults():
    room = Room(room_id="g1")
    assert room.enable_relay is True
    assert room.relay_rounds is None
    assert room.relay_style == "weighted"
    assert room.max_concurrency == 2
    assert room.member_timeout_s == 60


def test_room_update_config_whitelist():
    rm = RoomManager()
    rm.create_room("g1")
    ok = rm.update_room_config("g1", {
        "enable_relay": False,
        "relay_rounds": 0,
        "relay_style": "all",
        "max_concurrency": 5,
        "member_timeout_s": 30,
        "member_config": {"kasumi": {"participate": False, "weight": 0.5}},
    })
    assert ok is True
    room = rm.get_room("g1")
    assert room.enable_relay is False
    assert room.relay_rounds == 0
    assert room.relay_style == "all"
    assert room.max_concurrency == 5
    assert room.member_timeout_s == 30
    assert room.get_member_participate("kasumi") is False
    assert room.get_member_weight("kasumi") == 0.5
    # 默认角色默认参与
    assert room.get_member_participate("sayo") is True


def test_room_update_config_ignores_unknown():
    rm = RoomManager()
    rm.create_room("g1")
    ok = rm.update_room_config("g1", {"bogus_field": 1, "max_concurrency": 99})
    assert ok is True
    room = rm.get_room("g1")
    assert room.max_concurrency == 99
    assert not hasattr(room, "bogus_field")
    # 不存在的房间
    assert rm.update_room_config("nope", {}) is False


# ===================== B: MessageBus 上下文 =====================

@pytest.fixture
def bus_tmp():
    d = tempfile.mkdtemp()
    return os.path.join(d, "hist.json")


def _seed_messages(bus, room_id, n=8):
    for i in range(n):
        bus.message_histories.setdefault(room_id, []).append(ChatMessage(
            room_id=room_id, sender_role=f"r{i%3}", content=f"msg-{i}",
            is_user=(i % 4 == 0), msg_type="text",
        ))


def test_formatted_context_exclude_role():
    bus = _fresh_bus()
    rm = RoomManager(); rm.create_room("g1")
    room = rm.get_room("g1"); room.members["a"] = None; room.members["b"] = None
    bus.message_histories["g1"] = [
        ChatMessage(room_id="g1", sender_role="a", content="A说", is_user=False),
        ChatMessage(room_id="g1", sender_role="b", content="B说", is_user=False),
        ChatMessage(room_id="g1", sender_role="a", content="A又说", is_user=False),
    ]
    ctx = bus.get_formatted_context("g1", n=10, exclude_role="a")
    assert "A说" not in ctx and "A又说" not in ctx
    assert "B说" in ctx


def test_formatted_context_max_chars():
    bus = _fresh_bus()
    room_id = "g1"
    msgs = []
    for i in range(20):
        msgs.append(ChatMessage(room_id=room_id, sender_role=f"r{i%3}",
                                content="这是一条比较长的测试消息内容" + "-" * 30,
                                is_user=False))
    bus.message_histories[room_id] = msgs
    full = bus.get_formatted_context(room_id, n=20)
    clipped = bus.get_formatted_context(room_id, n=20, max_chars=300)
    assert len(clipped) <= 300 + 100  # 宽松边界
    assert len(clipped) < len(full)   # 确实被裁剪


def test_message_bus_persistence(tmp_path):
    f = str(tmp_path / "hist.json")
    bus = _fresh_bus(history_file=f)
    bus._store_message("g1", ChatMessage(room_id="g1", sender_role="a", content="hello", is_user=False))
    assert os.path.exists(f)
    # 重建 bus 恢复
    MessageBus._instance = None
    bus2 = _fresh_bus(history_file=f)
    hist = bus2.get_recent_messages("g1")
    assert len(hist) == 1 and hist[0].content == "hello"


# ===================== A: Scheduler =====================

def _fresh_scheduler(room_id="g1"):
    bus = _fresh_bus()
    rm = RoomManager()
    room = make_room(room_id)
    rm._rooms[room_id] = room
    sched = RoomTurnScheduler(bus, rm, config={"relay_rounds": 2, "max_concurrency": 2})
    return sched, bus, rm, room


def test_mentions_extraction():
    bus = _fresh_bus()
    rm = RoomManager(); room = make_room("g1")
    sched = RoomTurnScheduler(bus, rm)
    m = sched._extract_mentions(room, "喂 @kasumi 你怎么看")
    assert m == ["kasumi"]
    m2 = sched._extract_mentions(room, "大家好，没有点名")
    assert m2 is None
    m3 = sched._extract_mentions(room, "@sayo @rimi 说说看")
    assert set(m3) == {"sayo", "rimi"}


def test_select_speakers_weighted_and_participate():
    bus = _fresh_bus()
    rm = RoomManager(); room = make_room("g1", ("a", "b", "c", "d", "e"))
    sched = RoomTurnScheduler(bus, rm)
    room.relay_style = "weighted"
    # 全员可参与
    sp = sched._select_speakers(room)
    assert 2 <= len(sp) <= 3
    # 高冷角色不参与
    room.member_config = {"a": {"participate": False}}
    sp2 = sched._select_speakers(room)
    assert "a" not in sp2


def test_mention_select_speakers():
    bus = _fresh_bus()
    rm = RoomManager(); room = make_room("g1")
    room.relay_style = "all"  # 确保未点名时全员
    sched = RoomTurnScheduler(bus, rm)
    sel = sched._select_speakers_for_message(room, "@kasumi 在吗")
    assert sel == ["kasumi"]
    # 未点名走常规（all 风格 => 全员）
    sel2 = sched._select_speakers_for_message(room, "大家最近怎么样")
    assert set(sel2) == set(room.members)


def test_scheduler_playout_calls_agents():
    """完整跑一轮：用户消息 -> 每位成员 agent.chat 被调用，且 persist_emotion=False，上下文排除自己。"""
    sched, bus, rm, room = _fresh_scheduler("g1")
    bus.message_histories["g1"] = [
        ChatMessage(room_id="g1", sender_role="web_user", content="大家好啊", is_user=True),
    ]
    room.enable_relay = True
    room.relay_rounds = 1
    room.relay_style = "all"

    async def run():
        await sched._playout("g1", 1)

    asyncio.run(run())

    for rid, agent in room.members.items():
        assert len(agent.calls) >= 1
        user_id, prompt, ctx, persist = agent.calls[0]
        assert user_id == "_room_g1"
        assert persist is False
        # 上下文排除该角色自己
        assert ctx is not None
        assert f"[{rid}]" not in (ctx or "")


def test_scheduler_respects_no_relay():
    sched, bus, rm, room = _fresh_scheduler("g1")
    bus.message_histories["g1"] = [
        ChatMessage(room_id="g1", sender_role="web_user", content="嗨", is_user=True),
    ]
    room.enable_relay = False  # 只回应，不接力
    room.relay_style = "all"
    agents = list(room.members.values())

    async def run():
        await sched._playout("g1", 1)

    asyncio.run(run())
    for a in agents:
        assert len(a.calls) == 1  # 只首轮一次，无接力


def test_scheduler_skips_failed_member():
    """某角色发言抛错或超时不影响其他角色，且不产生异常。"""
    sched, bus, rm, room = _fresh_scheduler("g1")
    bus.message_histories["g1"] = [
        ChatMessage(room_id="g1", sender_role="web_user", content="测试", is_user=True),
    ]
    room.enable_relay = False
    room.relay_style = "all"
    a, b, c = room.members["kasumi"], room.members["sayo"], room.members["rimi"]
    a.error = RuntimeError("boom")
    c.delay = 0.2  # 若 timeout 很短会超时；这里不放太短，只验证异常不扩散

    async def run():
        await sched._playout("g1", 1)

    asyncio.run(run())
    # a 失败但未抛到外层；b 正常；c 正常或超时
    assert len(b.calls) == 1
    assert c.calls  # 至少被调用过（0.2s < 默认 timeout 60）
