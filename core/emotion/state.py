# core/emotion/state.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class EmotionState:
    """情感状态，完全由 LLM 定义"""
    primary: str = "neutral"          # 情绪类型（LLM 自由决定，如 happy/sad/excited）
    intensity: float = 0.5            # 强度 0-1
    valence: float = 0.0              # 正负倾向 -1 到 1
    description: str = ""             # 简短描述（LLM 生成）

    @classmethod
    def default(cls) -> "EmotionState":
        return cls(primary="neutral", intensity=0.5, valence=0.0, description="平静")