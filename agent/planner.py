"""
任务规划器（TaskPlanner）——窗口 A（AI Agent 助手）M2：长程自主 / 任务循环

目的：把 Agent 从「一句一答」升级为「多步任务」。在用户消息进入 LangGraph 主流程时，
用一个**纯函数、确定性**的规划器判断：

    1. 这条消息是「简单一句消费」（单句/单个工具即可解决）——不需要任务循环；
    2. 还是「需要多步」——答案有明显依赖顺序 / 需多个工具串联，需要 plan → act → observe。

设计原则：
- **纯函数、无副作用**：`should_plan` / `plan` 只依赖入参文本，不访问 IO，不调用 LLM。
  这样保证 (a) 离线可复现、可单测；(b) 不改变既有假 LLM 测试的 enqueue 顺序（不新增 LLM 调用）。
- **确定性分界**：以「消息中命中 ≥2 个**可行动工具意图**」作为进入任务循环的边界。
  纯闲聊 / 单个工具请求（即使句子较长）不触发，保持既有单轮行为不回归。
- 规划只产出「任务骨架」（目标 goal + 有序子步 steps + 每步工具提示 tool_hint），
  真正逐步执行仍交给已有 LangGraph ReAct 循环（LLM 自主路由 + ToolNode + observe 记录）。

意图检测复用 core.tools 的天气/百科词表，并对提醒/时间/文件/PC 控制等内置工具建立本地词表，
避免引入跨模块耦合（本工具只读，不修改 core.tools）。
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ===================== 工具意图词表（只读辅助，供步骤工具提示） =====================

# 与 core.tools.WEATHER_HINTS / WIKI_HINTS 保持一致的轻量判定（副本，避免耦合）
_WEATHER_HINTS = [
    "天气", "气温", "温度", "降雨", "下雪", "雨", "雪", "℃", "度", "预报",
    "晴", "阴", "大风", "台风", "雾霾", "湿度", "weather",
]
_WIKI_HINTS = [
    "是什么", "什么是", "是谁", "谁", "简介", "背景", "历史", "概念",
    "定义", "由来", "含义", "哪个", "著名", "介绍", "百科",
]
# 各内置工具的本地触发词（只用于给子步贴 tool_hint，不改变 core.tools 的注册）
_REMIND_HINTS = ["提醒", "设提醒", "记", "定时", "别忘", "到点", "提醒我"]
_TIME_HINTS = ["几点", "时间", "现在几点了", "几点钟", "什么时候了"]
_FILE_HINTS = ["读文件", "写文件", "打开文件", "读一下", "写一下", "文件", "笔记", "notes"]
_PC_HINTS = ["打开", "启动", "关机", "锁屏", "应用", "程序", "音量", "静音", "控制电脑"]


def _detect_single_intent(text: str) -> Optional[str]:
    """检测一段文本命中的意图 -> 对应的内置工具名（若命中）。None=无明确工具。"""
    t = (text or "").lower()
    for h in _WEATHER_HINTS:
        if h in t:
            return "search_web"
    for h in _WIKI_HINTS:
        if h in t:
            return "search_web"
    for h in _REMIND_HINTS:
        if h in t:
            return "remind_me"
    for h in _TIME_HINTS:
        if h in t:
            return "get_current_time"
    for h in _FILE_HINTS:
        if h in t:
            return "file"
    for h in _PC_HINTS:
        if h in t:
            return "control_pc"
    return None


def _split_clauses(text: str) -> List[str]:
    """把消息按逗号/句读/连接词切分为子句，供拆解子步用。"""
    parts = re.split(
        r"[，,。！？!?；;]|然后|再|顺便|并且|同时|接着|最后|先|首先|其次",
        text or "",
    )
    out = [p.strip() for p in parts if p.strip()]
    return out


@dataclass
class TaskStep:
    """任务的一个子步。status: pending | in_progress | done"""
    index: int
    description: str
    tool_hint: Optional[str] = None
    status: str = "pending"

    def to_dict(self) -> Dict:
        return {
            "index": self.index,
            "description": self.description,
            "tool_hint": self.tool_hint,
            "status": self.status,
        }


@dataclass
class TaskPlan:
    """一个多步任务骨架（仅规划，不含执行状态）。"""
    goal: str
    steps: List[TaskStep] = field(default_factory=list)
    source: str = "heuristic"

    def to_dict(self) -> Dict:
        return {
            "goal": self.goal,
            "source": self.source,
            "steps": [s.to_dict() for s in self.steps],
        }

    def total_steps(self) -> int:
        return len(self.steps)


class TaskPlanner:
    """多步任务分界 + 骨架规划。纯函数、确定性。"""

    @staticmethod
    def should_plan(user_msg: str) -> bool:
        """判断消息是否需要进入任务循环（多步）。

        确定性边界：消息中命中 **≥2 个不同的可行动工具意图** 才算多步。
        - 单个工具请求 / 纯闲聊（即使句子长、有逗号）不触发 → 保持单轮行为。
        - 聊天再自然也不误伤：只有真需要串多个工具才进任务。
        """
        intents = TaskPlanner._distinct_intents(user_msg)
        return len(intents) >= 2

    @staticmethod
    def plan(user_msg: str) -> Optional[TaskPlan]:
        """若为多步任务，返回 TaskPlan 骨架；否则返回 None（简单单轮消费）。

        拆解：把消息切成子句，按顺序为每个「可归属工具意图的子句」生成一个子步。
        子步的 tool_hint 只作提示，真正执行由 LLM 在 ReAct 循环里自主路由。
        """
        intents = TaskPlanner._distinct_intents(user_msg)
        if len(intents) < 2:
            return None

        clauses = _split_clauses(user_msg)
        steps: List[TaskStep] = []
        seen: set = set()
        idx = 0
        for clause in clauses:
            hint = _detect_single_intent(clause)
            if hint is None:
                continue
            key = clause  # 同子句不重复
            if key in seen:
                continue
            seen.add(key)
            steps.append(TaskStep(
                index=idx,
                description=clause or f"子任务 {idx + 1}",
                tool_hint=hint,
            ))
            idx += 1

        # 兜底：若词表未能从子句归属到 ≥2 步（一般不会），按意图名生成占位步
        if len(steps) < 2:
            steps = []
            for i, intent in enumerate(intents):
                steps.append(TaskStep(
                    index=i,
                    description=f"子任务 {i + 1}：{intent}",
                    tool_hint=intent if intent in ("search_web", "remind_me",
                                                   "get_current_time", "control_pc", "file") else None,
                ))

        return TaskPlan(goal=user_msg, steps=steps)

    @staticmethod
    def _distinct_intents(user_msg: str) -> List[str]:
        """返回消息中命中的【不同】工具意图（保持出现顺序）。"""
        intents: List[str] = []
        for clause in _split_clauses(user_msg):
            hint = _detect_single_intent(clause)
            if hint is None:
                continue
            if hint not in intents:
                intents.append(hint)
        return intents
