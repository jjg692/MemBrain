"""
感知层

让 Agent 具备"持续观察"而非"被动等待"的能力，分四块：
1. 时序/日程感知    - 当前时间、时段(早/午/晚/深夜)、每周几天、距下一个关键时点
2. 全局环境/系统感知 - 系统运行时长、前台应用、网络/在线状态、忙碌与否（尽力而为，读不到则置空）
3. 位置与情境感知    - 用户常驻城市 + 由此派生的情境(上班通勤/居家等，按时间段)
4. 情绪趋势曲线      - 记录历次好感度/情绪样本，算出"近期心情走向"

设计原则：
- 全量本地文件持久化（perception.json），与 ChromaDB 解耦，避免嵌入调用
- 敏感/读不到的项一律返回空，绝不伪造
- 感知结果汇成一段文本注入 system prompt，让模型"意识到当下时空与用户状态"
"""
import json
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from core.config import (
    PROJECT_ROOT, PERCEPTION_FILE, PERCEPTION_CITY,
    MOOD_TREND_MAX_SAMPLES, ROUTINE_WINDOW_DAYS,
    FOREGROUND_SENSING_ENABLED, FOREGROUND_SENSING_TIMEOUT,
    PERCEPTION_PROMPT_MAX_CHARS, PERCEPTION_SYSTEM_CACHE_TTL,
)

_PERCEPTION_FILE = Path(PROJECT_ROOT) / "perception.json"

# 系统 boot-time 缓存（第 5 项优化）：PowerShell 查 LastBootUpTime 慢（约 4s），
# 而系统运行时长变化很慢，短期缓存避免每轮 summarize 都卡一次。仅缓存慢的 OS 时长查询，
# 前台窗口是高动态信号（不作为缓存，靠 sensing_hint 感知变化）。
_system_cache = {"ts": 0.0, "boot": None}


# ===================== 1. 时序/日程感知 =====================

def time_situation(now: Optional[datetime] = None) -> dict:
    """返回当前时序情境"""
    now = now or datetime.now()
    hour = now.hour
    if hour < 5:
        period = "深夜"
    elif hour < 8:
        period = "清晨"
    elif hour < 11:
        period = "上午"
    elif hour < 13:
        period = "中午"
    elif hour < 18:
        period = "下午"
    elif hour < 23:
        period = "晚上"
    else:
        period = "深夜"
    return {
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": now.strftime("%A"),  # 英文，兼容
        "weekday_cn": _weekday_cn(now),
        "period": period,
        "is_weekend": now.weekday() >= 5,
    }


def _weekday_cn(now: datetime) -> str:
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return names[now.weekday()]


# ===================== 2. 全局环境/系统感知 =====================

def foreground_window() -> str:
    """读取 Windows 用户当前聚焦的前台窗口文本（应用名 + 窗口标题）。

    用 GetForegroundWindow + GetWindowTextW 取"真正在前台的窗口"，比 PowerShell
    扫 MainWindowTitle 更准、更快。仅 Windows 且开关开启时可用；其他平台/失败返回空。
    返回形如 "应用名：窗口标题"，或空串（不伪造）。
    """
    if not FOREGROUND_SENSING_ENABLED or sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        # 前台窗口句柄
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        # 进程 id
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # 窗口标题
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return ""
        # 进程名（尽力）
        app = ""
        try:
            import psutil
            app = psutil.Process(pid.value).name()
            if app.endswith(".exe"):
                app = app[:-4]
        except Exception:
            app = ""
        if app:
            return f"{app}：{title}"
        return title
    except Exception:
        return ""


def _query_boot_time() -> Optional[datetime]:
    """慢速查询系统启动时刻（尽力；失败返回 None，不伪造）。单独抽出以便缓存。"""
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime"],
                capture_output=True, text=True, timeout=4,
                encoding="utf-8", errors="replace",
            )
            if r.returncode == 0 and r.stdout.strip():
                try:
                    return datetime.fromisoformat(r.stdout.strip().replace("+00:00", ""))
                except Exception:
                    return None
        else:
            secs = float(open("/proc/uptime").read().split()[0])
            return datetime.now() - timedelta(seconds=secs)
    except Exception:
        return None


