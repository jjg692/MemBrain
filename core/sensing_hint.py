"""
感知→表达触发对齐（harness 级）
================================
当"用户正在看什么/在用哪个前台窗口"发生变化时，产生一句**低频、克制的触发提示**，
供表达层（回复/主动开口）自然地接上一句，落地"主动择时/感知共鸣"。

设计原则（与感知层一致）：
- 纯本地、无额外 LLM 调用：只需读当前前台窗口 / 浏览器标签页，与上次对比语义是否变化；
- 低频率：带冷却时间（默认 60s），避免每轮都打扰，也不会在用户安静时反复触发；
- 不伪造：读不到前台/标签页时返回空，绝不自造"用户切到了 X"；
- 有开关：受 ENVIRONMENT_SENSING_ENABLED + SENSING_TRIGGER_ENABLED 双重控制，
  关闭时完全静默，不影响任何既有行为。

用法：
    from core.sensing_hint import sensing_change_hint
    hint = sensing_change_hint(user_id, cooldown_sec=60)   # 返回提示文本或 ""
    若有 hint，把它追加进回复/主动的 prompt 提示位即可。
"""

import threading
import time

from core.config import (
    ENVIRONMENT_SENSING_ENABLED,
    SENSING_TRIGGER_ENABLED,
)

#: 仅内存记录（keyed by user_id），进程重启即失忆——本就只是"本次会话内的变化感"
_last = {}
_lock = threading.RLock()


def _current_state() -> dict:
    """尽力读取当前前台窗口+标签页（都不许失败时伪造，读不到即空）。"""
    state = {"foreground": "", "tab": ""}
    try:
        from core.perception import foreground_window
        state["foreground"] = foreground_window() or ""
    except Exception:
        pass
    try:
        from core.sensing import get_current_tab
        text = get_current_tab()
        # 过滤"未开启/读取失败/读取为空"这类标记，只认真实内容
        if text and not text.startswith(("（浏览器感知未开启", "（读取失败", "（读取为空")):
            state["tab"] = text
    except Exception:
        pass
    return state


def _clean(text: str) -> str:
    """归一化用于比较：去空白/换行，避免纯排版差异误判为"变化"。"""
    return " ".join(str(text or "").split())


def sensing_change_hint(user_id: str = "default_user", cooldown_sec: int = 60) -> str:
    """检测前台窗口/当前标签页相对上次是否发生变化；变化显著且过冷却期则返回提示文本。

    返回形如：
      "用户刚把浏览器切换到了：..." 或 "用户刚切换到了前台窗口：..."
    无变化 / 读不到 / 未开启 / 冷却期内 / 首次观测(基线) -> 返回空串（不打扰）。
    """
    if not ENVIRONMENT_SENSING_ENABLED or not SENSING_TRIGGER_ENABLED:
        return ""
    uid = user_id or "default_user"
    try:
        now = time.time()
        cur = _current_state()
        if not cur["foreground"] and not cur["tab"]:
            return ""
        with _lock:
            prev = _last.get(uid)
            # 首次观测（无历史基线）：登记但不打扰，避免把首帧当"变化"
            if not prev:
                _last[uid] = {"ts": now, "foreground": cur["foreground"], "tab": cur["tab"]}
                return ""
            # 冷却期：距上次记录太快不触发，避免刷屏
            if now - prev.get("ts", 0.0) < cooldown_sec:
                return ""
            # 是否有**有效的变化**（前台或标签页至少一个内容发生变化）
            fg_changed = bool(cur["foreground"]) and _clean(cur["foreground"]) != _clean(prev.get("foreground", ""))
            tab_changed = bool(cur["tab"]) and _clean(cur["tab"]) != _clean(prev.get("tab", ""))
            if not (fg_changed or tab_changed):
                # 内容未变：只刷新时间线，不打扰
                _last[uid] = {"ts": now, "foreground": cur["foreground"], "tab": cur["tab"]}
                return ""
            # 有变化：更新记录并返回提示
            _last[uid] = {"ts": now, "foreground": cur["foreground"], "tab": cur["tab"]}
            if tab_changed:
                return "用户刚把浏览器切换到了（可以自然接一句）：" + cur["tab"]
            return "用户刚切换到了前台窗口（可以自然接一句）：" + cur["foreground"]
    except Exception:
        return ""
