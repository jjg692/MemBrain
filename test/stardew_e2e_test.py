"""
星露谷 MCP 端到端集成测试（不依赖真实游戏 / node / SMAPI）：
用 test/fake_stardew_mcp.py 假 server，通过真实 McpManager 走完整 JSON-RPC 协议，
验证：工具发现 -> schema 命名(mcp_stardew_stardew_get_state) -> 调用 -> 轮询器动态解析 -> 记忆反写。

运行：   py -3.11 -c "import sys;sys.path.insert(0,'test');import stardew_e2e_test"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.mcp_client import McpServer
from stardew.runtime import GameStatePoller


def main():
    fake = str((Path(__file__).parent / "fake_stardew_mcp.py").resolve())

    # 1) 真实 McpServer 连假 server
    srv = McpServer(name="stardew", command=sys.executable, args=[fake])
    srv.start()
    try:
        tools = srv.list_tools()
        assert "stardew_get_state" in [t["name"] for t in tools], "工具发现失败"
        schemas = srv.build_schemas()
        reg_name = schemas[0]["function"]["name"]
        assert reg_name == "mcp_stardew_stardew_get_state", f"命名异常: {reg_name}"

        # 2) 注册到真实 TOOL_REGISTRY（模拟 tools.py 的注册动作）
        from core.tools import TOOL_REGISTRY
        TOOL_REGISTRY[reg_name] = lambda *a, **k: srv.call_tool("stardew_get_state", {})

        # 3) 轮询器动态解析工具名
        assert GameStatePoller._find_state_tool_name() == reg_name

        # 4) 轮询器 + 记忆桥
        class Bridge:
            def __init__(self):
                self.events = []
            def record_event(self, u, n, importance_hint=None):
                self.events.append(n)

        bridge = Bridge()
        poller = GameStatePoller(memory_bridge=bridge, interval=60)
        # 注入绑定到测试 server 的调用（生产环境 `_default_tool_call` 走同一 get_mcp_manager 单例）
        poller._tool_call = lambda name, args: srv.call_tool("stardew_get_state", {})
        poller.record_fingerprint()
        assert len(bridge.events) == 1, "应反写一条记忆"
        assert "春" in bridge.events[0]
        assert "农场" in bridge.events[0]

        print("STARDEW E2E OK — tool:", reg_name, "| event:", bridge.events[0][:30] + "…")
        srv.stop()
        return 0
    finally:
        try:
            srv.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
