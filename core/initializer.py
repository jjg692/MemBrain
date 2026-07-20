"""
系统初始化：AgentFactory、群聊系统、L3、角色事实
"""
from core.memory import SimpleMemory
from core.room.room_manager import RoomManager
from core.room.message_bus import MessageBus
from core.room.role_pool import RoleInstancePool, RoleConfig
from core.info_collector import L3Manager
from core.adapters import OllamaAdapter
from core.config import CHROMA_DB_PATH, LLM_MODEL, TOOL_LLM_MODEL
from agent.graph import LangGraphMemoryAgent


class AppInitializer:
    """统一管理所有全局单例和初始化逻辑"""
    
    def __init__(self, system_prompt: str, api_base: str = None):
        self.system_prompt = system_prompt
        self.api_base = api_base
        
        # 1. 记忆系统
        self.memory = SimpleMemory(path=CHROMA_DB_PATH)
        
        # 2. AgentFactory
        self.agent_factory = self._create_agent_factory()
        
        # 3. 群聊系统
        self.room_manager = RoomManager()
        self.message_bus = MessageBus()
        self.role_pool = RoleInstancePool()
        
        # 4. L3 信息池
        self.l3_manager = L3Manager(self.memory, OllamaAdapter(model=TOOL_LLM_MODEL))
    
    def _create_agent_factory(self):
        class AgentFactory:
            def __init__(self, memory, llm_model, system_prompt, api_base, tool_llm_model, l3_manager):
                self.memory = memory
                self.llm_model = llm_model
                self.tool_llm_model = tool_llm_model
                self.system_prompt = system_prompt
                self.api_base = api_base
                self._agents = {}
                self.l3_manager = l3_manager
            
            def get_agent(self, user_id):
                if user_id not in self._agents:
                    self._agents[user_id] = LangGraphMemoryAgent(
                        memory=self.memory,
                        llm_model=self.llm_model,
                        tool_llm_model=self.tool_llm_model,
                        system_prompt=self.system_prompt,
                        l3_manager=self.l3_manager
                    )
                return self._agents[user_id]
        
        return AgentFactory(
            memory=self.memory,
            llm_model=LLM_MODEL,
            system_prompt=self.system_prompt,
            api_base=self.api_base,
            tool_llm_model=TOOL_LLM_MODEL,
            l3_manager=self.l3_manager
        )
    
    def init_role_facts(self):
        """初始化角色事实到 ChromaDB"""
        from core.role.loader import init_role_to_memory
        tool_adapter = OllamaAdapter(model=TOOL_LLM_MODEL)
        success = init_role_to_memory(
            role_prompt=self.system_prompt,
            role_id="kasumi",
            tool_adapter=tool_adapter,
            memory=self.memory
        )
        if success:
            print("[启动] 角色事实初始化完成")
        else:
            print("[启动] 角色事实初始化失败（不影响运行）")
        
        # 注册角色到角色池
        self.role_pool.register_role(RoleConfig(
            role_id="kasumi",
            display_name="户山香澄",
            role_prompt=self.system_prompt,
        ))
        print("[启动] 角色池已注册: kasumi")
    
    def start_l3_scheduler(self):
        """启动 L3 主动信息池后台任务"""
        import time
        import threading
        from core.config import L3_UPDATE_INTERVAL
        
        l3_manager = self.l3_manager
        memory = self.memory
        
        def background_task():
            active_users = ["web_user", "default_user"]
            for user_id in active_users:
                facts = memory.get_facts(user_id, n=5)
                interests = []
                for f in facts:
                    doc = f["document"]
                    if "喜欢" in doc or "偏好" in doc:
                        interests.append(doc[:20])
                if interests:
                    l3_manager.update_for_user(user_id, interests)
        
        def scheduler():
            while True:
                try:
                    background_task()
                except Exception as e:
                    print(f"[L3] 后台任务异常: {e}")
                time.sleep(L3_UPDATE_INTERVAL)
        
        thread = threading.Thread(target=scheduler, daemon=True)
        thread.start()
        print("[启动] L3 主动信息池后台任务已启动")
    