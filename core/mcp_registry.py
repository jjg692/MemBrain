"""
MCP 服务注册中心（插件化 + 运行时启停）
=========================================
职责：
- 读取 config/mcp.json 的插件化声明（每个 server 含 enabled 开关）；
- 持有 McpManager，统一对外提供"启动/停止/重启单个 MCP 服务"能力；
- 启停时**动态同步** core.tools 的 ALL_TOOLS / TOOL_REGISTRY：
    * 启动某服务 -> 把它的全部工具 schema + 可调用函数注入 LLM 工具集；
    * 停止某服务 -> 移除对应 mcp_<server>_* 工具；
- 启停后通过回调使 AgentFactory 失效（重建 Agent 以拾取新工具集）。

这样 MCP（星露谷等）以可插拔插件格式存在，后台可随时启停，无需重启后端。
"""

import threading
from typing import Callable, Dict, List, Optional

from core.logger import log_error, log_info
from core.mcp_client import McpManager, get_mcp_manager, McpError


class McpRegistry:
    """MCP 服务注册中心：插件化启停 + 工具集动态同步。

    agent_invalidator: callable() 无参回调，用于清空 Agent 缓存（在
        initializer 里绑定 AgentFactory.invalidate_all）。
    """

    def __init__(self, manager: Optional[McpManager] = None,
                 agent_invalidator: Optional[Callable[[], None]] = None):
        self.manager = manager or get_mcp_manager()
        self.agent_invalidator = agent_invalidator
        self._lock = threading.RLock()
        # 记录当前已注册进 LLM 工具集的 server 名（与正在运行一致）
        self._registered: set = set()
        # 星露谷自主游玩心跳（MCP 扩展，惰性构建）
        self._heartbeat = None

    # ---------------- 工具集同步 ----------------

    def _register_tools(self, server_name: str):
        """把一个已运行 server 的全部工具注册进 core.tools 的 LLM 工具集。"""
        from core import tools
        srv = self.manager.find(server_name)
        if srv is None:
            return
        # 为每个工具生成可调用包装（docstring 复用原始描述，避免 LangGraph 报错）
        schema_by_name = {s["function"]["name"]: s["function"].get("description", "")
                          for s in srv._schemas}
        for tname in [s["function"]["name"] for s in srv._schemas]:
            if tname in tools.TOOL_REGISTRY:
                continue
            desc = schema_by_name.get(tname, tname)

            def _mk(tn, desc_text, mgr):
                def _call(arguments=None, **kw):
                    return mgr.call(tn, arguments or kw)
                _call.__doc__ = desc_text or "调用外部 MCP 工具。"
                return _call

            tools.TOOL_REGISTRY[tname] = _mk(tname, desc, self.manager)
        # 若该 server 是星露谷桥接（带 stardew_spawn），注册一个高阶"加入游戏"便捷工具，
        # 让 LLM 有单一直观入口（spawn + 设 player 模式），避免它自行错序调用原始工具。
        has_stardew_spawn = any(k.endswith("stardew_spawn") for k in tools.TOOL_REGISTRY)
        self._maybe_register_stardew_join(has_stardew_spawn)
        # ALL_TOOLS = 基础工具 + 当前 manager 内所有 MCP schema（单一事实源）
        self._sync_all_tools()
        with self._lock:
            self._registered.add(server_name)

    def _maybe_register_stardew_join(self, has_stardew_spawn: bool):
        """检测到星露谷桥接工具时，注册 stardew_join 高阶工具（幂等）。

        星露谷特有逻辑已收敛到 stardew.boot（boot.register_join_tool），
        这里只做委托，不内嵌星露谷实现。
        """
        if not has_stardew_spawn:
            return
        try:
            from stardew.boot import register_join_tool
            register_join_tool()
        except Exception:
            pass
        # schema 由 _sync_all_tools 统一维护（调用方随后会调用它）

    def _unregister_tools(self, server_name: str):
        """把某个 server 的工具从 LLM 工具集移除。"""
        from core import tools
        prefix = f"mcp_{server_name}_"
        for k in [k for k in list(tools.TOOL_REGISTRY) if k.startswith(prefix)]:
            tools.TOOL_REGISTRY.pop(k, None)
        self._sync_all_tools()
        with self._lock:
            self._registered.discard(server_name)

    def _sync_all_tools(self):
        """重算 ALL_TOOLS：基础工具 + 当前 manager 内所有 MCP schema + 高阶便捷工具。"""
        from core import tools
        # 找 ALL_TOOLS 里非 mcp 的基础部分（search/control/assistant 等）
        base = [t for t in tools.ALL_TOOLS
                if not (t.get("function", {}).get("name", "").startswith("mcp_"))
                and t.get("function", {}).get("name") != "stardew_join"]
        mcp_schemas = self.manager.schemas()  # 当前 running servers 的 schema
        extra = []
        # 高阶星露谷便捷工具 schema（若注册过，保留在工具集里）
        if "stardew_join" in tools.TOOL_REGISTRY:
            extra.append({
                "type": "function",
                "function": {
                    "name": "stardew_join",
                    "description": "让 AI 伙伴作为同伴加入当前星露谷游戏世界（spawn），并可选设为指定模式。用户邀请一起进星露谷时调用。参数 companion 可选（Companion1/Companion2），mode 默认 player。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "companion": {"type": "string", "description": "同伴名，如 Companion1/Companion2（可选）"},
                            "mode": {"type": "string", "description": "模式：player/follow/farm/mine/fish/stay，默认 player"},
                        },
                        "required": [],
                    },
                },
            })
        tools.ALL_TOOLS = base + mcp_schemas + extra

    # ---------------- 对外启停 ----------------

    def load(self):
        """启动时加载所有 enabled 的 MCP 服务并注册工具。

        遵守总开关 STARDEW_MCP_ENABLED（关闭则不启动任何 MCP，完全离线）。
        每个 server 再按 config/mcp.json 的 enabled 字段决定是否启动。
        """
        from core.config import STARDEW_MCP_ENABLED
        if not STARDEW_MCP_ENABLED:
            # 总开关关闭：不启动任何 MCP（保持零影响、完全离线）
            self.manager.close()
            return
        try:
            self.manager.load()
        except Exception as e:
            log_error("MCP", f"MCP 加载失败: {e}")
        # 把当前已运行的服务工具全部注册进工具集，并同步 _registered
        with self._lock:
            for srv in list(self.manager.servers):
                try:
                    self._register_tools(srv.name)
                except Exception as e:
                    log_error("MCP", f"注册服务 {srv.name} 工具失败: {e}")
                else:
                    self._registered.add(srv.name)

    def start(self, name: str) -> dict:
        """启动单个 MCP 服务并注册工具。"""
        with self._lock:
            try:
                res = self.manager.start_server(name)
            except McpError as e:
                return {"ok": False, "name": name, "message": str(e)}
            except Exception as e:
                return {"ok": False, "name": name, "message": f"启动失败: {e}"}
        if res.get("ok"):
            try:
                self._register_tools(name)
                self._invalidate_agents()
            except Exception as e:
                log_error("MCP", f"注册 {name} 工具失败: {e}")
        return res

    def stop(self, name: str) -> dict:
        """停止单个 MCP 服务并注销其工具。"""
        with self._lock:
            try:
                res = self.manager.stop_server(name)
            except Exception as e:
                return {"ok": False, "name": name, "message": f"停止失败: {e}"}
        if name in self._registered:
            try:
                self._unregister_tools(name)
                self._invalidate_agents()
            except Exception as e:
                log_error("MCP", f"注销 {name} 工具失败: {e}")
        return res

    def restart(self, name: str) -> dict:
        """重启单个 MCP 服务（注销 -> 启动 -> 注册）。"""
        try:
            if self.find(name):
                self.stop(name)
        except Exception:
            pass
        return self.start(name)

    def set_enabled(self, name: str, enabled: bool) -> dict:
        """持久化 enabled 开关（仅配置，不直接启停；重启后端或手动 start 生效）。"""
        try:
            res = self.manager.set_enabled(name, enabled)
        except Exception as e:
            return {"ok": False, "name": name, "message": f"保存失败: {e}"}
        return res

    def find(self, name: str):
        return self.manager.find(name)

    def status_all(self) -> List[dict]:
        """返回所有配置 MCP 服务的状态（含是否运行/启用/工具数/错误）。"""
        items = self.manager.status_all()
        for it in items:
            it["registered"] = it["name"] in self._registered
        return items

    def test(self, name: str) -> dict:
        """测试指定 MCP 服务的工具清单（尝试调用 tools/list）。"""
        srv = self.manager.find(name)
        if srv is None:
            return {"ok": False, "name": name, "message": "服务未运行"}
        try:
            tools_list = srv.list_tools()
            return {"ok": True, "name": name, "tools_count": len(tools_list),
                    "tool_names": [t.get("name", "") for t in tools_list][:50]}
        except Exception as e:
            return {"ok": False, "name": name, "message": f"测试失败: {e}"}

    # ---------------- 星露谷自主游玩心跳（MCP 扩展） ----------------

    def ensure_heartbeat(self):
        """惰性构建星露谷自主游玩心跳（作为 MCP 扩展由后台统一管理）。

        星露谷特有逻辑已收敛到 stardew.boot（boot.build_heartbeat），
        这里只做委托，不内嵌星露谷实现。
        """
        if getattr(self, "_heartbeat", None) is None:
            try:
                from core.config import STARDEW_AUTONOMY_INTERVAL, STARDEW_AUTONOMY_LLM_ENABLED
                from stardew.boot import build_heartbeat
                self._heartbeat = build_heartbeat(
                    interval=float(STARDEW_AUTONOMY_INTERVAL),
                    llm_enabled=bool(STARDEW_AUTONOMY_LLM_ENABLED),
                )
            except Exception as e:
                log_error("MCP", f"构建星露谷自主游玩心跳失败: {e}")
                self._heartbeat = None
        return self._heartbeat

    def set_llm_adapter(self, adapter):
        """把 LLM 适配器接线到星露谷自主游玩心跳（LLM 决策版用）。"""
        hb = self.ensure_heartbeat()
        if hb is not None:
            try:
                from core.config import STARDEW_AUTONOMY_LLM_ENABLED
                hb.set_llm_adapter(adapter)
                if STARDEW_AUTONOMY_LLM_ENABLED:
                    hb.set_llm_enabled(True)
            except Exception as e:
                log_error("MCP", f"接线星露谷心跳 LLM 适配器失败: {e}")

    @property
    def heartbeat(self):
        return self.ensure_heartbeat()

    def start_heartbeat(self) -> dict:
        """启动星露谷自主游玩心跳（需 MCP 已就绪、总开关已开）。"""
        hb = self.ensure_heartbeat()
        if hb is None:
            return {"ok": False, "message": "星露谷自主游玩心跳不可用（未装插件）"}
        if not self.manager.servers:
            return {"ok": False, "message": "星露谷 MCP 服务未运行，请先启动 MCP"}
        try:
            hb.set_enabled(True)
            return {"ok": True, "running": bool(hb._thread and hb._thread.is_alive()),
                    "message": "星露谷自主游玩心跳已启动"}
        except Exception as e:
            log_error("MCP", f"启动星露谷自主游玩心跳失败: {e}")
            return {"ok": False, "message": f"启动失败: {e}"}

    def stop_heartbeat(self) -> dict:
        hb = self.ensure_heartbeat()
        if hb is None:
            return {"ok": False, "message": "星露谷自主游玩心跳不可用"}
        try:
            hb.set_enabled(False)
            return {"ok": True, "running": False, "message": "星露谷自主游玩心跳已停止"}
        except Exception as e:
            log_error("MCP", f"停止星露谷自主游玩心跳失败: {e}")
            return {"ok": False, "message": f"停止失败: {e}"}

    def heartbeat_status(self) -> dict:
        hb = self.ensure_heartbeat()
        if hb is None:
            return {"available": False, "running": False, "enabled": False}
        return {"available": True, **hb.status()}

    def set_heartbeat_llm(self, enabled: bool) -> dict:
        """运行时切换星露谷心跳的 LLM 决策开关。"""
        hb = self.ensure_heartbeat()
        if hb is None:
            return {"ok": False, "message": "星露谷自主游玩心跳不可用"}
        if enabled and hb.llm_adapter is None:
            return {"ok": False, "message": "LLM 适配器未装配，无法开启 LLM 决策"}
        try:
            hb.set_llm_enabled(bool(enabled))
            return {"ok": True, "llm_enabled": bool(hb.llm_enabled), "message": "已切换 LLM 决策开关"}
        except Exception as e:
            log_error("MCP", f"切换星露谷心跳 LLM 决策失败: {e}")
            return {"ok": False, "message": f"切换失败: {e}"}

    def feed_heartbeat_command(self, text: str) -> bool:
        """把玩家聊天里的星露谷行动指令喂给自主游玩心跳（有指令听指令，没指令自己判断）。

        属于星露谷 MCP 扩展（插件域）归口：仅当心跳存在时投递，不影响主项目。
        未启动/未构建心跳时静默返回 False，不抛错、不污染调用方。
        """
        try:
            hb = self.ensure_heartbeat()
            if hb is None or not getattr(hb, "enabled", False):
                return False
            return bool(hb.feed_command(text))
        except Exception as e:
            log_error("MCP", f"投递星露谷指令失败: {e}")
            return False

    # ---------------- 内部 ----------------

    def _invalidate_agents(self):
        """工具集变化后使 Agent 缓存失效，让下一个会话重建（拾取新工具）。"""
        if self.agent_invalidator:
            try:
                self.agent_invalidator()
            except Exception as e:
                log_error("MCP", f"Agent 缓存失效失败: {e}")


# 单例注册中心（由 initializer 绑定 agent_invalidator 后使用）
_default_registry = McpRegistry()


def get_mcp_registry() -> McpRegistry:
    return _default_registry
