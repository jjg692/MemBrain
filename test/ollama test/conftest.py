"""
pytest 共享脚手架 / 夹具
================================================
为"MemBrain 桌面宠物"核心功能提供隔离、确定性的单元测试环境：

1. fake embedding：用哈希向量代替 Ollama / sentence-transformers，
   使 ChromaDB 记忆测试完全离线、可复现、快速。
2. FakeAdapter：用脚本化/默认响应代替真实 LLM，
   覆盖 emotion 分析 / 工具调用 / 指代消解等各类 prompt，避免联网。
3. 全部数据（chromadb / 感知 / 提醒 / 用户资料 / 角色）写入 pytest 临时目录，
   绝不污染项目真实数据（chromadb/、perception.json、reminders.json、user_profiles.json）。
"""
import hashlib
from pathlib import Path

import pytest

# 一个"合法的情感分析 JSON"默认响应（作为 tool 适配器的兜底）。
# 注意：其中第一个 \d+ 命中为 0.0，使 _judge_importance 判为 0.0 < 0.6，从而跳过事实抽取，
# 避免后台线程在无关测试里额外写 L4。
DEFAULT_EMOTION_JSON = (
    '{"emotion":{"primary":"开心","intensity":0.6,"valence":0.4,"description":"用户看起来心情不错"},'
    '"affection":{"liking":0.6,"trust":0.5,"familiarity":0.5,"respect":0.5,"interest":0.6,"attachment":0.3},'
    '"needs_tool":false,"tool_decision":""}'
)

# 一个合法的角色事实 JSON（供 L5 / 助手默认兜底）
DEFAULT_ROLE_FACTS_JSON = '["户山香澄是Popping Party的主唱与主音吉他手","户山香澄以元气活泼的性格著称"]'


# ===================== 假嵌入函数 =====================

class FakeEmbedding:
    """确定性哈希向量嵌入，替代 Ollama/本地模型。"""
    NAME = "fake-hash-embedding"

    def name(self) -> str:
        return self.NAME

    def _vec(self, text: str, dim: int = 384) -> list:
        v = [0.0] * dim
        for ch in str(text or ""):
            h = int(hashlib.md5(ch.encode("utf-8", "ignore")).hexdigest()[:8], 16)
            v[h % dim] += 1.0
        # 归一化（避免全零向量，cosine 不稳定）
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [round(x / norm, 5) for x in v]

    def __call__(self, input):
        if isinstance(input, str):
            input = [input]
        return [self._vec(t) for t in input]

    def embed_query(self, input=None, query=None):
        if query is not None:
            texts = [query] if isinstance(query, str) else list(query)
        elif input is not None:
            texts = [input] if isinstance(input, str) else list(input)
        else:
            texts = [""]
        return [self._vec(t) for t in texts]


# ===================== 假 LLM 适配器 =====================

class FakeAdapter:
    """
    模拟 LLMAdapter 接口（chat / chat_with_tools）。
    - 可向队列预置按序返回的响应
    - 记录每次收到的 messages（供断言：如 L1 全量历史是否注入、情感分析是否发生）
    - 队列取空后回退 default_*，保证无关调用确定返回、不抛错
    """

    def __init__(self, name="fake", default_chat="", default_tools=None):
        self.name = name
        self.chat_queue: list = []
        self.tools_queue: list = []
        self.default_chat = default_chat
        self.default_tools = default_tools or {"content": "", "tool_calls": []}
        # 记录调用历史
        self.sent_chat: list = []      # 每次 chat 收到的 messages
        self.sent_tools: list = []     # 每次 chat_with_tools 收到的 (messages, tools)
        self.chat_call_count = 0
        self.tools_call_count = 0

    # ---- 预置响应 ----
    def enqueue_chat(self, text):
        self.chat_queue.append(str(text))
        return self

    def enqueue_tools(self, content="", tool_calls=None):
        """
        预置一次 chat_with_tools 的返回。
        content 接受字符串 或 {'content':..., 'tool_calls':...} 两种写法（归一化），
        保证返回的 content 始终是字符串。
        """
        if isinstance(content, dict):
            item = dict(content)
            item.setdefault("tool_calls", [])
            item["content"] = item.get("content", "")
        else:
            item = {"content": str(content), "tool_calls": tool_calls or []}
        self.tools_queue.append(item)
        return self

    # ---- 接口 ----
    def chat(self, messages, **kwargs) -> str:
        self.sent_chat.append(list(messages))
        self.chat_call_count += 1
        if self.chat_queue:
            return self.chat_queue.pop(0)
        return self.default_chat

    def chat_with_tools(self, messages, tools=None, **kwargs) -> dict:
        self.sent_tools.append((list(messages), tools))
        self.tools_call_count += 1
        if self.tools_queue:
            item = self.tools_queue.pop(0)
            if isinstance(item, str):
                item = {"content": item, "tool_calls": []}
            item = dict(item)
            return {"content": item.get("content", ""), "tool_calls": item.get("tool_calls", [])}
        return dict(self.default_tools)

    # ---- 便捷断言辅助 ----
    def all_chat_text(self) -> str:
        """所有 chat 调用的拼接文本（用于检查某段 prompt 是否出现）"""
        out = []
        for msgs in self.sent_chat:
            for m in msgs:
                out.append(str(m.get("content", "")))
        return "\n".join(out)

    def all_tools_text(self) -> str:
        out = []
        for msgs, _tools in self.sent_tools:
            for m in msgs:
                out.append(str(m.get("content", "")))
        return "\n".join(out)


