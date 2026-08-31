"""
任务循环（plan → act → observe）测试 —— 窗口 A M2
==================================================
覆盖 docs/dual-window-contract.md §1.2-A（长程自主 / 任务循环）的 M2 交付：

  A. TaskPlanner 纯函数（零依赖，离线确定性）：
     · should_plan 分界：≥2 个不同工具意图 -> 多步；单工具/闲聊 -> False
     · plan 骨架：goal + 有序步骤 + tool_hint
     · 边界不误伤：纯闲聊、单个工具请求（即使句子长）不触发
  B. 确定性规划不新增 LLM 调用 / 不改变假 LLM 的 enqueue 顺序
  C. Agent 集成：多步请求 -> 触发 plan；工具逐步执行；observe 累积结果；
     最终收敛 task_status.done + conclusion；单轮无 plan 时完全不进任务分支
  D. 不回归：单工具/闲聊请求行为与启用任务循环前一致

本文件自包含（内置最小夹具），不依赖 test/ 下其它 conftest。
"""
import json

import pytest

from agent.planner import TaskPlanner, TaskPlan


# ===================== A. TaskPlanner 纯函数 =====================

class TestShouldPlan:
    def test_multiple_tool_intents_is_task(self):
        """消息含 ≥2 个不同工具意图 -> 进入任务循环。"""
        assert TaskPlanner.should_plan("查一下北京天气，顺便提醒我明早八点开会") is True

    def test_weather_plus_reminder(self):
        assert TaskPlanner.should_plan("帮我查天气，并且设个提醒") is True

    def test_single_tool_request_is_not_task(self):
        """单个工具请求（即使句子长、有逗号）不触发。"""
        assert TaskPlanner.should_plan("北京今天天气怎么样") is False
        assert TaskPlanner.should_plan("帮我设提醒明早八点喝药") is False

    def test_casual_chat_is_not_task(self):
        """纯闲聊即使多句、有逗号也不触发。"""
        assert TaskPlanner.should_plan("今天好累啊，陪我聊聊天吧，我好想放松一下") is False
        assert TaskPlanner.should_plan("我最喜欢的一首歌是《Don't say lazy》") is False

    def test_empty_not_task(self):
        assert TaskPlanner.should_plan("") is False


class TestPlan:
    def test_plan_produces_ordered_steps(self):
        p = TaskPlanner.plan("查一下北京天气，顺便提醒我明早八点开会")
        assert isinstance(p, TaskPlan)
        assert p.goal
        assert p.total_steps() >= 2
        # 步骤有序，且至少一个带 tool_hint
        assert [s.status for s in p.steps] == ["pending"] * len(p.steps)
        assert any(s.tool_hint for s in p.steps)

    def test_plan_none_for_single(self):
        assert TaskPlanner.plan("北京今天天气怎么样") is None
        assert TaskPlanner.plan("陪我聊天吧") is None

    def test_plan_to_dict_roundtrip(self):
        p = TaskPlanner.plan("查天气，并且设提醒喝药")
        d = p.to_dict()
        assert d["goal"]
        assert "steps" in d and len(d["steps"]) >= 2
        assert all("index" in s and "description" in s for s in d["steps"])


# ===================== B/C/D. Agent 集成（最小装配，假 LLM） =====================

def _build_agent(tmp_path, monkeypatch, tools, tool_fallback=False):
    """组装真实 agent（假嵌入 + 假 tool 适配器 + 假 LLM）。返回 (agent, llm_fake)。"""
    import tempfile, os
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent
    from core.role.manager import RoleManager, RoleConfig

    import core.tools as T
    for name, fn in tools.items():
        monkeypatch.setitem(T.TOOL_REGISTRY, name, fn)

    class _FE:
        def name(self): return "fake"
        def __call__(self, input):
            return [[0.001] * 384 for _ in (input if isinstance(input, list) else [input])]
        def embed_query(self, input=None, query=None):
            return [(query or input or [""]) and [0.001] * 384]

    import core.memory.vector_store as VS
    VS.get_embedding_function = lambda: _FE()

    class FakeTool:
        def chat(self, messages, **kw):
            return json.dumps({"emotion": {"primary": "平静", "intensity": 0.5, "valence": 0.0, "description": ""},
                               "affection": {k: 0.5 for k in ("liking","trust","familiarity","respect","interest")},
                               "attachment": 0.3, "needs_tool": False, "tool_decision": ""})

    class FakeLLM:
        def __init__(self):
            self.queue = []
        def enqueue(self, content="", tool_calls=None):
            self.queue.append({"content": content, "tool_calls": tool_calls or []})
        def chat_with_tools(self, messages, tools=None, **kw):
            if self.queue:
                item = self.queue.pop(0)
                if isinstance(item, str):
                    item = {"content": item, "tool_calls": []}
                return {"content": item["content"], "tool_calls": item.get("tool_calls", [])}
            return {"content": "嗯…", "tool_calls": []}

    d = tempfile.mkdtemp()
    store = SimpleMemory(path=os.path.join(d, "c"))
    tool_fake = FakeTool()
    mngr = MemoryManager(store, tool_fake)
    rm = RoleManager.__new__(RoleManager)
    rm._roles = {"kasumi": RoleConfig(role_id="kasumi", display_name="测试", default=True)}
    rm._prompts = {"kasumi": "你是户山香澄。"}
    es = EmotionStore(store)

    llm = FakeLLM()
    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm, tool_adapter=tool_fake, perception=None,
        tool_fallback=tool_fallback,
    )
    return ag, llm


