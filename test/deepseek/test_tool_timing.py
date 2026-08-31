"""
工具调用时机测试（deepseek · 离线确定性 + 整链验证）
================================================
聚焦"是否在合适的时机调用了合适的工具"。这部分用**假 LLM**做确定性验证
（不依赖远程，离线可复现、不花 token），覆盖：

  A. 意图 -> 工具映射矩阵：不同意图应触发不同工具
  B. tool_fallback 兜底守卫整链：该调却不调时强制注入 search_web；
     已执行过工具/闲聊/关开关时不注入
  C. 多工具编排：混合句应依次触发多个正确工具
"""
import pytest


def _build_agent(tmp_path, monkeypatch, fake_embedding, tool_fn_map, tool_fallback=True):
    """先向 core.tools.TOOL_REGISTRY 写入假工具（构造 Agent 前生效），再建 Agent。

    返回 (agent, llm_fake, mngr)，直接用 ag.llm_adapter 预置响应。
    tool 适配器默认返回合法情感 JSON（不产生 tool_call），供"模型没调工具"场景。
    """
    import core.tools as T
    for name, fn in tool_fn_map.items():
        monkeypatch.setitem(T.TOOL_REGISTRY, name, fn)
    from test.deepseek.conftest import FakeAdapter, DEFAULT_EMOTION_JSON, make_role_manager as _mrm
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent
    store = SimpleMemory(path=str(tmp_path / "chroma_timing"))
    llm_fake = FakeAdapter(name="llm")
    tool_fake = FakeAdapter(name="tool", default_chat=DEFAULT_EMOTION_JSON)
    mngr = MemoryManager(store, tool_fake)
    rm = _mrm("kasumi", "你是户山香澄。")
    es = EmotionStore(store)
    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm_fake, tool_adapter=tool_fake, perception=None,
        tool_fallback=tool_fallback,
    )
    return ag, llm_fake, mngr


# ===================== A. 意图 -> 工具映射矩阵 =====================

@pytest.mark.parametrize("user_msg,name,args", [
    ("北京天气怎么样", "search_web", {"query": "北京天气怎么样"}),
    ("帮我设提醒明早八点喝药", "remind_me", {"text": "提醒我喝药", "when": "08:00"}),
    ("现在几点", "get_current_time", {}),
    ("读一下 notes/hello.txt", "read_file", {"path": "notes/hello.txt"}),
])
def test_intent_maps_to_tool(tmp_path, monkeypatch, fake_embedding, user_msg, name, args):
    """假 LLM 决定用某工具 -> 断言真正执行了那个工具且参数正确。"""
    calls = {}

    def search_web(query: str) -> str:
        """搜。"""
        calls["search_web"] = query
        return "结果"

    def remind_me(text: str = "", when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒。"""
        calls["remind_me"] = (text, when)
        return "已设"

    def get_current_time() -> str:
        """时间。"""
        calls["get_current_time"] = True
        return "现在：12:00"

    def read_file(path: str) -> str:
        """读文件。"""
        calls["read_file"] = path
        return "内容"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web, "remind_me": remind_me,
                             "get_current_time": get_current_time, "read_file": read_file},
                            tool_fallback=False)
    llm.enqueue_tools({"content": "帮你办好",
                       "tool_calls": [{"function": {"name": name, "arguments": args}}]})
    llm.enqueue_tools("搞定啦")
    reply = ag.chat("u1", user_msg)
    assert name in calls, f"预期调用 {name}，实际 {list(calls.keys())}"
    assert reply == "搞定啦"


def test_casual_chat_triggers_zero_tools(tmp_path, monkeypatch, fake_embedding):
    """纯闲聊 -> 工具调用数严格为 0（假 LLM 确定）。"""
    calls = {}

    def search_web(query: str) -> str:
        """搜索桩。"""
        calls["search_web"] = query
        return "x"

    def remind_me(text: str = "", when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒桩。"""
        calls["remind_me"] = text
        return "x"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web, "remind_me": remind_me},
                            tool_fallback=False)
    # 假 LLM 直接给最终文本（无 tool_call）
    llm.enqueue_tools("嗯嗯，聊聊天挺好的～")
    reply = ag.chat("u1", "今天好累啊，陪我聊聊天吧")
    assert calls == {}, f"闲聊却调用了工具：{list(calls.keys())}"
    assert reply


# ===================== B. tool_fallback 兜底守卫 =====================

def test_fallback_injects_search_when_model_forgets(tmp_path, monkeypatch, fake_embedding):
    """天气意图 + 模型不调工具 -> 守卫强制注入并执行 search_web。"""
    calls = []

    def search_web(query: str) -> str:
        """搜索桩。"""
        calls.append(query)
        return "北京晴 25°C"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web}, tool_fallback=True)
    llm.enqueue_tools("（这次模型没想着调工具，直接给文本）")  # 第1次：无 tool_call
    llm.enqueue_tools("我查到啦，北京晴天25度！")              # 第2次：拿到工具结果后回复
    reply = ag.chat("u1", "北京今天天气怎么样？")
    assert calls, "守卫应强制注入 search_web"
    assert reply == "我查到啦，北京晴天25度！"


