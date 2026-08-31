"""
pytest 共享脚手架 / 夹具（deepseek · 远程 LLM 版）
================================================
为基于【远程 LLM(DeepSeek)】的语义级 + 工具时机测试提供隔离、确定性环境。

- 离线的机制级测试完全复用确定性脚手架：FakeEmbedding + FakeAdapter + tmp 隔离
- 需要【真实远程 LLM】的语义用例定义一个 remote_agent fixture，
  通过 LLMManager 按当前 .env 的 provider=openai 构造 OpenAICompatAdapter，
  并做连通性就绪检查，远程不可达时自动 skip。
"""
import hashlib
from pathlib import Path
import os

import pytest

# ===================== 假嵌入 / 假 LLM（离线确定性） =====================

DEFAULT_EMOTION_JSON = (
    '{"emotion":{"primary":"开心","intensity":0.6,"valence":0.4,"description":"用户看起来心情不错"},'
    '"affection":{"liking":0.6,"trust":0.5,"familiarity":0.5,"respect":0.5,"interest":0.6,"attachment":0.3},'
    '"needs_tool":false,"tool_decision":""}'
)


class FakeEmbedding:
    """确定性哈希向量嵌入，替代远程/本地嵌入。"""
    NAME = "fake-hash-embedding"

    def name(self) -> str:
        return self.NAME

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

    def _vec(self, text: str, dim: int = 384) -> list:
        v = [0.0] * dim
        for ch in str(text or ""):
            h = int(hashlib.md5(ch.encode("utf-8", "ignore")).hexdigest()[:8], 16)
            v[h % dim] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [round(x / norm, 5) for x in v]


class FakeAdapter:
    """模拟 LLMAdapter 接口（chat / chat_with_tools），预置响应、记录入参。"""

    def __init__(self, name="fake", default_chat="", default_tools=None):
        self.name = name
        self.chat_queue = []
        self.tools_queue = []
        self.default_chat = default_chat
        self.default_tools = default_tools or {"content": "", "tool_calls": []}
        self.sent_chat = []
        self.sent_tools = []
        self.chat_call_count = 0
        self.tools_call_count = 0

    def enqueue_chat(self, text):
        self.chat_queue.append(str(text)); return self

    def enqueue_tools(self, content="", tool_calls=None):
        if isinstance(content, dict):
            item = dict(content); item.setdefault("tool_calls", []); item["content"] = item.get("content", "")
        else:
            item = {"content": str(content), "tool_calls": tool_calls or []}
        self.tools_queue.append(item); return self

    def chat(self, messages, **kwargs) -> str:
        self.sent_chat.append(list(messages)); self.chat_call_count += 1
        return self.chat_queue.pop(0) if self.chat_queue else self.default_chat

    def chat_with_tools(self, messages, tools=None, **kwargs) -> dict:
        self.sent_tools.append((list(messages), tools)); self.tools_call_count += 1
        if self.tools_queue:
            item = self.tools_queue.pop(0)
            if isinstance(item, str):
                item = {"content": item, "tool_calls": []}
            item = dict(item)
            return {"content": item.get("content", ""), "tool_calls": item.get("tool_calls", [])}
        return dict(self.default_tools)

    def all_chat_text(self) -> str:
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


# ===================== 角色管理（内存桩） =====================

def make_role_manager(role_id="kasumi", prompt="你是户山香澄，一个元气满满的吉他手。"):
    from core.role.manager import RoleManager, RoleConfig
    rm = RoleManager.__new__(RoleManager)
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
    monkeypatch.setattr("core.user_profile.USER_PROFILES_FILE", tmp_path / "up.json")
    return tmp_path


@pytest.fixture
def llm_fake():
    return FakeAdapter(name="llm")


@pytest.fixture
def tool_fake():
    return FakeAdapter(name="tool", default_chat=DEFAULT_EMOTION_JSON)


@pytest.fixture
def agent(tmp_path, monkeypatch, fake_embedding, llm_fake, tool_fake):
    """组装真实 LangGraphMemoryAgent（假 LLM，离线确定性），返回 (agent, llm_fake, tool_fake, memory)。"""
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent
    monkeypatch.setattr("core.user_profile.USER_PROFILES_FILE", tmp_path / "up.json")
    store = SimpleMemory(path=str(tmp_path / "chroma_agent"))
    mngr = MemoryManager(store, tool_fake)
    rm = make_role_manager("kasumi", "你是户山香澄，Popping Party 的主唱，元气又爱撒娇的吉他手。")
    es = EmotionStore(store)

    class _PerceptionStub:
        def record_user_activity(self, user_id): pass
        def record_mood(self, user_id, *a, **k): pass
        def summarize(self, user_id): return "现在是晚上，用户通常这个时段比较活跃。"

    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm_fake, tool_adapter=tool_fake, perception=_PerceptionStub(),
    )
    return ag, llm_fake, tool_fake, mngr


# ===================== 远程 LLM（DeepSeek）就绪 & fixture =====================

def _remote_ready() -> bool:
    """检测远程 LLM 是否可用（provider=openai 且 base_url+key 已配且连通）。"""
    try:
        from core.llm_manager import LLMManager
        res = LLMManager.test_connection()
        return bool(res and res.get("ok"))
    except Exception:
        return False


# marker：真实远程 LLM 语义用例；不可达/未配置时自动跳过
needs_remote = pytest.mark.skipif(not _remote_ready(), reason="远程 LLM 不可达，跳过语义集成测试")


@pytest.fixture
def remote_agent(tmp_path, monkeypatch, fake_embedding):
    """用真实远程 LLM（DeepSeek）组装 Agent（双模型：主回复 + 工具/分析）。

    注意：OpenAICompatAdapter 默认无 temperature；这里给它 set_temperature 方法
    并注入较低温度，语义测试更稳定。工具/分析模型与主模型可同源（R1）。
    """
    from core.memory.vector_store import SimpleMemory
    from core.memory.memory_manager import MemoryManager
    from core.emotion import EmotionStore
    from agent.graph import LangGraphMemoryAgent

    if not _remote_ready():
        pytest.skip("远程 LLM 不可达")

    monkeypatch.setattr("core.user_profile.USER_PROFILES_FILE", tmp_path / "up.json")
    from core.llm_manager import LLMManager
    mgr = LLMManager()
    llm = mgr.build_llm_adapter()
    tool_llm = mgr.build_tool_adapter()
    # 远程适配器补 set_temperature（若实现了）
    for a in (llm, tool_llm):
        if not hasattr(a, "set_temperature"):
            a.set_temperature = lambda v: None

    store = SimpleMemory(path=str(tmp_path / "chroma_remote"))
    mngr = MemoryManager(store, tool_llm)
    rm = make_role_manager("kasumi", "你是户山香澄，BanG Dream! Poppin'Party 的主唱和吉他手，元气、活泼、爱撒娇。")
    es = EmotionStore(store)

    class _PerceptionStub:
        def record_user_activity(self, user_id): pass
        def record_mood(self, user_id, *a, **k): pass
        def summarize(self, user_id): return ""

    ag = LangGraphMemoryAgent(
        memory_manager=mngr, role_manager=rm, emotion_store=es, role_id="kasumi",
        llm_adapter=llm, tool_adapter=tool_llm, perception=_PerceptionStub(),
    )
    return ag, mngr
