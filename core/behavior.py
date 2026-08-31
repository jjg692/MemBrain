"""
行为映射器（BehaviorMapper）——窗口 A 产出、窗口 B 消费

用途：把「角色情绪 + 本次回复文本」映射成宠物壳可直接消费的标准行为事件 `behavior`，
让桌面宠物（Live2D 壳）的表情 / 口型 / 动作由内核输出，而不是由前端靠猜。契约见
docs/dual-window-contract.md §3.2。

设计原则（遵契约 §3.2 规则 3）：
- 纯函数、无副作用：`derive` 只依赖传入的 emotion + 文本，不访问任何外部状态 / IO。
- 输出结构固定，且每次调用都返回完整字段（便于壳端 `if behavior` 判断后直接使用）：
    { "emotion": {...}, "expression": str, "mouth_open": float,
      "actions": [str], "pitch_hint": float|null }
- 文本关键词只作为决策的辅助信号；情绪（agent 已算好的 emotion state）是主要信号。
"""
from typing import Dict, List, Optional


# ===================== 表情映射（情绪 -> live2d exp.json 表情名） =====================

# primary 情绪关键字 -> expression 名（映射到角色 live2d <model>/expressions/<name>.exp.json）
_EMOTION_EXPRESSION = {
    "开心": "smile01",
    "兴奋": "f01",
    "高兴": "smile01",
    "愉悦": "smile02",
    "难过": "sad01",
    "伤心": "sad01",
    "沮丧": "serious01",
    "生气": "angry01",
    "愤怒": "angry01",
    "平静": "default",
    "平和": "default",
    "焦虑": "surprised01",
    "担心": "serious02",
    "惊讶": "surprised01",
    "害羞": "shame01",
    "撒娇": "f02",
    "感动": "smile03",
    "满足": "smile02",
    "疲惫": "default",
    "困": "default",
}

# 负面 / 正面情绪分组（用于 pital hint 与动作偏向）
_NEGATIVE = {"难过", "伤心", "沮丧", "生气", "愤怒", "焦虑", "担心", "惊讶", "疲惫"}
_POSITIVE = {"开心", "兴奋", "高兴", "愉悦", "感激", "感动", "满足", "撒娇", "害羞"}

# 文本情绪信号词 -> (expression, intensity_bias) ，作为 emotion 主信号的辅助
_TEXT_HINTS = [
    ("哈哈", "f01", 0.1), ("哈哈哈", "f01", 0.15), ("嘿嘿", "smile02", 0.1),
    ("哭", "sad01", -0.1), ("难过", "sad01", -0.1), ("伤心", "sad01", -0.1),
    ("生气", "angry01", 0.0), ("讨厌", "angry01", 0.0),
    ("喜欢", "smile03", 0.1), ("超喜欢", "f01", 0.2),
    ("加油", "kime01", 0.0), ("拜托", "f02", 0.1), ("呜", "cry01", -0.1),
    ("?" , "surprised01", 0.0), ("？", "surprised01", 0.0),
    ("！", "f01", 0.05), ("!", "f01", 0.05),
]

# ===================== 动作映射（文本/情绪 -> 可叠加动作名） =====================

_ACTION_WAVE = ("挥手", "拜拜", "再见", "嗨", "你好", "哈喽", "早上好", "晚上好")
_ACTION_NOD = ("嗯", "对的", "没错", "明白", "懂了", "知道", "当然", "好呀")
_ACTION_CLAP = ("好棒", "太棒", "厉害", "恭喜", "好耶", "wow", "哇")
_ACTION_SHRUG = ("不知道", "不清楚", "也许", "大概", "不懂", "算啦")
_ACTION_BOW = ("谢谢", "感谢", "拜托", "对不起", "抱歉")


