"""
关系记忆内核（RelationMemory）测试
==================================
覆盖"底层内在状态层"的三块能力：
  A. 持久化 + 结构
  B. 时间衰减（真人式淡忘）
  C. 自我模型 / 反思
本文件自包含（用临时 JSON 文件，不触碰 ChromaDB / 不依赖 Ollama）。
"""
import datetime
from core.relation_memory import (
    RelationMemory, get_relation_memory,
    half_life_decay, build_reflection_prompt, parse_reflection,
)


def test_half_life_decay():
    assert abs(half_life_decay(1.0, 21.0, 21.0) - 0.5) < 1e-6
    assert half_life_decay(1.0, 0, 21.0) == 1.0
    assert abs(half_life_decay(0.5, 42.0, 21.0) - 0.125) < 1e-6
    assert half_life_decay(1.0, 10.0, 0) == 1.0   # halflife<=0 不衰减


def test_add_episode_and_reload(tmp_path):
    p = str(tmp_path / "rel.json")
    r = RelationMemory(path=p, halflife_days=21, resonance_threshold=0.55)
    ep = r.add_episode("u1", "我爱吃辣", "好耶！我也超爱！", {"primary": "开心"}, resonance=0.9)
    assert ep is not None
    r2 = RelationMemory(path=p, halflife_days=21, resonance_threshold=0.55)
    eps = r2.recent_episodes("u1")
    assert len(eps) == 1 and eps[0]["user_msg"] == "我爱吃辣"


def test_low_resonance_skipped(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.55)
    assert r.add_episode("u1", "嗯嗯", "好的", {"primary": "平静"}, resonance=0.1) is None
    assert r.recent_episodes("u1") == []
    ep2 = r.add_episode("u1", "帮我设提醒", "已为你设置提醒", {"primary": "平静"}, resonance=0.1, impact="做了一个动作")
    assert ep2 is not None and len(r.recent_episodes("u1")) == 1


def test_user_isolation(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.0)
    r.add_episode("A", "a", "a1", resonance=0.9)
    r.add_episode("B", "b", "b1", resonance=0.9)
    assert len(r.recent_episodes("A")) == 1 and r.recent_episodes("A")[0]["user_msg"] == "a"
    assert len(r.recent_episodes("B")) == 1


def test_decayed_episodes_forgetting(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), halflife_days=7, resonance_threshold=0.0)
    old_iso = (datetime.datetime.now() - datetime.timedelta(days=60)).isoformat()
    r._user("u1")["episodes"].append({
        "ts": old_iso, "user_msg": "老记忆", "reply": "r", "resonance": 1.0, "emotion": {}, "impact": "",
    })
    r.add_episode("u1", "新记忆", "fresh", resonance=0.9)
    eps = r.decayed_episodes("u1", n=10, min_vitality=0.1)
    assert all(e["user_msg"] != "老记忆" for e in eps)   # 60 天前的被淡忘
    assert any(e["user_msg"] == "新记忆" for e in eps)


