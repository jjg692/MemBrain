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


# ===================== ProactiveHeartbeat 心跳线程 =====================


class FakeWSE:
    def __init__(self, users):
        self._users = users or []
    def user_ids(self):
        return list(self._users)
    def get_all(self):
        return list(self._users)


class FakeAgent:
    def __init__(self, role_id="kasumi", should=True):
        self.role_id = role_id
        self._should = should
        self._sent = []
    def proactive_message(self, uid, trigger="", context=""):
        self._sent.append({"uid": uid, "trigger": trigger, "context": context})
        return f"主动消息({trigger})" if self._should else ""
    def _get_relation(self):
        return None


class FakeAF:
    def __init__(self, agent=None, role="kasumi"):
        self.agent = agent or FakeAgent()
        self._role = role
        self.initializer = type("I", (), {"role_manager": type("R", (), {
            "get_default_role": lambda self: role})()})()
    def get_agent(self, uid, role):
        return self.agent


def test_heartbeat_disabled_no_act(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", False)
    hb = P.ProactiveHeartbeat(agent_factory=FakeAF(), push_callback=lambda u, d: sent.append(d))
    sent = []
    assert hb.heartbeat_once() == 0


def test_heartbeat_offline_users_no_act(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    # 覆盖"无在线用户"场景
    class _WS:
        def user_ids(self): return []
    monkeypatch.setattr("api.websocket_manager.single_ws_manager", _WS())
    hb = P.ProactiveHeartbeat(agent_factory=FakeAF(), push_callback=lambda u, d: None)
    assert hb.heartbeat_once() == 0


def test_heartbeat_pushes_when_should_act(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    # 让 decide 返回 should_act=True：给 relation 承诺信号
    class _WS:
        def user_ids(self): return ["u1"]
    monkeypatch.setattr("api.websocket_manager.single_ws_manager", _WS())
    pushed = []
    ag = FakeAgent()
    # 注入一个"有承诺"的 relation，使 decide 应该开口
    class Rel:
        def pending_promises(self, uid, n=6): return [{"text": "陪你吃饭", "status": "pending"}]
        def decayed_episodes(self, uid, n=5, min_vitality=0.3): return []
        def _mood_trend_text(self, uid): return ""
    # 替换 decide 直接给 should_act（避免依赖真实感知/变化）
    orig_decide = P.ProactiveDecider.decide
    def fake_decide(self, uid):
        return {"should_act": True, "trigger": "promise", "context": "你曾答应：陪你吃饭", "type": "promise"}
    monkeypatch.setattr(P.ProactiveDecider, "decide", fake_decide)
    try:
        hb = P.ProactiveHeartbeat(agent_factory=FakeAF(ag), push_callback=lambda u, d: pushed.append(d))
        n = hb.heartbeat_once()
    finally:
        monkeypatch.setattr(P.ProactiveDecider, "decide", orig_decide)
    assert n == 1
    assert len(ag._sent) == 1
    assert ag._sent[0]["uid"] == "u1"
    assert pushed and pushed[0]["type"] == "proactive"
    assert pushed[0]["content"] == "主动消息(promise)"
    assert pushed[0]["via"] == "heartbeat"


def test_heartbeat_no_text_no_push(monkeypatch):
    monkeypatch.setattr(P, "PROACTIVITY_ENABLED", True)
    class _WS:
        def user_ids(self): return ["u1"]
    monkeypatch.setattr("api.websocket_manager.single_ws_manager", _WS())
    pushed = []
    ag = FakeAgent(should=False)  # proactive_message 返回空
    monkeypatch.setattr(P.ProactiveDecider, "decide",
                        lambda self, uid: {"should_act": True, "trigger": "t", "context": "c", "type": "promise"})
    hb = P.ProactiveHeartbeat(agent_factory=FakeAF(ag), push_callback=lambda u, d: pushed.append(d))
    assert hb.heartbeat_once() == 0
    assert pushed == []
