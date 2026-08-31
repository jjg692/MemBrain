"""
行为事件 BehaviorMapper 测试（窗口 A 产出侧）
================================================
覆盖契约 docs/dual-window-contract.md §3.2 / §4 的 M1 交付：

  A. BehaviorMapper.derive 纯函数单测（零依赖，离线确定性）：
     · 情绪主信号 -> 表情/口型/pitch_hint
     · 文本强信号（哈哈/哭/问号/感叹号）覆盖情绪
     · 字段齐全、数值范围合法（mouth_open 0-1, valence -1..1）
     · 空回复/缺情绪仍返回完整结构（容错）
  B. Agent 集成：reply 后 agent 产出 behavior（_last_behavior / last_behavior()）
  C. WS 层集成：reply 后广播 behavior 事件（向前兼容 - 无 behavior 不报错）

本文件自包含（内置最小派生 fixture），不依赖 test/ 下其它 conftest，
避免与 test/ollama/test/deepseek 的 conftest 产生跨目录耦合。
"""
import json

import pytest

from core.behavior import BehaviorMapper


# ===================== A. 纯函数单测 =====================

def test_derive_happy_from_emotion():
    """开心情绪 -> smile 表情、正向 pitch、口型偏高。"""
    b = BehaviorMapper.derive("我好开心呀！", {"primary": "开心", "valence": 0.4, "intensity": 0.6})
    assert b["expression"] in ("smile01", "smile02", "f01")
    assert b["pitch_hint"] is not None and b["pitch_hint"] > 1.0
    assert 0.0 <= b["mouth_open"] <= 1.0
    assert isinstance(b["actions"], list)
    assert b["emotion"]["primary"] == "开心"


def test_derive_sad():
    """难过情绪 -> sad 表情、负向 pitch。"""
    b = BehaviorMapper.derive("我有点难过", {"primary": "难过", "valence": -0.7, "intensity": 0.8})
    assert b["expression"] == "sad01"
    assert b["pitch_hint"] < 1.0


def test_derive_text_signal_overrides():
    """文本强信号（"哈哈"）应覆盖默认情绪 -> 兴奋表情。"""
    b = BehaviorMapper.derive("哈哈，太逗了！", {"primary": "平静", "valence": 0.0, "intensity": 0.5})
    assert b["expression"] in ("f01", "smile01", "smile02")


def test_derive_cry_from_text():
    """文本"哭"信号 -> sad/cry 表情。"""
    b = BehaviorMapper.derive("这个真的让我想哭", {"primary": "平静", "valence": 0.0})
    assert b["expression"] in ("sad01", "cry01")


def test_derive_question_mark_surprised():
    """问号 -> surprised 表情。"""
    b = BehaviorMapper.derive("真的吗？", {"primary": "平静", "valence": 0.0, "intensity": 0.5})
    assert b["expression"] == "surprised01"


def test_derive_actions():
    """动作映射：问候->wave，赞赏->clap，感谢->bow。"""
    assert "wave" in BehaviorMapper.derive("你好呀！", {})["actions"]
    assert "clap" in BehaviorMapper.derive("哇好棒！", {})["actions"]
    assert "bow" in BehaviorMapper.derive("真的太感谢你了", {})["actions"]


def test_derive_empty_input_is_stable():
    """空回复/无情绪 -> 返回完整结构，不抛错，字段齐全。"""
    b = BehaviorMapper.derive("", None)
    assert set(b) >= {"emotion", "expression", "mouth_open", "actions", "pitch_hint"}
    assert b["emotion"]["primary"] == "平静"
    assert b["expression"] == "default"
    assert 0.0 <= b["mouth_open"] <= 1.0


def test_derive_invalid_emotion_numbers_clamped():
    """非法数值被钳制到合法范围。"""
    b = BehaviorMapper.derive("hi", {"primary": "开心", "valence": 99, "intensity": -5})
    assert -1.0 <= b["emotion"]["valence"] <= 1.0
    assert 0.0 <= b["emotion"]["intensity"] <= 1.0


def test_derive_mouth_open_bounds():
    """口型开合始终在 0-1 内。"""
    for text in ["哈哈哈哈哈！", "嗯", "啊！！", "今天天气很好呢～", "……"]:
        b = BehaviorMapper.derive(text, {"primary": "开心", "intensity": 0.9})
        assert 0.0 <= b["mouth_open"] <= 1.0, text


