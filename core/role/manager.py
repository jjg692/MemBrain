"""
角色管理器 - 多角色系统
- 读取 config/roles.json
- 每个角色独立 Prompt 文件（role_prompts/role_prompt_{role_id}.txt）
- 前端角色下拉切换
- L5 事实在系统启动时按角色一次性加载
- 支持后台管理增删改查 / 头像 / 默认角色
"""
import json
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional

from core.config import ROLES_FILE, ROLE_PROMPTS_DIR, AVATARS_DIR, UPLOADS_DIR
from core.logger import log_info, log_error


@dataclass
class RoleConfig:
    role_id: str
    display_name: str = ""
    prompt_file: str = ""
    avatar: str = ""
    default: bool = False
    description: str = ""
    live2d_model: str = ""      # Live2D 模型路径（live2d/ 下的相对目录路径），留空用全局默认
    render_enabled: bool = False  # 「渲染角色管理」：是否在桌面宠物里渲染此角色（每角色一个独立窗口）

    def to_dict(self) -> dict:
        return asdict(self)

    def is_rendered(self) -> bool:
        """是否应渲染此角色：默认角色（看板娘）始终渲染，其他按 render_enabled 开关。
        默认角色一旦被设为 default 即无条件渲染，保证看板娘一定在桌面上。"""
        return bool(self.default) or bool(self.render_enabled)


class RoleManager:
    def __init__(self):
        self._roles: Dict[str, RoleConfig] = {}
        self._prompts: Dict[str, str] = {}
        self._load()

    # ===================== 加载 =====================

    def _load(self):
        path = Path(ROLES_FILE)
        if not path.exists():
            self._roles = {}
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("roles", []):
                _default = item.get("default", False)
                # 向后兼容：默认角色（看板娘）默认开放渲染；其他角色缺省默认关闭渲染
                role = RoleConfig(
                    role_id=item["role_id"],
                    display_name=item.get("display_name", item["role_id"]),
                    prompt_file=item.get("prompt_file", f"role_prompt_{item['role_id']}.txt"),
                    avatar=item.get("avatar", ""),
                    default=_default,
                    description=item.get("description", ""),
                    live2d_model=item.get("live2d_model", ""),
                    render_enabled=item.get("render_enabled", _default),
                )
                self._roles[role.role_id] = role
        except Exception as e:
            log_error("Role", f"加载 roles.json 失败: {e}")

    def load_prompt(self, role_id: str) -> str:
        """读取角色的 Prompt 文件（带缓存）"""
        if role_id in self._prompts:
            return self._prompts[role_id]
        role = self._roles.get(role_id)
        if not role:
            return ""
        path = Path(ROLE_PROMPTS_DIR) / role.prompt_file
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8").strip()
        self._prompts[role_id] = text
        return text

    def reload_prompt(self, role_id: str):
        """清除提示词缓存（修改后调用）"""
        self._prompts.pop(role_id, None)

    # ===================== 查询 =====================

    def all_roles(self) -> List[RoleConfig]:
        return [self._roles[k] for k in self._roles]

    def list_contacts(self) -> List[dict]:
        items = []
        for role in self.all_roles():
            d = role.to_dict()
            d["has_prompt"] = bool(self.load_prompt(role.role_id))
            d["avatar_url"] = f"/static/avatars/agents/{role.avatar}" if role.avatar else ""
            # 计算渲染状态：默认角色（看板娘）视为渲染中
            d["rendered"] = role.is_rendered()
            items.append(d)
        return items

    def rendered_roles(self) -> List[RoleConfig]:
        """返回需要渲染的角色（默认角色无条件 + 显式开启渲染的角色）"""
        return [r for r in self.all_roles() if r.is_rendered()]

    def set_render_enabled(self, role_id: str, enabled: bool) -> bool:
        role = self._roles.get(role_id)
        if not role:
            return False
        role.render_enabled = bool(enabled)
        self._save()
        return True

    def get(self, role_id: str) -> Optional[RoleConfig]:
        return self._roles.get(role_id)

    def get_default_role(self) -> Optional[str]:
        for r in self.all_roles():
            if r.default:
                return r.role_id
        if self._roles:
            return next(iter(self._roles))
        return None

    def get_prompt_file_path(self, role_id: str) -> Path:
        role = self._roles.get(role_id)
        fname = role.prompt_file if role and role.prompt_file else f"role_prompt_{role_id}.txt"
        return Path(ROLE_PROMPTS_DIR) / fname

    # ===================== 写操作（后台管理） =====================

    def _save(self):
        data = {"roles": [r.to_dict() for r in self.all_roles()]}
        Path(ROLES_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(ROLES_FILE).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def create_role(self, role_id: str, display_name: str = "", prompt: str = "",
                    description: str = "", live2d_model: str = "") -> Optional[RoleConfig]:
        if not role_id or role_id in self._roles:
            return None
        role = RoleConfig(
            role_id=role_id,
            display_name=display_name or role_id,
            prompt_file=f"role_prompt_{role_id}.txt",
            description=description,
            live2d_model=live2d_model or "",
        )
        # 写入 prompt 文件
        self._write_prompt_file(role, prompt)
        self._roles[role_id] = role
        self._save()
        return role

    def update_role(self, role_id: str, display_name: str = None, prompt: str = None,
                    description: str = None, default: bool = None,
                    live2d_model: str = None, render_enabled: bool = None) -> bool:
        role = self._roles.get(role_id)
        if not role:
            return False
        if display_name is not None:
            role.display_name = display_name
        if description is not None:
            role.description = description
        if live2d_model is not None:
            role.live2d_model = live2d_model or ""
        if render_enabled is not None:
            role.render_enabled = bool(render_enabled)
        if prompt is not None:
            self._write_prompt_file(role, prompt)
            self.reload_prompt(role_id)
        if default is not None and default:
            for r in self.all_roles():
                r.default = (r.role_id == role_id)
        self._save()
        return True

    def delete_role(self, role_id: str) -> bool:
        if role_id not in self._roles:
            return False
        # 删除 prompt 文件与头像
        role = self._roles[role_id]
        pf = self.get_prompt_file_path(role_id)
        try:
            if pf.exists():
                pf.unlink()
        except Exception:
            pass
        if role.avatar:
            try:
                av = Path(AVATARS_DIR) / role.avatar
                if av.exists():
                    av.unlink()
            except Exception:
                pass
        del self._roles[role_id]
        self._prompts.pop(role_id, None)
        self._save()
        return True

    def set_avatar(self, role_id: str, uploaded_bytes: bytes, filename: str) -> bool:
        """保存头像并更新角色配置"""
        role = self._roles.get(role_id)
        if not role:
            return False
        Path(AVATARS_DIR).mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix or ".png"
        av_name = f"{role_id}{ext}"
        # 清理旧头像
        if role.avatar and role.avatar != av_name:
            old = Path(AVATARS_DIR) / role.avatar
            try:
                if old.exists():
                    old.unlink()
            except Exception:
                pass
        Path(AVATARS_DIR, av_name).write_bytes(uploaded_bytes)
        role.avatar = av_name
        self._save()
        return True

    def delete_avatar(self, role_id: str) -> bool:
        role = self._roles.get(role_id)
        if not role or not role.avatar:
            return False
        av = Path(AVATARS_DIR) / role.avatar
        try:
            if av.exists():
                av.unlink()
        except Exception:
            pass
        role.avatar = ""
        self._save()
        return True

    def _write_prompt_file(self, role: RoleConfig, prompt: str):
        path = self.get_prompt_file_path(role.role_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
