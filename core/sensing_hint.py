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
    SENSING_FRAME_WINDOW,
)

#: 仅内存记录（keyed by user_id），进程重启即失忆——本就只是"本次会话内的变化感"
_last = {}
#: 滚动帧窗口（keyed by user_id，list）：最近若干帧环境快照 {ts, foreground, tab}，
#: 用于说出"你刚才从 X 切到了 Y"这类跨帧趋势。仅内存，重启即清空。
_frames: dict = {}
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


def _record_frame(uid: str, state: dict, now: float) -> None:
    """把一帧环境快照推入滚动窗口（配合 SENSING_FRAME_WINDOW 裁剪）。"""
    if not SENSING_FRAME_WINDOW:
        return
    frames = _frames.setdefault(uid, [])
    frames.append({
        "ts": now,
        "foreground": state.get("foreground", "") or "",
        "tab": state.get("tab", "") or "",
    })
    if len(frames) > SENSING_FRAME_WINDOW:
        del frames[: len(frames) - SENSING_FRAME_WINDOW]


def sensing_change_hint(user_id: str = "default_user", cooldown_sec: int = 60) -> str:
    """检测前台窗口/当前标签页相对上次是否发生变化；变化显著且过冷却期则返回提示文本。

    返回形如：
      "用户刚把浏览器切换到了：..." 或 "用户刚切换到了前台窗口：..."
    无变化 / 读不到 / 未开启 / 冷却期内 / 首次观测(基线) -> 返回空串（不打扰）。
    每次被调用都会把当前帧推进滚动窗口（供 frame_trend 使用）。
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
            _record_frame(uid, cur, now)
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


def frame_trend(user_id: str = "default_user") -> str:
    """滚动帧趋势感知：根据最近 N 帧环境快照，用一句话概括"用户刚才的注意力走向/专注点"。

    仅在窗口内 **内容确实有变化** 时才返回（不伪造）；窗口不足 / 无变化 / 未开启 / 读不到
    一律返回空串。返回值举例：
      - "你刚才从「写代码」切到了「浏览器」，最近一直专注在「X」"
      - "你最近一直专注在「X」"
    注意：这里只做**确定性归纳**（比较前台/标签页的变化与重复），不调 LLM。
    """
    if not ENVIRONMENT_SENSING_ENABLED or not SENSING_TRIGGER_ENABLED or not SENSING_FRAME_WINDOW:
        return ""
    uid = user_id or "default_user"
    try:
        frames = []
        with _lock:
            frames = [dict(f) for f in _frames.get(uid, [])]
        # 去掉空帧（前台/标签页都空的不算有效内容）
        frames = [f for f in frames if f.get("foreground") or f.get("tab")]
        if len(frames) < 2:
            return ""
        # 每帧取"前台窗口优先、无则标签页"作为关注点
        def _focus(f):
            return _clean(f.get("foreground") or f.get("tab") or "")
        foci = [_focus(f) for f in frames]
        foci = [x for x in foci if x]
        if not foci:
            return ""
        # 去除相邻重复（连看同一内容算一次），得到"关注序列"
        seq = []
        for x in foci:
            if not seq or seq[-1] != x:
                seq.append(x)
        if len(seq) <= 1:
            # 全程专注同一内容：给一句"一直专注在 X"
            return f"你最近一直专注在「{seq[0][:40]}」"
        # 有 scene 切换：说"从 X 切到了 Y，最近专注在 Z"
        return (
            "你刚才从「" + seq[0][:30] + "」切换到了「" + seq[-1][:30]
            + "」，最近一直专注在「" + seq[-1][:40] + "」"
        )
    except Exception:
        return ""