def test_store_reflection_and_snap(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    r.store_reflection("u1", {
        "about_user": "爱吃辣，作息不规律",
        "traits": ["直率", "熬夜"],
        "needs": ["提醒吃药"],
        "self_summary": "我是户山香澄",
        "cares_about": ["朋友", "音乐"],
        "current_mood_text": "想起你心情不错",
        "relationship": "熟悉的伙伴",
        "values": ["不骗人", "守约定"],
    })
    snap = r.snap("u1")
    assert snap["user_model"]["about_user"] == "爱吃辣，作息不规律"
    assert "直率" in snap["user_model"]["traits"]
    assert snap["self_model"]["summary"] == "我是户山香澄"
    assert "音乐" in snap["self_model"]["cares_about"]
    assert "不骗人" in snap["values"]


def test_reflection_helpers():
    p = build_reflection_prompt([{"user_msg": "你好", "reply": "嗨！"}], prev={"traits": ["x"]})
    assert "你好" in p and "最近对话" in p
    import json
    parsed = parse_reflection(json.dumps({"about_user": "x"}))
    assert parsed.get("about_user") == "x"
    parsed2 = parse_reflection("{\"self_summary\": \"我是\"}")
    assert parsed2.get("self_summary") == "我是"


def test_summary_text_injects(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.0)
    r.store_reflection("u1", {"self_summary": "我是香澄", "cares_about": ["朋友"]})
    r.add_episode("u1", "想吃火锅", "一起去！", resonance=0.8)
    t = r.summary_text("u1")
    assert "我是香澄" in t and "朋友" in t and "想吃火锅" in t




def test_apply_decay_with_emotion_objects(tmp_path):
    from core.emotion import EmotionState, AffectionState
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    es = EmotionState(primary="开心", intensity=0.8, valence=0.7)
    af = AffectionState(liking=0.6)
    d = r.apply_decay("u", es, af)
    assert d.get("last_active")              # 活跃时间被记录
    assert d["emotion"].get("primary") == "开心"
    assert 0.0 <= d.get("affection_avg", 0) <= 1.0
    r.mark_active("u")
    assert r.snap("u")["decay"].get("last_active")



def test_summary_text_is_bounded_and_prioritized(tmp_path):
    """注入文本必须有硬上限（防上下文膨胀）；经历按鲜活度、只留够重的。"""
    import datetime
    r = RelationMemory(path=str(tmp_path / "rel.json"), halflife_days=7, resonance_threshold=0.0)
    # 一条很久以前、极低共振的经历（应被淡忘，不注入）
    old = (datetime.datetime.now() - datetime.timedelta(days=40)).isoformat()
    r._user("u1")["episodes"].append({"ts": old, "user_msg": "四十天前的旧事", "reply": "r", "resonance": 0.3, "emotion": {}, "impact": ""})
    # 一条新近、高共振的经历（应保留）
    r.add_episode("u1", "最近在学吉他", "我们一起练呀！", {"primary": "开心"}, resonance=0.95)
    r.store_reflection("u1", {"about_user": "喜欢音乐", "self_summary": "我是爱音乐的角色", "cares_about": ["朋友", "吉他", "演出", "分享"]})
    t = r.summary_text("u1", max_chars=360)
    # 有界：硬预算生效
    assert len(t) <= 360
    # 老经历被淡忘，新经历在
    assert "四十天前的旧事" not in t
    assert "学吉他" in t
    # 高密度键在；cares_about 只以中文句呈现，不出现原始键名
    assert "你对自己的认知" in t
    assert "cares_about" not in t
    assert ("朋友" in t or "吉他" in t)


def test_summary_text_default_unbounded_but_prioritized(tmp_path):
    """默认（不传预算）仍保持少量经历、结构稳定；和旧版本容量一致或更小。"""
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.0)
    for i in range(20):
        r.add_episode("u1", "第%d次聊天" % i, "好的", resonance=0.6)
    t = r.summary_text("u1")
    # 经历最多 3 条，即使有 20 条
    assert t.count("你说「") <= 3

def test_per_role_file_isolation_default(tmp_path, monkeypatch):
    """多角色默认应隔离到不同文件（避免并发互相覆盖丢数据）。"""
    import core.relation_memory as RM
    monkeypatch.setattr(RM, "PROJECT_ROOT", tmp_path)
    a = RM.RelationMemory(role_id="kasumi")
    b = RM.RelationMemory(role_id="kokoro")
    assert a._path != b._path
    a.add_episode("u", "kasumi话题", "r", resonance=0.8)
    b.add_episode("u", "kokoro话题", "r", resonance=0.8)
    assert a.recent_episodes("u")[0]["user_msg"] == "kasumi话题"
    assert b.recent_episodes("u")[0]["user_msg"] == "kokoro话题"

