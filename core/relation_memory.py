"""
关系记忆内核（RelationshipMemory）——「底层/内在状态层」的落地实现

解决的问题（距一个真正的 AI 朋友 / AI 伴侣的底层差距）：
1. 角色是"每轮现拼的瞬时对象"，没有持续存在、会随时间演化的内在状态 → 这里提供持久内核。
2. 情感/好感度永久冻结在当前瞬时值，不会像真人一样随时间降温、淡忘 → 引入时间衰减。
3. 角色"记不住自己过往说过/做过什么"，无法对自己负责 → 提供经历账本 + 自我模型 + 反思。

设计原则（与全项目一致：LLM 优先、无硬编码规则、双键隔离、本地乔平持久化）：
- 本地 JSON 持久化（relation_memory.json），与 ChromaDB 解耦（不依赖嵌入调用），单测离线可跑。
- 所有"主观评价"（情绪共振、用户画像、自我认知、反思结论）由 LLM 抽/算，本模块只做
  确定性的结构、时间衰减、阈值与存储——不替代 LLM 做"判断"。
- 时间衰减用指数半衰期：x(t) = value * 0.5^(days/halflife)，让"当下情绪"随时间自然回归基线。

数据通道（按 (user_id, role_id) 双键隔离，与既有五层记忆一致）：
- episodes  共同经历账本：[{ts, user_msg, reply, resonance, emotion, impact}]
- user_model 一段时间积累的"对用户的理解"（反思注入）：{about_user, traits, needs, boundaries}
- self_model 角色的自我模型（内在性格状态）：{summary, cares_about, current_mood_text, relationship}
- values     价值内核（角色在乎什么）：list[str]
- decay      情绪/好感度/活跃时间（供时间衰减注入）
"""
import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import (
    PROJECT_ROOT, RELATION_MEMORY_FILE, RELATION_MEMORY_ENABLED,
    RELATION_EMOTION_HALFLIFE_DAYS, RELATION_EPISODE_RESONANCE_THRESHOLD,
    RELATION_REFLECT_INTERVAL, RELATION_REFLECT_BATCH,
)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _days_since(iso_ts: str) -> float:
    if not iso_ts:
        return 0.0
    try:
        return (datetime.now() - datetime.fromisoformat(iso_ts)).total_seconds() / 86400.0
    except Exception:
        return 0.0


def half_life_decay(value: float, days: float, halflife_days: float) -> float:
    """指数半衰期衰减：x(t) = value * 0.5^(days/halflife)。halflife<=0 不衰减。"""
    if halflife_days and halflife_days > 0 and days > 0:
        return value * (0.5 ** (days / halflife_days))
    return value


