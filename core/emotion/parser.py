import json
from typing import Tuple, Optional
from core.emotion.state import EmotionState
import re

def parse_emotion_from_reply(reply: str) -> Tuple[str, Optional[EmotionState]]:
    """
    从 LLM 回复中提取情感 JSON，返回清理后的回复和情感状态
    """
    # 匹配 `---` 后面的 JSON 块
    pattern = r'---\s*\n```json\s*(\{.*?\})\s*```'
    match = re.search(pattern, reply, re.DOTALL)
    if not match:
        # 尝试匹配更宽松的格式：直接 `---` 后跟 JSON（不带代码块）
        fallback_pattern = r'---\s*(\{.*?\})'
        match = re.search(fallback_pattern, reply, re.DOTALL)
        if not match:
            return reply, None

    try:
        data = json.loads(match.group(1))
        emotion = EmotionState(
            primary=data.get("primary", "neutral"),
            intensity=float(data.get("intensity", 0.5)),
            valence=float(data.get("valence", 0.0)),
            description=data.get("description", "")
        )
        # 移除情感标签部分（整个 `--- ... ```json ... ```` 块）
        clean_reply = re.sub(pattern, '', reply, flags=re.DOTALL).strip()
        # 如果使用 fallback，移除 `--- { ... }`
        if match and not re.search(r'```json', reply):
            clean_reply = re.sub(fallback_pattern, '', reply, flags=re.DOTALL).strip()
        return clean_reply, emotion
    except (json.JSONDecodeError, ValueError):
        return reply, None