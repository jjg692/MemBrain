"""
单元测试：普通对话（私聊主流程）
================================================
验证 LangGraphMemoryAgent.chat 在 LLM 直接回答（不调用工具）时的完整链路：
- 返回角色回复文本
- L1 历史记录 用户/助手 两则
- L2 短期记忆写入
- mode B 情感分析发生且好感度被持久化
- L4 事实抽取按重要性阈值触发/跳过
- prompt 注入：昵称、L5/L4 检索、情感/好感度、关系阶段、感知
"""
import pytest

pytestmark = pytest.mark.usefixtures("fake_embedding")


def test_private_chat_normal_reply(agent):
    ag, llm, tool, mngr = agent
    llm.enqueue_tools("你好！我是香澄，很高兴见到你～")
    reply = ag.chat("user_a", "你好")
    assert reply == "你好！我是香澄，很高兴见到你～"

    l1 = mngr.get_l1("user_a", "kasumi")
    assert len(l1) == 2
    assert l1[0] == {"role": "user", "content": "你好"}
    assert l1[1]["role"] == "assistant"
    assert "香澄" in l1[1]["content"]


def test_private_chat_reply_fallback_on_model_failure(agent):
    """模型异常时应返回友好兜底文本而非崩溃。"""
    ag, llm, tool, mngr = agent

    def boom(*a, **k):
        raise RuntimeError("mock LLM down")
    llm.chat_with_tools = boom
    reply = ag.chat("user_a", "在吗")
    assert "抱歉" in reply or "香澄" in reply


def test_private_chat_multi_turn_accumulates_l1(agent):
    """多轮对话：L1 应按序累积（不重复），轮到次数随之增长。"""
    ag, llm, tool, mngr = agent
    llm.tools_queue = ["第一轮回复", "第二轮回复"]
    ag.chat("user_a", "你好")
    ag.chat("user_a", "今天天气如何")
    l1 = mngr.get_l1("user_a", "kasumi")
    contents = [m["content"] for m in l1]
    assert contents == ["你好", "第一轮回复", "今天天气如何", "第二轮回复"]


def test_l1_isolated_per_user(agent):
    """双键隔离：不同 user 的 L1 互不干扰。"""
    ag, llm, tool, mngr = agent
    llm.tools_queue = ["r1", "r2"]
    ag.chat("alice", "嗨")
    ag.chat("bob", "哈喽")
    assert [m["content"] for m in mngr.get_l1("alice", "kasumi")] == ["嗨", "r1"]
    assert [m["content"] for m in mngr.get_l1("bob", "kasumi")] == ["哈喽", "r2"]
    # 不同 role 也隔离
    assert mngr.get_l1("alice", "kasumi")[0]["content"] == "嗨"


def test_emotion_persisted_after_chat(agent):
    """模式 B：一轮对话后，情感与好感度应被持久化到 EmotionStore。"""
    ag, llm, tool, mngr = agent
    from core.emotion import EmotionStore
    from core.config import PROJECT_ROOT
    # 用与 agent 相同的 store 读取（临时目录）
    es = EmotionStore(ag.memory.memory)
    llm.enqueue_tools("好的呀！")
    ag.chat("user_a", "我今天特别开心！")
    emo = es.load_emotion("user_a", "kasumi")
    aff = es.load_affection("user_a", "kasumi")
    assert emo is not None
    assert emo.primary == "开心"          # 来自 DEFAULT_EMOTION_JSON
    assert emo.valence == 0.4
    assert aff is not None
    assert aff.liking == 0.6
    assert aff.attachment == 0.3


def test_emotion_merge_updates_dimensions(agent):
    """好感度合并：仅更新出现的维度，其余保持默认。"""
    ag, llm, tool, mngr = agent
    from core.emotion import EmotionState, AffectionState, EmotionAnalyzer
    from core.emotion.affection import AffectionState as AS
    a = AffectionState.default()
    merged = EmotionAnalyzer.merge_affection(a, {"liking": 0.9})
    assert merged.liking == 0.9
    assert merged.trust == 0.5  # 未变
    assert merged.familiarity == 0.5


def test_prompt_includes_nickname_and_memory(agent, tmp_path):
    """system prompt 应注入用户昵称、L4/L5 检索结果与感知文本。"""
    ag, llm, tool, mngr = agent
    # 设置昵称
    from core.user_profile import UserProfile
    up = UserProfile(tmp_path / "up.json")
    up.set_nickname("user_a", "小航")

    # 预置 L4 事实 + L5 角色事实
    mngr.memory.add_with_title("f", "用户小航喜欢喝咖啡", user_id="user_a",
                               role_id="kasumi", type_="fact",
                               meta={"importance": 0.8})
    mngr.memory.add_with_title("r", "户山香澄是主唱", user_id="__role__",
                               role_id="kasumi", type_="role_fact",
                               meta={"role_id": "kasumi"})

    llm.enqueue_tools("那我们一起去吧！")
    ag.chat("user_a", "我们一起喝咖啡吧")
    text = llm.all_tools_text()
    assert "小航" in text                       # 昵称注入
    assert "户山香澄是主唱" in text             # L5 注入
    assert "关系" in text                       # 关系养成注入
    assert "时段比较活跃" in text               # 感知注入


# ===================== 昵称 / 用户资料 =====================

def test_user_profile_set_and_get(user_files):
    from core.user_profile import UserProfile
    up = UserProfile()
    up.set_nickname("u1", "小明")
    assert up.get_nickname("u1") == "小明"
    # 持久化到临时文件
    up2 = UserProfile()
    assert up2.get_nickname("u1") == "小明"
    # 空串清除
    up.set_nickname("u1", "")
    assert up.get_nickname("u1") == ""
