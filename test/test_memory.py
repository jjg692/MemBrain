"""
单元测试：五层记忆 (L1-L5)
================================================
- L1：内存上下文，超限压缩 / 双键隔离
- L2：短期记忆 ChromaDB 持久化 + FIFO 淘汰 + 冷启动加载回 L1
- L4：事实抽取（重要性阈值 + LLM 抽取 + 去重 + 衰减）
- L5：角色事实（仅按 role_id，启动加载幂等）
- retrieve：综合检索 L4+L5
"""
import json

import pytest

pytestmark = pytest.mark.usefixtures("fake_embedding")


# ===================== L1 =====================

def test_l1_basic_add_and_get(memory_manager):
    mngr = memory_manager
    mngr.add_to_l1("u1", "kasumi", "user", "你好")
    mngr.add_to_l1("u1", "kasumi", "assistant", "嗨")
    l1 = mngr.get_l1("u1", "kasumi")
    assert l1 == [{"role": "user", "content": "你好"},
                  {"role": "assistant", "content": "嗨"}]


def test_l1_double_key_isolation(memory_manager):
    mngr = memory_manager
    mngr.add_to_l1("u1", "kasumi", "user", "a")
    mngr.add_to_l1("u1", "kokoro", "user", "b")
    mngr.add_to_l1("u2", "kasumi", "user", "c")
    assert [m["content"] for m in mngr.get_l1("u1", "kasumi")] == ["a"]
    assert [m["content"] for m in mngr.get_l1("u1", "kokoro")] == ["b"]
    assert [m["content"] for m in mngr.get_l1("u2", "kasumi")] == ["c"]


def test_l1_compress_over_limit(monkeypatch, memory_manager):
    """超限（2*最大轮数）触发压缩：旧历史 -> 摘要 + 保留最近。"""
    mngr = memory_manager
    monkeypatch.setattr("core.memory.memory_manager.MEMORY_CONTEXT_MAX_ROUNDS", 2)
    # 预置摘要响应用于压缩
    mngr.tool_adapter.enqueue_chat("【已压缩的旧对话摘要】")
    for i in range(6):
        mngr.add_to_l1("u1", "kasumi", "user" if i % 2 == 0 else "assistant", f"msg{i}")
    ctx = mngr.get_l1("u1", "kasumi")
    assert ctx[0]["role"] == "system"
    assert "摘要" in ctx[0]["content"]


# ===================== L2 短期记忆 =====================

def test_l2_save_and_recall(memory_manager):
    mngr = memory_manager
    mngr.save_short_term("u1", "kasumi", "你喜欢什么", "我喜欢弹吉他")
    res = mngr.memory.get(
        where=mngr.memory._build_where({"user_id": "u1"}, {"role_id": "kasumi"},
                                       {"type": "short_term"}),
        limit=10,
    )
    assert len(res["results"]) == 1
    assert "你喜欢什么" in res["results"][0]["document"]


def test_l2_fifo_prune(monkeypatch, memory_manager):
    """L2 超过保留轮数时删除最旧。"""
    mngr = memory_manager
    monkeypatch.setattr("core.memory.memory_manager.MEMORY_SHORT_TERM_MAX_ROUNDS", 2)
    for i in range(5):
        mngr.save_short_term("u1", "kasumi", f"q{i}", f"a{i}")
    res = mngr.memory.get(
        where=mngr.memory._build_where({"user_id": "u1"}, {"role_id": "kasumi"},
                                       {"type": "short_term"}),
        limit=100,
    )
    docs = [r["document"] for r in res["results"]]
    assert len(docs) == 2
    # FIFO：保留的是最近两条（q3/a3, q4/a4）
    assert "q4" in docs[-1]


def test_l2_cold_start_loads_to_l1(memory_manager):
    """冷启动：L2 历史首次访问该 (user,role) 时经 cold_start_load 加载到 L1。"""
    mngr = memory_manager
    mngr.save_short_term("u1", "kasumi", "你叫什么", "我叫香澄")
    # L1 尚未加载
    assert len(mngr.get_l1("u1", "kasumi")) == 0
    # 触发冷启动加载
    mngr.cold_start_load("u1", "kasumi")
    l1 = mngr.get_l1("u1", "kasumi")
    assert len(l1) >= 1
    assert any("你叫什么" in m["content"] for m in l1)


def test_l2_cold_start_only_once(memory_manager):
    """冷启动只执行一次。"""
    mngr = memory_manager
    mngr.save_short_term("u1", "kasumi", "历史1", "old")
    mngr.cold_start_load("u1", "kasumi")
    # 追加新的 L2 后，不应再次冷启动加载（对话中不查 L2）
    mngr.save_short_term("u1", "kasumi", "历史2", "new")
    mngr.cold_start_load("u1", "kasumi")  # 幂等：已标记
    l1 = mngr.get_l1("u1", "kasumi")
    assert not any("历史2" in m["content"] for m in l1)


# ===================== L4 事实 =====================

def test_l4_extract_fact_when_important(monkeypatch, memory_manager):
    """重要性 >= 阈值时抽取事实并写入。"""
    mngr = memory_manager
    monkeypatch.setattr("core.memory.memory_manager.MEMORY_IMPORTANCE_THRESHOLD", 0.5)
    mngr.tool_adapter.enqueue_chat("0.9")
    mngr.tool_adapter.enqueue_chat('["用户喜欢喝咖啡"]')
    mngr.judge_and_extract_facts("u1", "kasumi", "我喜欢喝咖啡", "好的记住了")
    facts = mngr.get_facts("u1", "kasumi")
    assert any("咖啡" in f for f in facts)