def system_situation() -> dict:
    """尽力读取系统状态；Windows 优先，失败则返回空字段（不伪造）。

    第 5 项优化（缓存 boot-time）：PowerShell 查 LastBootUpTime 慢（约 4s），
    而启动时刻变化极慢。故把 boot 时刻按 PERCEPTION_SYSTEM_CACHE_TTL 缓存，
    每次调用用 now - boot 实时换算运行时长（保证时长准确），避免每轮卡 4s。
    前台窗口是高动态信号，仍实时读取（不作为缓存，靠 sensing_hint 感知变化）。
    """
    info = {"os": platform.system(), "runtime_sec": None, "active_app": None,
            "foreground": None}
    # 系统运行时长（尽力 + 缓存，详见函数 docstring）
    now = time.time()
    boot = _system_cache.get("boot") if isinstance(_system_cache, dict) else None
    if boot is None or (now - _system_cache.get("ts", 0.0)) >= PERCEPTION_SYSTEM_CACHE_TTL:
        boot = _query_boot_time()
        _system_cache["ts"] = now
        _system_cache["boot"] = boot
    if boot is not None:
        info["runtime_sec"] = int((datetime.now() - boot).total_seconds())
    # 前台窗口（Windows 原生，更准；失败置空）
    info["foreground"] = foreground_window() or None
    if info["foreground"]:
        info["active_app"] = info["foreground"]
    return info




def _current_tab_text() -> str:
    """读取浏览器当前标签页文本；仅在感知开关开启且真读到内容时返回，否则空（不伪造）。
    用 core.sensing.get_current_tab()，但过滤掉其返回的"未开启/读取失败/读取为空"标记。
    """
    try:
        from core.sensing import get_current_tab
        from core.config import ENVIRONMENT_SENSING_ENABLED
        if not ENVIRONMENT_SENSING_ENABLED:
            return ""
        text = get_current_tab()
        if not text:
            return ""
        if text.startswith(("（浏览器感知未开启", "（读取失败", "（读取为空")):
            return ""
        # 截断，避免塞进 prompt 过长
        return text[:200]
    except Exception:
        return ""



def _current_screen_text() -> str:
    # 场景 B（可选）：用本地视觉模型识别"屏幕/当前窗口"补充感知。
    # 依赖 VISION_ENABLED + VISION_SCREEN_ON_DEMAND 与本地 Ollama 视觉模型；
    # 未启用/无模型/失败时返回空串（不伪造）。作为前台活跃感知的补充。
    try:
        from core.vision import get_vision_service
        from core.config import VISION_SCREEN_ON_DEMAND, VISION_ENABLED
        if not (VISION_ENABLED and VISION_SCREEN_ON_DEMAND):
            return ""
        text = get_vision_service().describe_screen()
        if not text:
            return ""
        return "用户屏幕/当前窗口（视觉识别）：" + text[:200]
    except Exception:
        return ""


# ===================== 3. 位置与情境感知 =====================

def location_situation(city: str = "") -> dict:
    """由常驻城市 + 当前时段推导情境（尽力，未配置城市则只给通用情境）"""
    city = (city or PERCEPTION_CITY or "").strip()
    now = datetime.now()
    hour = now.hour
    if hour >= 9 and hour < 12 or (hour >= 14 and hour < 18):
        scene = "工作/学习时间"
    elif 12 <= hour < 14:
        scene = "午休时间"
    elif hour < 7 or hour >= 22:
        scene = "休息/睡眠时间"
    else:
        scene = "自由/休闲时间"
    return {"city": city, "scene": scene, "is_weekend": now.weekday() >= 5}


# ===================== 4. 情绪趋势曲线 =====================

