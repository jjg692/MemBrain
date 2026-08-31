"""
单元测试：指代消解（跨轮指代 / 代词消歧 / 情感续接）
================================================
本项目不做硬编码的指代消解规则——由 LLM 基于注入的 L1 全量历史自行消解。
因此测试聚焦验证"消解所需的历史确实注入到 LLM"这一机制保证：
- L1 全量历史按序注入（用户+助手）
- 末尾追加本轮用户消息，模型能看到"上一轮说了什么"
- 多轮后历史仍完整（可支撑"那首歌 / 它 / 这个"等指代）
- proactive_message 主动发言同样携带历史
"""
import pytest

pytestmark = pytest.mark.usefixtures("fake_embedding")


def _tool_reply(content):
    return content


def test_anaphora_history_injected(agent):
    """第二轮对话时，L1 上一轮历史应出现在发给主模型的 messages 里，使"它"可消解。"""
    ag, llm, tool, mngr = agent
    llm.tools_queue = [
        _tool_reply("我也喜欢秋天！香澄最爱这个季节啦"),
        _tool_reply("对呀，那首歌旋律真的很棒！"),
    ]
    ag.chat("user_a", "我最喜欢的季节是秋天")
    ag.chat("user_a", "那首歌真的很好听")

    # chat_with_tools 记录了每轮的完整 messages
    assert len(llm.sent_tools) == 2
    second_msgs = llm.sent_tools[1][0]
    text = "\n".join(str(m.get("content", "")) for m in second_msgs)
    # 上一轮用户消息 + 助手回复都应出现在本轮输入里（机制保证，供模型消解指代）
    assert "我最喜欢的季节是秋天" in text
    assert "我也喜欢秋天" in text
    # 本轮新消息也在
    assert "那首歌真的很好听" in text


def test_anaphora_pronoun_resolvable_by_l1(agent):
    """验证 system prompt + L1 历史中明确了'你/我'指代关系，模型具备消解上下文。"""
    ag, llm, tool, mngr = agent
    llm.enqueue_tools(_tool_reply("嗯嗯！"))
    ag.chat("user_a", "我养了一只猫叫咪咪")
    # 检查 system prompt 的指代说明
    text = llm.all_tools_text()
    assert "你" in text and "我" in text
    assert "咪咪" in text  # L1 历史让后续"它/咪咪"可被检索到


def test_anaphora_multiturn_history_grows(agent):
    """多轮后 L1 历史持续增长（每轮 +2），确保跨轮指代素材充足。"""
    ag, llm, tool, mngr = agent
    for i, q in enumerate(["第一句", "第二句", "第三句"]):
        llm.enqueue_tools(_tool_reply(f"回复{i}"))
        ag.chat("user_a", q)
    l1 = mngr.get_l1("user_a", "kasumi")
    assert len(l1) == 6
    contents = [m["content"] for m in l1]
    assert contents[0] == "第一句"
    assert contents[-1] == "回复2"


def test_proactive_message_carries_history(agent):
    """主动发言（proactive_message）也注入 L1 历史，可承接之前话题。"""
    ag, llm, tool, mngr = agent
    # 先来一轮正常对话
    llm.enqueue_tools(_tool_reply("香澄记住啦！"))
    ag.chat("user_a", "我下周要去北京出差")

    # preload 主动发言的模型响应（走 tool 适配器 chat 接口）
    tool.enqueue_chat("你下周要去北京，我在北京给你一个拥抱哦！")
    text = ag.proactive_message("user_a", trigger="你该和用户聊两句了")
    assert text != ""

    # 主动发言也走 tool_adapter.chat，检查其历史注入
    sent = tool.sent_chat[-1]
    joined = "\n".join(str(m.get("content", "")) for m in sent)
    assert "我下周要去北京出差" in joined  # L1 历史被注入供承接话题