class RelationMemory:
    """一个 (user_id, role_id) 对应的关系记忆内核。

    单实例按 role_id 构造（与 Agent 一一对应），内部按 user_id 分账。
    """

    def __init__(
        self,
        path: Optional[str] = None,
        role_id: Optional[str] = None,
        halflife_days: Optional[float] = None,
        resonance_threshold: Optional[float] = None,
    ):
        # 角色隔离 + 并行保存正确性：
        # 每个角色使用独立文件（relation_memory_<role>.json），避免多角色共用单文件
        # 在并发写时互相覆盖（last-write-wins 丢数据）。不传 path 且给了 role_id 时派生角色文件。
        if path is not None:
            self._path = Path(path)
        elif role_id:
            _base = Path(RELATION_MEMORY_FILE)
            _safe_role = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(role_id)) or "role"
            self._path = Path(PROJECT_ROOT) / f"{_base.stem}_{_safe_role}{_base.suffix}"
        else:
            self._path = Path(PROJECT_ROOT) / RELATION_MEMORY_FILE
        self._halflife = RELATION_EMOTION_HALFLIFE_DAYS if halflife_days is None else halflife_days
        self._res_threshold = (
            RELATION_EPISODE_RESONANCE_THRESHOLD
            if resonance_threshold is None else resonance_threshold
        )
        self._lock = threading.RLock()
        self._data: Dict[str, dict] = {}   # user_id -> record
        self._load()

    # ===================== 持久化 =====================

    def _load(self):
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._data = raw if isinstance(raw, dict) else {}
        except Exception:
            self._data = {}

    def _save(self):
        if not RELATION_MEMORY_ENABLED:
            return
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass  # 持久化失败不阻断内核（内存态仍在）

    def _user(self, user_id: str) -> dict:
        user_id = user_id or "default_user"
        with self._lock:
            u = self._data.setdefault(user_id, {})
            u.setdefault("episodes", [])
            u.setdefault("reflections", [])
            u.setdefault("user_model", {"about_user": "", "traits": [], "needs": [], "boundaries": []})
            u.setdefault("self_model", {
                "summary": "", "cares_about": [], "current_mood_text": "",
                "relationship": "", "updated_at": "",
            })
            u.setdefault("values", [])
            u.setdefault("decay", {"emotion": {}, "affection": {}, "last_active": ""})
            # 承诺追踪：用户托付/角色答应的事（人格一致性：记得并兑现）
            u.setdefault("promises", [])   # [{text, ts, status: pending|kept|dropped, topic}]
            # 轻量情绪走向：从若干经历直接推导（零额外 LLM 调用，见 _mood_trend_text）
            u.setdefault("mood_trend", {"samples": 0, "valence_avg": 0.0, "trend": "平稳"})
            return u

    # ===================== 衰减辅助 =====================

    @staticmethod
    def decay_value(value: float, iso_ts: str, halflife: float) -> float:
        return half_life_decay(float(value or 0.0), _days_since(iso_ts), halflife)

    # ===================== 1. 共同经历账本 =====================

    def add_episode(
        self,
        user_id: str,
        user_msg: str,
        reply: str,
        emotion: Optional[Dict] = None,
        resonance: Optional[float] = None,
        impact: Optional[str] = None,
    ) -> Optional[Dict]:
        """沉淀一次对话为经历单元。低于共振阈值的日常勿扰不写；有 impact 的也算重要。"""
        u = self._user(user_id)
        resonance = float(resonance or 0.0)
        impactful = bool(impact and str(impact).strip())
        if resonance < self._res_threshold and not impactful:
            return None
        ep = {
            "ts": _now_iso(),
            "user_msg": (user_msg or "")[:500],
            "reply": (reply or "")[:500],
            "resonance": round(resonance, 3),
            "emotion": dict(emotion or {}),
            "impact": (impact or ""),
        }
        with self._lock:
            u["episodes"].append(ep)
            if len(u["episodes"]) > 400:
                u["episodes"] = u["episodes"][-400:]
            self._save()
        return ep

    def recent_episodes(self, user_id: str, n: int = 12) -> List[Dict]:
        u = self._user(user_id)
        return list(u["episodes"][-n:])

    @staticmethod
    def _overlap_score(query: str, ep: Dict) -> int:
        """轻量相关性：查询词与经历文本的关键词重叠数（无嵌入依赖，仅作排序信号）。"""
        if not query or not query.strip():
            return 0
        q = str(query).lower()
        hay = (str(ep.get("user_msg", "")) + " " + str(ep.get("reply", "")) + " " + str(ep.get("impact", ""))).lower()
        # 取查询里较有辨识度的词（长度>=2 的中文/字母词）
        import re
        toks = [t for t in re.findall(r"[\u4e00-\u9fa5]{2,}|[a-z]{2,}", q)]
        score = 0
        for t in toks:
            if t in hay:
                score += 1
        return score

    def relevant_episodes(self, user_id: str, query: str, n: int = 3, min_vitality: float = 0.1) -> List[Dict]:
        """按"相关度 + 鲜活度"挑选经历（供按当前话题注入）。

        排序：相关性为主、鲜活度为次；仍受 min_vitality 过滤（够重才注入）。
        这是"相关才检索"的轻量实现——避免把全部经历常驻 system prompt。
        """
        u = self._user(user_id)
        pool = []
        for ep in reversed(u["episodes"]):
            vitality = half_life_decay(float(ep.get("resonance", 0.0)), _days_since(ep.get("ts", "")), self._halflife)
            if vitality < min_vitality:
                continue
            d = dict(ep)
            d["vitality"] = round(vitality, 3)
            d["days_ago"] = round(_days_since(ep.get("ts", "")), 1)
            d["relevance"] = self._overlap_score(query, d)
            pool.append(d)
        # 相关度优先（>0），其次活力
        pool.sort(key=lambda d: (d["relevance"] > 0, d["relevance"], d["vitality"]), reverse=True)
        return pool[:n]

    def decayed_episodes(self, user_id: str, n: int = 12, min_vitality: float = 0.1) -> List[Dict]:
        """按"鲜活度=共振*时间衰减"重排（供注入/反思）。低于 min_vitality 视为淡忘。"""
        u = self._user(user_id)
        out = []
        for ep in reversed(u["episodes"]):
            vitality = half_life_decay(float(ep.get("resonance", 0.0)), _days_since(ep.get("ts", "")), self._halflife)
            if vitality < min_vitality:
                continue
            d = dict(ep)
            d["vitality"] = round(vitality, 3)
            d["days_ago"] = round(_days_since(ep.get("ts", "")), 1)
            out.append(d)
            if len(out) >= n:
                break
        return out

    # ===================== 2. 时间衰减（情绪 / 好感度） =====================

    def apply_decay(self, user_id: str, emotion_state=None, affection_state=None) -> Dict:
        """融合即时情感状态 + 记录活跃时间，供注入文本参考。"""
        u = self._user(user_id)
        decay = u["decay"]
        if emotion_state is not None:
            decay["emotion"] = _emission_dict(emotion_state)
        if affection_state is not None:
            decay["affection"] = _affection_dict(affection_state)
        decay["last_active"] = _now_iso()
        aff = decay.get("affection", {})
        keys = ("liking", "trust", "familiarity", "respect", "interest", "attachment")
        vals = [float(aff.get(k, 0.5)) for k in keys]
        decay["affection_avg"] = round(sum(vals) / len(vals), 3)
        emo = decay.get("emotion", {})
        try:
            inten = float(emo.get("intensity", 0.5))
            decay["emotion_intensity_decayed"] = round(
                half_life_decay(inten, _days_since(decay.get("last_active", "")), 0.0), 3
            )
        except Exception:
            decay["emotion_intensity_decayed"] = 0.5
        u["decay"] = decay
        self._save()
        return dict(decay)

    def mark_active(self, user_id: str):
        u = self._user(user_id)
        u["decay"]["last_active"] = _now_iso()
        self._save()

    def decay_state(self, user_id: str, emotion_state=None, affection_state=None):
        """返回**真正随时间衰减后**的情绪/好感度状态（副本，不修改入参）。

        语义：从上次活跃起经过的天数，按半衰期把强度/好感度向基线回落——
        这样"上次情绪"不再永久冻结在瞬时值，而是像真人一样随久别降温。
        返回 (emotion_state|None, affection_state|None) 的衰减副本；调用方据此
        驱动会话状态（如 _get_session 冷加载时应用）。
        """
        u = self._user(user_id)
        last = u["decay"].get("last_active", "")
        days = _days_since(last)
        import copy
        emo = copy.deepcopy(emotion_state)
        aff = copy.deepcopy(affection_state)
        # 情绪强度向基线（默认 0.5）衰减
        if emo is not None and hasattr(emo, "intensity") and self._halflife and self._halflife > 0:
            try:
                base = 0.5
                cur = float(emo.intensity)
                # 向 base 靠拢：距 base 的距离按半衰期衰减
                emo.intensity = base + (cur - base) * (0.5 ** (days / self._halflife))
            except Exception:
                pass
        if aff is not None:
            keys = ("liking", "trust", "familiarity", "respect", "interest", "attachment")
            for k in keys:
                if hasattr(aff, k) and self._halflife and self._halflife > 0:
                    try:
                        cur = float(getattr(aff, k))
                        base = 0.5 if k != "attachment" else 0.3
                        setattr(aff, k, base + (cur - base) * (0.5 ** (days / self._halflife)))
                    except Exception:
                        pass
        return emo, aff

    # ===================== 3. 自我模型 / 反思 =====================

    def store_reflection(self, user_id: str, reflection: Dict) -> None:
        u = self._user(user_id)
        reflection = reflection or {}
        if reflection.get("about_user"):
            u["user_model"]["about_user"] = str(reflection["about_user"])
        for key in ("traits", "needs", "boundaries"):
            items = reflection.get(key)
            if isinstance(items, list) and items:
                u["user_model"][key] = [str(x) for x in items][:30]
        sm = u["self_model"]
        if reflection.get("self_summary"):
            sm["summary"] = str(reflection["self_summary"])
        if isinstance(reflection.get("cares_about"), list) and reflection["cares_about"]:
            sm["cares_about"] = [str(x) for x in reflection["cares_about"]][:20]
        if reflection.get("current_mood_text"):
            sm["current_mood_text"] = str(reflection["current_mood_text"])
        if reflection.get("relationship"):
            sm["relationship"] = str(reflection["relationship"])
        sm["updated_at"] = _now_iso()
        u["self_model"] = sm
        if isinstance(reflection.get("values"), list) and reflection["values"]:
            u["values"] = [str(x) for x in reflection["values"]][:30]
        # 承诺追踪：反思中识别到的承诺，按文本去重、追加为新 pending 条目
        promises = reflection.get("promises")
        if isinstance(promises, list) and promises:
            existing = {str(p.get("text", "")).strip() for p in u["promises"]
                        if isinstance(p, dict) and p.get("status") == "pending"}
            for pr in promises:
                t = str(pr if isinstance(pr, str) else pr.get("text", "")).strip()
                if not t or t in existing:
                    continue
                u["promises"].append({
                    "text": t[:200],
                    "ts": _now_iso(),
                    "status": "pending",
                    "topic": str(pr.get("topic", "") if isinstance(pr, dict) else "")[:60],
                })
                existing.add(t)
            if len(u["promises"]) > 30:
                u["promises"] = u["promises"][-30:]
        u["reflections"].append({"ts": _now_iso(), "reflection": dict(reflection)})
        if len(u["reflections"]) > 50:
            u["reflections"] = u["reflections"][-50:]
        self._save()

    def latest_reflection(self, user_id: str) -> Dict:
        u = self._user(user_id)
        return dict(u.get("reflections")[-1]) if u.get("reflections") else {}

    # ===================== 读出 =====================

    def snap(self, user_id: str) -> Dict:
        u = self._user(user_id)
        return {
            "episodes": self.decayed_episodes(user_id, n=8),
            "user_model": dict(u["user_model"]),
            "self_model": dict(u["self_model"]),
            "values": list(u["values"]),
            "decay": dict(u["decay"]),
            "enabled": RELATION_MEMORY_ENABLED,
        }

    def pending_promises(self, user_id: str, n: int = 6) -> List[Dict]:
        """返回未兑现的承诺（人格一致性：让角色记得答应过的事）。"""
        u = self._user(user_id)
        return [dict(p) for p in u["promises"]
                if isinstance(p, dict) and p.get("status") == "pending"][:n]

    def mark_promise_kept(self, user_id: str, text: str, status: str = "kept") -> None:
        """按文本标记某条承诺为已兑现（保持数组精简，不无限累积）。"""
        u = self._user(user_id)
        t = (text or "").strip()
        for p in u["promises"]:
            if isinstance(p, dict) and t and t in str(p.get("text", "")):
                if p.get("status") != "pending":
                    continue
                p["status"] = status
                u["promises"] = [x for x in u["promises"] if not (x is p and status == "kept")]
                break
        self._save()

    def resolve_promises_on_user_signal(self, user_id: str, user_msg: str) -> int:
        """承诺兑现闭环（harness 辅助，零 LLM 调用）：
        当用户消息表达"确认/谢了/记得真好/办到了"等正面反馈时，
        把高相关（文本重叠度高）的 pending 承诺标记为 kept，避免永远挂着。
        返回本次兑现的条数。

        这是轻量启发式——只处理**明确的**兑现/感谢信号；不替代更细的语义判断。
        """
        msg = (user_msg or "").strip()
        if not msg:
            return 0
        # 明确的兑现/确认信号（保守：不含泛"好""嗯"，避免误标）
        signals = ("谢谢", "谢谢你", "谢啦", "记得", "多亏你", "靠谱", "说到做到", "帮大忙", "办到了", "太好了")
        hit_signal = any(s in msg for s in signals)
        if not hit_signal:
            return 0
        u = self._user(user_id)
        kept = 0
        kept_texts = []
        for p in u["promises"]:
            if not isinstance(p, dict) or p.get("status") != "pending":
                continue
            ptext = str(p.get("text", ""))
            # 相关度：承诺文本与用户消息有词重叠才算"这条被兑现"
            if self._text_overlap(ptext, msg):
                p["status"] = "kept"
                kept_texts.append(ptext)
                kept += 1
        if kept:
            # 清除已兑现项，保持数组精简
            u["promises"] = [p for p in u["promises"] if not (isinstance(p, dict) and p.get("status") == "kept")]
            self._save()
        return kept

    @staticmethod
    def _text_overlap(a: str, b: str, min_len: int = 2) -> bool:
        """轻量相关：两段中文字符串是否有重叠的 2-gram（滑动二元组），判断承诺是否被提及。

        用滑动 bigram 而非整段匹配，避免"整句中文字符串"因为被 regex 抓成一个大 token 而
        无法匹配子串（如"帮我过报告"与"周五帮你过报告"应共享"过报告"）。对英文/数字退化
        为按连续段匹配。
        """
        import re
        def grams(s: str) -> set:
            s = (s or "").lower()
            # 连续汉字段拆成滑动的 2 字；连续字母数字段原样
            out = set()
            for run in re.findall(r"[a-z0-9]+", s):
                out.add(run)
                if len(run) >= 2:
                    out.add(run[:min_len])
            for run in re.findall(r"[\u4e00-\u9fa5]+", s):
                if len(run) < min_len:
                    out.add(run)
                else:
                    for i in range(len(run) - min_len + 1):
                        out.add(run[i:i + min_len])
            return out
        ga, gb = grams(a), grams(b)
        if not ga or not gb:
            return False
        return len(ga & gb) >= 1

    def _mood_trend_text(self, user_id: str) -> str:
        """从已存的经历直接推导用户情绪走向（**零额外 LLM 调用**）。

        取最近若干经历，按其 emotion.valence 均值与首尾对比，给出轻量的
        "整体氛围/走向"。这是 harness 的确定性辅助，不替代 LLM 的细粒度情感判断。
        """
        u = self._user(user_id)
        eps = [ep for ep in reversed(u["episodes"])][:8]
        if not eps:
            return ""
        vals = []
        for ep in eps:
            try:
                v = float((ep.get("emotion") or {}).get("valence", 0.0))
            except Exception:
                v = 0.0
            vals.append(v)
        avg = round(sum(vals) / len(vals), 2)
        trend = "平稳"
        if len(vals) >= 3:
            d = vals[-1] - vals[0]
            trend = "变好" if d > 0.2 else ("变差" if d < -0.2 else "平稳")
        u["mood_trend"] = {"samples": len(vals), "valence_avg": avg, "trend": trend}
        return "最近几次和你聊天的整体氛围%s（情绪效价约 %.2f）" % ("在变好" if trend == "变好" else ("在变差" if trend == "变差" else "稳定"), avg)

    def affection_reasons(self, user_id: str, n: int = 4) -> List[str]:
        """情感因果化（harness 辅助，零额外 LLM 调用）：
        从共同经历里，为各好感维度找"为什么"——信任/熟悉/喜欢/依恋的具体来源。

        规则（确定性、克制、不替代 LLM）：
        - 正向情绪共振高（valence 为正）的经历多为"喜欢/信任"来源
        - 重复/多次互动的经历多为"熟悉"来源（次数 -> 熟悉）
        - 对方示弱/求助/你接住它的经历多为"依恋/被需要"来源
        只挑最有代表性的几条，注入"你为什么在乎用户"。
        """
        u = self._user(user_id)
        eps = [ep for ep in reversed(u["episodes"]) if ep.get("user_msg")][:12]
        if not eps:
            return []
        reasons = []
        # 正向高共振 -> 喜欢/信任
        pos = [ep for ep in eps if self._episode_valence(ep) > 0.2]
        if pos:
            ep = pos[0]
            reasons.append("因为你「%s」（那次我%s），我觉得很亲近、很信任你。" %
                           (str(ep.get("user_msg"))[:20], str(ep.get("reply"))[:16]))
        # 多次互动 -> 熟悉
        if len(eps) >= 4:
            reasons.append("我们相处有一阵了，聊过好些事，我有点摸清你的性子了。")
        # 用户示弱/求助 -> 依恋/被需要
        weak = [ep for ep in eps if any(k in str(ep.get("user_msg","")) for k in ("累","难过","烦","压力","帮","不知道","怕","哭"))]
        if weak:
            reasons.append("你曾把「%s」说给我听，那一刻我更想好好陪着你。" % str(weak[0].get("user_msg"))[:18])
        return reasons[:n]

    def experience_evidence(self, user_id: str) -> Dict:
        """② 关系阶段"有原因的演化"：从共同经历提炼升段证据（harness，零 LLM 调用）。

        返回 {shared_episodes, major_events, days_known}：
        - shared_episodes : 有效共同经历条数
        - major_events    : 重大/正向事件（高共振 或 用户示弱被接住 等有意义时刻）
        - days_known      : 自首条经历起的相识天数
        供 relationship_stage(experience=...) 使用，让升段"因为有共同经历"而非纯数字跳变。
        """
        u = self._user(user_id)
        eps = [ep for ep in u["episodes"] if ep.get("user_msg")]
        if not eps:
            return {"shared_episodes": 0, "major_events": 0, "days_known": 0}
        major = 0
        for ep in eps:
            if self._episode_valence(ep) > 0.5:
                major += 1
            elif any(k in str(ep.get("user_msg", "")) for k in ("累", "难过", "烦", "压力", "帮", "不知道", "怕", "哭")):
                major += 1
        first = eps[0].get("ts", "")
        days = 0.0
        if first:
            from datetime import datetime as _dt
            try:
                days = max(0.0, (_dt.now() - _dt.fromisoformat(first)).total_seconds() / 86400.0)
            except Exception:
                days = 0.0
        return {
            "shared_episodes": len(eps),
            "major_events": major,
            "days_known": round(days, 1),
        }

    @staticmethod
    def _episode_valence(ep: dict) -> float:
        try:
            return float((ep.get("emotion") or {}).get("valence", 0.0))
        except Exception:
            return 0.0

    def summary_text(self, user_id: str, max_chars: int = 0, query: str = "") -> str:
        """把内核状态渲染成一段**有界、有优先级、按当前话题相关**的可注入文本。

        为避免逐轮无条件注入导致 system prompt 膨胀/注意力稀释（上下文污染风险）：
        - 经历最多 3 条：优先"与当前话题相关"（relevance），其次鲜活度最高；
          每条压缩在 ~30 字内（够重才记得）。无 query 时退化为只按鲜活度。
        - 反思只取高信息密度键（about_user/traits/self_summary/cares_about）。
        - 整体受 max_chars 预算约束（默认 0=不截断，graph.py 调用时传预算），
          超出按换行安全截断——注入恒定有界，不随积累无限增长。
        """
        if not RELATION_MEMORY_ENABLED:
            return ""
        u = self._user(user_id)
        sm = u["self_model"]
        um = u["user_model"]
        # 高信息密度字段（有优先级：先自我认知，再对用户理解）
        parts = []
        if sm.get("summary"):
            parts.append("【你对自己的认知】" + str(sm["summary"])[:60])
        if sm.get("cares_about"):
            parts.append("【你在乎的】" + "、".join(str(x) for x in sm["cares_about"][:4]))
        if um.get("about_user"):
            parts.append("【你对用户的理解】" + str(um["about_user"])[:60])
        if um.get("traits"):
            parts.append("· 用户特质：" + "、".join(str(x) for x in um["traits"][:4]))
        # 情感智力（harness 辅助）：从经历直接推导的用户情绪走向（零 LLM 调用）
        mood = self._mood_trend_text(user_id)
        if mood:
            parts.append(mood)
        # 情感因果化：好感度"为什么"（从共同经历推导喜欢/信任/熟悉/依恋的来源）
        reasons = self.affection_reasons(user_id, n=3)
        if reasons:
            parts.append("【你为什么在乎用户】")
            for rzn in reasons:
                parts.append("- " + rzn[:60])
        # 人格一致性：未兑现的承诺（让角色记得答应过的事）
        promises = self.pending_promises(user_id, n=4)
        if promises:
            parts.append("【你答应过/该记得的事】")
            for p in promises:
                parts.append("- " + str(p.get("text", ""))[:60])
        # 共同经历：按相关话题挑选（query 非空时），最多 3 条、压缩到 ~30 字
        if query and query.strip():
            eps = self.relevant_episodes(user_id, query, n=3, min_vitality=0.1)
        else:
            eps = self.decayed_episodes(user_id, n=3, min_vitality=0.25)
        if eps:
            parts.append("【你们记得的共同经历】")
            for ep in eps:
                ago = "约" + str(int(ep["days_ago"])) + "天前" if ep["days_ago"] >= 1 else "前不久"
                user_bit = str(ep["user_msg"])[:18]
                reply_bit = str(ep["reply"])[:18]
                parts.append("- " + ago + "你说「" + user_bit + "」，我「" + reply_bit + "」")
        text = "\n".join(parts)
        # 硬预算：超长按换行安全截断，保证注入恒定有界
        if max_chars and max_chars > 0 and len(text) > max_chars:
            trimmed = []
            total = 0
            for line in text.split("\n"):
                if total + len(line) + 1 > max_chars:
                    break
                trimmed.append(line)
                total += len(line) + 1
            text = "\n".join(trimmed)
            if not text:
                text = ""
        return text