class MoodTrend:
    """记录历次好感度/情绪样本，计算近期心情走向（时间序列持久化）"""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else _PERCEPTION_FILE
        self._lock = threading.RLock()
        self._data: Dict[str, list] = {}  # user_id -> [ {ts, primary, valence, affection_avg} ]
        self._load()

    def _load(self):
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._data = raw if isinstance(raw, dict) else {}
            # 迁移动态字段
            for uid in self._data:
                rec = self._data[uid]
                if isinstance(rec, dict):  # 旧结构
                    self._data[uid] = rec.get("samples", [])
        except Exception:
            self._data = {}

    def _save(self):
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                from core.logger import log_error
                log_error("Perception", "保存感知数据失败")

    def record(self, user_id: str, primary: str, valence: float, affection_avg: float):
        """记录一次情绪/好感度样本"""
        user_id = user_id or "default_user"
        samples = self._data.setdefault(user_id, [])
        samples.append({
            "ts": datetime.now().isoformat(),
            "primary": primary,
            "valence": round(float(valence), 3),
            "affection_avg": round(float(affection_avg), 3),
        })
        # 裁剪
        if len(samples) > MOOD_TREND_MAX_SAMPLES:
            self._data[user_id] = samples[-MOOD_TREND_MAX_SAMPLES:]
        self._save()

    def trend(self, user_id: str, window: int = 7) -> dict:
        """返回最近 window 天的情绪走向摘要"""
        samples = self._data.get(user_id or "default_user", [])
        if not samples:
            return {"samples": 0, "recent_samples": 0, "valence_avg": 0.0,
                    "valence_trend": "", "affection_avg": 0.0,
                    "affection_trend": "", "recent": []}
        # 只看近 window 天
        cutoff = datetime.now() - timedelta(days=window)
        recent = [s for s in samples if self._iso(s.get("ts")) >= cutoff]
        if not recent:
            recent = samples[-10:]
        vals = [s.get("valence") or 0 for s in recent]
        affs = [s.get("affection_avg") or 0 for s in recent]
        avg_v = sum(vals) / len(vals)
        avg_a = sum(affs) / len(affs)
        # 趋势：首尾对比
        v_trend = "平稳"
        if len(vals) >= 2:
            d = vals[-1] - vals[0]
            v_trend = "变好" if d > 0.1 else ("变差" if d < -0.1 else "平稳")
        a_trend = "平稳"
        if len(affs) >= 2:
            d = affs[-1] - affs[0]
            a_trend = "上升" if d > 0.05 else ("下降" if d < -0.05 else "平稳")
        return {
            "samples": len(samples),
            "recent_samples": len(recent),
            "valence_avg": round(avg_v, 2),
            "valence_trend": v_trend,
            "affection_avg": round(avg_a, 2),
            "affection_trend": a_trend,
            "recent": recent[-8:],
        }

    @staticmethod
    def _iso(ts) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return None


# ===================== 5. 作息习惯模型（活跃时段） =====================

