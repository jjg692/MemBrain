"""
用户资料存储（昵称等）

说明：
- 与角色/记忆隔离体系解耦，按 user_id 存一份轻量 JSON 文件。
- 用途：让角色在对话中用"用户昵称"称呼使用者（注入 system prompt）。
- 纯本地文件持久化（users.json），避免走 ChromaDB 嵌入调用，简单可靠。
只有昵称相关字段，后续可扩展头像/签名等。
"""
import json
import threading
from pathlib import Path
from typing import Dict, Optional

from core.config import PROJECT_ROOT

# 用户资料文件
USER_PROFILES_FILE = Path(PROJECT_ROOT) / "user_profiles.json"


class UserProfile:
    """用户资料管理（按 user_id）"""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or USER_PROFILES_FILE
        # 用可重入锁：set_nickname 加锁后内部调用 _save 会再次加锁，RLock 可重入避免死锁
        self._lock = threading.RLock()
        self._data: Dict[str, dict] = {}
        self._load()

    # ===================== 加载 / 保存 =====================

    def _load(self):
        if not self._path.exists():
            self._data = {}
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except Exception:
            self._data = {}

    def _save(self):
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:
                # 写入失败不阻塞主流程
                from core.logger import log_error
                log_error("UserProfile", f"保存用户资料失败: {e}")

    # ===================== CRUD =====================

    def get_nickname(self, user_id: str) -> str:
        """返回用户昵称；未设置返回空串"""
        user_id = user_id or "default_user"
        prof = self._data.get(user_id) or {}
        return (prof.get("nickname") or "").strip()

    def set_nickname(self, user_id: str, nickname: str) -> str:
        """设置昵称（空串会清除昵称）；返回规范化后的昵称"""
        user_id = user_id or "default_user"
        nickname = (nickname or "").strip()[:20]
        with self._lock:
            prof = self._data.setdefault(user_id, {})
            if nickname:
                prof["nickname"] = nickname
            else:
                prof.pop("nickname", None)
            # 没有字段则清理整条记录
            if not prof:
                self._data.pop(user_id, None)
            self._save()
        return nickname

    def get_profile(self, user_id: str) -> dict:
        return {
            "user_id": user_id or "default_user",
            "nickname": self.get_nickname(user_id),
        }

    def all(self) -> dict:
        return dict(self._data)
