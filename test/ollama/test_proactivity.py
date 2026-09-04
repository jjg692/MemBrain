"""
单元测试：主动性心跳 ProactiveDecider（core/proactivity.py）
==============================================================
覆盖决策规则与克制节流：
  - 关闭开关 / 无信号 -> 不主动
  - 各信号（承诺>经历>情绪>感知变化>断联）识别与素材
  - 承诺优先级最高
  - 最小触发间隔、每日次数上限的节流
  - relation/perception 为 None 时健壮
"""
import core.proactivity as P


class FakeRel:
    def __init__(self, promises=None, eps=None, mood=""):
        self._p = promises or []
        self._e = eps or []
        self._m = mood
    def pending_promises(self, uid, n=6):
        return list(self._p)
    def decayed_episodes(self, uid, n=5, min_vitality=0.3):
        return list(self._e)
    def _mood_trend_text(self, uid):
        return self._m


class _Routine:
    def __init__(self, days=0):
        self._days = days
    def attendance(self, uid):
        return {"has_log": True, "days_since_contact": self._days}


class FakePer:
    def __init__(self, days=0):
        self.routine = _Routine(days)


def _no_change(monkeypatch):
    monkeypatch.setattr(P.ProactiveDecider, "_signal_sensing_change",
                        lambda self, uid: "")


def test_disabled_no_act(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", False)
    d = P.ProactiveDecider(relation=FakeRel(promises=[{"text": "x", "status": "pending"}]))
    assert d.decide("u")["should_act"] is False


def test_no_signal_no_act(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    _no_change(monkeypatch)
    d = P.ProactiveDecider(relation=FakeRel(), perception=FakePer(0))
    assert d.decide("u")["should_act"] is False


def test_promise_highest_priority(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    _no_change(monkeypatch)
    rel = FakeRel(promises=[{"text": "陪你打游戏", "status": "pending"}],
                  eps=[{"user_msg": "想学吉他"}])
    d = P.ProactiveDecider(relation=rel, perception=FakePer(0))
    r = d.decide("u")
    assert r["should_act"] is True
    assert r["type"] == "promise"
    assert "打游戏" in r["context"]


def test_mood_decline_signal(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    _no_change(monkeypatch)
    rel = FakeRel(mood="你最近心情整体变差")
    d = P.ProactiveDecider(relation=rel, perception=FakePer(0))
    r = d.decide("u")
    assert r["should_act"] is True and r["type"] == "mood"


def test_reconnect_signal(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    _no_change(monkeypatch)
    d = P.ProactiveDecider(relation=FakeRel(), perception=FakePer(days=5))
    r = d.decide("u")
    assert r["should_act"] is True and r["type"] == "reconnect"


def test_sensing_change_signal(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    d = P.ProactiveDecider(relation=FakeRel(), perception=FakePer(0))
    monkeypatch.setattr(P.ProactiveDecider, "_signal_sensing_change",
                        lambda self, uid: "用户刚切换了浏览器：B站")
    r = d.decide("u")
    assert r["should_act"] is True and r["type"] == "sensing_change"


def test_min_interval_blocks_followup(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "PROACTIVITY_MIN_INTERVAL_MIN", 60)
    monkeypatch.setattr(P, "PROACTIVITY_DAILY_CAP", 0)  # 不限次数
    _no_change(monkeypatch)
    rel = FakeRel(promises=[{"text": "陪你", "status": "pending"}])
    d = P.ProactiveDecider(relation=rel, perception=FakePer(0))
    assert d.decide("u")["should_act"] is True
    assert d.decide("u")["should_act"] is False  # 间隔未到


def test_daily_cap_blocks(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    monkeypatch.setattr(P, "PROACTIVITY_MIN_INTERVAL_MIN", 0)
    monkeypatch.setattr(P, "PROACTIVITY_DAILY_CAP", 2)
    _no_change(monkeypatch)
    rel = FakeRel(promises=[{"text": "陪你", "status": "pending"}])
    d = P.ProactiveDecider(relation=rel, perception=FakePer(0))
    assert d.decide("u")["should_act"] is True
    assert d.decide("u")["should_act"] is True
    assert d.decide("u")["should_act"] is False  # 达到上限


def test_none_relation_perception_ok(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    _no_change(monkeypatch)
    d = P.ProactiveDecider(relation=None, perception=None)
    assert d.decide("u")["should_act"] is False
