"""
情感状态 + 情感分析器（模式 B：先分析后生成）

模式 B 两阶段：
  第一阶段：LLM 只输出 JSON（情感 + 好感度更新）
  第二阶段：基于分析结果 + 记忆 + 角色人设生成回复
"""
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional

from core.adapters import LLMAdapter
from core.emotion.affection import AffectionState


@dataclass
class EmotionState:
    primary: str = "平静"          # 主要情绪（开心/难过/兴奋/平静/生气/焦虑等）
    intensity: float = 0.5          # 情绪强度 0-1
    valence: float = 0.0            # 效价 -1（负面）~ +1（正面）
    description: str = ""           # 情绪描述
    updated_at: str = ""

    def __post_init__(self):
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "EmotionState":
        def clamp(v, low, high, default):
            try:
                return max(low, min(high, float(v)))
            except (TypeError, ValueError):
                return default
        return cls(
            primary=str(data.get("primary", "平静")),
            intensity=clamp(data.get("intensity"), 0.0, 1.0, 0.5),
            valence=clamp(data.get("valence"), -1.0, 1.0, 0.0),
            description=str(data.get("description", "")),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )

    @classmethod
    def default(cls) -> "EmotionState":
        return cls()


class EmotionAnalyzer:
    """
    模式 B 情感/好感度分析器。
    第一阶段：给 LLM 一个分析 prompt，要求只输出 JSON：
      {"emotion": {...}, "affection": {...}, "needs_tool": bool, "tool_decision": "..."}
    """

    # 用 __AFFECTION_JSON__ / __LAST_TURNS__ 占位，避免与 JSON 字面量花括号冲突
    ANALYSIS_PROMPT = """你是情感分析引擎。根据用户最新消息和对话上下文，输出一个 JSON（不要输出其他内容）：

{
  "emotion": {"primary": "情绪名", "intensity": 0.0-1.0, "valence": -1.0到1.0, "description": "一句话描述"},
  "affection": {
    "liking": 0.0-1.0, "trust": 0.0-1.0, "familiarity": 0.0-1.0,
    "respect": 0.0-1.0, "interest": 0.0-1.0, "attachment": 0.0-1.0
  },
  "needs_tool": false,
  "tool_decision": "不需要工具，或 search_web(理由)，或 control_pc(理由)"
}

规则：
- emotion 反映用户当前情绪（对用户情绪的感知），primary 选一个最贴切的中文情绪词。
- affection 是"角色对用户"的好感度，只在有明显变化时大改，否则维持当前值。
- intensity/valence 范围必须合法。
- needs_tool 为 true 仅当用户明确要求实时信息或操作电脑。
当前好感度：__AFFECTION_JSON__
最近的对话摘要：__LAST_TURNS__
"""

    def __init__(self, tool_adapter: LLMAdapter):
        self.tool_adapter = tool_adapter

    def analyze(self, user_msg: str, emotions: EmotionState, affection: AffectionState,
                last_turns: List[str] = None) -> Dict:
        """第一阶段：返回分析的 JSON dict"""
        prompt = (
            self.ANALYSIS_PROMPT
            .replace("__AFFECTION_JSON__", json.dumps(affection.to_dict(), ensure_ascii=False))
            .replace("__LAST_TURNS__", "\n".join(last_turns[-4:]) if last_turns else "(无)")
        )
        full = [{"role": "system", "content": prompt},
                {"role": "user", "content": user_msg}]
        try:
            text = self.tool_adapter.chat(full).strip()
            text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
            data = json.loads(_extract_json(text))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def merge_emotion(state: EmotionState, data: dict) -> EmotionState:
        if not data:
            return state
        return EmotionState.from_dict(data)

    @staticmethod
    def merge_affection(state: AffectionState, data: dict) -> AffectionState:
        if not data:
            return state
        d = state.to_dict()
        for k in ("liking", "trust", "familiarity", "respect", "interest", "attachment"):
            if k in data:
                d[k] = data[k]
        d["updated_at"] = datetime.now().isoformat()
        return AffectionState.from_dict(d)


def _extract_json(text: str) -> str:
    """从文本中提取第一个 JSON 对象（容错）"""
    try:
        json.loads(text)
        return text
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else "{}"


# ===================== 关系养成：好感度 -> 关系阶段 -> 行为差异 =====================

RELATION_STAGES = ["陌生", "熟悉", "亲密", "挚友"]


