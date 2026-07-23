# core/emotion/affection.py
"""
好感度系统（6维度）
- liking: 喜欢程度
- trust: 信任程度
- familiarity: 熟悉程度
- respect: 尊重程度
- interest: 兴趣程度
- attachment: 依恋程度
所有值范围 0.0-1.0
"""
from dataclasses import dataclass
from typing import Optional
import json
import re

@dataclass
class AffectionState:
    liking: float = 0.5
    trust: float = 0.5
    familiarity: float = 0.5
    respect: float = 0.5
    interest: float = 0.5
    attachment: float = 0.3  # 初始较低，需时间积累

    def to_dict(self) -> dict:
        return {
            "liking": self.liking,
            "trust": self.trust,
            "familiarity": self.familiarity,
            "respect": self.respect,
            "interest": self.interest,
            "attachment": self.attachment
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AffectionState":
        return cls(
            liking=float(data.get("liking", 0.5)),
            trust=float(data.get("trust", 0.5)),
            familiarity=float(data.get("familiarity", 0.5)),
            respect=float(data.get("respect", 0.5)),
            interest=float(data.get("interest", 0.5)),
            attachment=float(data.get("attachment", 0.3))
        )

    @classmethod
    def default(cls) -> "AffectionState":
        return cls()

def inject_affection_to_prompt(base_prompt: str, affection: AffectionState) -> str:
    """将好感度信息注入 System Prompt"""
    def desc(v: float, high="较高", mid="中等", low="较低"):
        if v > 0.65: return high
        if v > 0.35: return mid
        return low

    text = f"""
    【你对用户的好感度】
    - 喜欢程度：{affection.liking:.2f}（{desc(affection.liking, "很喜欢", "一般", "不太喜欢")}）
    - 信任程度：{affection.trust:.2f}（{desc(affection.trust, "很信任", "一般", "不太信任")}）
    - 熟悉程度：{affection.familiarity:.2f}（{desc(affection.familiarity, "很熟悉", "一般", "不太熟悉")}）
    - 尊重程度：{affection.respect:.2f}（{desc(affection.respect, "很尊重", "一般", "不太尊重")}）
    - 兴趣程度：{affection.interest:.2f}（{desc(affection.interest, "很感兴趣", "一般", "不太感兴趣")}）
    - 依恋程度：{affection.attachment:.2f}（{desc(affection.attachment, "很依恋", "一般", "不太依恋")}）

    请根据这些好感度调整你的语气和互动方式。
    在回复完成后，请用 JSON 格式输出更新后的好感度（如果变化了），放在回复末尾，用 `---AFFECTION---` 分隔。
    格式：
    {{
    "liking": 0.0-1.0,
    "trust": 0.0-1.0,
    "familiarity": 0.0-1.0,
    "respect": 0.0-1.0,
    "interest": 0.0-1.0,
    "attachment": 0.0-1.0
    }}
    如果某项未变化，可以不写，系统默认保持原值。
    """
    return base_prompt + "\n" + text

def parse_affection_from_reply(reply: str) -> tuple:
    """从回复中解析好感度 JSON，返回 (清理后的回复, AffectionState 或 None)"""
    pattern = r'---AFFECTION---\s*\njson\s*(\{.*?\})\s*'
    match = re.search(pattern, reply, re.DOTALL)
    if not match:
        fallback = r'---AFFECTION---\s({.?})'
    match = re.search(fallback, reply, re.DOTALL)
    if not match:
        return reply, None
    try:
        data = json.loads(match.group(1))
        affection = AffectionState.from_dict(data)
        clean_reply = re.sub(pattern, '', reply, flags=re.DOTALL).strip()
        if not re.search(r'```json', reply):
            clean_reply = re.sub(fallback, '', reply, flags=re.DOTALL).strip()
            return clean_reply, affection
    except (json.JSONDecodeError, ValueError):
        return reply, None