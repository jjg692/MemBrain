"""
星露谷游戏记忆桥（stardew/game_memory.py）单元测试：
- record_event：写 L1 + 去重 + importance_hint
- record_scene / record_milestone
- remember：关键词过滤 + 最新优先 + 多关键词命中
- recent：最近 N 条
用假 MemoryManager，不依赖真实记忆后端/LLM。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stardew.game_memory import GameMemoryBridge


class FakeMemory:
    """模拟 MemoryManager 的 add_to_l1 / get_l1 / judge_and_extract_facts。"""

    def __init__(self):
        self.l1_entries = []  # 按加入顺序的完整 L1 列表（list[dict]）
        self.added = []       # 记录 add_to_l1 的参数
        self.extract_calls = []

    # ---- 模拟 MemoryManager ----
    def add_to_l1(self, user_id, role_id, speaker, content):
        self.l1_entries.append({"speaker": speaker, "content": content})
        self.added.append(content)

    def get_l1(self, user_id, role_id):
        return list(self.l1_entries)

    def judge_and_extract_facts(self, *a, **k):
        self.extract_calls.append((a, k))


@pytest.fixture()
def bridge():
    return GameMemoryBridge(memory_manager=FakeMemory(), role_id="kasumi")


def test_record_event_writes_l1(bridge):
    bridge.record_event("u", "今天在矿洞挖到一颗钻石")
    assert len(bridge.memory.l1_entries) == 1
    line = bridge.memory.l1_entries[0]["content"]
    assert "[星露谷]" in line
    # 触发事实抽取
    assert len(bridge.memory.extract_calls) == 1


def test_record_event_dedup(bridge):
    bridge.record_event("u", "站在农场门口")
    bridge.record_event("u", "站在农场门口")  # 与上一条相同 -> 跳过
    assert len(bridge.memory.l1_entries) == 1


def test_record_event_with_importance_hint(bridge):
    bridge.record_event("u", "钓到了传说鱼", importance_hint="对一起玩的回忆很重要")
    line = bridge.memory.l1_entries[0]["content"]
    assert "对一起玩的回忆很重要" in line


def test_record_event_no_memory_noop():
    b = GameMemoryBridge(memory_manager=None)
    # 不应报错
    b.record_event("u", "x")
    b.remember("u", "x")
    b.recent("u")
    b.record_scene("u", "y")
    b.record_milestone("u", "z")


def test_record_scene_and_milestone(bridge):
    bridge.record_scene("u", "来到矿洞第40层")
    bridge.record_milestone("u", "挖到钻石")
    # milestone 带强 importance_hint
    milestone_line = bridge.memory.l1_entries[1]["content"]
    assert "对一起玩的回忆很重要" in milestone_line
    assert len(bridge.memory.l1_entries) == 2


def test_remember_orders_newest_first(bridge):
    bridge.record_event("u", "第一次采到蓝莓")
    bridge.record_event("u", "和用户一起去河边钓鱼")
    bridge.record_event("u", "又去河边钓鱼")
    # 检索"钓鱼"应命中后两条，最新（又去河边钓鱼）在前
    res = bridge.remember("u", "钓鱼")
    lines = [l for l in res.split("\n") if l]
    assert len(lines) == 2
    assert "又去河边钓鱼" in lines[0]  # 最新优先
    assert "和用户一起去河边钓鱼" in lines[1]


def test_remember_multi_keyword(bridge):
    bridge.record_event("u", "在矿洞挖到钻石")
    bridge.record_event("u", "在河边钓到鱼")
    # 多关键词任一命中
    res = bridge.remember("u", "钻石 鱼")
    assert "钻石" in res
    assert "鱼" in res


def test_remember_ignores_non_game(bridge):
    bridge.memory.add_to_l1("u", "kasumi", "assistant", "普通对话，无标记")
    bridge.record_event("u", "[星露谷] 金色消息")  # content里含标记（人为）
    res = bridge.remember("u", "普通")
    # 无 [星露谷] 标记的普通对话应被过滤
    assert "普通对话" not in res


def test_recent_returns_latest_first(bridge):
    for t in ("事件一", "事件二", "事件三"):
        bridge.record_event("u", t)
    res = bridge.recent("u", top_k=2)
    lines = [l for l in res.split("\n") if l]
    assert len(lines) == 2
    assert "事件三" in lines[0]
    assert "事件二" in lines[1]