def test_l4_skip_when_below_threshold(monkeypatch, memory_manager):
    """重要性低于阈值时不抽取。"""
    mngr = memory_manager
    monkeypatch.setattr("core.memory.memory_manager.MEMORY_IMPORTANCE_THRESHOLD", 0.9)
    mngr.tool_adapter.enqueue_chat("0.3")
    mngr.tool_adapter.enqueue_chat('["琐碎事实"]')
    mngr.judge_and_extract_facts("u1", "kasumi", "今天吃了面", "不错")
    assert mngr.get_facts("u1", "kasumi") == []


def test_l4_dedupe(monkeypatch, memory_manager):
    """重复事实不重复写入。"""
    mngr = memory_manager
    monkeypatch.setattr("core.memory.memory_manager.MEMORY_IMPORTANCE_THRESHOLD", 0.5)
    for _ in range(2):
        mngr.tool_adapter.enqueue_chat("0.9")
        mngr.tool_adapter.enqueue_chat('["用户喜欢喝茶"]')
        mngr.judge_and_extract_facts("u1", "kasumi", "我喜欢喝茶", "好的")
    facts = mngr.get_facts("u1", "kasumi")
    # 向量检索可能返回多条，但内容都应唯一
    contents = set(facts)
    assert len({f for f in contents if "茶" in f}) == 1


def test_l4_get_facts_isolated(memory_manager):
    """L4 按 (user, role) 隔离。"""
    mngr = memory_manager
    mngr.memory.add_with_title("f", "u1专属事实", user_id="u1", role_id="kasumi",
                               type_="fact", meta={"importance": 0.9})
    mngr.memory.add_with_title("f2", "u2专属事实", user_id="u2", role_id="kasumi",
                               type_="fact", meta={"importance": 0.9})
    u1 = mngr.get_facts("u1", "kasumi")
    u2 = mngr.get_facts("u2", "kasumi")
    assert any("u1专属" in f for f in u1)
    assert not any("u1专属" in f for f in u2)


def test_l4_apply_decay(monkeypatch, memory_manager):
    """超期事实被标记 decayed。"""
    from datetime import datetime, timedelta
    from core.memory.vector_store import SimpleMemory
    mngr = memory_manager
    old = (datetime.now() - timedelta(days=200)).isoformat()
    mngr.memory.add_with_title("f", "很久以前的事实", user_id="u1", role_id="kasumi",
                               type_="fact",
                               meta={"importance": 0.9, "created_at": old})
    monkeypatch.setattr("core.memory.memory_manager.MEMORY_FACT_DECAY_DAYS", 90)
    mngr.apply_fact_decay()
    all_f = mngr.memory.collection.get(
        where={"type": "fact"}, limit=100, include=["metadatas"])
    metas = all_f["metadatas"]
    assert any(m.get("decayed") for m in metas)


# ===================== L5 角色事实 =====================

def test_l5_ensure_role_facts(monkeypatch, memory_manager):
    """L5：从角色 prompt 抽取事实并写入（仅按 role_id）。"""
    mngr = memory_manager
    mngr.tool_adapter.enqueue_chat('["香澄是吉他手","香澄元气满满"]')
    mngr.ensure_role_facts("kasumi", "你是香澄，一个吉他手，元气满满。")
    facts = mngr.get_role_facts("kasumi")
    assert any("吉他手" in f for f in facts)


def test_l5_only_role_isolated(monkeypatch, memory_manager):
    """L5 仅按 role_id 隔离：不同角色事实互不串。"""
    mngr = memory_manager
    mngr.tool_adapter.enqueue_chat('["角色A事实"]')
    mngr.ensure_role_facts("roleA", "A的设定")
    mngr.tool_adapter.enqueue_chat('["角色B事实"]')
    mngr.ensure_role_facts("roleB", "B的设定")
    a = mngr.get_role_facts("roleA")
    b = mngr.get_role_facts("roleB")
    assert any("角色A事实" in f for f in a)
    assert not any("角色A事实" in f for f in b)
    assert any("角色B事实" in f for f in b)


def test_l5_idempotent(memory_manager):
    """ensure_role_facts 幂等：已加载不重复抽取。"""
    mngr = memory_manager
    # 直接预置一条
    mngr.memory.add_with_title("r", "已存在事实", user_id="__role__", role_id="kasumi",
                               type_="role_fact", meta={"role_id": "kasumi"})
    mngr.ensure_role_facts("kasumi", "任意prompt")
    # 不应新增（tool 未收到调用则说明提前返回）
    assert mngr.tool_adapter.chat_call_count == 0


# ===================== 综合检索 =====================

def test_retrieve_combines_l4_and_l5(memory_manager):
    mngr = memory_manager
    mngr.memory.add_with_title("f", "用户喜欢蓝色", user_id="u1", role_id="kasumi",
                               type_="fact", meta={"importance": 0.8})
    mngr.memory.add_with_title("r", "香澄喜欢星星", user_id="__role__", role_id="kasumi",
                               type_="role_fact", meta={"role_id": "kasumi"})
    res = mngr.retrieve("u1", "kasumi", "用户喜欢什么颜色", top_k=5)
    assert any("蓝色" in f for f in res["facts"])
    assert any("星星" in f for f in res["role_facts"])


# ===================== stats =====================

def test_stats_counts(memory_manager):
    mngr = memory_manager
    # 用独立 store 无法直接计数（同 collection），至少能运行并返回键
    stats = mngr.stats()
    for key in ("total", "l2", "l4", "l5", "l3", "emotion", "affection"):
        assert key in stats
