"""
主动性心跳（harness 级，LLM 可被驱动主动开口）
===============================================
把项目里"已有但彼此孤立"的主动信号统一收进一个**低频、克制**的决策器，
回答两件事：
  1) 现在该不该主动打扰用户？
  2) 该优先说什么（素材）？

综合的信号源（均为已有能力，不新增采集 / 不额外调 LLM）：
  - 关系记忆：未兑现承诺 / 待续话题(鲜活经历) / 情绪走向（_compose_proactive_hint 里已用）
  - 感知层：断联天数 / 作息异常(熬夜) / 感知变化提示(sensing_hint)

克制原则（避免变成骚扰）：
  - 低频：有最小触发间隔（默认 30 分钟）
  - 封顶：每日主动次数上限（默认 8 次）
  - 有料才开口：素材不够好 / 读不到一律 should_act=False（不伪造）
  - 节流状态仅内存（进程重启重置）

用法（由低频调度线程驱动，本项目暂未接常驻线程）：
    from core.proactivity import ProactiveDecider
    decider = ProactiveDecider(relation=rel, perception=perception)
    act = decider.decide(user_id)
    if act["should_act"]:
        agent.proactive_message(user_id, trigger=act["trigger"], context=act["context"])
"""

import time

from core.config import (
    PROACTIVITY_ENABLED,
    PROACTIVITY_MIN_INTERVAL_MIN,
    PROACTIVITY_DAILY_CAP,
)


class ProactiveDecider:
    """平衡多信号，决定此刻是否值得主动开口、优先说什么。"""

    # 素材优先级（0 最高）：承诺 > 进行中经历 > 情绪关怀 > 感知变化 > 断联回归
    _PRIORITY = {
        "promise": 0,
        "episode": 1,
        "mood": 2,
        "sensing_change": 3,
        "reconnect": 4,
    }

    def __init__(self, relation=None, perception=None):
        """relation: RelationMemory（可空）；perception: PerceptionManager（可空）。"""
        self.relation = relation
        self.perception = perception
        # 节流状态：user_id -> {"last": ts, "today": date_str, "count": int}
        self._throttle = {}

    # --------------------- 信号采集（不伪造、读不到即空） ---------------------

    def _signal_promises(self, user_id: str) -> list:
        try:
            if self.relation is None:
                return []
            return self.relation.pending_promises(user_id, n=3) or []
        except Exception:
            return []

    def _signal_episodes(self, user_id: str) -> list:
        """进行中/鲜活话题：取最近有共鸣的经历（排除过短的无奈话术）。"""
        try:
            if self.relation is None:
                return []
            eps = self.relation.decayed_episodes(user_id, n=5, min_vitality=0.3) or []
            out = []
            for ep in eps:
                msg = str(ep.get("user_msg", "")).strip()
                # 只认"像话题"的文本：长度适中、非纯语气词
                if len(msg) >= 2 and len(msg) <= 60:
                    out.append(msg)
            return out[:2]
        except Exception:
            return []

    def _signal_mood(self, user_id: str) -> str:
        """情绪走向：仅在明显变差时作为关怀信号返回描述，否则空。"""
        try:
            if self.relation is None:
                return ""
            mood = self.relation._mood_trend_text(user_id) or ""
            if "变差" in mood or "下降" in mood:
                return mood
            return ""
        except Exception:
            return ""

    def _signal_sensing_change(self, user_id: str) -> str:
        """感知变化：前台/标签页刚变化时的提示（有冷却，读不到即空）。"""
        try:
            from core.sensing_hint import sensing_change_hint
            return sensing_change_hint(user_id, cooldown_sec=60)
        except Exception:
            return ""

    def _signal_reconnect(self, user_id: str) -> str:
        """断联天数：超过阈值值得主动打个招呼（但要克制，不过度）。"""
        try:
            if self.perception is None:
                return ""
            att = self.perception.routine.attendance(user_id)
            if not att or not att.get("has_log"):
                return ""
            days = int(att.get("days_since_contact", 0) or 0)
            if days >= 3:
                return f"你们约 {days} 天没联系了"
            return ""
        except Exception:
            return ""

    # --------------------- 决策 ---------------------

    def _throttle_ok(self, user_id: str) -> bool:
        """节流：未超最小间隔且未超每日封顶才允许主动。"""
        today = time.strftime("%Y-%m-%d")
        rec = self._throttle.get(user_id)
        now = time.time()
        if not rec or rec.get("date") != today:
            return True  # 首次或新的一天
        # 最小间隔始终生效（0=不限间隔）
        if PROACTIVITY_MIN_INTERVAL_MIN > 0 and now - rec.get("last", 0) < PROACTIVITY_MIN_INTERVAL_MIN * 60:
            return False
        # 每日封顶（0=不限次数）
        if PROACTIVITY_DAILY_CAP > 0 and rec.get("count", 0) >= PROACTIVITY_DAILY_CAP:
            return False
        return True
    def _record_act(self, user_id: str):
        today = time.strftime("%Y-%m-%d")
        rec = self._throttle.get(user_id)
        now = time.time()
        if not rec or rec.get("date") != today:
            self._throttle[user_id] = {"date": today, "count": 1, "last": now}
            return
        rec["count"] = rec.get("count", 0) + 1
        rec["last"] = now

    def decide(self, user_id: str = "default_user"):
        """返回 {should_act, trigger, context, type}。
        有足够好的素材且节流允许时才 should_act=True；否则 False（不伪造、不骚扰）。
        """
        if not PROACTIVITY_ENABLED:
            return {"should_act": False, "trigger": "", "context": "", "type": ""}
        candidates = []
        # 收集素材（按优先级）
        promises = self._signal_promises(user_id)
        if promises:
            text = str(promises[0].get("text", "")).strip()
            if text:
                candidates.append({
                    "type": "promise",
                    "trigger": "你可能答应过对方某件事",
                    "context": f"你曾答应/记得要做的事：「{text[:40]}」",
                })
        episodes = self._signal_episodes(user_id)
        if episodes:
            candidates.append({
                "type": "episode",
                "trigger": "你们有最近聊到的话题可以延续",
                "context": "你们最近聊过：" + "、".join(episodes),
            })
        mood = self._signal_mood(user_id)
        if mood:
            candidates.append({
                "type": "mood",
                "trigger": "对方最近情绪可能不太好",
                "context": mood,
            })
        change = self._signal_sensing_change(user_id)
        if change:
            candidates.append({
                "type": "sensing_change",
                "trigger": "感知到对方环境刚有变化",
                "context": change,
            })
        reconnect = self._signal_reconnect(user_id)
        if reconnect:
            candidates.append({
                "type": "reconnect",
                "trigger": reconnect,
                "context": reconnect,
            })
        if not candidates:
            return {"should_act": False, "trigger": "", "context": "", "type": ""}
        if not self._throttle_ok(user_id):
            return {"should_act": False, "trigger": "", "context": "", "type": ""}
        # 选优先级最高的素材
        best = min(candidates, key=lambda c: self._PRIORITY.get(c["type"], 99))
        self._record_act(user_id)
        return {
            "should_act": True,
            "trigger": best["trigger"],
            "context": best["context"],
            "type": best["type"],
        }
