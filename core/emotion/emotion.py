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
