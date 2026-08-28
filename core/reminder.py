"""
日程/提醒引擎 - 存储层

让 Agent（宠物/助手）具备"到点主动提醒/关怀"的能力，而不是只能被动等用户说话：
- 用户/前端可通过 API 创建一条提醒（一次性/重复）
- 调度线程到点后调用 Agent.proactive_message 生成角色口吻的提醒，并经 WS 推送给在线用户
- 若用户离线，则标记 miss 保留，下次在线时补推
- JSON 文件持久化（同 user_profile，避免走 ChromaDB 嵌入调用，简单可靠）

字段设计：
  id         唯一 id
  user_id    提醒属于哪个用户（默认 default_user）
  role_id    由哪个角色来提醒（留空=用该用户的默认角色）
  text       提醒内容（角色要说的话）
  trigger_at 触发时间（ISO 字符串；一次性提醒的绝对时刻）
  repeat     重复方式：""(不重复) | "daily" | "weekly" | "hourly"
  weekdays   每周几触发（repeat=weekly 时 0-6，0=周一；可多个）
  tz_offset  本地时区偏移（分钟），用于把"本地时间"转 UTC
  done       是否已触发
  enabled    是否启用
  created_at 创建时间
  last_fired 最近一次触发时间
"""
import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import time

from core.config import PROJECT_ROOT, REMINDER_FILE

_REMINDER_FILE = Path(PROJECT_ROOT) / "reminders.json"


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _to_iso(dt: datetime) -> str:
    return dt.isoformat()


