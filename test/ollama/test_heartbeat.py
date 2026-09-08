"""
星露谷自主游玩心跳（stardew/heartbeat.py）单元测试。

覆盖：
- 未启用时 tick 直接跳过
- 不可用（无工具）时 tick 跳过、不动作不伪造
- 快照不可用时跳过
- 未进场时 tick 会 enter_game（spawn+模式）
- 进场后 tick 会执行一个低动作
- 动作冷却：同一动作在冷却窗口内不重复
全部通过注入假工具到 TOOL_REGISTRY，不依赖真实游戏/线程/LLM。
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import tools
from stardew.heartbeat import StardewHeartbeat


class FakeBridge:
    def __init__(self):
        self.events = []
    def record_event(self, user_id, narrative, importance_hint=None):
        self.events.append(narrative)


def _no_comp_state(**kw):
    d = {"time": 1000, "day": 15, "season": "spring", "weather": "sunny",
         "location": "Farm", "player": {"money": 300}, "companions": []}
    d.update(kw)
    return d


@pytest.fixture()
def fake_tools(monkeypatch):
    """注入星露谷桥接假工具：get_state(带 companions)、spawn、set_mode、chat、move_to。"""
    import json as _json
    def json_dumps(o): return _json.dumps(o, ensure_ascii=False)
    state = {"value": {"companions": None}}
    def get_state(**k):
        return json_dumps(_no_comp_state(companions=state["value"]["companions"] or []))
    fn = {
        "mcp_s_stardew_get_state": get_state,
        "mcp_s_stardew_spawn": lambda **k: "Command sent.",
        "mcp_s_stardew_set_mode": lambda **k: "mode set",
        "mcp_s_stardew_chat": lambda **k: "chat ok",
        "mcp_s_stardew_move_to": lambda **k: "moved",
    }
    for name, impl in fn.items():
        impl.__doc__ = "test tool"
        monkeypatch.setitem(tools.TOOL_REGISTRY, name, impl)
    # 返回控制 companions 的引用，测试里可改状态
    yield state


def test_disabled_tick_skips(fake_tools):
    hb = StardewHeartbeat(enabled=False)
    hb.tick(force=True)
    assert hb.stats["skip"] == 1
    assert hb.stats["enter"] == 0
    assert hb.stats["act"] == 0


def test_unavailable_tick_skips(monkeypatch):
    from core import tools as T
    saved = {k: v for k, v in T.TOOL_REGISTRY.items() if k.startswith("mcp_")}
    for k in list(saved):
        monkeypatch.delitem(T.TOOL_REGISTRY, k)
    try:
        hb = StardewHeartbeat(enabled=True)
        hb.tick(force=True)
        assert hb.stats["skip"] == 1
    finally:
        T.TOOL_REGISTRY.update(saved)


def test_enter_when_no_companions(fake_tools):
    events = []
    hb = StardewHeartbeat(enabled=True, memory_bridge=FakeBridge(),
                          event_callback=lambda k, d: events.append((k, d)))
    hb.tick(force=True)
    assert hb.stats["enter"] == 1
    assert hb.stats["act"] == 0
    # 触发了一次 enter 事件
    assert any(k == "enter" for k, _ in events)


def test_act_when_already_in_game(fake_tools):
    # 让快照里已有 companions -> 不再进场，走动作
    fake_tools["value"]["companions"] = [{"name": "Companion1", "mode": "player"}]
    hb = StardewHeartbeat(enabled=True, memory_bridge=FakeBridge())
    hb.tick(force=True)
    assert hb.stats["enter"] == 0
    assert hb.stats["act"] == 1
    assert hb.stats["last_act_at"] is not None


def test_action_cooldown(monkeypatch):
    # 只注入 chat，确保唯一候选走冷却阻挡
    from core import tools as T
    saved = {k: v for k, v in T.TOOL_REGISTRY.items() if k.startswith("mcp_")}
    for k in list(saved):
        monkeypatch.delitem(T.TOOL_REGISTRY, k)
    import json as _json
    def js(o): return _json.dumps(o, ensure_ascii=False)
    def get_state(**k):
        return js(_no_comp_state(companions=[{"name": "Companion1"}]))
    fn = {
        "mcp_s_stardew_get_state": get_state,
        "mcp_s_stardew_chat": lambda **k: "chat ok",
    }
    for name, impl in fn.items():
        impl.__doc__ = "t"
        monkeypatch.setitem(T.TOOL_REGISTRY, name, impl)
    try:
        hb = StardewHeartbeat(enabled=True)
        hb.tick(force=True)
        first = hb.stats["act"]
        assert first == 1
        # 同动作（chat）在冷却窗口内 -> 不再动作
        hb.tick(force=True)
        assert hb.stats["act"] == first
        assert hb.stats["skip"] >= 1
    finally:
        T.TOOL_REGISTRY.update(saved)


def test_enter_cooldown_no_respawn(fake_tools, monkeypatch):
    # 尚未进场 -> tick1 进场；之后 companions 仍空，但 enter 冷却内不应再次 spawn
    hb = StardewHeartbeat(enabled=True)
    hb.tick(force=True)
    assert hb.stats["enter"] == 1
    hb.tick(force=True)
    # 冷却内（默认 60s）不再进场
    assert hb.stats["enter"] == 1


# =============================== LLM 决策版 ===============================

class FakeLLM:
    """模拟 LLM 适配器（chat_with_tools）。"""
    def __init__(self, tool_calls=None, call_count=None):
        self._tool_calls = tool_calls or []
        self.calls = []
        self.call_count = call_count  # 若设置，超过后返回空（模拟失败）

    def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append((messages, tools))
        if self.call_count is not None and len(self.calls) > self.call_count:
            return {"content": "", "tool_calls": []}
        return {"content": "", "tool_calls": self._tool_calls}


def _llm_hb(fake_tools, llm, **kw):
    # 已在场
    fake_tools["value"]["companions"] = [{"name": "Companion1"}]
    kw.setdefault("llm_enabled", True)
    kw.setdefault("llm_adapter", llm)
    # 显式提供给 LLM 的工具 schema（测试里只注入了 TOOL_REGISTRY，未同步 ALL_TOOLS）
    kw.setdefault("llm_tools", [
        {"type": "function", "function": {"name": "mcp_s_stardew_get_state", "description": "st", "parameters": {}}},
        {"type": "function", "function": {"name": "mcp_s_stardew_chat", "description": "st", "parameters": {"type": "object", "properties": {"message": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "mcp_s_stardew_move_to", "description": "st", "parameters": {}}},
    ])
    return StardewHeartbeat(enabled=True, **kw)


def test_llm_decides_action(fake_tools):
    llm = FakeLLM([{"function": {"name": "mcp_s_stardew_chat", "arguments": {"message": "你好呀"}}}])
    hb = _llm_hb(fake_tools, llm)
    hb.tick(force=True)
    assert hb.stats["act"] == 1
    assert hb.stats["llm_act"] == 1
    assert hb.stats["rule_act"] == 0
    # LLM 被调用且给了星露谷工具
    assert llm.calls
    tools_names = [t.get("function", {}).get("name") for t in llm.calls[0][1]]
    assert any("stardew_" in n for n in tools_names)


def test_llm_no_call_falls_back_to_rule(fake_tools):
    llm = FakeLLM([])  # 不返回工具调用
    hb = _llm_hb(fake_tools, llm)
    hb.tick(force=True)
    assert hb.stats["act"] == 1
    # LLM 未返回 -> 回退规则
    assert hb.stats["rule_act"] == 1
    assert hb.stats["llm_fallback"] == 1


def test_llm_non_stardew_tool_rejected(fake_tools):
    llm = FakeLLM([{"function": {"name": "search_web", "arguments": {}}}])
    hb = _llm_hb(fake_tools, llm)
    hb.tick(force=True)
    # 非星露谷工具被拒 -> 回退规则
    assert hb.stats["rule_act"] == 1
    assert hb.stats["llm_fallback"] == 1


def test_llm_exception_falls_back(monkeypatch, fake_tools):
    class Boom:
        def chat_with_tools(self, *a, **k):
            raise RuntimeError("boom")
    hb = _llm_hb(fake_tools, Boom())
    hb.tick(force=True)
    assert hb.stats["rule_act"] == 1
    assert hb.stats["llm_fallback"] == 1


def test_llm_enabled_requires_adapter(fake_tools):
    # 无 adapter 时 llm_enabled 自动置回 False
    hb = StardewHeartbeat(enabled=True, llm_enabled=True)  # adapter=None
    assert hb.llm_enabled is False
    # set_llm_adapter(None) 不会误开
    hb.set_llm_adapter(None)
    assert hb.llm_enabled is False


def test_set_llm_enabled(monkeypatch, fake_tools):
    llm = FakeLLM([{"function": {"name": "mcp_s_stardew_chat", "arguments": {}}}])
    hb = _llm_hb(fake_tools, llm)
    hb.set_llm_enabled(False)
    hb.tick(force=True)
    # 关掉 LLM -> 走规则
    assert hb.stats["llm_act"] == 0
    assert hb.stats["rule_act"] == 1


# =============================== 指令驱动版 ===============================

def test_feed_command_sets_pending_and_wakes():
    hb = StardewHeartbeat(enabled=True)
    assert hb.feed_command("跟我一起出来种地") is True
    assert hb.get_pending_command() == "跟我一起出来种地"
    assert hb._wake.is_set()  # 触发立即决策
    # 空/空白指令不接收
    assert hb.feed_command("   ") is False


def test_feed_command_clean_pending_and_feeds_llm(fake_tools):
    llm = FakeLLM([{"function": {"name": "mcp_s_stardew_move_to", "arguments": {"x": 1, "y": 2}}}])
    hb = _llm_hb(fake_tools, llm)
    hb.feed_command("跟我一起出来种地")
    hb.tick(force=True)
    # 指令被消费，动作下发
    assert hb.get_pending_command() is None
    assert hb.stats["act"] == 1
    assert hb.stats["llm_act"] == 1
    # 指令文本进入 LLM 的 user 消息
    assert llm.calls
    user_content = llm.calls[0][0][1]["content"]
    assert "种地" in user_content


def test_no_command_still_autonomous(fake_tools):
    # 无指令时 LLM 仍被要求"自主判断"（不再只是回我在呢~）
    llm = FakeLLM([{"function": {"name": "mcp_s_stardew_move_to", "arguments": {"x": 3, "y": 4}}}])
    hb = _llm_hb(fake_tools, llm)
    hb.tick(force=True)
    assert hb.get_pending_command() is None
    assert hb.stats["act"] == 1
    user_content = llm.calls[0][0][1]["content"]
    assert "没有玩家指令" in user_content