class RoutineModel:
    """记录用户活跃时段(何时在聊天)，聚合作息特征"""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else _PERCEPTION_FILE
        self._lock = threading.RLock()
        self._data: Dict[str, dict] = {}  # user_id -> {"acts": [ {hour, weekday, ts} ]}
        self._load()

    def _load(self):
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            # 兼容：perception.json 可能同时存 mood 与 routine，用标记区分
            data = raw.get("routine", {}) if isinstance(raw.get("routine"), dict) else {}
            self._data = data
        except Exception:
            self._data = {}

    def _save(self):
        with self._lock:
            try:
                # 与 mood 共用同一文件：读取现有完整文件，更新 routine 键
                full = {}
                if self._path.exists():
                    try:
                        full = json.loads(self._path.read_text(encoding="utf-8")) or {}
                    except Exception:
                        full = {}
                full["routine"] = self._data
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                from core.logger import log_error
                log_error("Perception", "保存作息数据失败")

    def record_activity(self, user_id: str):
        """记录一次活跃（用户在聊天/在线）"""
        user_id = user_id or "default_user"
        now = datetime.now()
        acts = self._data.setdefault(user_id, {}).setdefault("acts", [])
        acts.append({"hour": now.hour, "weekday": now.weekday(), "ts": now.isoformat()})
        # 裁剪：只留 ROUTINE_WINDOW_DAYS 内
        cutoff = datetime.now() - timedelta(days=ROUTINE_WINDOW_DAYS)
        acts[:] = [a for a in acts if self._iso(a.get("ts")) >= cutoff]
        self._save()

    def summary(self, user_id: str) -> dict:
        acts = self._data.get(user_id or "default_user", {}).get("acts", [])
        if not acts:
            return {"active_hours": {}, "peak_period": "", "observed_days": 0, "has_log": False}
        hours = {}
        for a in acts:
            h = a.get("hour")
            hours[h] = hours.get(h, 0) + 1
        peak = max(hours, key=hours.get) if hours else None
        return {
            "active_hours": dict(sorted(hours.items())),
            "peak_period": _hour_period(peak) if peak is not None else "",
            "observed_days": len(set(a.get("ts", "")[:10] for a in acts)),
            "has_log": True,
        }

    @staticmethod
    def _times(acts: List[dict]) -> List[datetime]:
        out = []
        for a in acts:
            t = RoutineModel._iso(a.get("ts"))
            if t:
                out.append(t)
        out.sort()
        return out

    def attendance(self, user_id: str, now: Optional[datetime] = None) -> dict:
        """关注度/关系投入 + 在场感：
        从活动时间戳计算最近联系、断联天数、连续活跃、近7天活跃等。"""
        now = now or datetime.now()
        acts = self._data.get(user_id or "default_user", {}).get("acts", [])
        if not acts:
            return {"has_log": False}
        times = self._times(acts)
        if not times:
            return {"has_log": False}
        last = times[-1]
        days = set(t.date() for t in times)
        # 连续活跃天数（到最近一天为止）
        consec = 0
        d = last.date()
        while d in days:
            consec += 1
            d -= timedelta(days=1)
        return {
            "has_log": True,
            "last_active": last.isoformat(),
            "minutes_since_last": int((now - last).total_seconds() // 60),
            "days_since_contact": (now.date() - last.date()).days,
            "active_days_last7": sum(1 for t in times if (now - t).days <= 7),
            "consecutive_active_days": consec,
        }

    def quiet_hours(self, user_id: str, window: int = 30) -> set:
        """返回该用户近期基本不活跃的小时集合（用于判断是否异常时段）"""
        acts = self._data.get(user_id or "default_user", {}).get("acts", [])
        cutoff = datetime.now() - timedelta(days=window)
        active = set()
        for a in acts:
            t = self._iso(a.get("ts"))
            if t and t >= cutoff:
                active.add(t.hour)
        all_hours = set(range(24))
        return all_hours - active

    @staticmethod
    def _iso(ts) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return None


def _hour_period(hour: int) -> str:
    if hour < 8:
        return "清晨/深夜"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


# ===================== 汇总：感知管理器 =====================

def _density_trim(parts, max_chars) -> list:
    """密度裁剪：超 max_chars 时从后往前裁低价值项，最大程度保留高价值项。

    保留优先级（靠条目开头关键字判断价值，不依赖具体文本）：
      - 高价值保留：时序("现在：")、位置("你所在"/"当前应是")、关系/在场
        ("没找你了"/"没聊了"/"连续")、作息异常("熬夜")、情绪趋势("最近的心情")
      - 其余视为可裁（前台窗口/标签页/屏幕快照等瞬时项优先级最低）
    纯函数、确定性；只删整项不截断单行，保证输出仍是一段可读中文。
    """
    _KEEP_HINT = ("现在：", "你所在", "当前应是", "没找你了", "没聊了", "连续",
                  "熬夜", "最近的心情")
    if len("\n".join(parts)) <= max_chars:
        return parts
    kept = [p for p in parts if p.startswith(_KEEP_HINT)]
    rest = [p for p in parts if not p.startswith(_KEEP_HINT)]
    # 若高价值项本身已超限（极端），退化从前往后截断。
    if len("\n".join(kept)) > max_chars:
        out = []
        for p in kept:
            if len("\n".join(out + [p])) > max_chars and out:
                break
            out.append(p)
        return out
    # 有富余容量时，把中价值项从前往后尽量补回（不覆盖高价值项顺序）。
    out = list(kept)
    for p in rest:
        if len("\n".join(out + [p])) > max_chars:
            break
        out.append(p)
    return out


def _build_detailed_supplement(pm, user_id: str) -> str:
    """构造感知摘要的"详细补充部分"（供 summarize_detailed 使用，纯 harness）。

    追加比精简版更可追溯的原始信息：最近活跃明细、作息明细、情绪样本数、
    滚动帧趋势。全部来自已有模型，不新增采集、不调 LLM；读不到即省去该项。
    """
    lines = []
    uid = user_id or "default_user"
    # 滚动帧趋势（sensing_hint 的跨帧归纳；无则省）
    try:
        from core.sensing_hint import frame_trend
        trend = frame_trend(uid)
        if trend:
            lines.append(trend)
    except Exception:
        pass
    # 最近活跃明细（最近 3 条带时间）
    try:
        acts = pm.routine._data.get(uid, {}).get("acts", [])
        if acts:
            recent = [a for a in acts[-3:] if a.get("ts")]
            if recent:
                detail = "；".join(
                    f"{a['ts'][11:16]}({a.get('hour')}时)" for a in recent
                )
                lines.append(f"最近活跃时刻：{detail}")
    except Exception:
        pass
    # 作息明细（活跃高峰时段 + 观察天数）
    try:
        s = pm.routine.summary(uid)
        if s.get("has_log") and s.get("observed_days"):
            lines.append(
                f"作息：活跃高峰{s['peak_period'] or '未知'}，已观察{s['observed_days']}天"
            )
    except Exception:
        pass
    # 情绪样本数（统计置信度）
    try:
        tr = pm.mood_trend.trend(uid)
        if tr["samples"]:
            lines.append(f"情绪样本：共{tr['samples']}条，近期{tr['recent_samples']}条")
    except Exception:
        pass
    return "\n".join(lines)


class PerceptionManager:
    """汇总时序/系统/情境/作息/情绪趋势，生成注入 system prompt 的感知文本"""

    def __init__(self, mood_trend: MoodTrend, routine: RoutineModel, city: str = ""):
        self.mood_trend = mood_trend
        self.routine = routine
        self.city = (city or PERCEPTION_CITY or "").strip()

    def record_user_activity(self, user_id: str):
        self.routine.record_activity(user_id)

    def record_mood(self, user_id: str, primary: str, valence: float, affection_avg: float):
        self.mood_trend.record(user_id, primary, valence, affection_avg)

    #: 感知摘要的短 TTL 缓存（第 5/二(B) 项）：避免 LLM 主动/重复拉取时每次都重算+读 IO
    _summary_cache = {"ts": 0.0, "text": None}

    def summarize_detailed(self, user_id: str) -> str:
        """比 summarize 更细/更具体的一版感知（供 get_perception_summary 工具用）。

        与被动注入的精简版区分开：在 summarize 基础上，追加最近活跃明细、作息明细、
        情绪样本数、滚动帧趋势等"原始/可追溯"信息，真正兑现工具承诺的
        "比已有上下文更新的用户当下状态细节"。带缓存；失败降级为精简版（不伪造）。
        """
        try:
            # 缓存：300 秒内复用，避免工具被连问时反复重算 + 触发前台窗口实时读取
            now = time.time()
            if (now - self._summary_cache["ts"]) < PERCEPTION_SYSTEM_CACHE_TTL                     and self._summary_cache["text"] is not None:
                return self._summary_cache["text"]
            detailed = _build_detailed_supplement(self, user_id)
            text = self.summarize(user_id)
            if detailed:
                text = (text + "\n" + detailed) if text else detailed
            if text:
                self._summary_cache.update({"ts": now, "text": text})
            return text
        except Exception:
            return self.summarize(user_id)

    def summarize(self, user_id: str) -> str:
        """生成感知文本注入 system prompt；读不到/默认状态时尽量克制"""
        parts = []
        t = time_situation()
        parts.append(
            f"现在：{t['now']}（{t['weekday_cn']}{'，周末' if t['is_weekend'] else ''}，{t['period']}时段）"
        )
        # 位置情境
        loc = location_situation(self.city)
        scene = loc["scene"]
        if loc["city"]:
            parts.append(f"你所在的城市：{loc['city']}；当前应是{scene}{'，周末' if loc['is_weekend'] else ''}。")
        else:
            parts.append(f"当前应是{scene}{'，周末' if loc['is_weekend'] else ''}。")
        # 系统感知（尽力；有值才说）
        sysinfo = system_situation()
        if sysinfo.get("runtime_sec") is not None:
            parts.append(f"系统已运行约 {int(sysinfo['runtime_sec']//3600)} 小时")
        # 用户此刻在看什么：优先浏览器当前标签页（更具体），读不到则回退到前台窗口
        tab_text = _current_tab_text()
        if tab_text:
            parts.append(tab_text)
        elif sysinfo.get("foreground"):
            parts.append(f"用户当前可能在用：{sysinfo['foreground']}")

        # 场景 B（可选）：屏幕/当前窗口视觉识别，作为前台活跃感知的补充（默认关）
        try:
            screen_text = _current_screen_text()
            if screen_text:
                parts.append(screen_text)
        except Exception:
            pass
        # 作息习惯
        routine = self.routine.summary(user_id)
        if routine["observed_days"]:
            parts.append(
                f"按这段时间观察，你通常在{routine['peak_period']}比较活跃（已观察{routine['observed_days']}天）。"
                if routine["peak_period"] else f"你通常的活动高峰：{routine['peak_period']}"
            )
        # 情绪趋势
        trend = self.mood_trend.trend(user_id)
        if trend["recent_samples"]:
            parts.append(
                f"你最近的心情整体{trend['valence_trend']}（评价 {trend['valence_avg']:.2f}），"
                f"好感度趋势{trend['affection_trend']}。"
            )

        # ---------- 1. 忙碌度/在场感 ----------
        # 根据最后活跃距今多久，判断"此刻是否在线/是否可能不在"（克制，不伪造）
        att = self.routine.attendance(user_id)
        if att.get("has_log"):
            mins = att.get("minutes_since_last", 0)
            if mins < 10:
                parts.append("用户此刻似乎在线（刚才还在）。")
            elif mins < 60:
                parts.append(f"用户最近 {mins} 分钟内活跃过。")
            elif mins < 300:
                parts.append(f"用户约 {mins//60} 小时前活跃过，可能暂时不在。")
            else:
                days = att.get("days_since_contact", 0)
                parts.append(f"用户约 {days} 天没找你了，可能比较忙或没上线。")

        # ---------- 2. 关注度/关系投入 ----------
        if att.get("has_log") and att.get("days_since_contact", 0) >= 2:
            parts.append(f"你们已有 {att['days_since_contact']} 天没聊了，可以自然地想念/关心一下。")
        if att.get("has_log") and att.get("consecutive_active_days", 0) >= 3:
            parts.append(f"你已经连续 {att['consecutive_active_days']} 天都在，我们的联系很稳定。")

        # ---------- 5. 作息异常检测 ----------
        # 用 quiet_hours（该时段通常不活跃）判断"现在是否异常时段（如熬夜）"
        # 仅当确有历史（has_log）才判定，避免初始无数据时把所有小时当"异常"
        if att.get("has_log"):
            qh = self.routine.quiet_hours(user_id)
            if qh and datetime.now().hour in qh and not time_situation()["is_weekend"]:
                parts.append("这个时间点你平时通常已经休息了，可以留意是否在熬夜、适当关心。")

        if not parts:
            return ""
        # 新鲜度标记（第 1 项之一）：给"用户此刻在看什么"这类瞬时采样项打上时刻戳，
        # 避免模型把一瞬快照误当长期状态。克制：仅对当下变化类项标注，其余不加。
        _now = datetime.now().strftime("%H:%M")
        _fresh = []
        for p in parts:
            if p.startswith("用户当前可能在用") or "标签页" in p or "屏幕/当前窗口" in p:
                if not p.endswith("。"):
                    p = p + "。"
                _fresh.append(f"{p}（此刻采集 {_now}）")
            else:
                _fresh.append(p)
        parts = _fresh
        # 密度裁剪（第 1 项之二）：超 PERCEPTION_PROMPT_MAX_CHARS 时保护高价值项
        #（时序、位置、关系/情绪），从后往前裁掉低价值项，避免感知 prompt 无限膨胀。
        if len("\n".join(parts)) > PERCEPTION_PROMPT_MAX_CHARS:
            parts = _density_trim(parts, PERCEPTION_PROMPT_MAX_CHARS)
        return "\n".join(parts)