def test_true_time_decay_affects_state(tmp_path):
    """decay_state 应真正把跨会话的状态值衰减到基线，并返回副本不改入参。"""
    import datetime
    from core.emotion import EmotionState, AffectionState
    r = RelationMemory(path=str(tmp_path / "rel.json"), halflife_days=7)
    r._user("u")["decay"]["last_active"] = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
    es = EmotionState(primary="开心", intensity=0.9)
    af = AffectionState(liking=0.9, attachment=0.9)
    eo, ao = r.decay_state("u", es, af)
    assert abs(eo.intensity - 0.7) < 1e-6
    assert abs(ao.liking - 0.7) < 1e-6
    assert es.intensity == 0.9 and af.liking == 0.9
    assert abs(ao.attachment - 0.6) < 1e-6

def test_relevant_episodes_prioritize_topic(tmp_path):
    """相关经历应按当前话题优先于纯新鲜度。"""
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.0)
    r.add_episode("u", "我今天去看了演唱会", "好棒！", resonance=0.5)
    r.add_episode("u", "我在背英语单词", "加油！", resonance=0.99)
    eps = r.relevant_episodes("u", "演唱会", n=2)
    assert eps[0]["user_msg"] == "我今天去看了演唱会"

def test_summary_text_relevance_query(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.0)
    r.add_episode("u", "演唱会门票真贵", "哈哈", resonance=0.6)
    r.add_episode("u", "我在学做饭", "不错哦", resonance=0.9)
    t = r.summary_text("u", max_chars=360, query="演唱会")
    assert "演唱会" in t

def test_reflection_batch_cap(tmp_path):
    ep = [{"user_msg": "a", "reply": "b"}] * 20
    p = build_reflection_prompt(ep, {}, max_batch=3)
    assert p.count("你说：「") == 3

def test_build_reflection_prompt_default_all(tmp_path):
    ep = [{"user_msg": "a", "reply": "b"}] * 5
    p = build_reflection_prompt(ep, {})
    assert p.count("你说：「") == 5



# ===================== 认知架构/记忆/人格一致性/情感智力/主动性 测试 =====================

def test_promises_tracked_and_injected(tmp_path):
    """反思中的承诺应被去重存储、从 summary_text 注入；可标记已兑现。"""
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    r.store_reflection("u1", {"about_user": "x", "promises": ["周五帮你过报告", "明天提醒你吃药"]})
    r.store_reflection("u1", {"promises": ["周五帮你过报告", "新承诺"]})
    pending = r.pending_promises("u1")
    texts = [p["text"] for p in pending]
    assert texts.count("周五帮你过报告") == 1      # 去重
    assert "新承诺" in texts
    # 注入到 summary_text
    t = r.summary_text("u1")
    assert "周五帮你过报告" in t
    # 标记已兑现后不再 pending
    r.mark_promise_kept("u1", "报告", "kept")
    assert all(p["text"] != "周五帮你过报告" for p in r.pending_promises("u1"))


def test_mood_trend_from_episodes_no_llm(tmp_path):
    """情绪走向从已存经历推导，零 LLM 调用。"""
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.0)
    r.add_episode("u1", "有点难过", "抱抱", {"emotion": {"valence": -0.6}}, resonance=0.6)
    r.add_episode("u1", "还是烦", "我在", {"emotion": {"valence": -0.5}}, resonance=0.6)
    m = r._mood_trend_text("u1")
    assert "变差" in m or "稳定" in m
    # 注入进 summary_text
    t = r.summary_text("u1")
    assert "整体氛围" in t


