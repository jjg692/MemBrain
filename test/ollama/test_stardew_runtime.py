"""
stardew/runtime.is_state_tool_name 单测：
- 兼容 luy-0 现代只读工具（query_runtime 等）
- 兼容 amarisaster 旧工具（get_state）
- 非状态工具 / 非 mcp_ 前缀为 False
"""
from stardew.runtime import is_state_tool_name, GameStatePoller


def test_luy0_query_runtime_matches():
    assert is_state_tool_name("mcp_stardew_query_runtime") is True
    assert is_state_tool_name("mcp_stardew_query_world") is True
    assert is_state_tool_name("mcp_stardew_query_players") is True
    assert is_state_tool_name("mcp_stardew_query_inventory") is True
    assert is_state_tool_name("mcp_stardew_query_ui") is True
    assert is_state_tool_name("mcp_stardew_inspect") is True


def test_legacy_get_state_matches():
    assert is_state_tool_name("mcp_stardew_stardew_get_state") is True


def test_non_state_tool_false():
    assert is_state_tool_name("mcp_stardew_say") is False
    assert is_state_tool_name("mcp_stardew_face") is False
    assert is_state_tool_name("search_web") is False
    assert is_state_tool_name("") is False
    assert is_state_tool_name(None) is False


def test_find_first_match_in_tool_registry():
    # 同时注册 luy-0 与旧工具名，_find_state_tool_name 应能命中其中一个
    from core.tools import TOOL_REGISTRY
    TOOL_REGISTRY["mcp_stardew_query_runtime"] = lambda *a, **k: ""
    try:
        found = GameStatePoller._find_state_tool_name()
        assert found and is_state_tool_name(found)
    finally:
        TOOL_REGISTRY.pop("mcp_stardew_query_runtime", None)
