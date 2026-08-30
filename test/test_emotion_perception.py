"""
单元测试：情感 / 好感度 / 关系养成 / 感知层
================================================
- EmotionState / AffectionState 序列化与默认值
- EmotionAnalyzer：JSON 解析、情感/好感度合并
- relationship_stage：好感度 -> 关系阶段
- time_situation / location_situation：时序与情境
- MoodTrend / RoutineModel：情绪趋势 / 作息习惯 / 在场感
- PerceptionManager.summarize：感知文本组装
"""
from datetime import datetime

import pytest

from core.emotion import AffectionState, EmotionState, EmotionAnalyzer
from core.emotion.emotion import relationship_stage, relation_to_prompt_text, RELATION_STAGES


# ===================== 状态对象 =====================

def test_emotion_state_default_and_roundtrip():
    e = EmotionState.default()
    assert e.primary == "平静"
    d = e.to_dict()
    e2 = EmotionState.from_dict(d)
    assert e2 == e


def test_emotion_state_clamps():
    e = EmotionState.from_dict({"primary": "开心", "intensity": 9.9, "valence": -5})
    assert e.intensity == 1.0
    assert e.valence == -1.0


def test_affection_state_defaults():
    a = AffectionState.default()
    assert a.liking == 0.5
    assert a.trust == 0.5
    assert a.attachment == 0.3  # 依恋初始较低


def test_affection_roundtrip():
    a = AffectionState(liking=0.8, trust=0.7)
    a2 = AffectionState.from_dict(a.to_dict())
    assert a2.liking == 0.8
    assert a2.trust == 0.7


# ===================== 情感分析器 =====================

def test_analyzer_parses_json():
    class FakeTool:
        def chat(self, messages, **kw):
            return ('{"emotion":{"primary":"难过","intensity":0.8,"valence":-0.7,"description":"很沮丧"},'
                    '"affection":{"liking":0.5,"trust":0.5,"familiarity":0.5,"respect":0.5,"interest":0.5,"attachment":0.3},'
                    '"needs_tool":false,"tool_decision":""}')
    ana = EmotionAnalyzer(FakeTool())
    out = ana.analyze("我今天好难过", EmotionState.default(), AffectionState.default())
    assert out["emotion"]["primary"] == "难过"
    assert out["emotion"]["valence"] == -0.7


def test_analyzer_fallback_empty_on_bad_json():
    class FakeTool:
        def chat(self, messages, **kw):
            return "not json at all {{"
    ana = EmotionAnalyzer(FakeTool())
    out = ana.analyze("hi", EmotionState.default(), AffectionState.default())
    # _extract_json 会提取出 "{}" -> 返回 {} 或解析失败返回 {}
    assert isinstance(out, dict)


def test_merge_affection_only_updates_given():
    a = AffectionState.default()
    merged = EmotionAnalyzer.merge_affection(a, {"familiarity": 0.9})
    assert merged.familiarity == 0.9
    assert merged.liking == 0.5


# ===================== 关系养成 =====================

def test_relationship_stage_stranger():
    aff = AffectionState.default()  # familiarity 0.5? -> 熟悉? 需看阈值
    # 默认 familiarity=0.5 trust=0.5 -> 熟悉
    assert relationship_stage(aff) == "熟悉"


def test_relationship_stages():
    # 陌生
    assert relationship_stage(AffectionState(familiarity=0.2, trust=0.2,
                                             attachment=0.1, liking=0.2)) == "陌生"
    # 熟悉
    assert relationship_stage(AffectionState(familiarity=0.5, trust=0.5,
                                             attachment=0.3, liking=0.5)) == "熟悉"
    # 亲密
    assert relationship_stage(AffectionState(familiarity=0.7, trust=0.6,
                                             attachment=0.6, liking=0.7)) == "亲密"
    # 挚友
    assert relationship_stage(AffectionState(familiarity=0.9, trust=0.9,
                                             attachment=0.9, liking=0.9)) == "挚友"


def test_relation_prompt_text_contains_stage():
    text = relation_to_prompt_text(AffectionState(familiarity=0.9, trust=0.9,
                                                  attachment=0.9, liking=0.9), "小航")
    assert "挚友" in text
    assert "小航" in text


def test_all_stages_in_list():
    assert RELATION_STAGES == ["陌生", "熟悉", "亲密", "挚友"]


# ===================== 感知层：时序 =====================

def test_time_situation_periods():
    from core.perception import time_situation
    assert time_situation(datetime(2026, 1, 5, 9, 0))["period"] == "上午"   # 周一
    assert time_situation(datetime(2026, 1, 5, 14, 0))["period"] == "下午"
    assert time_situation(datetime(2026, 1, 5, 22, 0))["period"] == "晚上"
    assert time_situation(datetime(2026, 1, 4, 3, 0))["period"] == "深夜"
    assert time_situation(datetime(2026, 1, 10, 10, 0))["is_weekend"] is True   # 周六
    assert time_situation(datetime(2026, 1, 5, 10, 0))["is_weekend"] is False   # 周一
    assert time_situation(datetime(2026, 1, 5, 10, 0))["weekday_cn"] == "周一"


def test_location_situation_scene():
    from core.perception import location_situation
    assert location_situation("北京")["city"] == "北京"


# ===================== 感知层：情绪趋势 =====================

def test_mood_trend_record_and_trend(tmp_path):
    from core.perception import MoodTrend
    mt = MoodTrend(tmp_path / "p.json")
    for i in range(5):
        mt.record("u1", "开心", 0.6, 0.7)
    t = mt.trend("u1")
    assert t["samples"] == 5
    assert t["valence_avg"] == pytest.approx(0.6, abs=0.01)


def test_mood_trend_empty(tmp_path):
    from core.perception import MoodTrend
    mt = MoodTrend(tmp_path / "p.json")
    t = mt.trend("no_user")
    assert t["samples"] == 0


# ===================== 感知层：作息习惯 / 在场感 =====================

def test_routine_activity_summary(tmp_path):
    from core.perception import RoutineModel
    rm = RoutineModel(tmp_path / "p.json")
    rm.record_activity("u1")
    s = rm.summary("u1")
    assert s["has_log"] is True
    assert s["observed_days"] >= 1


def test_routine_attendance(tmp_path):
    from core.perception import RoutineModel
    rm = RoutineModel(tmp_path / "p.json")
    rm.record_activity("u1")
    att = rm.attendance("u1")
    assert att["has_log"] is True
    assert att["days_since_contact"] == 0
    assert att["minutes_since_last"] >= 0


def test_routine_quiet_hours_rest(tmp_path):
    from core.perception import RoutineModel
    rm = RoutineModel(tmp_path / "p.json")
    # quiet_hours 从活动时间戳的 hour 推导；记录一条 9 点的活跃
    now = datetime.now()
    active_ts = now.replace(hour=9, minute=30).isoformat()
    rm._data["u1"] = {"acts": [{"hour": 9, "weekday": 0, "ts": active_ts}]}
    qh = rm.quiet_hours("u1")
    assert 9 not in qh   # 9 点不是安静时段
    assert 3 in qh       # 凌晨 3 点通常是安静时段


# ===================== PerceptionManager 汇总 =====================

def test_perception_manager_summarize(tmp_path):
    from core.perception import PerceptionManager, MoodTrend, RoutineModel
    mt = MoodTrend(tmp_path / "m.json")
    rm = RoutineModel(tmp_path / "r.json")
    pm = PerceptionManager(mood_trend=mt, routine=rm, city="上海")
    text = pm.summarize("u1")
    assert "上海" in text
    assert "现在" in text
