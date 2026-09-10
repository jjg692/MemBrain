"""
目标提炼器（Goal Extractor）——让角色能从对话里"自己发现"用户的长期目标。

补上"目标记忆"骨架的最后一块：不只是能存/能主动提/能后台管，还能在聊天中
自动识别"用户正在追求的目标 / 进度更新 / 达成 / 搁置"，并把它们写入
RelationMemory 的 goals 账本（与手动添加共用同一存储与同一套鲜活度衰减）。

设计（对齐全项目：LLM 优先 + 不伪造 + 可开关 + 失败降级 + 节省调用）：
- 先做**轻量关键词闸门**：只有消息疑似目标性（浓缩的、未来向的追求）才值得让 LLM 判断。
  纯闲聊 / 过去的回忆不触发，避免每句对话都调 LLM。
- LLM 只负责"判断 + 结构化输出"，本模块只做提取与写库。
- 节流：两次真正的 LLM 调用之间受 GOAL_EXTRACT_INTERVAL_SEC 约束（防高频膨胀）。
- 全程 try/except，失败/不可用一律静默（在 _after_reply 后台线程跑，不阻塞回复）。
"""
import json
import re
import time
from typing import Optional

from core.config import (
    GOAL_EXTRACT_ENABLED,
    GOAL_EXTRACT_INTERVAL_SEC,
    GOAL_EXTRACT_MIN_KEYWORDS,
)
from core.logger import log_debug
from core.relation_memory import get_relation_memory  # noqa: F401 （测试可 monkeypatch）

# 目标性关键词（初次闸门）：命中才值得让 LLM 判断。
# 刻意用"未来/持续追求"含义较明确的词组，避免"我今天吃了饭""帮我查天气"触发。
# 即使命中也会再经 LLM 判断 involve_goal，故这里只需"够可能"即可。
_GOAL_HINTS = [
    "我要", "我想", "我打算", "我准备", "计划", "目标", "坚持", "正在学",
    "我想学", "打算学", "正在练", "准备考", "减肥", "增肌", "减脂", "攒钱",
    "存钱", "学外语", "学日语", "学英语", "学会", "想学", "练习", "备考",
    "戒掉", "养成习惯", "今年想", "打算", "争取",
]

# 按话用提炼结果写的状态映射（LLM 输出的 status 规范化）
_STATUS_NORM = {
    "active": "active", "ongoing": "active", "进行中": "active", "推进中": "active",
    "paused": "paused", "暂停": "paused", "搁置": "paused",
    "done": "done", "完成": "done", "达成": "done", "成功": "done",
    "abandoned": "abandoned", "放弃": "abandoned", "不做了": "abandoned",
}

_EXTRACT_SYSTEM = (
    "你是长期目标理解引擎。判断用户这句话是否涉及一个【清晰、可长期追踪的目标】"
    "（如减肥、学外语/技能、考研、攒钱、养成习惯等，是用户在一段时间内持续追求的『未来的事』）。\n"
    "只输出一个 JSON，键必须用以下英文名：\n"
    "{\n"
    "  \"involve_goal\": true或false,   // 是否真的涉及长期目标；闲聊/回忆/一次性请求=false\n"
    "  \"action\": \"create|update|complete|abandon|none\",\n"
    "      // create: 新目标; update: 推进/更新已有目标; complete: 达成; abandon: 放弃; none: 无动作\n"
    "  \"title\": \"目标名（简洁，如『学会 N2 日语』）\",\n"
    "  \"progress\": \"具体进度/最近进展（一句话，可为空串）\",\n"
    "  \"next\": \"给用户下一步的提醒/建议（一句话，可为空串）\",\n"
    "  \"note\": \"角色对这条目标自然的记忆备注（可为空串）\",\n"
    "  \"status\": \"active|paused|done|abandoned\"\n"
    "}\n"
    "规则：\n"
    "1. 只有长期、持续的目标才 involve_goal=true；『帮我查天气』『今天吃了什么』绝不触发。\n"
    "2. 一句话里如果明确现已有目标（如提到上次聊过的进度），用 update 而非 create。\n"
    "3. title 用用户自己的简洁说法概括，不含语气词。\n"
    "4. 其余字段没有内容就给空串，不要编造。"
)