def test_proactive_hint_composes_relation_memory(tmp_path):
    """主动开口素材应优先来自未兑现承诺（人格一致性变现为主动性）。"""
    import tempfile, os
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent
    from core.role.manager import RoleManager, RoleConfig
    from core.relation_memory import RelationMemory

    class _FE:
        def name(self): return "fake"
        def __call__(self, input):
            return [[0.0]*384 for _ in (input if isinstance(input, list) else [input])]
        def embed_query(self, input=None, query=None): return [[0.0]*384]
    import core.memory.vector_store as VS
    VS.get_embedding_function = lambda: _FE()
    d = tempfile.mkdtemp()
    store = SimpleMemory(path=os.path.join(d, "c"))
    mngr = MemoryManager(store, None)
    rm = RoleManager.__new__(RoleManager)
    rm._roles = {"kasumi": RoleConfig(role_id="kasumi", display_name="t", default=True)}
    rm._prompts = {"kasumi": "你是户山香澄。"}
    rel = RelationMemory(path=os.path.join(d, "rel.json"))
    rel.store_reflection("u1", {"promises": ["记得你周五要交报告"]})
    rel.add_episode("u1", "最近在练吉他", "酷！", {"emotion": {"valence": 0.4}}, resonance=0.8)
    ag = LangGraphMemoryAgent(memory_manager=mngr, role_manager=rm, emotion_store=EmotionStore(store),
                              role_id="kasumi", llm_adapter=None, tool_adapter=None, perception=None, tool_fallback=False)
    ag.relation = rel
    hint = ag._compose_proactive_hint("u1")
    assert "报告" in hint or "吉他" in hint



# ===================== ①情感因果化 / ②承诺兑现闭环 =====================

def test_affection_reasons_from_episodes(tmp_path):
    """情感因果化：从正向经历推导好感来源，注入 summary_text。"""
    r = RelationMemory(path=str(tmp_path / "rel.json"), resonance_threshold=0.0)
    r.add_episode("u1", "今天被你安慰了，心里好受多", "我一直都在呀", {"emotion": {"valence": 0.6}}, resonance=0.9)
    r.add_episode("u1", "我最近压力好大", "辛苦了，慢慢来", {"emotion": {"valence": -0.3}}, resonance=0.7)
    reasons = r.affection_reasons("u1")
    assert len(reasons) >= 1
    t = r.summary_text("u1")
    assert "你为什么在乎用户" in t


def test_promise_resolved_on_user_signal(tmp_path):
    """用户表达感谢/确认时，相关承诺应被标记为已兑现并从 pending 移除。"""
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    r.store_reflection("u1", {"promises": ["明天提醒你吃药", "周五帮你过报告"]})
    # 不含确认信号 -> 不兑现
    assert r.resolve_promises_on_user_signal("u1", "随便聊聊") == 0
    assert len(r.pending_promises("u1")) == 2
    # 含感谢 + 提及"报告" -> 兑现相关那条
    kept = r.resolve_promises_on_user_signal("u1", "谢谢你还记得帮我过报告！")
    assert kept >= 1
    pending = [p["text"] for p in r.pending_promises("u1")]
    assert not any("报告" in t for t in pending)
    assert any("吃药" in t for t in pending)


def test_promise_overlap_only_related(tmp_path):
    """确认信号 + 无相关承诺词 -> 不误兑现。"""
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    r.store_reflection("u1", {"promises": ["明天要交演出排练表"]})
    # 谢谢了但没提排练表/演出 -> 不该兑现
    assert r.resolve_promises_on_user_signal("u1", "谢谢你今天陪我聊天！") == 0
    assert len(r.pending_promises("u1")) == 1



# ===================== ② 关系阶段有原因演化 / ④ 主动择时 =====================

def test_relationship_stage_evolves_with_experience(tmp_path):
    """有原因的演化：好感度数值不够时，靠共同经历证据也能升到对应阶段。"""
    from core.emotion.emotion import relationship_stage, AffectionState
    aff = AffectionState(familiarity=0.3, trust=0.3, attachment=0.2, liking=0.4)  # 数值偏低 => 陌生
    assert relationship_stage(aff) == "陌生"
    # 但若共同经历足够（6次以上、认识超过7天），应有据地升到"熟悉"
    assert relationship_stage(aff, {
"shared_episodes": 8, "major_events": 1, "days_known": 10}) == "熟悉"
    # 经历不足时维持数字阶段（不凭空升级）
    assert relationship_stage(aff, {"shared_episodes": 2}) == "陌生"


