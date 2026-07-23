# core/emotion/injector.py
from core.emotion.state import EmotionState

def inject_emotion_to_prompt(base_prompt: str, emotion: EmotionState) -> str:
    """
    将当前情感状态注入 System Prompt，并指导 LLM 输出新情感
    """
    emotion_text = f"""
    【当前情感状态】
    - 情绪：{emotion.primary}（强度 {emotion.intensity:.2f}）
    - 心情倾向：{"积极" if emotion.valence > 0 else "消极" if emotion.valence < 0 else "中性"}
    - 简短描述：{emotion.description}

    请根据这个情感状态调整你的语气和回应方式。

    【重要】在回复完成后，请在最后一行用 JSON 格式输出你的新情感状态，格式如下：
    {{
    "primary": "情感类型（自由定义，如 happy/sad/excited/curious 等）",
    "intensity": 0.0-1.0,
    "valence": -1.0-1.0,
    "description": "一句话描述你的情感"
    }}
    将 JSON 放在回复的最后，用 --- 单独一行分隔（前面加空行）。"""
    return base_prompt + "\n" + emotion_text