def relationship_stage(affection: AffectionState, experience: Optional[Dict] = None) -> str:
    """由好感度 + 共同经历推导当前关系阶段（陌生 / 熟悉 / 亲密 / 挚友）

    综合 6 维，重点看 familiarity（熟悉）+ attachment（依恋）+ trust（信任）。
    依恋初始低（0.3）需时间积累，天然把"熟识"与"深交"区分开。

    ②"有原因的演化"：experience（可选 dict）提供共同经历的**证据**，让升段"因为有
    共同经历"而非纯数字跳变：
      - shared_episodes  : 有意义的共同经历条数（累计）
      - major_events     : 重大/正向事件数（安慰、一起达成、约定等）
      - days_known       : 相识天数
    当经历证据足够时，可把阶段"提前一档"（有据可依），不会无证据地凭空升级。
    """
    familiar = getattr(affection, "familiarity", 0.5)
    attach = getattr(affection, "attachment", 0.3)
    trust = getattr(affection, "trust", 0.5)

    experience = experience or {}
    shared = int(experience.get("shared_episodes", 0))
    major = int(experience.get("major_events", 0))
    days = float(experience.get("days_known", 0) or 0)

    base = "陌生"
    if familiar >= 0.75 and attach >= 0.75 and trust >= 0.75:
        base = "挚友"
    elif familiar >= 0.6 and attach >= 0.5 and trust >= 0.5:
        base = "亲密"
    elif familiar >= 0.4 and trust >= 0.4:
        base = "熟悉"
    else:
        base = "陌生"

    # 有原因的演化："足够经历"可把阶段提升（封顶挚友），否则维持数字决定的阶段
    stage = base
    if base == "陌生":
        if shared >= 6 and days >= 7:
            stage = "熟悉"
    elif base == "熟悉":
        if (shared >= 15 or major >= 2) and days >= 14:
            stage = "亲密"
    elif base == "亲密":
        if (shared >= 30 or major >= 4) and days >= 30:
            stage = "挚友"
    return stage


def relation_call_name(stage: str, nickname: str = "") -> str:
    """按关系阶段返回角色对用户的称呼风格"""
    if stage == "挚友":
        return ("昵称“%s”或亲密爱称" % nickname) if nickname else "只呼名字/爱称"
    if stage == "亲密":
        return ("昵称“%s”" % nickname) if nickname else "直接称呼名字"
    if stage == "熟悉":
        return ("可称呼“%s”" % nickname) if nickname else "用正常称呼（同学/朋友/你）"
    return "礼貌称呼（名字/同学/你）"


def _stage_behavior_text(stage: str) -> str:
    """按关系阶段给出可落地的行为约束（开放度 / 亲昵度通道）"""
    m = {
        "陌生": (
            "关系还比较生疏：保持礼貌、有分寸，不主动打听私事，"
            "不过分亲昵，保持一点距离感；以建立信任为主。"
        ),
        "熟悉": (
            "已经比较熟络：可以自然地闲聊、开玩笑、分享日常，"
            "能适当表达关心；但仍留有一定个人空间，不过度腻歪。"
        ),
        "亲密": (
            "关系很亲近：可以流露撒娇、小情绪、专属的小互动，"
            "愿意分享自己的心事和偏好，对用户有信任感和依赖感；"
            "称呼可以更亲昵。"
        ),
        "挚友": (
            "你们是无可替代的挚友/灵魂伴侣：毫无保留的信任与默契，"
            "可以无话不谈，会主动挂念、给惊喜、记得彼此的每个重要时刻；"
            "用最亲昵自然的语气。"
        ),
    }
    return m.get(stage, m["陌生"])


def relation_to_prompt_text(affection: AffectionState, nickname: str = "", experience: Optional[Dict] = None) -> str:
    """构造"关系养成"注入块：关系阶段 + 称呼风格 + 行为约束

    让好感度（而不是模型自觉）真正驱动行为差异。
    experience：共同经历证据（可选），用于"有原因的演化"（见 relationship_stage）。
    """
    stage = relationship_stage(affection, experience)
    name_style = relation_call_name(stage, nickname)
    behavior = _stage_behavior_text(stage)
    return f"""
【你与用户的关系（好感度驱动，务必按此把握距离感）】
- 你们当前的关系阶段：{stage}
- 称呼风格：{name_style}
- 相处方式：{behavior}
（这些是由你们相处积累的好感度自然形成的；不要刻意向用户复述"我现在是X阶段"之类的话。）
"""


def emotion_to_prompt_text(emotion: EmotionState, affection: AffectionState) -> str:
    """构造注入 system prompt 的情感+好感度区块"""
    return f"""
【当前对话氛围与好感度】
- 用户当前情绪：{emotion.primary}（强度 {emotion.intensity:.2f}，{("正向" if emotion.valence>=0 else "负向")}）
- 你对用户的好感度：
  · 喜欢 {affection.liking:.2f}  信任 {affection.trust:.2f}  熟悉 {affection.familiarity:.2f}
  · 尊重 {affection.respect:.2f}  兴趣 {affection.interest:.2f}  依恋 {affection.attachment:.2f}
请根据这些自然地调整你的语气，但不要刻意提及数值。
"""
