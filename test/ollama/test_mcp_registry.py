"""
MCP 注册中心单元/集成测试：
- 用假 MCP server（test/fake_stardew_mcp.py，子进程 JSON-RPC）验证插件化启停：
  * McpManager 支持按 name 启动/停止单个服务 + status_all 透出 enabled/running
  * McpRegistry.start() 把 mcp_* 工具注册进 TOOL_REGISTRY / ALL_TOOLS
  * McpRegistry.stop() 移除对应工具并触发 agent 失效
  * setIsEnabled 持久化开关
- 全部用临时 config，不改动真实 config/mcp.json
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.mcp_client import McpManager, McpError
from core.mcp_registry import McpRegistry


FAKE = str((Path(__file__).resolve().parents[1] / "fake_stardew_mcp.py"))


@pytest.fixture()
def fake_config(tmp_path):
    """生成指向假 server 的 mcp.json。"""
    cfg = {
        "servers": [
            {
                "name": "stardew",
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
    srv = mgr.find("stardew")
    assert srv is not None and srv.alive
    # tools/list 发现 1 个工具
    names = [t["name"] for t in srv.list_tools()]
    assert "stardew_get_state" in names
    # status_all 透出 enabled/running
    status = mgr.status_all()
    assert status[0]["running"] is True
    assert status[0]["enabled"] is True
    # 停止
    mgr.stop_server("stardew")
    assert mgr.find("stardew") is None
    assert mgr.status_all()[0]["running"] is False
    # 重启
    mgr.start_server("stardew")
    assert mgr.find("stardew") is not None and mgr.find("stardew").alive
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
        # 假 server 工具注册名：mcp_stardew_stardew_get_state
        assert "mcp_stardew_stardew_get_state" in tools.TOOL_REGISTRY
        assert any(t.get("function", {}).get("name") == "mcp_stardew_stardew_get_state"
                   for t in tools.ALL_TOOLS)
        # 调用一次（假 server 返回游戏状态 JSON）
        out = tools.TOOL_REGISTRY["mcp_stardew_stardew_get_state"]()
        assert "Farm" in out

        # 停止 -> 工具被注销
        registry.stop("stardew")
        assert "mcp_stardew_stardew_get_state" not in tools.TOOL_REGISTRY
        assert all(t.get("function", {}).get("name") != "mcp_stardew_stardew_get_state"
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
        registry.start("stardew")
        assert calls, "启动应触发 agent 失效"
        calls.clear()
        registry.stop("stardew")
        assert calls, "停止应触发 agent 失效"
    finally:
        registry.manager.close()


def test_set_enabled_persists(fake_config, tmp_path):
    mgr = McpManager(config_path=fake_config)
    registry = McpRegistry(manager=mgr)
    try:
        registry.set_enabled("stardew", False)
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


def test_stardew_join_tool_registered_and_works(fake_config):
    """星露谷桥接工具注册后，应暴露高阶 stardew_join 便捷工具（spawn+set_mode）。"""
    from core import tools
    mgr = McpManager(config_path=fake_config)
    registry = McpRegistry(manager=mgr)
    orig_all = list(tools.ALL_TOOLS)
    orig_reg = dict(tools.TOOL_REGISTRY)
    try:
        registry.load()
        # 高阶加入工具存在且对 LLM 可见
        assert "stardew_join" in tools.TOOL_REGISTRY
        assert any(t.get("function", {}).get("name") == "stardew_join"
                   for t in tools.ALL_TOOLS)
        # 调用它 -> 应 spawn + 设 mode（假 server 返回对应文本）
        res = tools.TOOL_REGISTRY["stardew_join"]({"mode": "player"})
        assert isinstance(res, dict) and res.get("ok") is True
        msg = res["message"]
        assert "spawn" in msg
        assert "player" in msg
    finally:
        tools.ALL_TOOLS = orig_all
        tools.TOOL_REGISTRY = orig_reg
        registry.manager.close()


def test_feed_heartbeat_command_requires_enabled(fake_config):
    """feed_heartbeat_command：心跳未启用时静默返回 False，不抛错、不污染主项目。"""
    mgr = McpManager(config_path=fake_config)
    registry = McpRegistry(manager=mgr)
    try:
        # 心跳构建存在但未 enable（默认）-> 不投递
        assert registry.feed_heartbeat_command("跟我一起出来种地") is False
    finally:
        registry.manager.close()


def test_feed_heartbeat_command_after_enable(fake_config):
    """启用心跳后 feed_heartbeat_command 应写入 pending 并返回 True。"""
    mgr = McpManager(config_path=fake_config)
    registry = McpRegistry(manager=mgr)
    try:
        hb = registry.ensure_heartbeat()
        assert hb is not None
        hb.set_enabled(True)
        assert registry.feed_heartbeat_command("来农场一起挖矿") is True
        assert hb.get_pending_command() == "来农场一起挖矿"
        hb.set_enabled(False)
    finally:
        registry.manager.close()