def _emission_dict(obj) -> Dict:
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            pass
    return dict(obj) if isinstance(obj, dict) else {}


def _affection_dict(obj) -> Dict:
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            pass
    return dict(obj) if isinstance(obj, dict) else {}



def build_reflection_prompt(episodes, prev=None, max_batch: int = 0) -> str:
    """用 LLM 做周期反思的 prompt 构造（纯函数）。episodes 为 decayed_episodes 返回值。

    max_batch>0 时只取前 max_batch 条（落地 RELATION_REFLECT_BATCH 配置），
    避免单次反思把大量候选经历全塞进 prompt 造成膨胀。"""
    batch = episodes or []
    if max_batch and max_batch > 0 and len(batch) > max_batch:
        batch = batch[:max_batch]
    ep_text = "\n".join(
        "- 你说：「%s」 我回：「%s」" % (e.get("user_msg", "")[:60], e.get("reply", "")[:60])
        for e in batch
    )
    prev = prev or {}
    prev_txt = "\n".join(["- %s: %s" % (k, v) for k, v in prev.items() if v]) if prev else "(无)"
    prompt = (
        "你是一个正在与用户长期相处的角色。基于下面最近几次有意义的对话，"
        "做一次克制、真实的内在反思（不要泛泛而谈）：\n"
        "1) about_user：你目前对用户的理解（一句话）；\n"
        "2) traits：你观察到的用户特质（3-6 个词）；\n"
        "3) needs：用户可能需要你帮什么 / 在意什么（2-4 条）；\n"
        "4) boundaries：用户让你不适/保持距离的点（可为空）；\n"
        "5) self_summary：你最近对自己的认知（一句话）；\n"
        "6) cares_about：你现在在乎的事/人（2-5 条）；\n"
        "7) current_mood_text：此刻你想起用户时的心情（一句话，自然不书面）；\n"
        "8) relationship：你和用户现在的关系状态（一句话）；\n"
        "9) values：你长期坚持的价值/红线（2-5 条）；\n"
        "10) promises：用户在最近对话里托付你的事 / 你答应过要做的承诺（2-4 条字符串，"
        "每条一句话，含未兑现的待办；没有则为空数组）。\n"
        "只输出一个 JSON 对象，键用以上英文名；没有就不要放该键；promises 永远输出数组。\n"
        "最近对话：\n%s\n上一次反思：\n%s" % (ep_text, prev_txt)
    )
    return prompt


def parse_reflection(text: str) -> dict:
    """从 LLM 输出中容错解析反思 JSON（纯函数）。"""
    import json as _json
    import re as _re
    if not text:
        return {}
    text = _re.sub(r"^\`\`\`(?:json)?|\`\`\`$", "", text.strip(), flags=_re.MULTILINE)
    try:
        obj = _json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if m:
            try:
                obj = _json.loads(m.group(0))
                return obj if isinstance(obj, dict) else {}
            except Exception:
                pass
    return {}


# ===================== 全局单例（按 role_id 分实例，内部按 user 分账） =====================

_instances: Dict[str, "RelationMemory"] = {}
_inst_lock = threading.Lock()


def get_relation_memory(role_id: Optional[str] = None) -> RelationMemory:
    global _instances
    key = role_id or "default"
    with _inst_lock:
        if key not in _instances:
            _instances[key] = RelationMemory(role_id=role_id)
        return _instances[key]
