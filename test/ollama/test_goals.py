"""
长期目标（前瞻记忆）测试
=======================
覆盖：
  A. RelationMemory.goals 账本：增/查/改/删 + 去重 + 持久化
  B. 鲜活度（vitality）随半衰期自然淡忘、live_goals 过滤
  C. ProactiveDecider 的 _signal_goal：有鲜活目标才开口、无目标/未开/节流不开
  D. 零回归：无目标时目标信号为空，不挤占既有信号优先级
本文件自包含（临时 JSON，不依赖 ChromaDB / Ollama；用 FakeRel / Fake LLM）。
"""
import core.proactivity as P
from core.relation_memory import RelationMemory, half_life_decay


# ===================== A. 目标账本 CRUD + 持久化 =====================

def test_add_goal_and_reload(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), halflife_days=21)
    g = r.add_goal("u1", "学会 N2 日语", progress="背到第 8 课")
    assert g["id"] and g["status"] == "active"
    r2 = RelationMemory(path=str(tmp_path / "rel.json"), halflife_days=21)
    assert len(r2.goals("u1")) == 1
    assert r2.goals("u1")[0]["title"] == "学会 N2 日语"


def test_add_goal_dedupe_same_title(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    r.add_goal("u1", "减重到 60kg", progress="第一周")
    g2 = r.add_goal("u1", "减重到 60kg", progress="第二周")
    assert len(r.goals("u1")) == 1
    assert r.goals("u1")[0]["progress"] == "第二周"  # 更新而非重复
    # 宽松判同也应视为同一条（含空格的标题）
    r.add_goal("u2", "减重到 60 kg")
    assert len(r.goals("u2")) == 1


def test_update_and_delete_goal(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    g = r.add_goal("u1", "跑全马")
    assert r.update_goal("u1", g["id"], progress="10km", status="ongoing") is True
    gs = r.goals("u1")
    assert gs[0]["progress"] == "10km" and gs[0]["status"] == "ongoing"
    assert r.delete_goal("u1", g["id"]) is True
    assert r.goals("u1") == []


def test_goal_completed_not_in_live(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"))
    g = r.add_goal("u1", "学会吉他")
    r.update_goal("u1", g["id"], status="done")
    assert r.live_goals("u1") == []          # 终态不参与主动
    assert len(r.goals("u1")) == 1           # 记录保留（不删）


# ===================== B. 鲜活度衰减 =====================

def test_vitality_decays_over_time(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), halflife_days=21)
    r.add_goal("u1", "学绘画")
    # 回填 last_engaged 为 42 天前 -> 鲜活度约 0.25
    goal = r._user("u1")["goals"][0]
    r._data["u1"]["goals"][0]["last_engaged"] = _iso_days_ago(42)
    v = r._goal_vitality(r._user("u1")["goals"][0])
    assert abs(v - 0.25) < 1e-6
    # 低于默认 live 阈值(0.15)—— 0.25 仍在，但 63 天前(0.125)则淡出
    r._data["u1"]["goals"][0]["last_engaged"] = _iso_days_ago(63)
    assert r.live_goals("u1", min_vitality=0.15) == []


def test_live_goals_sorted_by_vitality_and_capped(tmp_path):
    r = RelationMemory(path=str(tmp_path / "rel.json"), halflife_days=21)
    for title in ("目标A", "目标B", "目标C", "目标D"):
        r.add_goal("u1", title)
    # 最近加的 last_engaged 最新 -> 鲜活度 1.0；最多返回 n 条
    gs = r.live_goals("u1", n=2, min_vitality=0.0)
    assert len(gs) == 2
    assert all(gs[i]["vitality"] >= gs[i+1]["vitality"] for i in range(len(gs)-1))


def _iso_days_ago(days):
    import datetime
    return (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()


# ===================== C. ProactiveDecider 目标信号 =====================

class FakeGoalRel:
    """同时暴露 promises 与 live_goals 的假关系内核（用于目标信号测试）。"""
    def __init__(self, goals=None, promises=None, mood=""):
        self._g = goals or []
        self._p = promises or []
        self._m = mood
    def live_goals(self, uid, n=3, min_vitality=0.15):
        return list(self._g)[:n]
    def pending_promises(self, uid, n=6):
        return list(self._p)
    def decayed_episodes(self, uid, n=5, min_vitality=0.3):
        return []
    def _mood_trend_text(self, uid):
        return self._m


def test_goal_signal_when_goal_exists(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_ACTIVE_MIN_INTERVAL_SEC", 0)   # 不限间隔
    rel = FakeGoalRel(goals=[{"title": "学会 N2", "progress": "背到第 8 课", "vitality": 0.9}])
    d = P.ProactiveDecider(relation=rel, perception=None)
    r = d.decide("u1")
    assert r["should_act"] is True and r["type"] == "goal"
    assert "学会 N2" in r["context"]


def test_goal_signal_no_goal_returns_false(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    d = P.ProactiveDecider(relation=FakeGoalRel(goals=[]), perception=None)
    assert d.decide("u1")["should_act"] is False


def test_goal_disabled_no_signal(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", False)
    rel = FakeGoalRel(goals=[{"title": "目标", "progress": "", "vitality": 0.9}])
    d = P.ProactiveDecider(relation=rel, perception=None)
    assert d._signal_goal("u1") is None
    assert d.decide("u1")["should_act"] is False


def test_goal_throttle_blocks_repeat(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_ACTIVE_MIN_INTERVAL_SEC", 999999)  # 很长间隔
    rel = FakeGoalRel(goals=[{"title": "目标A", "progress": "", "vitality": 0.9}])
    d = P.ProactiveDecider(relation=rel, perception=None)
    assert d._signal_goal("u1") is not None          # 首触可以
    assert d._signal_goal("u1") is None              # 节流拦截


# ===================== D. 零回归：无目标时不影响既有信号 =====================

def test_promise_still_wins_when_no_goal(monkeypatch):
    """无目标时，承诺仍是最高优先级素材，目标信号不挤占。"""
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P.ProactiveDecider, "_signal_sensing_change", lambda self, uid: "")
    rel = FakeGoalRel(goals=[], promises=[{"text": "陪你吃饭", "status": "pending"}])
    d = P.ProactiveDecider(relation=rel, perception=None)
    r = d.decide("u1")
    assert r["should_act"] is True and r["type"] == "promise"


def test_goal_preferred_over_promise_when_both(monkeypatch):
    """既有目标又有承诺时，目标（更主动、更有信息量）优先。"""
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_ACTIVE_MIN_INTERVAL_SEC", 0)
    monkeypatch.setattr(P.ProactiveDecider, "_signal_sensing_change", lambda self, uid: "")
    rel = FakeGoalRel(
        goals=[{"title": "学日语", "progress": "", "vitality": 0.9}],
        promises=[{"text": "陪你吃饭", "status": "pending"}],
    )
    d = P.ProactiveDecider(relation=rel, perception=None)
    assert d.decide("u1")["type"] == "goal"


# ===================== D2. 修2/修3：note 注入 + 择时/情绪校准 =====================

def test_goal_context_includes_note(monkeypatch):
    """修2：进度 + 提炼给的下一步建议(note)都应出现在主动 context，而不是只有标题。"""
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_ACTIVE_MIN_INTERVAL_SEC", 0)
    rel = FakeGoalRel(goals=[{
        "title": "学会 N2", "progress": "背到第 8 课",
        "note": "每天背 20 个词", "vitality": 0.9,
    }])
    d = P.ProactiveDecider(relation=rel, perception=None)
    ctx = d._signal_goal("u1")["context"]
    assert "学会 N2" in ctx and "背到第 8 课" in ctx and "每天背 20 个词" in ctx


def test_goal_deferred_when_mood_low(monkeypatch):
    """修3：用户情绪低落时，目标信号让位（返回 None），避免扫兴。"""
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_ACTIVE_MIN_INTERVAL_SEC", 0)
    rel = FakeGoalRel(goals=[{"title": "学日语", "progress": "", "vitality": 0.9}],
                      mood="最近几次和你聊天的整体氛围在变差")
    d = P.ProactiveDecider(relation=rel, perception=None)
    assert d._signal_goal("u1") is None


def test_mood_signal_wins_over_goal_when_low(monkeypatch):
    """修3：情绪低落时有目标也有情绪信号，情绪关怀（而非目标）被选。"""
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_ACTIVE_MIN_INTERVAL_SEC", 0)
    rel = FakeGoalRel(goals=[{"title": "学日语", "progress": "", "vitality": 0.9}],
                      mood="最近几次和你聊天的整体氛围在变差（情绪效价约 -0.40）")
    d = P.ProactiveDecider(relation=rel, perception=None)
    r = d.decide("u1")
    assert r["type"] == "mood"      # 不再是 goal
    assert r["should_act"] is True


def test_goal_not_deferred_by_normal_timing(monkeypatch):
    """修3：正常时段（非深夜/非低落）目标信号正常返回。"""
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_MEMORY_ENABLED", True)
    monkeypatch.setattr(P, "GOAL_ACTIVE_MIN_INTERVAL_SEC", 0)
    # 用真实 RelationMemory + 无感知（无深夜信息）→ 不误拦
    import tempfile, os
    rel = RelationMemory(path=os.path.join(tempfile.gettempdir(), "g_timing.json"),
                         halflife_days=21)
    rel.add_goal("u1", "学钢琴", progress="")
    d = P.ProactiveDecider(relation=rel, perception=None)
    assert d._signal_goal("u1") is not None


# ===================== E. 目标提炼器（对话自动发现） =====================

class _LLM:
    """可控假 LLM：返回预设 JSON 文本。"""
    def __init__(self, text=""):
        self._text = text
        self.calls = 0
    def chat(self, messages, **kw):
        self.calls += 1
        return self._text


def _make_extractor(tmp_path, role="kasumi"):
    """返回 (extractor_module, 临时 RelationMemory)；monkeypatch get_relation_memory。"""
    import core.goal_extractor as GE
    rel = RelationMemory(path=str(tmp_path / f"{role}.json"), halflife_days=21)
    GE.get_relation_memory = lambda r: rel  # 注入临时实例
    return GE, rel


def test_extractor_creates_new_goal(tmp_path, monkeypatch):
    GE, rel = _make_extractor(tmp_path)
    llm = _LLM('{"involve_goal":true,"action":"create","title":"学会 N2 日语","progress":"背到第 8 课","next":"每天背 20 词","status":"active"}')
    GE.extract_and_update("u1", "kasumi", "我打算今年把 N2 考下来", llm, force=True)
    goals = rel.goals("u1")
    assert len(goals) == 1 and goals[0]["title"] == "学会 N2 日语"
    assert goals[0]["progress"] == "背到第 8 课"


def test_extractor_updates_existing_goal(tmp_path, monkeypatch):
    GE, rel = _make_extractor(tmp_path)
    rel.add_goal("u1", "学会 N2 日语", progress="背到第 5 课")
    llm = _LLM('{"involve_goal":true,"action":"update","title":"学会 N2 日语","progress":"背到第 10 课","status":"active"}')
    GE.extract_and_update("u1", "kasumi", "我今天背到第 10 课啦", llm, force=True)
    goals = rel.goals("u1")
    assert len(goals) == 1
    assert goals[0]["progress"] == "背到第 10 课"
    assert goals[0]["status"] == "active"


def test_extractor_complete_and_abandon(tmp_path, monkeypatch):
    GE, rel = _make_extractor(tmp_path)
    g1 = rel.add_goal("u1", "学会吉他")
    g2 = rel.add_goal("u1", "跑全马")
    # 完成吉他
    llm = _LLM('{"involve_goal":true,"action":"complete","title":"学会吉他","status":"done"}')
    GE.extract_and_update("u1", "kasumi", "我吉他终于学会了！", llm, force=True)
    assert rel.live_goals("u1")[0]["title"] == "跑全马"   # 完成的吉他不再进 live
    # 放弃跑全马（模糊匹配"跑全马"）
    llm2 = _LLM('{"involve_goal":true,"action":"abandon","title":"跑全马","status":"abandoned"}')
    GE.extract_and_update("u1", "kasumi", "跑全马我不练了，太累", llm2, force=True)
    assert rel.live_goals("u1") == []                     # 两个都退出灯
    gone = [g for g in rel.goals("u1") if g["title"] == "跑全马"]
    assert gone and gone[0]["status"] == "abandoned"


def test_extractor_ignores_chitchat(tmp_path, monkeypatch):
    GE, rel = _make_extractor(tmp_path)
    # 无关键词闸门拦截的说法：即使 force=False，闲聊也应无目标
    llm = _LLM('{"involve_goal":false,"action":"none"}')
    GE.extract_and_update("u1", "kasumi", "今天天气不错，出去走走", llm, force=True)
    assert rel.goals("u1") == []
    assert llm.calls == 1


def test_extractor_keyword_gate_skips_llm(tmp_path, monkeypatch):
    """纯闲聊不命中关键词闸门 -> 不调用 LLM。"""
    GE, rel = _make_extractor(tmp_path)
    llm = _LLM()
    GE.extract_and_update("u1", "kasumi", "嗯嗯，好的，我知道了", llm, force=False)
    assert llm.calls == 0
    assert rel.goals("u1") == []


def test_extractor_failure_is_silent(tmp_path, monkeypatch):
    """LLM 返回非法 JSON / 抛异常 -> 静默，不崩、不写。"""
    GE, rel = _make_extractor(tmp_path)
    class _BadLLM:
        def chat(self, messages, **kw):
            raise RuntimeError("boom")
    GE.extract_and_update("u1", "kasumi", "我打算学好钢琴", _BadLLM(), force=True)
    assert rel.goals("u1") == []   # 失败静默，无目标写入
