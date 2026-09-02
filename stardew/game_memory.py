"""
星露谷游戏记忆桥（方向一）：把"跟用户玩星露谷的经历"反写进记忆

设计原则：
- 纯"桥接"：只复用现有 MemoryManager 的能力（add_to_l1 → L1 会话、
  judge_and_extract_facts → L4 重要事实），不改动记忆核心。
- 把 MCP 游戏事件转成"对话-回复"形态喂给记忆管线，完全复用现有记忆机制。
- 可选导入：本模块在使用处 try 包裹；缺依赖/记忆不可用时降级为空操作。
"""
import datetime
from typing import Optional


class GameMemoryBridge:
    """把星露谷游戏事件沉淀进记忆。"""

    def __init__(self, memory_manager=None, role_id: str = "kasumi"):
        # memory_manager: MemoryManager 实例（由外部传入，避免本模块自己装配）
        self.memory = memory_manager
        self.role_id = role_id or "kasumi"

    # ---------- 记录一条"游戏事件" ----------

    def record_event(self, user_id: str, narrative: str, importance_hint: Optional[str] = None):
        """记录一条游戏事件为可记忆文本。

        Args:
            user_id: 用户 id
            narrative: 面向"角色视角"的游戏事件描述，如
                "今天和用户一起在矿井第40层挖到一颗钻石"
            importance_hint: 可选，追加在事件后的强调词（如"对用户很重要"），
                帮助 L4 重要性判定更倾向保留。
        """
        if self.memory is None:
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"{ts} [星露谷] {narrative}"
        if importance_hint:
            line += f"。{importance_hint}"
        # 1) 写进 L1 会话（角色口吻，成为可检索的对话历史）
        try:
            self.memory.add_to_l1(user_id, self.role_id, "assistant", line)
        except Exception as e:
            from core.logger import log_error
            log_error("GameMemory", f"写 L1 失败: {e}")
        # 2) 尝试抽取 L4 重要事实（低于阈值自动跳过，不强制）
        try:
            # 以"事件"为用户侧、"角色回应"为回复侧，喂给事实抽取
            self.memory.judge_and_extract_facts(user_id, self.role_id, narrative, line)
        except Exception as e:
            from core.logger import log_error
            log_error("GameMemory", f"抽取事实失败: {e}")

    # ---------- 查询"游戏回忆" ----------

    def remember(self, user_id: str, query: str, top_k: int = 5) -> str:
        """检索与 query 相关的游戏记忆。

        游戏事件写进了 L1（带 [星露谷] 标记），且 L1 会在对话时被注入 system prompt
        （宠物天然"记得"）。这里按关键词在 L1 历史里筛出相关条目，供主动引用。
        """
        if self.memory is None:
            return ""
        try:
            l1 = self.memory.get_l1(user_id, self.role_id) or []
            # 关键词（query 里拆中文词）+ 标记双重过滤
            q = (query or "").strip()
            keys = [w for w in q.replace(",", " ").split() if w] or [q]
            hits = []
            for m in l1:
                c = m.get("content") or ""
                if "[星露谷]" not in c:
                    continue
                if any(k and k in c for k in keys):
                    hits.append(c)
            return "\n".join(hits[-top_k:]) if hits else ""
        except Exception as e:
            from core.logger import log_error
            log_error("GameMemory", f"检索游戏记忆失败: {e}")
            return ""


# 便捷工厂（可选）
def get_game_memory(memory_manager=None, role_id: str = "kasumi") -> GameMemoryBridge:
    try:
        return GameMemoryBridge(memory_manager, role_id)
    except Exception:
        # 绝对不因这个可选扩展影响主进程
        return None