def _pick_expression(primary: str, text: str) -> str:
    """选表情：文本强信号优先，否则按情绪。"""
    # 1) 文本强情绪信号（叠加情感词）
    for word, expr, _bias in _TEXT_HINTS:
        if word in text:
            # 如果同时命中 "哈" 这类中性偏正，最后命中为准（后写优先）
            pass
    # 从后往前找第一个命中的文本信号（后写覆盖前写，让"哈哈"优先级高于前置）
    for word, expr, _bias in reversed(_TEXT_HINTS):
        if word in text and word not in ("？", "?", "！", "!"):
            return expr
    # 标点信号强于"平静/平和"这类弱情绪默认
    if text and ("?" in text or "？" in text):
        return "surprised01"
    if text and ("!" in text or "！" in text):
        return "f01"
    # 情绪主信号（弱情绪"平静/平和"兜底，不覆盖上面的标点信号）
    for k, expr in _EMOTION_EXPRESSION.items():
        if k in (primary or ""):
            return expr
    return "default"


def _pick_actions(primary: str, text: str) -> List[str]:
    """选动作：可叠加，最多 2 个。"""
    actions: List[str] = []
    for name, words in (("wave", _ACTION_WAVE), ("clap", _ACTION_CLAP),
                        ("shrug", _ACTION_SHRUG), ("bow", _ACTION_BOW), ("nod", _ACTION_NOD)):
        if name == "nod" and len(actions) >= 1:
            continue  # nod 作兜底，尽量不叠加
        if any(w in text for w in words):
            actions.append(name)
            if len(actions) >= 2:
                break
    return actions


def _mouth_open(primary: str, text: str, intensity: float) -> float:
    """估算口型开合 0-1：积极/兴奋时偏高，简短语气偏低，默认中低。"""
    base = 0.35
    if primary in _NEGATIVE:
        base = 0.25
    elif primary in _POSITIVE:
        base = 0.5
    # 感情号/强调推高
    if "！" in text or "!" in text or "哈哈" in text or "啊" in text:
        base += 0.15
    # 简短语气词降低
    if text in ("嗯", "好", "对", "行", "哼", "哦"):
        base = 0.15
    return round(max(0.0, min(1.0, base + (intensity - 0.5) * 0.2)), 3)


def _pitch_hint(primary: str) -> Optional[float]:
    """语音情感基调（供 TTS）：>1 偏快高，<1 偏慢低，None=中性。"""
    if primary in _POSITIVE:
        return 1.15
    if primary in _NEGATIVE:
        return 0.85
    return None


class BehaviorMapper:
    """把 (情绪 + 回复文本) 映射为宠物壳可消费的 behavior 事件。纯函数。"""

    @staticmethod
    def derive(reply: str, emotion: Optional[Dict] = None) -> Dict:
        """核心入口。

        Args:
            reply: Agent 生成的最终回复文本（可不传/为空，空则按情绪映射）。
            emotion: 情绪 dict，通常来自 EmotionState.to_dict()：
                {"primary": str, "valence": float, "intensity": float, ...}
                可为 None（退化为文本信号）。

        Returns:
            完整 behavior dict（字段齐全，供壳端直接使用）。
        """
        reply = reply or ""
        emotion = emotion or {}
        primary = str(emotion.get("primary") or "平静")
        valence = emotion.get("valence")
        try:
            valence = float(valence)
        except (TypeError, ValueError):
            valence = 0.0
        try:
            intensity = float(emotion.get("intensity") or 0.5)
        except (TypeError, ValueError):
            intensity = 0.5

        expression = _pick_expression(primary, reply)
        actions = _pick_actions(primary, reply)
        mouth = _mouth_open(primary, reply, intensity)
        pitch = _pitch_hint(primary)

        return {
            "emotion": {
                "primary": primary,
                "valence": round(max(-1.0, min(1.0, valence)), 3),
                "intensity": round(max(0.0, min(1.0, intensity)), 3),
            },
            "expression": expression,
            "mouth_open": mouth,
            "actions": actions,
            "pitch_hint": pitch,
        }

    # 便捷：从 EmotionState 对象取 emotion dict
    @staticmethod
    def derive_from_state(reply: str, emotion_state) -> Dict:
        if emotion_state is not None and hasattr(emotion_state, "to_dict"):
            emotion = emotion_state.to_dict()
        else:
            emotion = None
        return BehaviorMapper.derive(reply, emotion)
