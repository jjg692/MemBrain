"""
MCP 注册中心单元/集成测试（通用可插拔扩展机制）：
- 用假 MCP server（test/fake_mcp_server.py，子进程 JSON-RPC）验证插件化启停：
  * McpManager 支持按 name 启动/停止单个服务 + status_all 透出 enabled/running
  * McpRegistry.start() 把 mcp_* 工具注册进 TOOL_REGISTRY / ALL_TOOLS
  * McpRegistry.stop() 移除对应工具并触发 agent 失效
  * set_enabled 持久化开关
- 全部用临时 config，不改动真实 config/mcp.json
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.mcp_client import McpManager, McpError
from core.mcp_registry import McpRegistry


FAKE = str((Path(__file__).resolve().parents[1] / "fake_mcp_server.py"))


@pytest.fixture()
def fake_config(tmp_path):
    """生成指向假 server 的 mcp.json。"""
    cfg = {
        "servers": [
            {
                "name": "demo",
                "command": sys.executable,
                "args": [FAKE],
                "enabled": True,
            }
        ]
    }
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_manager_start_stop_status(fake_config):
    mgr = McpManager(config_path=fake_config)
    mgr.load()
    srv = mgr.find("demo")
    assert srv is not None and srv.alive
    # tools/list 发现工具
    names = [t["name"] for t in srv.list_tools()]
    assert "ping" in names
    assert "echo" in names
    # status_all 透出 enabled/running
    status = mgr.status_all()
    assert status[0]["running"] is True
    assert status[0]["enabled"] is True
    # 停止
    mgr.stop_server("demo")
    assert mgr.find("demo") is None
    assert mgr.status_all()[0]["running"] is False
    # 重启
    mgr.start_server("demo")
    assert mgr.find("demo") is not None and mgr.find("demo").alive
    mgr.close()


def test_registry_registers_and_unregisters_tools(fake_config):
    from core import tools
    mgr = McpManager(config_path=fake_config)
    registry = McpRegistry(manager=mgr)
    # 记录原本的工具集，避免污染其他测试
    orig_all = list(tools.ALL_TOOLS)
    orig_reg = dict(tools.TOOL_REGISTRY)
    try:
        registry.load()
        # 假 server 工具注册名：mcp_demo_ping / mcp_demo_echo
        assert "mcp_demo_ping" in tools.TOOL_REGISTRY
        assert "mcp_demo_echo" in tools.TOOL_REGISTRY
        assert any(t.get("function", {}).get("name") == "mcp_demo_ping"
                   for t in tools.ALL_TOOLS)
        # 调用一次（假 server 返回 pong）
        out = tools.TOOL_REGISTRY["mcp_demo_ping"]()
        assert "pong" in out

        # 停止 -> 工具被注销
        registry.stop("demo")
        assert "mcp_demo_ping" not in tools.TOOL_REGISTRY
        assert all(t.get("function", {}).get("name") != "mcp_demo_ping"
                   for t in tools.ALL_TOOLS)
    finally:
        tools.ALL_TOOLS = orig_all
        tools.TOOL_REGISTRY = orig_reg
        registry.manager.close()


def test_registry_agent_invalidator_called(fake_config):
    mgr = McpManager(config_path=fake_config)
    calls = []
    registry = McpRegistry(manager=mgr, agent_invalidator=lambda: calls.append(1))
    try:
        registry.start("demo")
        assert calls, "启动应触发 agent 失效"
        calls.clear()
        registry.stop("demo")
        assert calls, "停止应触发 agent 失效"
    finally:
        registry.manager.close()


def test_set_enabled_persists(fake_config, tmp_path):
    mgr = McpManager(config_path=fake_config)
    registry = McpRegistry(manager=mgr)
    try:
        registry.set_enabled("demo", False)
        # config 文件应已写入 enabled=false
        cfg = json.loads(Path(fake_config).read_text(encoding="utf-8"))
        assert cfg["servers"][0]["enabled"] is False
    finally:
        registry.manager.close()


def test_start_unknown_returns_error(fake_config):
    mgr = McpManager(config_path=fake_config)
    registry = McpRegistry(manager=mgr)
    res = registry.start("no_such_server")
    assert res.get("ok") is False
    registry.manager.close()
