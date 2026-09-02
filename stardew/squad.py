"""
星露谷多 agent 小队协调（方向二）：让多个角色同时进星露谷分工协作

设计：复用现有 RoomManager 作为"小队房间"，把多个 role 的 agent 挂进去，
再用 MessageBus / 共享 MCP 工具做分工协调。

约束与降级：
- 本模块**可选导入**，任何缺少（无 RoomManager、无 MCP server、开关关闭）都安全降级。
- 真正的"多同伴进游戏"依赖真实星露谷环境（SMAPI + .NET + 游戏），
  本模块只负责**协调层**；在没有游戏的环境下不会崩溃，只是不产生行动。
"""
from typing import Dict, List, Optional


class StardewSquad:
    """星露谷多 agent 小队协调器。

    职责：
    - 建立"小队房间"（含多个 role 的 agent）
    - 按指令把"行动"派发给对应 member（分工）
    - 汇总各 member 的最近行动结果
    """

    ROOM_ID = "stardew-squad"

    def __init__(self, room_manager=None, enabled: bool = False, memory_bridge_factory=None):
        self.room_manager = room_manager
        self.enabled = enabled
        # memory_bridge_factory: callable(agent) -> GameMemoryBridge | None（可选，用于反写记忆）
        self._bridge_factory = memory_bridge_factory
        self._roles: List[str] = []
        self._last_results: Dict[str, str] = {}

    # ---------- 生命周期 ----------

    def setup(self, role_agents: Dict[str, object]):
        """把 {role_id: agent实例} 挂进小队房间。开关关闭时直接不动作。"""
        self._roles = []
        if not self.enabled or self.room_manager is None:
            return
        try:
            rm = self.room_manager
            if rm.create_room(self.ROOM_ID, "星露谷小队") is None:
                pass  # 已存在则复用
            for role_id, agent in (role_agents or {}).items():
                rm.add_agent_to_room(self.ROOM_ID, role_id, agent)
                self._roles.append(role_id)
        except Exception as e:
            from core.logger import log_error
            log_error("StardewSquad", f"setup 失败: {e}")

    @property
    def members(self) -> List[str]:
        return list(self._roles)

    def is_active(self) -> bool:
        return self.enabled and bool(self._roles)

    # ---------- 分工派发 ----------

    def dispatch(self, role_id: str, fn_name: str, arguments: dict = None) -> str:
        """把一个 MCP 行动派发给某个 member 执行。

        使用场景示例：
            squad.dispatch("kasumi", "mcp_stardew_cast_fishing_rod")
            squad.dispatch("kokoro", "mcp_stardew_attack")
        """
        if not self.is_active():
            return "（星露谷小队未启用）"
        try:
            from core.tools import TOOL_REGISTRY
            fn = TOOL_REGISTRY.get(fn_name)
            if fn is None:
                return f"（星露谷小队）未知工具 {fn_name}"
            result = fn(**(arguments or {}))
            self._last_results[role_id] = result
            return result
        except Exception as e:
            from core.logger import log_error
            log_error("StardewSquad", f"dispatch {role_id}/{fn_name} 失败: {e}")
            return f"（星露谷小队）执行失败: {e}"

    def dispatch_parallel(self, plan: Dict[str, tuple]):
        """一次派发多个分工行动。

        plan: {role_id: (fn_name, arguments_dict)}
        示例:
            squad.dispatch_parallel({
              "kasumi": ("mcp_stardew_cast_fishing_rod", {}),
              "kokoro": ("mcp_stardew_attack", {}),
            })
        """
        out = {}
        for role_id, (fn_name, args) in (plan or {}).items():
            out[role_id] = self.dispatch(role_id, fn_name, args)
        return out

    # ---------- 结果与记忆 ----------

    def last_result(self, role_id: str) -> str:
        return self._last_results.get(role_id, "")

    def record_to_memory(self, user_id: str, narrative: str):
        """把小队的一次协作结果反写进记忆（方向一复用）。"""
        if not self._bridge_factory or not self.enabled:
            return
        try:
            # 取第一个 member 的 memory 作为桥（小队共享同一用户记忆）
            for role_id, agent in self._member_agents().items():
                bridge = self._bridge_factory(agent)
                if bridge is not None:
                    bridge.record_event(user_id, narrative, "这是和用户一起玩星露谷的重要回忆")
                    return
        except Exception as e:
            from core.logger import log_error
            log_error("StardewSquad", f"record_to_memory 失败: {e}")

    def _member_agents(self) -> Dict[str, object]:
        if self.room_manager is None:
            return {}
        try:
            return self.room_manager.get_member_agents(self.ROOM_ID) or {}
        except Exception:
            return {}


# 便捷工厂（可选）
def get_squad(room_manager=None, enabled: bool = False,
              memory_bridge_factory=None) -> Optional[StardewSquad]:
    try:
        return StardewSquad(room_manager, enabled, memory_bridge_factory)
    except Exception:
        return None
