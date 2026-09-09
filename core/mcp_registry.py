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

这样 MCP 扩展以可插拔插件格式存在，后台可随时启停，无需重启后端。
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
        # ALL_TOOLS = 基础工具 + 当前 manager 内所有 MCP schema（单一事实源）
        self._sync_all_tools()
        with self._lock:
            self._registered.add(server_name)

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
        """重算 ALL_TOOLS：基础工具 + 当前 manager 内所有 MCP schema。"""
        from core import tools
        # 找 ALL_TOOLS 里非 mcp 的基础部分（search/control/assistant 等）
        base = [t for t in tools.ALL_TOOLS
                if not t.get("function", {}).get("name", "").startswith("mcp_")]
        mcp_schemas = self.manager.schemas()  # 当前 running servers 的 schema
        tools.ALL_TOOLS = base + mcp_schemas

    # ---------------- 对外启停 ----------------

    def load(self):
        """启动时加载所有 enabled 的 MCP 服务并注册工具。

        读取 config/mcp.json，按每个 server 的 enabled 字段决定是否启动。
        """
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
