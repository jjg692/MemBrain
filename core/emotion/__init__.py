# core/emotion/__init__.py
from .state import EmotionState
from .injector import inject_emotion_to_prompt
from .parser import parse_emotion_from_reply
from .affection import AffectionState, inject_affection_to_prompt, parse_affection_from_reply

__all__ = [
    "EmotionState",
    "inject_emotion_to_prompt",
    "parse_emotion_from_reply",
    "AffectionState",
    "inject_affection_to_prompt",
    "parse_affection_from_reply"
]
