"""
应用初始化器：组装所有单例依赖
- 记忆（SimpleMemory + MemoryManager）
- 角色管理（RoleManager + L5 启动加载）
- 情感（EmotionStore）
- Agent 工厂（按 (user_id, role_id) 提供 Agent 实例）
- 房间 / 消息总线
"""
from typing import Dict, Optional, Tuple

from core.config import LLM_MODEL, TOOL_LLM_MODEL, OLLAMA_HOST, LLM_TEMPERATURE
from core.logger import log_info, log_error
from core.adapters import OllamaAdapter

from core.memory.vector_store import SimpleMemory
from core.memory.memory_manager import MemoryManager
from core.role.manager import RoleManager
from core.emotion import EmotionStore
from core.memory.l3 import L3Collector, L3Pusher
from core.config import L3_ENABLED
from core.room.message_bus import MessageBus
from core.room.room_manager import RoomManager

from agent.graph import LangGraphMemoryAgent


class AgentFactory:
    """按 (user_id, role_id) 缓存 Agent 实例"""

    def __init__(self, initializer: "AppInitializer"):
        self.initializer = initializer
        self._cache: Dict[Tuple[str, str], LangGraphMemoryAgent] = {}

    def get_agent(self, user_id: str, role_id: str) -> LangGraphMemoryAgent:
        key = (user_id, role_id)
        if key not in self._cache:
            agent = LangGraphMemoryAgent(
                memory_manager=self.initializer.memory_manager,
                role_manager=self.initializer.role_manager,
                emotion_store=self.initializer.emotion_store,
                role_id=role_id,
                message_bus=self.initializer.message_bus,
            )
            self._cache[key] = agent
        return self._cache[key]

    def get_role_agent(self, role_id: str) -> LangGraphMemoryAgent:
        """群聊用：按角色一个共享实例（用 __room__ 作为 user 维度）"""
        return self.get_agent("__room__", role_id)


class AppInitializer:
    def __init__(self):
        # 记忆
        self.memory = SimpleMemory()
        self.llm_adapter = OllamaAdapter(model=LLM_MODEL, host=OLLAMA_HOST).set_temperature(LLM_TEMPERATURE)
        self.tool_adapter = OllamaAdapter(model=TOOL_LLM_MODEL, host=OLLAMA_HOST)
        self.memory_manager = MemoryManager(self.memory, self.tool_adapter)

        # 角色 + 情感
        self.role_manager = RoleManager()
        self.emotion_store = EmotionStore(self.memory)

        # 房间
        self.message_bus = MessageBus()
        self.room_manager = RoomManager()

        # Agent 工厂
        self.agent_factory = AgentFactory(self)

        # L3 主动信息池（采集 + 推送）
        self.l3_collector = L3Collector(self.memory, self.tool_adapter)
        self.l3_pusher = L3Pusher(
            self.memory, self.agent_factory,
            push_callback=self._l3_push_callback,
        )
        self._l3_stop = None  # 由 web_app 启动线程时赋值

        log_info("Init", "AppInitializer 组装完成")

    def load_all_role_facts(self):
        """启动时一次性加载所有角色的 L5 事实（避免首次切换延迟）"""
        for role in self.role_manager.all_roles():
            prompt_text = self.role_manager.load_prompt(role.role_id)
            if not prompt_text:
                continue
            try:
                self.memory_manager.ensure_role_facts(role.role_id, prompt_text)
            except Exception as e:
                log_error("L5", f"角色 {role.role_id} 事实加载失败: {e}")
        log_info("L5", "所有角色 L5 事实加载完成")

    def _l3_push_callback(self, user_id: str, data: dict):
        """把主动消息推送到用户私聊 WS；在 async 上下文外调用，需调度到事件循环"""
        from api.websocket_manager import single_ws_manager
        ws = single_ws_manager.get(user_id)
        if ws is None:
            return
        try:
            # push 回调在后台线程调用；通过 asyncio 主循环推送
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    single_ws_manager.push_to_user(user_id, data), loop
                )
            else:
                # 未运行循环时直接同步不安全，仅记日志
                pass
        except Exception as e:
            log_error("L3", f"推送调度失败 {user_id}: {e}")

    def start_l3_loops(self):
        """启动 L3 采集/推送后台线程（幂等）"""
        if not L3_ENABLED:
            log_info("L3", "L3 已禁用（L3_ENABLED=false），跳过启动")
            return
        if self._l3_stop is None:
            import threading
            self._l3_stop = threading.Event()
            t1 = threading.Thread(target=self.l3_collector.start_loop, args=(self._l3_stop,), daemon=True)
            t2 = threading.Thread(target=self.l3_pusher.start_loop, args=(self._l3_stop,), daemon=True)
            t1.start(); t2.start()
            log_info("L3", "L3 采集与推送线程已启动")

    def stop_l3_loops(self):
        if self._l3_stop is not None:
            self._l3_stop.set()
            log_info("L3", "L3 线程已请求停止")

    def get_contact_info(self) -> list:
        return self.role_manager.list_contacts()

    def get_online_counts(self) -> dict:
        from api.websocket_manager import single_ws_manager, room_ws_manager
        return {
            "private": len(single_ws_manager.get_all()),
            "room": room_ws_manager.count(),
        }