def extract_and_update(
    user_id: str,
    role_id: str,
    user_msg: str,
    tool_adapter,
    force: bool = False,
) -> None:
    """对话后调用：判断是否涉及长期目标，是则写入 RelationMemory.goals。

    - user_id: 用户维度（目标按 role 分账、内部按 user 分账）
    - role_id: 该次对话的角色（决定写入哪个角色的关系记忆）
    - tool_adapter: 提供 .chat([...]) -> str 的 LLM 适配器
    - force: 测试用，跳过节流与关键词闸门（直接走 LLM）
    """
    if not GOAL_EXTRACT_ENABLED:
        return
    if not user_msg or not user_msg.strip():
        return
    # 节流（force 时跳过）
    if not force:
        if not _throttle_ok():
            return
    # 关键词闸门（force 时跳过）
    if not force and not _might_mention_goal(user_msg):
        return

    try:
        rel = get_relation_memory(role_id or "")
        if rel is None:
            return
        text = tool_adapter.chat([{
            "role": "system", "content": _EXTRACT_SYSTEM,
        }, {
            "role": "user", "content": (user_msg or "")[:400],
        }]).strip()
        data = _parse_json(text)
        if not isinstance(data, dict) or not data.get("involve_goal"):
            return
        action = str(data.get("action") or "none").strip().lower()
        title = _clean(str(data.get("title") or "").strip())
        status = _norm_status(data.get("status"))
        progress = str(data.get("progress") or "").strip()
        next_step = str(data.get("next") or "").strip()
        note = str(data.get("note") or "").strip()

        # 无实质动作或缺失标题 -> 不写
        if action in ("none", "") and not title:
            return

        if action == "abandon" or status == "abandoned":
            if title:
                # 找不到精确标题就标记所有含该关键词的为目标为放弃（保守：仅 title 匹配）
                if not rel.update_goal(user_id, title, status="abandoned", note=note or "用户在对话中表示搁置"):
                    # 标题未命中：尝试模糊找第一条相近的活性目标
                    _abandon_like(rel, user_id, title)
            return
        if action == "complete" or status == "done":
            if title:
                rel.update_goal(user_id, title, status="done",
                                note=note or "用户在对话中表示已达成")
            return

        existing = find_goal_title(rel, user_id, title) if title else None
        if action == "update" and existing:
            fields = {}
            if progress:
                fields["progress"] = progress
            if next_step:
                fields["note"] = next_step or note
            if status and status != "active":
                fields["status"] = status
            if note:
                fields.setdefault("note", note)
            rel.update_goal(user_id, title, **fields)
            return

        if not title:
            return
        # create（或 update 但标题暂不存在 -> 当作新目标）
        rel.add_goal(
            user_id, title,
            progress=progress,
            note=next_step or note,
            status=status or "active",
        )
    except Exception as e:
        log_debug("GoalExtract", f"目标提炼失败（静默）: {e}")


def _abandon_like(rel, user_id: str, title: str) -> None:
    """标题未精确命中时，模糊放弃活性目标里最相近的（保守：最多 1 条）。"""
    try:
        for g in rel.live_goals(user_id, n=5, min_vitality=0.0):
            if _similar(g.get("title", ""), title):
                rel.update_goal(user_id, g.get("id") or g.get("title", ""),
                                status="abandoned", note="用户在对话中表示搁置")
                return
    except Exception:
        pass


def _similar(a: str, b: str) -> bool:
    """简单标题相似：共享 ≥2 个 2-gram 或一方包含另一方（复用 RelationMemory 判同）。"""
    try:
        from core.relation_memory import RelationMemory
        return RelationMemory._goal_title_eq(a, b)
    except Exception:
        return False


def find_goal_title(rel, user_id: str, title: str) -> Optional[dict]:
    """在活性目标里按标题找一条（供 update 判断是否为新目标）。"""
    try:
        for g in rel.live_goals(user_id, n=20, min_vitality=0.0):
            if _similar(g.get("title", ""), title):
                return g
        return None
    except Exception:
        return None


def _norm_status(status) -> str:
    if not status:
        return ""
    s = str(status).strip().lower()
    return _STATUS_NORM.get(s, "")


def _clean(s: str) -> str:
    return re.sub(r"[「」\"'“”]+", "", s).strip()


def _might_mention_goal(msg: str) -> bool:
    """是否疑似目标性消息（关键词命中数 >= 阈值）。"""
    msg = (msg or "")
    hits = sum(1 for k in _GOAL_HINTS if k in msg)
    return hits >= GOAL_EXTRACT_MIN_KEYWORDS


# ---- 节流（进程内） ----
_last_llm_ts = [0.0]


def _throttle_ok() -> bool:
    now = time.time()
    if GOAL_EXTRACT_INTERVAL_SEC > 0 and now - _last_llm_ts[0] < GOAL_EXTRACT_INTERVAL_SEC:
        return False
    _last_llm_ts[0] = now
    return True


def _parse_json(text: str):
    """容错解析 LLM 输出（去掉代码块围栏、只取首个 JSON 对象）。"""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
        return None