def test_single_tool_no_task_branch(tmp_path, monkeypatch):
    """单个工具请求：不触发 plan，agent.last_task_plan 为 None（行为不回归）。"""
    calls = {}

    def get_current_time() -> str:
        """时间。"""
        calls["time"] = True
        return "现在是 12:00"

    ag, llm = _build_agent(tmp_path, monkeypatch, {"get_current_time": get_current_time})
    llm.enqueue("已执行工具", tool_calls=[{"function": {"name": "get_current_time", "arguments": {}}}])
    llm.enqueue("现在是中午12点！")
    reply = ag.chat("u1", "现在几点")
    assert reply == "现在是中午12点！"
    assert calls.get("time") is True
    # 单工具不触发任务循环
    assert ag.last_task_plan is None
    assert (ag.last_task_status or {}).get("done") is not True


def test_multi_tool_enters_task_loop(tmp_path, monkeypatch):
    """多步请求：plan 建立、工具逐步执行、observe 累积、最终收敛 done。"""
    calls = {"search_web": [], "remind_me": []}
    order = []

    def search_web(query: str) -> str:
        """搜。"""
        calls["search_web"].append(query)
        order.append("search")
        return "北京晴 25°C"

    def remind_me(text: str = "", when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒。"""
        calls["remind_me"].append((text, when))
        order.append("remind")
        return "已设"

    ag, llm = _build_agent(tmp_path, monkeypatch,
                           {"search_web": search_web, "remind_me": remind_me})
    # LLM 分步：先搜天气，再设提醒，最后总结
    llm.enqueue(tool_calls=[{"function": {"name": "search_web", "arguments": {"query": "北京天气"}}}])
    llm.enqueue(tool_calls=[{"function": {"name": "remind_me", "arguments": {"text": "明早八点开会", "when": "08:00"}}}])
    llm.enqueue("查好啦～北京晴天25度，也帮你记好明早八点开会啦！")
    reply = ag.chat("u1", "查一下北京天气，顺便提醒我明早八点开会")

    assert calls["search_web"] and calls["remind_me"], f"两个工具都应执行: {calls}"
    # 任务循环应建立 plan 并收敛
    assert ag.last_task_plan is not None, "多步任务应产生 plan"
    assert ag.last_task_plan["goal"]
    assert len(ag.last_task_plan["steps"]) >= 2
    ts = ag.last_task_status or {}
    assert ts.get("done") is True
    assert ts.get("conclusion") == reply
    # observe 累积了工具结果
    assert len(ts.get("observations") or []) >= 2


def test_casual_chat_no_task_loop(tmp_path, monkeypatch):
    """纯闲聊：完全不触发任务分支，不新增 LLM 调用（enqueue 顺序不变）。"""
    calls = {}

    def search_web(query: str) -> str:
        """搜。"""
        calls["search"] = query
        return "x"

    ag, llm = _build_agent(tmp_path, monkeypatch, {"search_web": search_web}, tool_fallback=False)
    llm.enqueue("嗯嗯，聊聊天挺好的～")
    reply = ag.chat("u1", "今天好累啊，陪我聊聊天吧")
    assert calls == {}, f"闲聊不应调用工具: {calls}"
    assert reply == "嗯嗯，聊聊天挺好的～"
    assert ag.last_task_plan is None
    assert (ag.last_task_status or {}).get("done") is not True


def test_planner_can_be_disabled(tmp_path, monkeypatch):
    """可注入替身（False）关闭任务循环，完全走单轮（向后兼容开关）。"""
    calls = {}
    order = []

    def search_web(query: str) -> str:
        """搜。"""
        calls["s"] = query
        order.append("s")
        return "晴"

    def remind_me(text: str = "", when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒。"""
        calls["r"] = text
        order.append("r")
        return "已设"

    ag, llm = _build_agent(tmp_path, monkeypatch,
                           {"search_web": search_web, "remind_me": remind_me})
    ag.task_planner = False  # 关闭任务循环
    llm.enqueue("帮你办好啦")
    reply = ag.chat("u1", "查一下天气，顺便提醒明早八点")
    # 关闭后：交给 LLM 自主（假 LLM 直接回复），不强制走任务分支
    assert reply == "帮你办好啦"
    assert ag.last_task_plan is None