class ReminderStore:
    """提醒存储：按 user_id 维护提醒列表，JSON 持久化"""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else _REMINDER_FILE
        self._lock = threading.RLock()
        self._data: Dict[str, list] = {}  # user_id -> list[reminder dict]
        self._load()

    # ===================== 持久化 =====================

    def _load(self):
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._data = raw if isinstance(raw, dict) else {}
        except Exception:
            self._data = {}

    def _save(self):
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                from core.logger import log_error
                log_error("Reminder", "保存提醒失败")

    def _user_list(self, user_id: str) -> list:
        user_id = user_id or "default_user"
        return self._data.setdefault(user_id, [])

    # ===================== CRUD =====================

    def add(self, user_id: str, text: str, trigger_at: str = "",
            repeat: str = "", weekdays: Optional[list] = None,
            role_id: str = "", enabled: bool = True) -> Optional[dict]:
        """新增一条提醒。trigger_at 为空且 repeat 为空则拒绝。返回新提醒或 None"""
        user_id = user_id or "default_user"
        text = (text or "").strip()
        if not text:
            return None
        trigger_at = (trigger_at or "").strip()
        if not trigger_at and not repeat:
            return None
        r = {
            "id": uuid.uuid4().hex[:12],
            "user_id": user_id,
            "role_id": (role_id or "").strip(),
            "text": text[:200],
            "trigger_at": trigger_at,
            "repeat": repeat,
            "weekdays": [int(w) for w in (weekdays or []) if 0 <= int(w) <= 6],
            "done": False,
            "enabled": bool(enabled),
            "created_at": _to_iso(datetime.now()),
            "last_fired": None,
        }
        with self._lock:
            self._user_list(user_id).append(r)
            self._save()
        return r

    def list(self, user_id: str, include_done: bool = True) -> List[dict]:
        items = self._user_list(user_id)
        if include_done:
            return list(items)
        return [r for r in items if not r.get("done")]

    def get(self, user_id: str, rid: str) -> Optional[dict]:
        for r in self._user_list(user_id):
            if r.get("id") == rid:
                return r
        return None

    def delete(self, user_id: str, rid: str) -> bool:
        with self._lock:
            lst = self._user_list(user_id)
            new = [r for r in lst if r.get("id") != rid]
            if len(new) == len(lst):
                return False
            self._data[user_id or "default_user"] = new
            self._save()
            return True

    def set_enabled(self, user_id: str, rid: str, enabled: bool) -> bool:
        r = self.get(user_id, rid)
        if not r:
            return False
        with self._lock:
            r["enabled"] = bool(enabled)
            self._save()
        return True

    def mark_fired(self, user_id: str, rid: str, now: datetime) -> bool:
        """标记为已触发；重复提醒则计算下一次触发时间，否则 done=True"""
        r = self.get(user_id, rid)
        if not r:
            return False
        with self._lock:
            r["last_fired"] = _to_iso(now)
            if not r.get("repeat"):
                r["done"] = True
                r["enabled"] = False
            else:
                nxt = self._next_trigger(r, now)
                r["trigger_at"] = _to_iso(nxt) if nxt else ""
                if r.get("done"):
                    r["done"] = False
            self._save()
        return True

    def due(self, user_id: str, now: datetime) -> List[dict]:
        """返回该用户下所有"到点未触发且启用"的提醒（不含重复的跨日判断细节，由调度器处理）"""
        due = []
        for r in self._user_list(user_id):
            if not r.get("enabled"):
                continue
            if r.get("done"):
                continue
            ta = _parse_iso(r.get("trigger_at") or "")
            if ta is None:
                continue
            if ta <= now:
                due.append(r)
        return due

    # ===================== 重复计算 =====================

    @staticmethod
    def _next_trigger(r: dict, now: datetime) -> Optional[datetime]:
        """计算重复提醒的下一次触发时间"""
        repeat = r.get("repeat")
        if not repeat:
            return None
        base = _parse_iso(r.get("trigger_at")) or now
        if repeat == "hourly":
            nxt = now + timedelta(hours=1)
            nxt = nxt.replace(minute=base.minute, second=0, microsecond=0)
            while nxt <= now:
                nxt += timedelta(hours=1)
            return nxt
        if repeat == "daily":
            nxt = now.replace(hour=base.hour, minute=base.minute, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            return nxt
        if repeat == "weekly":
            weekdays = [int(w) for w in (r.get("weekdays") or [])]
            if not weekdays:
                return now + timedelta(days=7)
            for d in range(1, 8):
                cand = now + timedelta(days=d)
                if cand.weekday() in weekdays:
                    return cand.replace(hour=base.hour, minute=base.minute, second=0, microsecond=0)
            return None
        return None


# ===================== 提醒调度器 =====================

class ReminderScheduler:
    """轮询 ReminderStore，到点触发提醒并经 WS 推送给在线用户。

    - 每次触发都会调用对应角色的 Agent.proactive_message 生成角色口吻的提醒内容
    - 若用户在线则经 WS 推送"reminder"消息；离线则保留 miss（由 store.mark_fired
      推进重复/标记），下次扫描不再重复触发同一时刻（daily/weekly 已推进）
    """

    def __init__(self, store: ReminderStore, agent_factory, push_callback=None):
        self.store = store
        self.agent_factory = agent_factory
        # push_callback: callable(user_id, data_dict) —— 由 initializer 注入调度到事件循环
        self.push_callback = push_callback
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, interval: int = 15):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(int(interval) or 15,), daemon=True
        )
        self._thread.name = "reminder-scheduler"
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self, interval: int):
        from core.logger import log_info
        log_info("Reminder", f"提醒调度器启动，扫描间隔 {interval}s")
        while not self._stop.wait(interval):
            try:
                self.tick()
            except Exception as e:
                from core.logger import log_error
                log_error("Reminder", f"调度扫描异常: {e}")

    def tick(self):
        """扫描一次：遍历在线用户，触发他们到点的提醒"""
        from api.websocket_manager import single_ws_manager
        users = self._online_users()
        now = datetime.now()
        for uid in users:
            for r in self.store.due(uid, now):
                self._fire(uid, r, now)

    def _online_users(self) -> List[str]:
        try:
            from api.websocket_manager import single_ws_manager
            if hasattr(single_ws_manager, "user_ids"):
                return list(single_ws_manager.user_ids())
            return list(single_ws_manager.get_all())
        except Exception:
            return []

    def _fire(self, user_id: str, reminder: dict, now: datetime):
        """触发并推送一条提醒；先推进/标记，再生成内容。若生成失败也计入已触发，避免重复轰炸。"""
        try:
            self.store.mark_fired(user_id, reminder["id"], now)
        except Exception:
            pass
        try:
            text = reminder.get("text") or "（提醒）"
            content = self._compose(user_id, reminder, text)
        except Exception:
            content = None
        if content:
            self._push(user_id, reminder, content)

    def _compose(self, user_id: str, reminder: dict, fallback_text: str) -> str:
        """调用角色 Agent 生成带身份口吻的提醒；失败则回退为原始文本"""
        try:
            role_id = reminder.get("role_id") or ""
            agent = self.agent_factory.get_agent(
                user_id, role_id or self._default_role()
            )
            text = agent.proactive_message(
                user_id,
                trigger="有一条到点提醒需要告诉你",
                context=f"提醒内容：{fallback_text}",
            )
            return text or fallback_text
        except Exception:
            return fallback_text

    def _default_role(self):
        try:
            return self.agent_factory.initializer.role_manager.get_default_role() or "kasumi"
        except Exception:
            return "kasumi"

    def _push(self, user_id: str, reminder: dict, content: str):
        data = {
            "type": "reminder",
            "reminder_id": reminder.get("id"),
            "content": content,
            "trigger": "reminder",
        }
        if self.push_callback:
            try:
                self.push_callback(user_id, data)
            except Exception:
                pass
        else:
            try:
                from api.websocket_manager import single_ws_manager
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        single_ws_manager.push_to_user(user_id, data), loop
                    )
            except Exception:
                pass
