"""
角色实例池
管理多个 Agent 实例的创建、复用和销毁
每个角色独立加载，按需激活，池化管理
"""

import os
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from core.logger import log_debug


@dataclass
class RoleConfig:
    """角色配置"""
    role_id: str
    display_name: str = ""            # 显示名称
    role_prompt_path: str = ""        # role_prompt.txt 路径
    role_prompt: str = ""             # 角色人设内容（优先使用）
    llm_model: str = ""               # 可覆盖模型（空则用默认）
    tool_llm_model: str = ""          # 可覆盖工具模型
    created_at: datetime = field(default_factory=datetime.now)

    def get_display_name(self) -> str:
        return self.display_name or self.role_id


@dataclass
class AgentInstance:
    """池中的 Agent 实例"""
    role_id: str
    instance: object                   # LangGraphMemoryAgent 实例
    created_at: datetime = field(default_factory=datetime.now)
    last_active_at: datetime = field(default_factory=datetime.now)
    ref_count: int = 1                 # 引用计数（被多少个房间引用）

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """检查是否过期（长时间未被使用）"""
        delta = datetime.now() - self.last_active_at
        return delta.total_seconds() > timeout_minutes * 60


class RoleInstancePool:
    """
    角色实例池（单例）

    职责：
    - 按角色 ID 创建 Agent 实例
    - 复用已有实例（同一角色可在多个房间共享）
    - 按引用计数管理生命周期
    - 自动清理过期实例
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # 角色配置注册表 (role_id → RoleConfig)
        self._configs: Dict[str, RoleConfig] = {}
        # 活跃 Agent 实例 (role_id → AgentInstance)
        self._instances: Dict[str, AgentInstance] = {}
        # 默认的 prompt 目录
        self._prompt_dir = Path(__file__).parent.parent.parent / "role_prompts"
        self._prompt_dir.mkdir(exist_ok=True)

    # ==================== 角色配置注册 ====================

    def register_role(self, config: RoleConfig) -> bool:
        """注册一个角色配置"""
        if config.role_id in self._configs:
            return False
        # 如果没有 prompt 内容但有路径，加载文件
        if not config.role_prompt and config.role_prompt_path:
            config.role_prompt = self._load_prompt_file(config.role_prompt_path)
        self._configs[config.role_id] = config
        return True

    def register_role_from_file(self, role_id: str, prompt_path: str, display_name: str = "") -> bool:
        """从文件注册角色"""
        prompt = self._load_prompt_file(prompt_path)
        if not prompt:
            return False
        return self.register_role(RoleConfig(
            role_id=role_id,
            display_name=display_name or role_id,
            role_prompt=prompt,
            role_prompt_path=prompt_path,
        ))

    def register_role_from_text(self, role_id: str, prompt_text: str, display_name: str = "") -> bool:
        """直接从文本注册角色"""
        return self.register_role(RoleConfig(
            role_id=role_id,
            display_name=display_name or role_id,
            role_prompt=prompt_text,
        ))

    def unregister_role(self, role_id: str) -> bool:
        """注销角色并释放其 Agent 实例"""
        self.release_agent(role_id)
        return self._configs.pop(role_id, None) is not None

    def get_role_config(self, role_id: str) -> Optional[RoleConfig]:
        """获取角色配置"""
        return self._configs.get(role_id)

    def list_registered_roles(self) -> List[str]:
        """列出所有已注册的角色 ID"""
        return list(self._configs.keys())

    # ==================== Agent 实例管理 ====================

    def get_agent(self, role_id: str, agent_factory_fn=None) -> Optional[object]:
        """
        获取 Agent 实例
        如果已存在且有效，复用；否则创建新的

        Args:
            role_id: 角色 ID
            agent_factory_fn: 创建 Agent 实例的回调
                              (config, role_id) → LangGraphMemoryAgent
                              如果不传，返回 None

        Returns:
            Agent 实例或 None
        """
        # 检查是否已有活跃实例
        existing = self._instances.get(role_id)
        if existing:
            existing.last_active_at = datetime.now()
            return existing.instance

        # 检查角色是否已注册
        config = self._configs.get(role_id)
        if not config:
            return None

        # 需要 factory 来创建实例
        if not agent_factory_fn:
            return None

        # 创建新实例
        instance = agent_factory_fn(config, role_id)
        if instance is None:
            return None

        self._instances[role_id] = AgentInstance(
            role_id=role_id,
            instance=instance,
        )
        log_debug("角色池", f"创建 Agent 实例: {role_id}")
        return instance

    def release_agent(self, role_id: str) -> bool:
        """释放一个 Agent 实例"""
        inst = self._instances.pop(role_id, None)
        if inst:
            # 清理实例资源（如果有 cleanup 方法）
            agent = inst.instance
            if hasattr(agent, 'cleanup') and callable(agent.cleanup):
                try:
                    agent.cleanup()
                except Exception as e:
                    log_debug("角色池", f"清理 Agent [{role_id}] 失败: {e}")
            log_debug("角色池", f"释放 Agent 实例: {role_id}")
            return True
        return False

    def refresh_agent(self, role_id: str, agent_factory_fn=None) -> Optional[object]:
        """刷新 Agent 实例（先释放再创建）"""
        self.release_agent(role_id)
        return self.get_agent(role_id, agent_factory_fn)

    # ==================== 实例维护 ====================

    def cleanup_expired(self, timeout_minutes: int = 30):
        """清理长时间未使用的 Agent 实例"""
        to_remove = []
        for role_id, inst in self._instances.items():
            if inst.is_expired(timeout_minutes):
                to_remove.append(role_id)

        for role_id in to_remove:
            self.release_agent(role_id)

        if to_remove:
            log_debug("角色池", f"清理了 {len(to_remove)} 个过期实例")

    def get_active_agents(self) -> List[str]:
        """获取当前活跃的 Agent 角色 ID 列表"""
        return list(self._instances.keys())

    def get_instance_count(self) -> int:
        """获取当前实例数量"""
        return len(self._instances)

    # ==================== 内部 ====================

    def _load_prompt_file(self, path: str) -> str:
        """从文件加载角色 prompt"""
        filepath = Path(path)
        if not filepath.is_absolute():
            filepath = self._prompt_dir / path
        if not filepath.exists():
            return ""
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
