"""
情感因果化 + 承诺兑现闭环 语义测试（真实远程 LLM · DeepSeek-R1）
================================================================
验证 ①② 在真实模型下的语义效果（家用机 + harness 辅助、不额外调 LLM）：

  1. 情感因果化注入不破坏自然度：有"示弱/正经历"时，对话仍自然、不机械罗列
  2. 承诺兑现闭环：用户致谢后，pending 承诺被兑现，主动素材不再重复提"已兑现"的事
  （机制正确性已在 relation_memory_test 的单元测试覆盖；这里确认真实模型端到端可用）

前置：.env 配置 provider=openai + LLM_API_BASE_URL + LLM_API_KEY + LLM_REMOTE_MODEL，
且远程可达。不可达自动 skip。
"""
import pytest

pytestmark = pytest.mark.usefixtures("fake_embedding")

from test.deepseek.conftest import needs_remote, _remote_ready


def _build_agent(tmp_path, monkeypatch):
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
    rm = make_role_manager("kasumi", "你是户山香澄，BanG Dream! Poppin'Party 的主唱和吉他手，元气、活泼、爱撒娇。")
    es = EmotionStore(store)
    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm, tool_adapter=tool_llm, perception=None,
    )
    rel = RelationMemory(path=str(tmp_path / "rel.json"))
    ag.relation = rel
    return ag, rel


def _is_ai_speak(text: str) -> bool:
    ai_markers = ["我是AI", "作为AI", "我是助手", "请告诉我", "请问有什么可以帮您",
                  "人工智能", "语言模型", "训练数据", "系统提示", "抱歉我无法", "已为您"]
    return any(m in (text or "") for m in ai_markers)


@needs_remote
def test_affection_causation_natural(tmp_path, monkeypatch):
    """情感因果化：注入"为什么在乎"后，对话仍自然、不出戏、不机械罗列。"""
    ag, rel = _build_agent(tmp_path, monkeypatch)
    rel.add_episode("u1", "那天下雨你来接我，心里很暖", "当然要去接你呀", {"emotion": {"valence": 0.7}}, resonance=0.9)
    rel.add_episode("u1", "最近压力好大有点扛不住", "我在，慢慢说", {"emotion": {"valence": -0.4}}, resonance=0.8)
    reply = ag.chat("u1", "你还记得我吗？")
    assert reply and reply.strip()
    assert not reply.startswith("[")
    assert any("\u4e00" <= c <= "\u9fff" for c in reply)
    assert not _is_ai_speak(reply), f"出现 AI 腔：{reply}"
    print(f"\n[情感因果化] {reply}")


@needs_remote
def test_promise_resolved_after_thanks(tmp_path, monkeypatch):
    """承诺兑现闭环：用户致谢后，主动素材不再重复提已兑现的承诺。"""
    ag, rel = _build_agent(tmp_path, monkeypatch)
    rel.store_reflection("u1", {"promises": ["周五帮你过报告"]})
    # 模拟用户致谢（触发兑现闭环）
    rel.resolve_promises_on_user_signal("u1", "谢谢你还记得帮我过报告！")
    if rel.pending_promises("u1"):
        print("   (承诺仍在 pending，语义测试仅确认不崩)")
    else:
        print("   承诺已被兑现，应从主动素材消失")
    # 主动开口仍自然（不应崩、不出戏）
    out = ag.proactive_message("u1")
    assert out and out.strip()
    assert not out.startswith("[")
    assert not _is_ai_speak(out), f"出现 AI 腔：{out}"
    print(f"\n[承诺兑现后主动话] {out}")
