"""
单元测试：日程/提醒引擎 (ReminderStore / ReminderScheduler)
================================================
- CRUD：新增 / 列出 / 获取 / 删除 / 启停
- due：到点触发判断
- mark_fired：一次性 done、重复推进下次触发
- 重复计算：hourly / daily / weekly
"""
from datetime import datetime, timedelta

from core.reminder import ReminderStore, ReminderScheduler


def _store(tmp_path):
    return ReminderStore(tmp_path / "rem.json")


# ===================== CRUD =====================

def test_add_requires_text_and_time(tmp_path):
    s = _store(tmp_path)
    assert s.add("u1", "", "2026-12-31 10:00") is None       # 空文本
    assert s.add("u1", "内容", "") is None                    # 无时间无重复
    assert s.add("u1", "内容", "2026-12-31 10:00") is not None


def test_add_list_get_delete(tmp_path):
    s = _store(tmp_path)
    r = s.add("u1", "开会", "2026-12-31 14:00")
    assert s.get("u1", r["id"]) is not None
    lst = s.list("u1")
    assert len(lst) == 1
    assert lst[0]["text"] == "开会"
    assert s.delete("u1", r["id"]) is True
    assert s.list("u1") == []


def test_set_enabled(tmp_path):
    s = _store(tmp_path)
    r = s.add("u1", "喝水", "2026-12-31 09:00")
    assert s.set_enabled("u1", r["id"], False) is True
    assert s.get("u1", r["id"])["enabled"] is False
    # 停用后 not due（due 只挑 enabled）
    assert s.due("u1", datetime(2026, 12, 31, 10, 0)) == []
    # 重新启用
    assert s.set_enabled("u1", r["id"], True) is True
    assert len(s.due("u1", datetime(2026, 12, 31, 10, 0))) == 1


# ===================== due / mark_fired =====================

def test_due_only_when_past_and_enabled(tmp_path):
    s = _store(tmp_path)
    past = s.add("u1", "过去提醒", (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"))
    future = s.add("u1", "未来提醒", (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"))
    due = s.due("u1", datetime.now())
    due_ids = [r["id"] for r in due]
    assert past["id"] in due_ids
    assert future["id"] not in due_ids


def test_mark_fired_once_done(tmp_path):
    s = _store(tmp_path)
    r = s.add("u1", "一次性", "2026-12-31 10:00")
    s.mark_fired("u1", r["id"], datetime(2026, 12, 31, 10, 1))
    updated = s.get("u1", r["id"])
    assert updated["done"] is True
    assert updated["enabled"] is False
    assert s.due("u1", datetime(2026, 12, 31, 11, 0)) == []


def test_mark_fired_daily_advances(tmp_path):
    """每日提醒触发后推进到第二天同一时刻。"""
    s = _store(tmp_path)
    r = s.add("u1", "每日喝水", "2026-12-31 09:00", repeat="daily")
    now = datetime(2026, 12, 31, 9, 0)
    s.mark_fired("u1", r["id"], now)
    updated = s.get("u1", r["id"])
    assert updated["done"] is False
    assert updated["trigger_at"] == datetime(2027, 1, 1, 9, 0).isoformat()


def test_next_trigger_daily_weekly_hourly(tmp_path):
    s = _store(tmp_path)
    now = datetime(2026, 1, 5, 10, 0)   # 周一
    # daily
    daily = {"trigger_at": "2026-01-05T09:00:00", "repeat": "daily"}
    assert s._next_trigger(daily, now) == datetime(2026, 1, 6, 9, 0)
    # weekly 指定周一(0)
    weekly = {"trigger_at": "2026-01-05T09:00:00", "repeat": "weekly", "weekdays": [0]}
    # 下一个周一
    assert s._next_trigger(weekly, now).weekday() == 0
    # hourly
    hourly = {"trigger_at": "2026-01-05T10:00:00", "repeat": "hourly"}
    assert s._next_trigger(hourly, now) == datetime(2026, 1, 5, 11, 0)


# ===================== 调度器 =====================

def test_scheduler_compose_uses_agent(monkeypatch, tmp_path):
    """调度器到点会调用 agent.proactive_message 生成提醒，失败回退原文。"""
    s = _store(tmp_path)
    r = s.add("u1", "原始提醒文本", (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M"))

    class FakeAgent:
        def __init__(self):
            self.calls = []
        def proactive_message(self, user_id, trigger="", context=""):
            self.calls.append((user_id, context))
            return "角色口吻：该喝水啦"

    class FakeFactory:
        def __init__(self):
            self.agent = FakeAgent()
        def get_agent(self, user_id, role_id):
            return self.agent

    factory = FakeFactory()
    scheduler = ReminderScheduler(s, factory)
    # 直接 tick（不启动线程），用 online 用户
    monkeypatch.setattr(scheduler, "_online_users", lambda: ["u1"])

    pushed = []
    scheduler.push_callback = lambda uid, data: pushed.append((uid, data))
    scheduler.tick()

    assert factory.agent.calls            # 调用了 agent
    assert "原始提醒文本" in factory.agent.calls[0][1]   # context 携带提醒内容
    assert pushed                          # 有推送
    assert pushed[0][0] == "u1"
    assert pushed[0][1]["type"] == "reminder"


def test_scheduler_compose_fallback_on_failure(tmp_path):
    """agent 生成失败时回退提醒原文。"""
    s = _store(tmp_path)
    r = s.add("u1", "原始文本", (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M"))

    class BoomAgent:
        def proactive_message(self, *a, **k):
            raise RuntimeError("agent down")

    class Factory:
        def get_agent(self, *a, **k):
            return BoomAgent()

    scheduler = ReminderScheduler(s, Factory())
    scheduler._online_users = lambda: ["u1"]
    text = scheduler._compose("u1", r, "原始文本")
    assert "原始文本" in text
