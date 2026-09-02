"""
Live2D 身体表达 —— B/C 方案核心

目标：让 LLM 作为"大脑"知道自己有一个 Live2D 身体，并能表达肢体/情绪。

- C 方案（默认推荐）：不新增工具。内核把情绪/文本自动映射为行为事件 behavior，
  LLM 只需自然地说话（告知其有身体即可）。
- B 方案：新增 express_body 工具，让 LLM 主动指定表情/动作/强度，
  在回复时显式"指挥"身体，再并入 behavior 广播。

本模块提供：
  EXPRESS_BODY_TOOL        : B 方案的 LLM 工具 schema（注册进 ALL_TOOLS）
  express_body(...)        : B 方案工具的实际实现（记录意图，返回字符串给 LLM）
  merge_express(intent)    : 把 express_body 记录的意图并入 behavior dict（前端 A 层消费）

安全/约束：
- 情绪、动作、表情都用**枚举**约束（不给 LLM 任意参数，防止生成无效/越界值）
- express_body 只是"记录意图"：不执行播放（播放仍由前端 A 层/BodyMapper 驱动），
  返回提示文本告知 LLM 已记录，避免它重复调用或误以为工具做了别的
"""
from typing import Dict, List, Optional

# ===================== 允许的取值范围（供 LLM schema + 校验） =====================

# 动作语义名（与前端 ACTION_MOTION_MAP / BehaviorMapper 对齐，跨模型解析）
EXPRESS_ACTIONS = [
    "wave", "clap", "bow", "shrug", "nod", "wink",
    "cry", "laugh", "smile", "surprised", "sleep", "idle",
]
# 表情名（exp.json 名 / 语义名，前端有别名兜底）
EXPRESS_EXPRESSIONS = [
    "default", "smile01", "smile02", "smile03", "f01", "f02",
    "sad01", "cry01", "angry01", "surprised01", "shame01",
    "serious01", "serious02", "idle01", "kime01",
]
# 情绪主名（中文，与 A 层 BODY_KEY / 内核 EmotionState 对齐）
EXPRESS_EMOTIONS = [
    "开心", "兴奋", "高兴", "愉悦", "平静", "难过", "伤心",
    "沮丧", "生气", "愤怒", "惊讶", "担心", "害羞", "撒娇",
    "感动", "满足", "疲惫", "困",
]


def express_body(
    emotion: str = "平静",
    actions: Optional[List[str]] = None,
    expression: str = "默认",
    intensity: float = 0.5,
) -> str:
    """B 方案：让 LLM 主动指定身体的情绪/动作/表情表达。

    只记录意图（写入模块级 pending_express 供 merge_express 并入行为事件），
    不直接控制前端播放。播放仍由内核/前端根据意图派发。
    """
    global _pending_express
    # 校验 + 归一化
    emo = emotion if emotion in EXPRESS_EMOTIONS else "平静"
    acts = [a for a in (actions or []) if a in EXPRESS_ACTIONS][:3]
    expr = expression if expression in EXPRESS_EXPRESSIONS else "default"
    it = max(0.0, min(1.0, float(intensity) if _isfinite(intensity) else 0.5))

    _pending_express = {
        "emotion": emo,
        "actions": acts,
        "expression": expr,
        "intensity": it,
    }
    # 返回给 LLM 的确认文本（自然，不生硬；同时防止它重复调用）
    line = f"已记录你的情绪表达（{emo}" + (f"，动作：{'、'.join(acts)}" if acts else "") + "）。就这样自然地继续说即可，无需再提这件事。"
    return line


_pending_express: Optional[Dict] = None


def _isfinite(x) -> bool:
    try:
        import math
        return math.isfinite(float(x))
    except Exception:
        return False


def merge_express(behavior: Dict, emotion_override: Optional[Dict] = None) -> Dict:
    """把 express_body 记录的意图并入 behavior dict（若无 pending 则原样返回）。

    - emotion: 以 LLM 指定的情绪主名覆盖（供前端 A 层持续驱动脸部）
    - actions/expression: 覆盖 behavior 对应字段（LLM 主动指定的优先）
    - intensity: 并入 emotion.intensity（A 层据此调制浓淡）
    behavior 为 None 时，构造一个新 dict。
    """
    global _pending_express
    b = dict(behavior or {})
    p = _pending_express
    if not p:
        return b
    # 取一次即清空（一次性生效，避免后续轮次残留）
    _pending_express = None

    emo = dict(b.get("emotion") or {})
    if p.get("emotion"):
        emo["primary"] = p["emotion"]
    if "intensity" in p:
        emo["intensity"] = round(p["intensity"], 3)
    b["emotion"] = emo
    if p.get("expression") and p["expression"] != "default":
        b["expression"] = p["expression"]
    if p.get("actions"):
        # 与自动推导的动作合并去重，LLM 指定的放前面
        merged = list(p["actions"])
        for a in (b.get("actions") or []):
            if a not in merged:
                merged.append(a)
        b["actions"] = merged[:3]
    return b


# ===================== LLM 工具 Schema（B 方案注册用） =====================
EXPRESS_BODY_TOOL = {
    "type": "function",
    "function": {
        "name": "express_body",
        "description": (
            "【你有 Live2D 身体】调用它以主动指定你此刻想用身体表达的情绪、表情、动作"
            "（如开心时挥手、惊讶时睁眼）。仅当你真的想强调某个肢体/表情表达时调用；"
            "不需要时不要调用。调用后正常说出你的话即可。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "emotion": {
                    "type": "string",
                    "enum": EXPRESS_EMOTIONS,
                    "description": "此刻想表达的主导情绪",
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "string", "enum": EXPRESS_ACTIONS},
                    "description": "想做的动作（可多选，最多3个）",
                },
                "expression": {
                    "type": "string",
                    "enum": EXPRESS_EXPRESSIONS,
                    "description": "想做的表情",
                },
                "intensity": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "情绪强度 0~1，越高表现得越明显",
                },
            },
            "required": ["emotion"],
        },
    },
}

EXPRESS_BODY_REGISTRY = {"express_body": express_body}