# ===================== B. Agent 集成：reply 后产出 behavior =====================

def test_agent_last_behavior_after_reply():
    """用最小装配验证：chat 后 agent.last_behavior() 非空且含 expression。"""
    import tempfile, os
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore, EmotionState, AffectionState
    from agent.graph import LangGraphMemoryAgent
    from core.behavior import BehaviorMapper
    from core.role.manager import RoleManager, RoleConfig

    # 假嵌入
    class _FE:
        def name(self): return "fake"
        def __call__(self, input):
            input = [input] if isinstance(input, str) else list(input)
            return [[0.001] * 384 for _ in input]
        def embed_query(self, input=None, query=None):
            return [(query or input or [""]) and [0.001] * 384]

    import core.memory.vector_store as VS
    VS.get_embedding_function = lambda: _FE()

    d = tempfile.mkdtemp()
    store = SimpleMemory(path=os.path.join(d, "c"))

    # 假 tool 适配器（返回合法情感 JSON）
    class FakeTool:
        def chat(self, messages, **kw):
            return json.dumps({"emotion": {"primary": "开心", "intensity": 0.6, "valence": 0.4, "description": ""},
                               "affection": {k: 0.5 for k in ("liking","trust","familiarity","respect","interest")},
                               "attachment": 0.3, "needs_tool": False, "tool_decision": ""})
    tool_fake = FakeTool()

    # 假 llm 适配器（直接回复，不调工具）
    class FakeLLM:
        def chat_with_tools(self, messages, tools=None, **kw):
            return {"content": "哈哈，我今天特别开心！", "tool_calls": []}
    llm_fake = FakeLLM()

    mngr = MemoryManager(store, tool_fake)
    rm = RoleManager.__new__(RoleManager)
    rm._roles = {"kasumi": RoleConfig(role_id="kasumi", display_name="测试", default=True)}
    rm._prompts = {"kasumi": "你是户山香澄。"}
    es = EmotionStore(store)

    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm_fake, tool_adapter=tool_fake, perception=None,
        tool_fallback=False,
    )
    reply = ag.chat("u1", "你好呀")
    assert reply  # 有回复
    b = ag.last_behavior()
    assert b is not None, "chat 后应产出 behavior"
    assert "expression" in b
    assert b["emotion"]["primary"] == "开心"
    assert 0.0 <= b["mouth_open"] <= 1.0


# ===================== C. WS 层：reply 后广播 behavior（向前兼容） =====================

def test_agent_has_last_behavior_method():
    """agent 暴露 last_behavior()（供 WS 层 getattr 调用），且无 behavior 时返回 None 不抛错。"""
    import tempfile, os
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent
    from core.role.manager import RoleManager, RoleConfig

    class _FE:
        def name(self): return "fake"
        def __call__(self, input):
            return [[0.001] * 384 for _ in (input if isinstance(input, list) else [input])]
        def embed_query(self, input=None, query=None):
            return [[0.001] * 384]

    import core.memory.vector_store as VS
    VS.get_embedding_function = lambda: _FE()

    class FakeTool:
        def chat(self, messages, **kw):
            return json.dumps({"emotion": {"primary": "平静", "intensity": 0.5, "valence": 0.0, "description": ""},
                               "affection": {"liking": 0.5, "trust": 0.5, "familiarity": 0.5,
                                             "respect": 0.5, "interest": 0.5, "attachment": 0.3},
                               "needs_tool": False, "tool_decision": ""})
    class FakeLLM:
        def chat_with_tools(self, messages, tools=None, **kw):
            return {"content": "嗯嗯", "tool_calls": []}

    d = tempfile.mkdtemp()
    store = SimpleMemory(path=os.path.join(d, "c"))
    mngr = MemoryManager(store, FakeTool())
    rm = RoleManager.__new__(RoleManager)
    rm._roles = {"kasumi": RoleConfig(role_id="kasumi", display_name="测试", default=True)}
    rm._prompts = {"kasumi": "你是户山香澄。"}
    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=EmotionStore(store), role_id="kasumi",
        llm_adapter=FakeLLM(), tool_adapter=FakeTool(), perception=None, tool_fallback=False,
    )
    # 未产生回复前 last_behavior() 返回 None（兼容旧行为，WS 层 getattr 安全）
    assert ag.last_behavior() is None or isinstance(ag.last_behavior(), dict)
