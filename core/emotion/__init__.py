"""情感与好感度系统"""
from core.emotion.affection import AffectionState
from core.emotion.emotion import (
    EmotionState, EmotionAnalyzer, emotion_to_prompt_text,
    relationship_stage, relation_to_prompt_text,
)
from core.emotion.emotion_store import EmotionStore

__all__ = [
    "AffectionState",
    "EmotionState",
    "EmotionAnalyzer",
    "EmotionStore",
    "emotion_to_prompt_text",
    "relationship_stage",
    "relation_to_prompt_text",
]
