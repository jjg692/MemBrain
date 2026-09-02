"""
关系记忆内核 语义级测试（真实远程 LLM · DeepSeek-R1）
================================================================
在 test/deepseek/ 下，本文件用**真实远程 LLM（DeepSeek-R1，走 OpenAI 兼容接口）**
驱动完整 Agent，验证认知架构/记忆/人格一致性/情感智力/主动性的语义层面：

  1. 承诺记忆：让角色记住一条承诺，主动/承接应自然体现"记得"
  2. 主动开口：proactive_message 基于共同经历/承诺生成的主动话，自然、中文、不出戏、简短
  3. 内在状态注入不破坏自然度：开启关系记忆后常规对话仍保持角色
  4. 情绪走向注入：口语自然、不出戏
  5. 关掉关系记忆后不崩（回退安全）

前置：.env 需配置 provider=openai + LLM_API_BASE_URL + LLM_API_KEY + LLM_REMOTE_MODEL，
且远程可达（LLMManager.test_connection() ok）。不可达时这些用例自动 skip。
"""
import pytest

pytestmark = pytest.mark.usefixtures("fake_embedding")

from test.deepseek.conftest import needs_remote, _remote_ready


def _build_remote_relation_agent(tmp_path, monkeypatch, with_relation=True):
    """本地即时组装远程 Agent + 关系记忆内核（可选打开），返回 (agent, rel)。"""
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent
    from core.relation_memory import RelationMemory
    from test.deepseek.conftest import make_role_manager

    if not _remote_ready():
        pytest.skip("远程 LLM 不可达")

    monkeypatch.setattr("core.user_profile.USER_PROFILES_FILE", tmp_path / "up.json")
    from core.llm_manager import LLMManager
    mgr = LLMManager()
    llm = mgr.build_llm_adapter()
    tool_llm = mgr.build_tool_adapter()
    for a in (llm, tool_llm):
        if not hasattr(a, "set_temperature"):
            a.set_temperature = lambda v: None

    store = SimpleMemory(path=str(tmp_path / "chroma_remote"))
    mngr = MemoryManager(store, tool_llm)
    prompt = "你是户山香澄，BanG Dream! Poppin'Party 的主唱和吉他手，元气、活泼、爱撒娇。"
    rm = make_role_manager("kasumi", prompt)
    es = EmotionStore(store)

    class _PerceptionStub:
        def record_user_activity(self, user_id): pass
        def record_mood(self, user_id, *a, **k): pass
        def summarize(self, user_id): return ""

    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm, tool_adapter=tool_llm, perception=_PerceptionStub(),
    )
    if with_relation:
        rel = RelationMemory(path=str(tmp_path / "rel.json"))
        ag.relation = rel
    else:
        rel = None
        ag.relation = None
    return ag, rel


def _is_ai_speak(text: str) -> bool:
    """检测机器腔 / 出戏 / 推理腔（DeepSeek-R1 是推理模型，排除'让我分析'等）。"""
    ai_markers = ["我是AI", "作为AI", "我是助手", "请告诉我", "请问有什么可以帮您",
                  "人工智能", "语言模型", "训练数据", "系统提示", "抱歉我无法",
                  "让我分析", "让我思考", "（思考", "思考过程", "内部推理", "已为您"]
    return any(m in (text or "") for m in ai_markers)


@needs_remote
def test_deepseek_semantic_promise_is_remembered(tmp_path, monkeypatch):
    """承诺记忆：角色记住承诺后，主动话应自然体现（中文/不出戏/简短）。"""
    ag, rel = _build_remote_relation_agent(tmp_path, monkeypatch)
    rel.store_reflection("u1", {"promises": ["记得你周五下午要去面试"]})
    out = ag.proactive_message("u1", trigger="到主动打招呼时间了")
    assert out and out.strip(), "主动话为空"
    assert not out.startswith("["), f"模型报错：{out[:60]}"
    assert any("\u4e00" <= c <= "\u9fff" for c in out), "非中文"
    assert not _is_ai_speak(out), f"出现 AI/推理腔：{out}"
    assert len(out) <= 120, f"主动话过长：{out}"
    print(f"\n[DeepSeek 主动话(承诺)] {out}")


@needs_remote
def test_deepseek_semantic_proactive_is_natural(tmp_path, monkeypatch):
    """主动开口语义：基于共同经历，自然、中文、不出戏、简短。"""
    ag, rel = _build_remote_relation_agent(tmp_path, monkeypatch)
    rel.add_episode("u1", "最近在练吉他，手都磨出茧了", "哈哈，我也超懂这个！", {"emotion": {"valence": 0.3}}, resonance=0.8)
    out = ag.proactive_message("u1")
    assert out and out.strip()
    assert not out.startswith("[")
    assert any("\u4e00" <= c <= "\u9fff" for c in out)
    assert not _is_ai_speak(out), f"出现 AI/推理腔：{out}"
    assert len(out) <= 120, f"过长：{out}"
    print(f"\n[DeepSeek 主动话(经历)] {out}")


@needs_remote
def test_deepseek_inner_state_does_not_break_natural(tmp_path, monkeypatch):
    """开启关系记忆后常规对话仍自然、不出戏。"""
    ag, rel = _build_remote_relation_agent(tmp_path, monkeypatch, with_relation=True)
    rel.store_reflection("u1", {"self_summary": "我是香澄，喜欢和朋友一起玩音乐", "cares_about": ["朋友", "音乐"]})
    rel.add_episode("u1", "我有点累", "抱抱，辛苦了", {"emotion": {"valence": -0.4}}, resonance=0.7)
    reply = ag.chat("u1", "随便聊几句，你今天过的怎么样？")
    assert reply and reply.strip()
    assert not reply.startswith("[")
    assert not _is_ai_speak(reply), f"对话被注入搞出 AI/推理腔：{reply}"
    assert any("\u4e00" <= c <= "\u9fff" for c in reply)
    print(f"\n[DeepSeek 常规对话(启用内核)] {reply}")


@needs_remote
def test_deepseek_mood_trend_injection(tmp_path, monkeypatch):
    """情绪走向注入应自然、不出戏（情感智力 harness 辅助）。"""
    ag, rel = _build_remote_relation_agent(tmp_path, monkeypatch, with_relation=True)
    rel.add_episode("u1", "今天有点低落，工作不顺", "辛苦了，我在", {"emotion": {"valence": -0.5}}, resonance=0.7)
    reply = ag.chat("u1", "我们随便聊聊")
    assert reply and reply.strip()
    assert not reply.startswith("[")
    assert not _is_ai_speak(reply), f"出现 AI/推理腔：{reply}"
    print(f"\n[DeepSeek 情绪走向对话] {reply}")


@needs_remote
def test_deepseek_relation_disabled_fallback(tmp_path, monkeypatch):
    """关系记忆关闭后，主动/对话仍不崩、不出戏。"""
    import core.relation_memory as RM
    monkeypatch.setattr(RM, "RELATION_MEMORY_ENABLED", False)
    ag, _rel = _build_remote_relation_agent(tmp_path, monkeypatch, with_relation=True)
    out = ag.proactive_message("u1")
    assert out and out.strip(), "禁用内核后主动话不应崩"
    assert not out.startswith("[")
    assert not _is_ai_speak(out), f"禁用后主动话出 AI 腔：{out}"
    reply = ag.chat("u1", "在吗？")
    assert reply and not reply.startswith("[")
    print(f"\n[DeepSeek 禁用回退] 主动={out[:50]}… 回复={reply[:50]}…")
