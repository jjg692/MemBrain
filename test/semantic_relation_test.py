"""
关系记忆内核 语义级测试
================================================================
与 test/ollama/ 的语义测试同风格：用【真实 Ollama 本地模型】驱动完整 Agent，
验证"认知架构/记忆/人格一致性/情感智力/主动性"在语义层面是否真的自然、合时宜，
而非只看确定性的数值/结构。

覆盖（皆在"家用机 + 本地模型 + harness 辅助"约束下）：
  1. 承诺记忆：较早让角色"记住"的承诺，后续主动/承接能自然体现"记得"
  2. 主动开口：proactive_message 用关系记忆生成的主动话，非空/中文/不出戏/简短
  3. 内在状态注入不破坏对话自然度（常规对话仍保持角色、不出戏）
  4. 关掉关系记忆后仍不崩（回退安全）

前置：需要本地 Ollama 在线（LLM_MODEL / TOOL_LLM_MODEL 已 pull）。
不可达则自动跳过，不影响离线单元测试。
"""
import pytest

pytestmark = pytest.mark.usefixtures("fake_embedding")


def _ollama_ready() -> bool:
    try:
        from core.config import OLLAMA_HOST
        import requests
        r = requests.get(OLLAMA_HOST.rstrip("/") + "/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


needs_ollama = pytest.mark.skipif(not _ollama_ready(), reason="Ollama 未在线，跳过关系记忆语义测试")


def _is_ai_speak(text: str) -> bool:
    ai_markers = ["我是AI", "作为AI", "我是助手", "请告诉我", "请问有什么可以帮您",
                  "人工智能", "语言模型", "训练数据", "系统提示", "抱歉我无法", "已为您"]
    return any(m in (text or "") for m in ai_markers)


def _make_agent(tmp_path, monkeypatch, with_relation=True):
    """真实 Ollama 适配器 + 关系记忆内核（可选打开），返回 (agent, rel)。"""
    from core.adapters import OllamaAdapter
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent
    from core.config import LLM_MODEL, TOOL_LLM_MODEL, OLLAMA_HOST, LLM_TEMPERATURE
    from core.relation_memory import RelationMemory
    from test.ollama.conftest import make_role_manager

    monkeypatch.setattr("core.user_profile.USER_PROFILES_FILE", tmp_path / "up.json")
    store = SimpleMemory(path=str(tmp_path / "chroma_rel"))
    llm = OllamaAdapter(model=LLM_MODEL, host=OLLAMA_HOST); llm.set_temperature(min(0.4, LLM_TEMPERATURE))
    tool_llm = OllamaAdapter(model=TOOL_LLM_MODEL, host=OLLAMA_HOST); tool_llm.set_temperature(min(0.4, LLM_TEMPERATURE))
    mngr = MemoryManager(store, tool_llm)
    prompt = "你是户山香澄，BanG Dream! Poppin'Party 的主唱和吉他手，元气、活泼、爱撒娇。"
    rm = make_role_manager("kasumi", prompt)
    es = EmotionStore(store)

    class _Perception:
        def record_user_activity(self, user_id): pass
        def record_mood(self, user_id, *a, **k): pass
        def summarize(self, user_id): return ""

    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm, tool_adapter=tool_llm, perception=_Perception(), tool_fallback=False,
    )
    if with_relation:
        rel = RelationMemory(path=str(tmp_path / "rel.json"))
        ag.relation = rel
    else:
        rel = None
        ag.relation = None
    return ag, rel


@needs_ollama
def test_semantic_promise_is_remembered(tmp_path, monkeypatch, fake_embedding):
    """承诺记忆语义：让角色记住一条承诺，主动/承接应自然体现"记得"。"""
    ag, rel = _make_agent(tmp_path, monkeypatch)
    rel.store_reflection("u1", {"promises": ["记得你周五下午要去面试"]})
    out = ag.proactive_message("u1", trigger="到主动打招呼时间了")
    assert out and out.strip(), "主动话为空"
    assert not out.startswith("["), f"模型报错：{out[:60]}"
    assert any("\u4e00" <= c <= "\u9fff" for c in out), "非中文"
    assert not _is_ai_speak(out), f"出现 AI 腔：{out}"
    assert len(out) <= 120, f"主动话过长：{out}"
    print(f"\n[主动话(承诺)] {out}")


@needs_ollama
def test_semantic_proactive_is_natural(tmp_path, monkeypatch, fake_embedding):
    """主动开口语义：中文、不出戏、简短、像朋友。"""
    ag, rel = _make_agent(tmp_path, monkeypatch)
    rel.add_episode("u1", "最近在练吉他，手都磨出茧了", "哈哈，我也超懂这个！", {"emotion": {"valence": 0.3}}, resonance=0.8)
    out = ag.proactive_message("u1")
    assert out and out.strip()
    assert not out.startswith("[")
    assert any("\u4e00" <= c <= "\u9fff" for c in out)
    assert not _is_ai_speak(out), f"出现 AI 腔：{out}"
    assert len(out) <= 120, f"过长：{out}"
    print(f"\n[主动话(经历)] {out}")


@needs_ollama
def test_semantic_inner_state_does_not_break_natural(tmp_path, monkeypatch, fake_embedding):
    """开启关系记忆后，常规对话仍自然、不出戏（注入不破坏主链路）。"""
    ag, rel = _make_agent(tmp_path, monkeypatch, with_relation=True)
    rel.store_reflection("u1", {"self_summary": "我是香澄，喜欢和朋友一起玩音乐", "cares_about": ["朋友", "音乐"]})
    rel.add_episode("u1", "我有点累", "抱抱，辛苦了", {"emotion": {"valence": -0.4}}, resonance=0.7)
    reply = ag.chat("u1", "随便聊几句，你今天过的怎么样？")
    assert reply and reply.strip()
    assert not reply.startswith("[")
    assert not _is_ai_speak(reply), f"对话被注入搞出 AI 腔：{reply}"
    assert any("\u4e00" <= c <= "\u9fff" for c in reply)
    print(f"\n[常规对话(启用内核)] {reply}")


@needs_ollama
def test_semantic_relation_disabled_fallback(tmp_path, monkeypatch, fake_embedding):
    """关系记忆关闭后，主动/对话仍不崩。"""
    import core.relation_memory as RM
    monkeypatch.setattr(RM, "RELATION_MEMORY_ENABLED", False)
    ag, _rel = _make_agent(tmp_path, monkeypatch, with_relation=True)
    out = ag.proactive_message("u1")
    assert out and out.strip(), "禁用内核后主动话不应崩"
    assert not out.startswith("[")
    reply = ag.chat("u1", "在吗？")
    assert reply and not reply.startswith("[")