# ===================== 角色管理（内存桩，避免读真实 roles.json） =====================

def make_role_manager(role_id="kasumi", prompt="你是户山香澄，一个元气满满的吉他手。"):
    from core.role.manager import RoleManager, RoleConfig
    rm = RoleManager.__new__(RoleManager)  # 跳过 __init__ 的文件读取
    rm._roles = {role_id: RoleConfig(role_id=role_id, display_name="测试角色", default=True)}
    rm._prompts = {role_id: prompt}
    return rm


# ===================== 通用夹具 =====================

@pytest.fixture
def fake_embedding(monkeypatch):
    fn = FakeEmbedding()
    monkeypatch.setattr("core.memory.vector_store.get_embedding_function", lambda: fn)
    return fn


@pytest.fixture
def user_files(monkeypatch, tmp_path):
    """把用户资料 / 感知 / 提醒文件全部指到临时目录，隔离真实数据。"""
    monkeypatch.setattr("core.user_profile.USER_PROFILES_FILE", tmp_path / "up.json")
    return tmp_path


@pytest.fixture
def llm_fake():
    """主模型适配器（回复生成 / 工具决策）。"""
    return FakeAdapter(name="llm")


@pytest.fixture
def tool_fake():
    """工具 / 抽取 / 情感分析适配器（默认返回合法情感 JSON）。"""
    return FakeAdapter(name="tool", default_chat=DEFAULT_EMOTION_JSON)


@pytest.fixture
def memory_manager(tmp_path, fake_embedding, tool_fake):
    """真实 SimpleMemory(临时目录) + MemoryManager(假工具适配器)。"""
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    store = SimpleMemory(path=str(tmp_path / "chroma_test"))
    mngr = MemoryManager(store, tool_fake)
    return mngr


@pytest.fixture
def agent(tmp_path, monkeypatch, fake_embedding, llm_fake, tool_fake):
    """组装一个真实 LangGraphMemoryAgent（全流程可用），返回 (agent, llm_fake, tool_fake, memory)。"""
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent

    # 隔离用户资料文件
    monkeypatch.setattr("core.user_profile.USER_PROFILES_FILE", tmp_path / "up.json")

    store = SimpleMemory(path=str(tmp_path / "chroma_agent"))
    mngr = MemoryManager(store, tool_fake)
    rm = make_role_manager("kasumi", "你是户山香澄，Popping Party 的主唱，元气又爱撒娇的吉他手。")
    es = EmotionStore(store)

    class _PerceptionStub:
        """让 agent 的感知路径可运行但不做系统调用/写文件。"""
        def record_user_activity(self, user_id): pass
        def record_mood(self, user_id, *a, **k): pass
        def summarize(self, user_id): return "现在是晚上，用户通常这个时段比较活跃。"

    ag = LangGraphMemoryAgent(
        memory_manager=mngr,
        role_manager=rm,
        emotion_store=es,
        role_id="kasumi",
        llm_adapter=llm_fake,
        tool_adapter=tool_fake,
        perception=_PerceptionStub(),
        tool_fallback=False,  # 假 LLM 确定性链路测试：关闭兜底守卫
    )
    return ag, llm_fake, tool_fake, mngr
