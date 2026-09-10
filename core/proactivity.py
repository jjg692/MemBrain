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

用法（由低频心跳线程驱动，见下方 ProactiveHeartbeat）：
    from core.proactivity import ProactiveDecider, ProactiveHeartbeat
    decider = ProactiveDecider(relation=rel, perception=perception)
    act = decider.decide(user_id)
    if act["should_act"]:
        agent.proactive_message(user_id, trigger=act["trigger"], context=act["context"])

    # 常驻心跳：低频评估"是否有值得主动开口的素材"，有则生成主动消息并推送
    hb = ProactiveHeartbeat(agent_factory=af, perception=pm)
    hb.start()   # daemon 线程
    hb.stop()    # 退出时请求停止
"""

import threading
import time
from typing import Optional

from core.config import (
    PROACTIVITY_ENABLED,
    PROACTIVITY_MIN_INTERVAL_MIN,
    PROACTIVITY_DAILY_CAP,
    PROACTIVITY_SCAN_INTERVAL,
    GOAL_MEMORY_ENABLED,
    GOAL_MIN_VITALITY,
    GOAL_ACTIVE_MIN_INTERVAL_SEC,
)


class ProactiveDecider:
    """平衡多信号，决定此刻是否值得主动开口、优先说什么。"""

    # 素材优先级（0 最高）：长期目标 > 承诺 > 进行中经历 > 情绪关怀 > 感知变化 > 断联回归
    # 长期目标最具"信息量与主动性"（有信息量的关心，而非空泛问候），故排最前。
    _PRIORITY = {
        "goal": -1,
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
        self._goal_throttle: dict = {}   # user_id -> last_ts，目标的主动态节流
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

    def _signal_goal(self, user_id: str) -> Optional[dict]:
        """长期目标信号：角色"放在心上"的用户目标（有信息量的主动关心）。

        只有同时满足才开口（克制、不伪造、读不到即空）：
        - GOAL_MEMORY_ENABLED 开启；
        - relation 里确有鲜活目标（vitality >= GOAL_MIN_VITALITY）且非完成/放弃；
        - 距上次主动提该用户目标超过 GOAL_ACTIVE_MIN_INTERVAL_SEC（防唠叨）；
        - **择时/情绪校准**：时机不合适（深夜且对方平时已睡）或对方情绪低落时，
          让位给关怀/其他信号（返回 None），不无差别硬提目标。
        返回素材 dict 或 None。
        """
        if not GOAL_MEMORY_ENABLED:
            return None
        try:
            if self.relation is None:
                return None
            # ③ 择时/情绪校准：时机或情绪不合适时，不提目标，让位给更合时宜的信号
            if self._goal_timing_inappropriate(user_id):
                return None
            goals = self.relation.live_goals(
                user_id, n=3, min_vitality=GOAL_MIN_VITALITY
            )
            if not goals:
                return None
            now = time.time()
            # 目标级节流：取最新一个可提目标，且距上次提及够久
            goal = goals[0]
            last = self._goal_throttle.get(user_id, 0)
            if GOAL_ACTIVE_MIN_INTERVAL_SEC > 0 and now - last < GOAL_ACTIVE_MIN_INTERVAL_SEC:
                return None
            title = str(goal.get("title", "")).strip()
            if not title:
                return None
            self._goal_throttle[user_id] = now
            # ② 有信息量的关心：把进度 + 提炼时给的"下一步建议(note)"都带进 context，
            #    让 LLM 不只是看到"目标名+进度"，还能看到具体的下一步，开口更有信息量。
            progress = str(goal.get("progress", "")).strip()
            note = str(goal.get("note", "")).strip()
            ctx = f"你有一个在推进的目标「{title}」"
            if progress:
                ctx += f"（进度：{progress[:40]}）"
            if note:
                ctx += f"；你记得的下一步：{note[:40]}"
            return {
                "type": "goal",
                "trigger": "想到你有一个还在坚持的目标",
                "context": ctx,
            }
        except Exception:
            return None

    def _goal_timing_inappropriate(self, user_id: str) -> bool:
        """③ 择时/情绪校准：判断此刻是否不适合提目标。

        返回 True（不适合）的情形：
        - 用户情绪明显低落/在变差（此时提目标很扫兴，应让位给情绪关怀）；
        - 深夜/清晨且该用户平时这个点通常已安静（不该为催目标打扰）。
        读不到一律返回 False（不误拦 → 保持原行为）。
        """
        try:
            # 情绪低落：与 _signal_mood 同源，低落时不硬提目标
            if self.relation is not None:
                mood = self.relation._mood_trend_text(user_id) or ""
                if "变差" in mood or "下降" in mood:
                    return True
        except Exception:
            pass
        try:
            # 深夜/清晨且平时安静：不去打扰
            from core.perception import time_situation
            if self.perception is not None and hasattr(self.perception, "routine"):
                t = time_situation()
                if t.get("period") in ("深夜", "清晨"):
                    quiet = self.perception.routine.quiet_hours(user_id)
                    if quiet and t.get("now") and len(str(t.get("now"))) >= 13:
                        hour = int(str(t["now"])[11:13])
                        if hour in quiet:
                            return True
        except Exception:
            pass
        return False

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
        # 长期目标：角色"放在心上"的用户目标，最有信息量、最主动（最高优先级）
        goal = self._signal_goal(user_id)
        if goal:
            candidates.append(goal)
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


class ProactiveHeartbeat:
    """主动性常驻心跳线程（harness 级，低频、克制）。

    让已写好的 ProactiveDecider 真正"跑起来"：周期性对**当前在线用户**评估一次
    "此刻是否有值得主动开口的素材"，有则生成主动消息并走 WS 推送。

    设计（对齐 L3Pusher/ReminderScheduler 的既有模式，保持克制与不伪造）：
      - 由 PROACTIVITY_ENABLED 总开关控制；关闭则线程立即退出，零开销。
      - 只对在线用户主动（w s manager 的 user_ids），离线不打扰。
      - 实际开口受 ProactiveDecider 内部节流约束（PROACTIVITY_MIN_INTERVAL_MIN +
        每日封顶），这里只做低频评估，不会因扫描间隔小就刷屏。
      - 主动消息由 agent.proactive_message 生成（模型在后台调用），
        失败/false 一律静默，不伪造、不抛异常。
      - 线程为 daemon，进程退出自动终止。
    """

    def __init__(self, agent_factory, perception=None, push_callback=None):
        self.agent_factory = agent_factory
        self.perception = perception
        # push_callback: callable(user_id, data_dict) 用于 WS 推送; None 只记 L1
        self.push_callback = push_callback
        self._stop = threading.Event()
        self._thread = None

    # --------------------- 单次评估（可独立调用/测试） ---------------------

    def heartbeat_once(self) -> int:
        """对每个在线用户评估一次，若有要主动说的则生成并推送，返回主动条数。"""
        if not PROACTIVITY_ENABLED:
            return 0
        try:
            from api.websocket_manager import single_ws_manager
            if hasattr(single_ws_manager, "user_ids"):
                users = list(single_ws_manager.user_ids())
            else:
                users = list(single_ws_manager.get_all())
        except Exception:
            users = []
        if not users:
            return 0  # 无在线用户，不打扰
        pushed = 0
        for uid in users:
            try:
                agent = self.agent_factory.get_agent(uid, self._default_role())
                rel = self._get_relation(agent)
                decider = ProactiveDecider(relation=rel, perception=self.perception)
                act = decider.decide(uid)
                if not act.get("should_act"):
                    continue
                text = agent.proactive_message(
                    uid, trigger=act.get("trigger", ""), context=act.get("context", "")
                )
                if not text:
                    continue
                if self.push_callback:
                    data = {
                        "type": "proactive",
                        "role_id": getattr(agent, "role_id", ""),
                        "content": text,
                        "trigger": act.get("type", "proactive"),
                        "via": "heartbeat",
                    }
                    self.push_callback(uid, data)
                pushed += 1
            except Exception:
                # 单个用户失败不影响其他用户；静默跳过（不伪造、不抛）
                continue
        return pushed

    def _get_relation(self, agent):
        """取 agent 的关系记忆内核（惰性），失败返回 None（signals 会读不到即空）。"""
        try:
            return agent._get_relation()
        except Exception:
            return None

    def _default_role(self):
        try:
            from core.memory.l3 import _default_role
            return _default_role(self.agent_factory.initializer)
        except Exception:
            return "kasumi"

    # --------------------- 线程生命周期 ---------------------

    def start(self):
        """启动 daemon 心跳线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(int(PROACTIVITY_SCAN_INTERVAL) or 60,), daemon=True
        )
        self._thread.start()

    def stop(self):
        """请求停止心跳线程。"""
        self._stop.set()

    def _loop(self, interval: int):
        interval = max(10, interval)
        while not self._stop.is_set():
            start = time.time()
            try:
                n = self.heartbeat_once()
                if n:
                    from core.logger import log_info
                    log_info("Proactive", f"本轮主动开口 {n} 条")
            except Exception as e:
                from core.logger import log_error
                log_error("Proactive", f"心跳循环异常: {e}")
            self._stop.wait(max(10, interval - (time.time() - start)))
