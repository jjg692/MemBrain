"""
星露谷自主游戏层（stardew/autonomy.py）单元测试 —— Node 桥接版。

对齐仓库自带 StardewMCPBridge（mcp-server/src/index.ts，25 工具）：
工具经 MCP 客户端注册为 `mcp_<server>_stardew_<tool>`，这里用真实工具名测试。

覆盖：
- snapshot：汇总只读工具（get_state/surroundings/inventory）为结构化快照
- enter_game：进场（spawn）+ 模式（player/follow）
- act：执行操作并沉淀记忆
- decide / snapshot_narrative / set_mode / 容错
全部通过注入假工具到 TOOL_REGISTRY，不依赖真实游戏/LLM。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import tools
from stardew.autonomy import StardewAutonomy, _find_tool


class FakeBridge:
    def __init__(self):
        self.events = []
    def record_event(self, user_id, narrative, importance_hint=None):
        self.events.append(narrative)


@pytest.fixture()
def fake_registry(monkeypatch):
    """注入真实桥接假工具到 TOOL_REGISTRY（注册名 = mcp_stardew_stardew_<tool>）。"""
    fn = {
        # 只读
        "mcp_stardew_stardew_get_state": lambda **k: json.dumps({
            "time": 1000, "day": 15, "season": "spring", "weather": "sunny",
            "location": "Farm",
            "player": {"name": "kasumi", "money": 999, "health": 100},
            "companions": [],
        }),
        "mcp_stardew_stardew_get_inventory": lambda **k: json.dumps({
            "inventory": [{"name": "钻石", "count": 1}],
        }),
        # 操作
        "mcp_stardew_stardew_chat": lambda **k: "已发送聊天",
        "mcp_stardew_stardew_move_to": lambda **k: "已移动",
        "mcp_stardew_stardew_face_direction": lambda **k: "已转向",
        # 进场 + 模式
        "mcp_stardew_stardew_spawn": lambda **k: "Command sent.",
        "mcp_stardew_stardew_set_mode": lambda **k: "mode set",
    }
    for name, impl in fn.items():
        impl.__doc__ = "test tool"
    for name, impl in fn.items():
        monkeypatch.setitem(tools.TOOL_REGISTRY, name, impl)
    yield


def test_find_tool_suffix():
    import core.tools as T
    T.TOOL_REGISTRY["mcp_x_stardew_get_state"] = lambda **k: ""
    try:
        # 后缀匹配 + 包含 stardew_ 匹配都应命中
        assert _find_tool("stardew_get_state") == "mcp_x_stardew_get_state"
        assert _find_tool("get_state") == "mcp_x_stardew_get_state"
    finally:
        T.TOOL_REGISTRY.pop("mcp_x_stardew_get_state", None)


def test_available_and_snapshot(fake_registry):
    au = StardewAutonomy(memory_bridge=FakeBridge(), role_id="kasumi")
    assert au.available is True
    snap = au.snapshot()
    assert snap["available"] is True
    # player 来自 get_state 的 player
    p = snap["player"]
    assert p is not None and p.get("money") == 999
    # world 来自 get_state 顶层
    w = snap["world"]
    assert w is not None and w.get("season") == "spring"
    # companions
    assert snap["companions"] == []
    # location
    assert snap["location"] == "Farm"


def test_snapshot_no_available_no_fake(monkeypatch):
    from core import tools as T
    saved = {k: v for k, v in T.TOOL_REGISTRY.items() if k.startswith("mcp_")}
    for k in list(saved):
        monkeypatch.delitem(T.TOOL_REGISTRY, k)
    try:
        au = StardewAutonomy(memory_bridge=FakeBridge())
        snap = au.snapshot()
        assert snap["available"] is False
        assert snap["player"] is None
        assert au.available is False
    finally:
        T.TOOL_REGISTRY.update(saved)


def test_enter_game_spawns_and_sets_player_mode(fake_registry):
    au = StardewAutonomy(memory_bridge=FakeBridge())
    res = au.enter_game("u", auto_join_mode="player")
    assert res["ok"] is True
    msg = res["message"]
    assert "spawn" in msg
    assert "player" in msg  # set_mode 到 player 被调用


def test_enter_game_follow_mode(fake_registry):
    au = StardewAutonomy(memory_bridge=FakeBridge())
    res = au.enter_game("u", auto_join_mode="follow", companion="Companion1")
    assert res["ok"] is True
    assert "Companion1" in res["message"]
    assert "follow" in res["message"]


def test_enter_game_unavailable_no_spawn(monkeypatch):
    from core import tools as T
    saved = {k: v for k, v in T.TOOL_REGISTRY.items() if k.startswith("mcp_")}
    for k in list(saved):
        monkeypatch.delitem(T.TOOL_REGISTRY, k)
    try:
        au = StardewAutonomy(memory_bridge=FakeBridge())
        res = au.enter_game("u")
        assert res["ok"] is False
        assert "未就绪" in res["message"]
    finally:
        T.TOOL_REGISTRY.update(saved)


def test_act_executes_and_records(fake_registry):
    bridge = FakeBridge()
    au = StardewAutonomy(memory_bridge=bridge, role_id="kasumi")
    res = au.act("default_user", "stardew_chat", {"message": "你好"})
    assert res  # 应有返回
    assert any("stardew_chat" in e for e in bridge.events)


def test_act_unknown_tool():
    au = StardewAutonomy(memory_bridge=FakeBridge())
    res = au.act("u", "stardew_no_such_action", {})
    assert "不可用" in res


def test_set_mode_player(fake_registry):
    au = StardewAutonomy(memory_bridge=FakeBridge())
    res = au.set_mode("u", "player", companion="Companion2")
    assert res  # 非空（调用了 set_mode）


def test_decide_returns_candidates(fake_registry):
    au = StardewAutonomy(memory_bridge=FakeBridge())
    snap = au.snapshot()
    cand = au.decide(snap)
    # companions 为空 -> 应先建议进场
    assert "enter_game" in cand
    assert "stardew_chat" in cand
    # 无快照时为空
    assert au.decide({}) == []


def test_snapshot_narrative(fake_registry):
    au = StardewAutonomy(memory_bridge=FakeBridge())
    snap = au.snapshot()
    narr = au.snapshot_narrative(snap)
    assert narr != ""
    assert au.snapshot_narrative({"available": False}) == ""


def test_read_json_tolerates_garbage():
    au = StardewAutonomy()
    assert au._read_json("mcp_stardew_stardew_get_state") is None  # 未注入 -> 空


def test_read_json_rejects_error_dict(monkeypatch):
    """桥接返回 {"error": "Bridge file not found"} 时应判定未就绪，不伪造状态。"""
    from core import tools as T
    monkeypatch.setitem(T.TOOL_REGISTRY, "mcp_s_stardew_get_state",
                        lambda **k: '{"error": "Bridge file not found. Is the SMAPI mod running?"}')
    au = StardewAutonomy()
    assert au._read_json("mcp_s_stardew_get_state") is None
    # snapshot 也不应 available
    snap = au.snapshot()
    assert snap["available"] is False
