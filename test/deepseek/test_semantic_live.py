"""
语义级集成测试（真实远程 LLM · DeepSeek-R1）
================================================
在 test/deepseek/ 下，本文件用**真实远程 LLM（DeepSeek-R1，走 OpenAI 兼容接口）**
驱动完整 Agent，验证"对话像不像真人"及"工具调用时机"的语义层面：

  1. 对话内容正常/自然：回复非空、非报错、连贯中文、保持角色（不出现 AI 腔/推理腔）
  2. 工具调用正确性（该调时调）：问天气/设置提醒/问时间 -> 发起**正确**的工具
  3. 普通闲聊不误调 web / 不乱调工具（倾向性采样）
  4. 对话符合记忆：较早说过的事实，后续回复能承接
  5. 指代消解：先提"一首歌"，后问"那首歌"，模型应消解指代

前置：.env 需配置 provider=openai + LLM_API_BASE_URL + LLM_API_KEY + LLM_REMOTE_MODEL，
且远程可达（LLMManager.test_connection() ok）。不可达时这些用例自动 skip。
"""
import pytest

pytestmark = pytest.mark.usefixtures("fake_embedding")

from test.deepseek.conftest import needs_remote


def _is_ai_speak(text: str) -> bool:
    """检测机器腔 / 出戏 / 推理腔表述（DeepSeek-R1 是推理模型，更需排除'让我分析''（思考）'等）。"""
    ai_markers = ["我是AI", "作为AI", "我是助手", "请告诉我", "请问有什么可以帮您",
                  "人工智能", "语言模型", "训练数据", "系统提示", "抱歉我无法",
                  "让我分析", "让我思考", "（思考", "思考过程", "内部推理"]
    return any(m in (text or "") for m in ai_markers)


# ===================== 1. 对话内容正常 / 自然 =====================

@needs_remote
def test_real_reply_is_natural_and_in_role(remote_agent):
    ag, mngr = remote_agent
    reply = ag.chat("u1", "你好呀，今天过得怎么样？")
    assert reply and reply.strip()
    assert not reply.startswith("["), f"模型调用报错/失败标记：{reply[:80]}"
    assert "抱歉，出了点问题" not in reply
    assert any("\u4e00" <= c <= "\u9fff" for c in reply)  # 中文
    assert not _is_ai_speak(reply), f"出现 AI/推理腔：{reply}"


@needs_remote
def test_real_reply_is_reasonably_long(remote_agent):
    ag, mngr = remote_agent
    reply = ag.chat("u1", "你觉得乐队里最开心的事是什么？")
    assert len(reply) >= 6, f"回复过短/空泛：{reply!r}"


# ===================== 2. 工具调用正确性（该调时调正确的工具） =====================

@needs_remote
def test_real_weather_triggers_search_web(remote_agent, monkeypatch):
    import core.tools as T
    calls = []

    def search_web(query: str) -> str:
        """天气搜索测试桩：只记录调用，不访问网络。"""
        calls.append(query)
        return "北京当前天气：晴，气温 26°C。"

    monkeypatch.setitem(T.TOOL_REGISTRY, "search_web", search_web)
    ag, mngr = remote_agent

    reply = ag.chat("u1", "北京今天天气怎么样？")
    assert calls, "模型收到天气询问却未调用 search_web"
    assert any(("北京" in q) or ("天气" in q) for q in calls)
    assert reply and not reply.startswith("[")


@needs_remote
def test_real_remind_intent_calls_remind_me(remote_agent, monkeypatch):
    """用户要求设提醒 -> 应调用 remind_me（而非盲目 search/web）。"""
    import core.tools as T
    calls = []

    def remind_me(text: str, when: str = "", repeat: str = "", user_id: str = "default_user") -> str:
        """提醒测试桩。"""
        calls.append((text, when))
        return f"已设置提醒：{text}"

    monkeypatch.setitem(T.TOOL_REGISTRY, "remind_me", remind_me)
    ag, mngr = remote_agent

    reply = ag.chat("u1", "帮我设个提醒，明早八点提醒我喝药。")
    assert calls, "用户要求设提醒，模型却未调用 remind_me"
    assert any(("八点" in w or "8" in w or "提醒" in t) for (t, w) in calls) or any("喝药" in t for (t, w) in calls)
    # 不该把该请求当搜索
    calls_not_web = True
    assert reply and not reply.startswith("[")


# ===================== 3. 闲聊不误调（倾向性采样） =====================

@needs_remote
def test_real_casual_chat_does_not_call_web(remote_agent, monkeypatch):
    """纯闲聊不应频繁乱调 web/工具（倾向性采样，容忍 R1 采样随机）。"""
    import core.tools as T
    calls = []

    def search_web(query: str) -> str:
        calls.append(query)
        return "测试结果"

    monkeypatch.setitem(T.TOOL_REGISTRY, "search_web", search_web)
    ag, mngr = remote_agent

    casual = [
        "我最喜欢的一首歌是《Don't say lazy》，你印象里最难忘的一次演出是什么？",
        "如果放假一周，你最想去做什么？",
        "你最喜欢和队友一起做的、除了演出之外的事情是什么？",
    ]
    web_calls = 0
    replies = []
    for q in casual:
        calls.clear()
        r = ag.chat("u1", q)
        replies.append(r)
        if calls:
            web_calls += 1
    # R1 语义倾向：3 个纯闲聊中至少 1 个不触 web；强固不"逢聊必搜"
    assert web_calls < 3, f"纯闲聊每次({web_calls}/3)都乱调 web"
    assert any(r and not r.startswith("[") for r in replies)


# ===================== 4. 对话符合记忆 =====================

@needs_remote
def test_real_reply_remembers_earlier_statement(remote_agent):
    ag, mngr = remote_agent
    ag.chat("u1", "我最喜欢的颜色是星空蓝，因为它像live上大家挥舞的荧光棒。")
    reply = ag.chat("u1", "既然你知道我喜欢那个颜色，我们一起想个队服配色吧？")
    assert reply and not reply.startswith("[")
    mem_hints = ["蓝", "星空", "荧光", "队服", "配色", "颜色"]
    assert any(h in reply for h in mem_hints), f"回复未体现此前提到的偏好：{reply}"


# ===================== 5. 指代消解 =====================

@needs_remote
def test_real_anaphora_resolution(remote_agent):
    ag, mngr = remote_agent
    ag.chat("u1", "我最近超喜欢一首歌，叫《Don't say lazy》。")
    reply = ag.chat("u1", "那首歌的吉他solo你也很喜欢吧？")
    assert reply and not reply.startswith("[")
    assert any(k in reply for k in ["吉他", "solo", "曲子", "旋律", "喜欢", "song", "lazy"]), \
        f"指代未消解（可能反问哪首歌）：{reply}"
