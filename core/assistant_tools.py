"""
助手核心工具（行动力 / "助手"属性）

提供：
- remind_me / list_reminders / cancel_reminder : 让模型能主动/安全地设提醒（复用 ReminderStore）
- get_current_time                             : 精确时间/星期/时段（复用感知层）
- read_file / write_file / list_files          : 本地文件读写（严格沙箱，仅限 ASSISTANT_WORKSPACE_DIR + uploads）

安全约束：
- 文件工具只允许在沙箱根目录（及 uploads）内，禁止任意路径访问（防 SSRF/越权）
- 所有函数返回字符串，失败/越权时诚实拒绝，不伪造
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from core.config import PROJECT_ROOT, ASSISTANT_WORKSPACE_DIR

_WORKSPACE = Path(ASSISTANT_WORKSPACE_DIR)
_UPLOADS = Path(PROJECT_ROOT) / "uploads"


def _init_workspace():
    _WORKSPACE.mkdir(parents=True, exist_ok=True)


def _allowed_path(rel_or_abs: str) -> Optional[Path]:
    """归一化路径并校验是否落在沙箱内；合法返回绝对 Path，否则 None"""
    if not rel_or_abs or not str(rel_or_abs).strip():
        return None
    p = Path(rel_or_abs).expanduser()
    if not p.is_absolute():
        p = _WORKSPACE / p
    p = p.resolve()
    for root in (_WORKSPACE, _UPLOADS):
        try:
            if p == root.resolve() or root.resolve() in p.parents:
                return p
        except Exception:
            continue
    return None


# ===================== 提醒 =====================

def _reminder_store():
    from core.reminder import ReminderStore
    return ReminderStore()


def remind_me(text: str, when: str = "", repeat: str = "",
              user_id: str = "default_user") -> str:
    """设置一条提醒。

    when: ISO 时间串（如 2026-08-29 09:00 或 2026-08-29T09:00:00）。为空则要求 repeat 非空。
    repeat: ""(一次性) | "daily" | "hourly" | "weekly"（weekly 可加 weekdays）
    """
    text = (text or "").strip()
    if not text:
        return "（提醒未创建）需要提供提醒内容 text。"
    when = (when or "").strip()
    if not when and not (repeat or "").strip():
        return "（提醒未创建）需要提供触发时间(when)或重复方式(repeat)。"
    store = _reminder_store()
    r = store.add(
        user_id=user_id, text=text, trigger_at=when, repeat=repeat, role_id=""
    )
    if r is None:
        return "（提醒未创建）参数有误，请检查时间格式。"
    return f"已为你设置提醒：{r['text']}（id={r['id']}）"


def list_reminders(user_id: str = "default_user") -> str:
    """列出该用户未触发的提醒"""
    store = _reminder_store()
    items = store.list(user_id, include_done=False)
    if not items:
        return "你目前没有待触发的提醒。"
    lines = [f"你有 {len(items)} 条待触发提醒："]
    for r in items[:20]:
        when = r.get("trigger_at") or ("重复：" + (r.get("repeat") or ""))
        lines.append(f"- [{r['id']}] {r.get('text')}（{when}）")
    return "\n".join(lines)


def cancel_reminder(reminder_id: str, user_id: str = "default_user") -> str:
    """取消一条提醒（按 id）"""
    store = _reminder_store()
    ok = store.delete(user_id, reminder_id)
    return "已取消该提醒。" if ok else "（取消失败）未找到该提醒。"


# ===================== 时间 =====================

def get_current_time() -> str:
    """返回当前精确时间、星期、时段"""
    from core.perception import time_situation
    t = time_situation()
    return (
        f"现在：{t['now']}（{t['weekday_cn']}，{t['period']}时段，"
        f"{'周末' if t['is_weekend'] else '工作日'}）"
    )


# ===================== 文件（沙箱） =====================

def read_file(path: str) -> str:
    """读取沙箱内的文件内容（仅限助手工作区/上传目录）"""
    p = _allowed_path(path)
    if p is None:
        return "（拒绝访问）该路径不在允许的目录内。"
    if not p.exists() or not p.is_file():
        return "（读取失败）文件不存在。"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        return content[:4000] if content else "（空文件）"
    except Exception as e:
        return f"（读取失败）{e}"


def write_file(path: str, content: str = "") -> str:
    """在沙箱内写入/覆盖文件（仅限助手工作区）"""
    p = _allowed_path(path)
    if p is None:
        return "（拒绝写入）该路径不在允许的目录内。"
    # 只允许写到 workspace，不允许写 uploads（uploads 是上传文件，只读更安全）
    try:
        if not (_WORKSPACE.resolve() in p.parents or p.parent == _WORKSPACE.resolve()):
            return "（拒绝写入）只允许写入助手工作区。"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content or ""), encoding="utf-8")
        return f"已写入：{p}"
    except Exception as e:
        return f"（写入失败）{e}"


def list_files(path: str = "") -> str:
    """列出沙箱内目录下的文件（仅限助手工作区/上传目录）"""
    p = _allowed_path(path or ".")
    if p is None:
        return "（拒绝访问）该路径不在允许的目录内。"
    if not p.exists() or not p.is_dir():
        return "（列目录失败）路径不存在或是文件。"
    try:
        names = sorted(
            [str(x.name) + ("/" if x.is_dir() else "") for x in p.iterdir()]
        )
        if not names:
            return "（目录为空）"
        return "\n".join(names[:100])
    except Exception as e:
        return f"（列目录失败）{e}"


# ===================== Schema（供 LLM） =====================

REMIND_ME_TOOL = {
    "type": "function",
    "function": {
        "name": "remind_me",
        "description": "为用户设置一条提醒（一次性或重复）。当用户要求你记住/提醒某事时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "提醒内容"},
                "when": {"type": "string", "description": "触发时间，ISO 格式：2026-08-29 09:00 或 2026-08-29T09:00:00。"},
                "repeat": {"type": "string", "enum": ["", "daily", "hourly", "weekly"], "description": "重复方式"},
            },
            "required": ["text", "when"],
        },
    },
}

LIST_REMINDERS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_reminders",
        "description": "列出用户当前所有待触发的提醒。",
        "parameters": {"type": "object", "properties": {}},
    },
}

CANCEL_REMINDER_TOOL = {
    "type": "function",
    "function": {
        "name": "cancel_reminder",
        "description": "取消一条提醒。",
        "parameters": {
            "type": "object",
            "properties": {"reminder_id": {"type": "string", "description": "要取消的提醒 id"}},
            "required": ["reminder_id"],
        },
    },
}

GET_CURRENT_TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "返回当前的精确时间、星期与时段。",
        "parameters": {"type": "object", "properties": {}},
    },
}

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取助手工作区内文件的内容（受沙箱限制）。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "相对工作区的路径"}},
            "required": ["path"],
        },
    },
}

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "在助手工作区内写入/覆盖一个文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的路径"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["path", "content"],
        },
    },
}

LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "列出助手工作区内目录下的文件。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "相对工作区的目录路径（默认根目录）"}},
        },
    },
}

ASSISTANT_TOOLS_SCHEMA = [
    REMIND_ME_TOOL, LIST_REMINDERS_TOOL, CANCEL_REMINDER_TOOL,
    GET_CURRENT_TIME_TOOL,
    READ_FILE_TOOL, WRITE_FILE_TOOL, LIST_FILES_TOOL,
]

ASSISTANT_TOOL_REGISTRY = {
    "remind_me": remind_me,
    "list_reminders": list_reminders,
    "cancel_reminder": cancel_reminder,
    "get_current_time": get_current_time,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
}


def ensure_workspace():
    """确保沙箱目录存在（供注册时调用一次）"""
    _init_workspace()
