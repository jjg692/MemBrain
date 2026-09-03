"""
星露谷扩展单元测试（不依赖真实游戏 / LLM / MCP server）：
- GameMemoryBridge：游戏事件 -> L1/L4 记忆反写 + 检索
- GameStatePoller：状态轮询、变化检测、降级友好
全部用假内存管理器 / 假工具调用，纯记忆逻辑，确定性。
"""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stardew.game_memory import GameMemoryBridge
from stardew.runtime import GameStatePoller


# ===================== Fake MemoryManager =====================

class FakeMemory:
    """记录 add_to_l1 / judge_and_extract_facts / get_l1 调用的假记忆。"""

    def __init__(self):
        self.l1 = []
        self.facts_calls = []

    def add_to_l1(self, user_id, role_id, role, content):
        self.l1.append({"role": role, "content": content, "user_id": user_id, "role_id": role_id})
        return self.l1

    def get_l1(self, user_id, role_id):
        return [m for m in self.l1 if m["user_id"] == user_id and m["role_id"] == role_id]

    def judge_and_extract_facts(self, user_id, role_id, user_msg, reply):
        self.facts_calls.append({"user_id": user_id, "role_id": role_id,
                                 "user_msg": user_msg, "reply": reply})


# ===================== GameMemoryBridge =====================

def test_bridge_record_and_remember():
    fb = FakeMemory()
    bridge = GameMemoryBridge(fb, role_id="kasumi")
    bridge.record_event("default_user", "今天和用户一起在矿井挖到一颗钻石", "对用户很重要")
    # 应写入 L1（带 [星露谷] 标记 + 时间戳）
    assert len(fb.l1) == 1
    assert "[星露谷]" in fb.l1[0]["content"]
    assert "钻石" in fb.l1[0]["content"]
    assert "对用户很重要" in fb.l1[0]["content"]
    assert fb.l1[0]["role"] == "assistant"
    assert fb.l1[0]["user_id"] == "default_user"
    assert fb.l1[0]["role_id"] == "kasumi"
    # 应触发 L4 事实抽取
    assert len(fb.facts_calls) == 1
    # 检索："矿井" 命中
    hits = bridge.remember("default_user", "矿井")
    assert "钻石" in hits
    # 无关词不命中
    assert bridge.remember("default_user", "海边") == ""


def test_bridge_none_memory_is_noop():
    bridge = GameMemoryBridge(None, role_id="kasumi")
    # 记忆为 None 时静默返回，不抛异常
    bridge.record_event("u", "游戏事件")
    assert bridge.remember("u", "游戏") == ""


def test_bridge_remember_filters_tag():
    fb = FakeMemory()
    # 一条带标记、一条不带标记
    fb.l1.append({"role": "assistant", "content": "[星露谷] 今天钓鱼", "user_id": "u", "role_id": "kasumi"})
    fb.l1.append({"role": "assistant", "content": "普通对话 钓鱼", "user_id": "u", "role_id": "kasumi"})
    bridge = GameMemoryBridge(fb, role_id="kasumi")
    hits = bridge.remember("u", "钓鱼")
    # 只召回带 [星露谷] 标记的
    assert "今天钓鱼" in hits
    assert "普通对话" not in hits


# ===================== GameStatePoller =====================

def _fake_tool(states):
    """根据调用次数依次返回状态；越界返回 'Error: no more'。"""
    calls = {"n": 0}
    def fn(name, args):
        i = calls["n"]
        calls["n"] += 1
        if i < len(states):
            return json.dumps(states[i], ensure_ascii=False)
        return "Error: no more states"
    return fn


class _FakeBridge:
    def __init__(self):
        self.events = []
    def record_event(self, user_id, narrative, importance_hint=None):
        self.events.append(narrative)


def test_poller_records_on_change_only():
    bridge = _FakeBridge()
    # 状态1：春5 晴天 农场；状态2：春6 雨天 矿井（有变化）；状态3：与状态2相同（无变化）
    s1 = {"season": "Spring", "day_of_month": 5, "weather": "Sun", "location": "Farm", "player": {"money": 100}}
    s2 = {"season": "Spring", "day_of_month": 6, "weather": "Rain", "location": "Mine", "player": {"money": 120}}
    poller = GameStatePoller(memory_bridge=bridge, tool_call=_fake_tool([s1, s2, s2]), interval=60)

    poller.record_fingerprint()  # 首次 -> 记录
    poller.record_fingerprint()  # 变化 -> 记录
    poller.record_fingerprint()  # 无变化 -> 不记录

    assert len(bridge.events) == 2
    assert "春" in bridge.events[0] and "晴天" in bridge.events[0]
    assert "矿井" in bridge.events[1]
    assert poller.recorded_events == 2


def test_poller_tolerates_no_bridge():
    # bridge=None 时仍能读到状态，但不写记忆、不抛错
    s = {"season": "Summer", "location": "Beach"}
    poller = GameStatePoller(memory_bridge=None, tool_call=_fake_tool([s]), interval=60)
    poller.record_fingerprint()
    assert poller.last_state is not None
    assert poller.recorded_events == 0


def test_poller_degrades_when_mcp_unavailable():
    # 工具调用抛异常 / 返回错误 -> 静默跳过，last_state 为 None，不抛错
    def boom(name, args):
        raise RuntimeError("MCP server not running")
    poller = GameStatePoller(memory_bridge=_FakeBridge(), tool_call=boom, interval=60)
    poller.record_fingerprint()
    assert poller.last_state is None
    assert poller.last_error is not None


def test_poller_non_json_messages():
    # 返回非 JSON 包裹文本 -> 解析失败但继续（last_error 记录，last_state None）
    def weird(name, args):
        return "（MCP 工具错误）game not started"
    poller = GameStatePoller(memory_bridge=_FakeBridge(), tool_call=weird, interval=60)
    poller.record_fingerprint()
    assert poller.last_state is None
    assert poller.last_error is not None


def test_poller_status():
    bridge = _FakeBridge()
    poller = GameStatePoller(memory_bridge=bridge, tool_call=_fake_tool([{"season": "Fall"}]), interval=60)
    poller.record_fingerprint()
    st = poller.status()
    assert st["recorded_events"] == 1
    assert "running" in st and "interval" in st and "current_state" in st


def test_poller_narrative():
    s = {"season": "Winter", "day_of_month": 12, "weather": "Snow",
         "location": "Mountain", "player": {"money": 500}}
    bridge = _FakeBridge()
    poller = GameStatePoller(memory_bridge=bridge, tool_call=_fake_tool([s]), interval=60)
    poller.record_fingerprint()
    assert len(bridge.events) == 1
    n = bridge.events[0]
    assert "冬天" in n and "下雪" in n and "山区" in n and "500" in n
