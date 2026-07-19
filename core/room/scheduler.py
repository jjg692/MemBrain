"""
发言调度器
决定群聊中"下一个谁说话"
支持三种策略：轮流 / 按话题 / 随机
支持抢话机制（urgency 优先级）
"""

import random
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core.room.room_manager import Room
from core.room.message_bus import ChatMessage
from core.logger import log_debug


@dataclass
class AgentTurnState:
    """每个 Agent 的发言状态"""
    role_id: str
    last_spoke_at: Optional[datetime] = None   # 上次发言时间
    speak_count: int = 0                       # 本轮发言次数
    urgency: float = 0.0                       # 当前抢话优先级 (0~1)


# ==================== 策略类型 ====================

class SpeakingStrategy:
    """发言策略基类"""
    def decide(self, agents: Dict[str, AgentTurnState], context: dict) -> Optional[str]:
        """返回选中的 role_id，None 表示无法决定"""
        raise NotImplementedError


class RoundRobinStrategy(SpeakingStrategy):
    """轮流制：按顺序轮流发言"""
    def __init__(self):
        self._order: List[str] = []
        self._index: int = 0

    def decide(self, agents: Dict[str, AgentTurnState], context: dict) -> Optional[str]:
        online = [rid for rid, state in agents.items() if state.last_spoke_at is not None or True]
        if not online:
            return None

        # 重新构建顺序（加入新来的 Agent）
        for rid in online:
            if rid not in self._order:
                self._order.append(rid)

        # 移除已不在线的
        self._order = [rid for rid in self._order if rid in online]

        if not self._order:
            return None

        # 取下一个
        selected = self._order[self._index % len(self._order)]
        self._index = (self._index + 1) % len(self._order)
        return selected

    def reset(self):
        self._order = []
        self._index = 0


class TopicRelevanceStrategy(SpeakingStrategy):
    """
    按话题相关性
    根据当前讨论话题，选出与话题最相关的 Agent
    需要外部提供相关性评分函数
    """
    def __init__(self, relevance_fn: Optional[Callable[[str, str], float]] = None):
        """
        Args:
            relevance_fn: (role_id, topic) → 相关性分数 (0~1)
                          如果不提供，默认返回 0.5
        """
        self.relevance_fn = relevance_fn

    def decide(self, agents: Dict[str, AgentTurnState], context: dict) -> Optional[str]:
        topic = context.get("topic", "")
        if not topic:
            return None

        online = [rid for rid in agents.keys()]
        if not online:
            return None

        if self.relevance_fn:
            scored = [(rid, self.relevance_fn(rid, topic)) for rid in online]
        else:
            scored = [(rid, 0.5) for rid in online]

        # 选出相关性最高的
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored else None


class RandomStrategy(SpeakingStrategy):
    """随机策略"""
    def decide(self, agents: Dict[str, AgentTurnState], context: dict) -> Optional[str]:
        online = list(agents.keys())
        return random.choice(online) if online else None


# ==================== 调度器主类 ====================

class SpeakingScheduler:
    """
    发言调度器
    管理每个 Agent 的发言状态，决定下一个发言人
    """

    def __init__(self, strategy: str = "round_robin"):
        """
        Args:
            strategy: "round_robin" | "topic" | "random"
        """
        self.turn_states: Dict[str, AgentTurnState] = {}
        self._strategy = self._create_strategy(strategy)
        self._strategy_name = strategy
        # 抢话冷却时间（秒），同一 Agent 发言后需等待
        self.cooldown_seconds = 2.0
        # 是否启用抢话（启用时，urgency 高的 Agent 可打断轮流）
        self.allow_preemption = True

    def _create_strategy(self, strategy: str) -> SpeakingStrategy:
        if strategy == "round_robin":
            return RoundRobinStrategy()
        elif strategy == "topic":
            return TopicRelevanceStrategy()
        elif strategy == "random":
            return RandomStrategy()
        else:
            return RoundRobinStrategy()

    def register_agent(self, role_id: str):
        """注册一个新 Agent 到调度器"""
        if role_id not in self.turn_states:
            self.turn_states[role_id] = AgentTurnState(role_id=role_id)

    def unregister_agent(self, role_id: str):
        """移除一个 Agent"""
        self.turn_states.pop(role_id, None)

    def update_urgency(self, role_id: str, urgency: float):
        """
        更新 Agent 的抢话优先级（由 Agent 自身在生成回复时设置）
        
        Args:
            role_id: 角色 ID
            urgency: 0~1，越高越想抢话
        """
        state = self.turn_states.get(role_id)
        if state:
            state.urgency = max(0.0, min(1.0, urgency))

    def record_speak(self, role_id: str):
        """记录 Agent 发言"""
        state = self.turn_states.get(role_id)
        if state:
            state.last_spoke_at = datetime.now()
            state.speak_count += 1
            # 发言后 urgency 自动降低
            state.urgency = max(0.0, state.urgency - 0.3)

    def _is_in_cooldown(self, role_id: str) -> bool:
        """检查 Agent 是否在冷却中"""
        state = self.turn_states.get(role_id)
        if not state or not state.last_spoke_at:
            return False
        return (datetime.now() - state.last_spoke_at) < timedelta(seconds=self.cooldown_seconds)

    def decide_next(self, room: Room, context: dict = None) -> Optional[str]:
        """
        决定下一个发言的 Agent

        Args:
            room: 房间实例
            context: 上下文信息，包含 {"topic": "...", "last_message": "...", ...}

        Returns:
            选中的 role_id，None 表示无需发言
        """
        if context is None:
            context = {}

        # 确保所有在线 Agent 已注册
        for role_id in room.get_online_agent_list():
            if role_id not in self.turn_states:
                self.register_agent(role_id)

        # 过滤掉冷却中的 Agent
        eligible = {
            rid: state
            for rid, state in self.turn_states.items()
            if rid in room.get_online_agent_list() and not self._is_in_cooldown(rid)
        }

        if not eligible:
            return None

        # 抢话检测：如果有 Agent urgency > 0.7，直接选中
        if self.allow_preemption:
            urgent_candidates = [
                (rid, state.urgency)
                for rid, state in eligible.items()
                if state.urgency > 0.7
            ]
            if urgent_candidates:
                # 选 urgency 最高的
                urgent_candidates.sort(key=lambda x: x[1], reverse=True)
                selected = urgent_candidates[0][0]
                log_debug("调度器", f"抢话: {selected} (urgency={urgent_candidates[0][1]:.2f})")
                return selected

        # 按策略选择
        selected = self._strategy.decide(eligible, context)

        if selected:
            log_debug("调度器", f"策略({self._strategy_name})选中: {selected}")

        return selected

    def reset(self):
        """重置调度器状态"""
        self.turn_states.clear()
        if hasattr(self._strategy, 'reset'):
            self._strategy.reset()
