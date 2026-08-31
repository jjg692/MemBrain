"""
单元测试：Agent 的真正 ReAct 工具执行闭环
================================================
之前只测了工具"纯函数"，这里补上最关键的一环——**Agent 在 LLM 发出
tool_call 后是否真正执行工具、把结果回喂给 LLM、并产出最终回复**。

用假 LLM 分两轮驱动：
  第 1 轮：LLM 返回 tool_call(search_web/remind_me, ...)  -> 图中走 tools 节点执行
  第 2 轮：LLM 收到工具结果后返回最终文本                -> 走 end
验证：
  - 工具真的被调用（收到正确的 query 参数）
  - 工具结果作为 tool message 回喂给 LLM
  - 最终返回的是第 2 轮 LLM 的回复文本
  - L1 正确记录用户消息与最终回复

⚠️ 关键：LangGraph 的 ToolNode 在 Agent.__init__ 时就把 TOOL_REGISTRY 里的函数
包装成 tool，因此必须在**构造 Agent 之前**把假工具写进 core.tools.TOOL_REGISTRY，
否则后补的 patch 不生效。故这里不用 `agent` fixture，而是先 patch 再自建 Agent。
"""
import json
import tempfile
import os

import pytest

from core.memory.vector_store import SimpleMemory
from core.memory.memory_manager import MemoryManager
from core.emotion import EmotionStore
from agent.graph import LangGraphMemoryAgent
from test.ollama.conftest import make_role_manager


def _build_agent(tmp_path, monkeypatch, fake_embedding, tool_fn_map, llm_fake, tool_fake):
    """
    先向 core.tools 的 TOOL_REGISTRY 写入假工具（构造 Agent 前生效），再建 Agent。
    假函数必须：带类型注解 + docstring（LangChain tool() 强制要求）。
    用 monkeypatch 在测试结束后自动恢复原工具，避免污染其他测试。
    """
    import core.tools as T
    for name, fn in tool_fn_map.items():
        monkeypatch.setitem(T.TOOL_REGISTRY, name, fn)  # 测试后自动还原
    store = SimpleMemory(path=str(tmp_path / "chroma_tools"))
    mngr = MemoryManager(store, tool_fake)
    rm = make_role_manager("kasumi", "你是户山香澄。")
    es = EmotionStore(store)
    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es,
        role_id="kasumi", llm_adapter=llm_fake, tool_adapter=tool_fake,
        perception=None, tool_fallback=False,  # 假 LLM 确定性闭环：关闭兜底守卫
    )
    return ag, mngr


def test_agent_executes_search_web_tool_and_returns(tmp_path, monkeypatch, fake_embedding, llm_fake, tool_fake):
    calls = []

    def search_web(query: str) -> str:
        """联网搜索（测试桩）：验证工具被以正确参数调用。"""
        calls.append(query)
        return f"（假天气结果）{query}：晴 25°C"

    ag, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web}, llm_fake, tool_fake)

    # 第 1 轮：LLM 决定调用 search_web
    llm_fake.enqueue_tools({"content": "让我搜一下",
                            "tool_calls": [{"function": {"name": "search_web",
                                                         "arguments": {"query": "北京天气"}}}]})
    # 第 2 轮：收到工具结果后给最终回复
    llm_fake.enqueue_tools("我查到啦！北京今天晴天，25°C，很舒服～")

    reply = ag.chat("user_a", "北京天气怎么样？")

    assert calls == ["北京天气"]                      # 工具确实以正确参数被调用
    assert reply == "我查到啦！北京今天晴天，25°C，很舒服～"

    # 工具结果应回喂给 LLM（第 2 轮的输入里能看到）
    assert len(llm_fake.sent_tools) == 2
    second_msgs = llm_fake.sent_tools[1][0]
    texts = "\n".join(str(m.get("content", "")) for m in second_msgs)
    assert "晴 25°C" in texts or "假天气" in texts

    # L1：用户 + 最终回复
    l1 = [m["content"] for m in mngr.get_l1("user_a", "kasumi")]
    assert l1 == ["北京天气怎么样？", "我查到啦！北京今天晴天，25°C，很舒服～"]


def test_agent_executes_reminder_tool_with_result_feedback(tmp_path, monkeypatch, fake_embedding, llm_fake, tool_fake):
    from core.reminder import ReminderStore
    store = ReminderStore(tmp_path / "rem.json")   # 隔离真实 reminders.json

    def remind_me(text: str, when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """设置提醒（测试桩）：验证工具真实写入存储。"""
        r = store.add(user_id=user_id, text=text, trigger_at=when, repeat=repeat)
        return f"已为你设置提醒：{text}（id={r['id']}）"

    ag, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"remind_me": remind_me}, llm_fake, tool_fake)

    llm_fake.enqueue_tools({"content": "好，我帮你记住",
                            "tool_calls": [{"function": {"name": "remind_me",
                                                         "arguments": {"text": "下午3点开会",
                                                                       "when": "2026-12-31 15:00"}}}]})
    llm_fake.enqueue_tools("搞定！下午3点开会，我会提醒你的～")

    reply = ag.chat("user_a", "帮我设个提醒")
    assert reply == "搞定！下午3点开会，我会提醒你的～"
    # 注意：工具调用参数未自动绑定当前 user_id，remind_me 的 user_id 默认 default_user。
    # 这里断言真实写入处的行为（记录下这个事实，供排查多用户隔离）。
    items_default = store.list("default_user", include_done=False)
    items_me = store.list("user_a", include_done=False)
    assert any("开会" in r["text"] for r in items_default)   # 工具真实写入存储
    assert items_me == []                                      # 当前 user 未绑定到工具参数


def test_agent_tool_loop_stays_bounded():
    """工具循环应在迭代上限处终止，不无限执行（纯路由规则验证）。"""
    from agent.graph import LangGraphMemoryAgent
    from langchain_core.messages import AIMessage
    _self = object()  # 仅用于调用实例方法的路由逻辑（不依赖 self 状态）

    # 迭代达到上限(8)之上 -> 即使带 tool_calls 也强制 end，防死循环
    tool_msg = {"iteration": 9,
                "messages": [AIMessage(content="", tool_calls=[{"name": "search_web",
                                                                "args": {"query": "x"},
                                                                "id": "c1"}])]}
    assert LangGraphMemoryAgent._route_after_agent(_self, tool_msg) == "end"

    # 迭代未超限且带 tool_calls -> 走 tools 执行
    tool_msg2 = {"iteration": 2,
                 "messages": [AIMessage(content="", tool_calls=[{"name": "search_web",
                                                                 "args": {"query": "x"},
                                                                 "id": "c1"}])]}
    assert LangGraphMemoryAgent._route_after_agent(_self, tool_msg2) == "tools"

    # 迭代未超限且无 tool_calls -> end
    no_tool = {"iteration": 2, "messages": [AIMessage(content="普通回复")]}
    assert LangGraphMemoryAgent._route_after_agent(_self, no_tool) == "end"