def test_relationship_stage_experience_caps_at_friend(tmp_path):
    """经历证据逐档提升、不跳级：数字"熟悉"只能靠经历升到"亲密"，不能凭经历一步到"挚友"。"""
    from core.emotion.emotion import relationship_stage, AffectionState
    aff = AffectionState(familiarity=0.5, trust=0.5, attachment=0.4)  # 数字: 熟悉
    # 足够重大经历 + 相识久 -> 从"熟悉"升到"亲密"
    assert relationship_stage(aff, {"shared_episodes": 20, "major_events": 2, "days_known": 20}) == "亲密"
    # 即使经历很多，"熟悉"基座也不会一步跨到"挚友"（需数字先到"亲密"基座）
    assert relationship_stage(aff, {"shared_episodes": 200, "major_events": 20, "days_known": 200}) == "亲密"
    # 数字到"亲密"基座 + 足经历 -> 挚友
    aff2 = AffectionState(familiarity=0.65, trust=0.55, attachment=0.55)  # 数字: 亲密
    assert relationship_stage(aff2, {"shared_episodes": 40, "major_events": 5, "days_known": 40}) == "挚友"


def test_relation_to_prompt_accepts_experience(tmp_path):
    """relation_to_prompt_text 传 experience 不崩，且默认（不传）行为不变。"""
    from core.emotion.emotion import relation_to_prompt_text, AffectionState
    # 默认（无 experience）与旧签名一致
    txt_default = relation_to_prompt_text(AffectionState(familiarity=0.9, trust=0.9, attachment=0.9))
    assert "挚友" in txt_default
    # 传 experience 也正常
    txt_exp = relation_to_prompt_text(AffectionState(), experience={"shared_episodes": 10, "days_known": 9})
    assert txt_exp.strip()


def test_timing_hint_uses_perception(tmp_path):
    """主动择时：有"几天没聊"的感知数据时，hint 应包含想念/关心提示。"""
    import tempfile, os
    from agent.graph import LangGraphMemoryAgent
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from core.role.manager import RoleManager, RoleConfig
    from core.relation_memory import RelationMemory
    class _FE:
        def name(self): return "fake"
        def __call__(self, input): return [[0.0]*384 for _ in (input if isinstance(input,list) else [input])]
        def embed_query(self, input=None, query=None): return [[0.0]*384]
    import core.memory.vector_store as VS
    VS.get_embedding_function = lambda: _FE()
    d = tempfile.mkdtemp()
    store = SimpleMemory(path=os.path.join(d, "c"))
    mngr = MemoryManager(store, None)
    rm = RoleManager.__new__(RoleManager)
    rm._roles = {"kasumi": RoleConfig(role_id="kasumi", display_name="t", default=True)}
    rm._prompts = {"kasumi": "你是户山香澄。"}
    rel = RelationMemory(path=os.path.join(d, "rel.json"))
    ag = LangGraphMemoryAgent(memory_manager=mngr, role_manager=rm, emotion_store=EmotionStore(store),
                              role_id="kasumi", llm_adapter=None, tool_adapter=None, perception=None, tool_fallback=False)
    ag.relation = rel
    # 无感知 -> 无择时提示
    assert ag._timing_hint("u1") == ""
    # 模拟有感知：注入一个带 attendance 的假 perception
    class FakeRoutine:
        def attendance(self, uid):
            return {"has_log": True, "days_since_contact": 5}
        def quiet_hours(self, uid): return set()
    class FakePerception:
        def __init__(self, ex): self.routine = ex
    ag.perception = FakePerception(FakeRoutine())
    hint = ag._timing_hint("u1")
    assert "想念" in hint or "关心" in hint or "聊" in hint

def test_get_relation_memory_singleton_per_role():
    a = get_relation_memory("kasumi")
    b = get_relation_memory("kasumi")
    c = get_relation_memory("kokoro")
    assert a is b and a is not c
