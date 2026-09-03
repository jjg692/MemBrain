"""
单元测试：感知→表达触发对齐（core/sensing_hint）
================================================
覆盖：
  - 首次观测（基线）不误报"变化"
  - 相同状态不触发
  - 冷却期内不触发（低频率）
  - 内容变化且过冷却期后触发提示
  - 开关关闭（总开关/触发开关）时完全静默
  - 读不到任何状态时返回空（不伪造）
"""
import time

import core.config as C
import core.sensing_hint as H


def _setup(monkeypatch):
    """打开开关，并打桩前台窗口/标签页读取为可控确定值。"""
    monkeypatch.setattr(H, "ENVIRONMENT_SENSING_ENABLED", True)
    monkeypatch.setattr(H, "SENSING_TRIGGER_ENABLED", True)
    H._last.clear()
    state = {"fg": "msedge：测试页面", "tab": ""}

    def fake_state():
        return {"foreground": state["fg"], "tab": state["tab"]}

    monkeypatch.setattr(H, "_current_state", fake_state)
    return state


def test_first_observation_is_baseline_not_a_change(monkeypatch):
    """首次观测只登记基线，不误判为"用户切换了"。"""
    _setup(monkeypatch)
    assert H.sensing_change_hint("u1") == ""


def test_same_state_no_trigger(monkeypatch):
    """前台/标签页未变，不触发。"""
    st = _setup(monkeypatch)
    H.sensing_change_hint("u1")  # 首帧基线
    assert H.sensing_change_hint("u1") == ""


def test_change_within_cooldown_not_trigger(monkeypatch):
    """冷却期内变化不触发（低频率）。"""
    st = _setup(monkeypatch)
    H.sensing_change_hint("u1")  # 基线
    st["tab"] = "用户当前浏览器正打开：\n- 标题：B\n- 链接：y"
    assert H.sensing_change_hint("u1") == ""  # 冷却期内


def test_change_after_cooldown_trigger(monkeypatch):
    """变化且过冷却期后触发提示（不含冷却期之外的 tab 变更）。"""
    st = _setup(monkeypatch)
    H.sensing_change_hint("u1")  # 基线
    st["tab"] = "用户当前浏览器正打开：\n- 标题：B\n- 链接：y"
    # 把上次记录时间回拨到超过 60s 冷却
    H._last["u1"]["ts"] = time.time() - 120
    hint = H.sensing_change_hint("u1")
    assert "标题：B" in hint or "切换" in hint


def test_trigger_then_same_state_again_empty(monkeypatch):
    """触发一次后，若状态稳定则不再触发。"""
    st = _setup(monkeypatch)
    H.sensing_change_hint("u1")  # 基线
    st["tab"] = "A"
    H._last["u1"]["ts"] = time.time() - 120
    assert H.sensing_change_hint("u1") != ""
    assert H.sensing_change_hint("u1") == ""  # 已记录新状态，不再触发


def test_disabled_when_master_off(monkeypatch):
    """总开关关闭时完全静默。"""
    _setup(monkeypatch)
    monkeypatch.setattr(H, "ENVIRONMENT_SENSING_ENABLED", False)
    assert H.sensing_change_hint("u1") == ""


def test_disabled_when_trigger_off(monkeypatch):
    """触发开关关闭时完全静默。"""
    _setup(monkeypatch)
    monkeypatch.setattr(H, "SENSING_TRIGGER_ENABLED", False)
    assert H.sensing_change_hint("u1") == ""


def test_no_state_returns_empty_not_fake(monkeypatch):
    """读不到前台/标签页时返回空，不伪造。"""
    _setup(monkeypatch)
    monkeypatch.setattr(H, "_current_state", lambda: {"foreground": "", "tab": ""})
    assert H.sensing_change_hint("u1") == ""