def test_fallback_not_reinject_after_tool_executed(tmp_path, monkeypatch, fake_embedding):
    """已有 ToolMessage（工具已执行）-> 守卫不再二次注入。"""
    search_calls = []

    def search_web(query: str) -> str:
        """搜索桩。"""
        search_calls.append(query)
        return "北京晴"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web}, tool_fallback=True)
    llm.enqueue_tools({"content": "我查一下",
                       "tool_calls": [{"function": {"name": "search_web", "arguments": {"query": "北京天气"}}}]})
    llm.enqueue_tools("好了，北京今天晴天")  # 已执行工具后正常回复
    reply = ag.chat("u1", "北京天气怎么样？")
    # 工具被正常执行一次，守卫不额外注入第二次
    assert search_calls and len(search_calls) == 1


def test_fallback_casual_no_toolword(tmp_path, monkeypatch, fake_embedding):
    """不含资料/天气触发词的纯闲聊 -> 守卫不注入。"""
    search_calls = []

    def search_web(query: str) -> str:
        """搜索桩。"""
        search_calls.append(query)
        return "x"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web}, tool_fallback=True)
    llm.enqueue_tools("我也超喜欢这首歌！")
    reply = ag.chat("u1", "我最喜欢的一首歌是《Don't say lazy》")
    assert search_calls == [], f"纯闲聊被误注入 search_web"


def test_fallback_disabled_via_flag(tmp_path, monkeypatch, fake_embedding):
    """tool_fallback=False -> 守卫完全不干预。"""
    search_calls = []

    def search_web(query: str) -> str:
        """搜索桩。"""
        search_calls.append(query)
        return "x"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web}, tool_fallback=False)
    llm.enqueue_tools("（模型没调工具，直接文本）")
    reply = ag.chat("u1", "北京今天天气怎么样？")
    assert search_calls == [], "关闭 fallback 后不应强制注入"


def test_fallback_injects_remind_when_model_forgets(tmp_path, monkeypatch, fake_embedding):
    """明确要求设提醒 + 模型不调工具 -> 守卫强制注入并执行 remind_me。"""
    calls = []

    def remind_me(text: str = "", when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒桩。"""
        calls.append((text, when))
        return f"已设置提醒：{text}"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"remind_me": remind_me}, tool_fallback=True)
    # 第1次：模型"光说不做"（口述答应，无 tool_call）
    llm.enqueue_tools("（这次模型没想着调工具，直接说'好勒我去给你设个提醒'）")
    # 第2次：拿到 remind_me 结果后回复
    llm.enqueue_tools("好嘞，记住了，明早八点叫你喝药！")
    reply = ag.chat("u1", "帮我在明早八点设个提醒，提醒我喝药")
    assert calls, "守卫应强制注入 remind_me"
    assert reply == "好嘞，记住了，明早八点叫你喝药！"


def test_fallback_not_inject_remind_for_casual(tmp_path, monkeypatch, fake_embedding):
    """含"记"的纯闲聊（如'记得'）-> 不作为设提醒，守卫不注入。"""
    calls = []

    def remind_me(text: str = "", when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒桩。"""
        calls.append((text, when))
        return "已设"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"remind_me": remind_me}, tool_fallback=True)
    llm.enqueue_tools("记得呀，那首歌的旋律我也超喜欢！")
    reply = ag.chat("u1", "你还记得那首歌吗？我最喜欢它了")
    assert calls == [], f"纯闲聊含'记得'却被误注入 remind_me"


# ===================== C. 多工具编排 =====================

def test_multi_tool_orchestration(tmp_path, monkeypatch, fake_embedding):
    """混合句：天气 + 设提醒 -> 依次触发 search_web 与 remind_me。"""
    order = []
    calls = {"search_web": [], "remind_me": []}

    def search_web(query: str) -> str:
        """搜索桩。"""
        calls["search_web"].append(query)
        order.append("search_web")
        return "北京晴"

    def remind_me(text: str = "", when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒桩。"""
        calls["remind_me"].append((text, when))
        order.append("remind_me")
        return "已设"

    ag, llm, mngr = _build_agent(tmp_path, monkeypatch, fake_embedding,
                            {"search_web": search_web, "remind_me": remind_me},
                            tool_fallback=False)
    # LLM 分两步：先搜天气，再设提醒
    llm.enqueue_tools({"content": "",
                                  "tool_calls": [{"function": {"name": "search_web", "arguments": {"query": "北京天气"}}}]})
    llm.enqueue_tools({"content": "",
                        "tool_calls": [{"function": {"name": "remind_me", "arguments": {"text": "明早八点喝药", "when": "08:00"}}}]})
    llm.enqueue_tools("查完也帮你记好了！")
    reply = ag.chat("u1", "北京天气如何，顺便提醒我明早八点喝药")
    assert calls["search_web"] and calls["remind_me"], f"应两个工具都调用: {calls}"
    assert reply == "查完也帮你记好了！"
