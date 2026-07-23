"""
PERSONAL 分支处理器
- 接入混合检索（L2 + L4）
- 事实优先注入
- 自治路由：LLM 通过工具调用自主决策
- 情感模块：LLM 驱动的情感状态更新与注入
"""
import time
from datetime import datetime
from core.logger import log_time, log_debug, log_router
from core.config import MEMORY_DEBUG
from typing import Optional
from agent.handlers.realtime import force_search
from core.tools import SEARCH_TOOL_OLLAMA, CONTROL_PC_TOOL_OLLAMA

# 情感模块导入
from core.emotion import (
    EmotionState, inject_emotion_to_prompt, parse_emotion_from_reply,
    AffectionState, inject_affection_to_prompt, parse_affection_from_reply
)


def handle_personal(self, user_message: str, state: dict, role_id: Optional[str] = None) -> dict:
    """PERSONAL 分支：记忆检索 + LLM 自主决策（带工具调用） + 情感驱动"""
    _start = time.time()
    log_debug("PERSONAL", f"开始处理: {user_message[:30]}...")
    
    image = state.get("image", None)
    user_id = state.get("user_id", "default_user")
    iteration = state.get("iteration", 0)
    current_date = datetime.now().strftime("%Y年%m月%d日")

    # ========== 1. 情感状态加载 ==========
    emotion = self._get_emotion_state(user_id) or EmotionState.default()
    log_debug("PERSONAL", f"当前情感: {emotion.primary} (强度 {emotion.intensity:.2f})")

    # ========== 1b. 好感度加载（新增） ==========
    affection = self._get_affection_state(user_id, role_id) or AffectionState.default()

    # ========== 2. 综合检索（L5 角色事实 + L4 事实 + L2 短期记忆） ==========
    retrieval_result = self.memory_manager.retrieve_memory_context(
        user_id=user_id,
        query=user_message,
        top_k=5,
        role_id=role_id
    )
    
    fact_texts = retrieval_result.get("facts", [])
    short_term_texts = retrieval_result.get("short_term", [])
    memory_context = retrieval_result.get("context", "")
    
    log_debug("PERSONAL", f"检索到: {len(fact_texts)} 条事实, {len(short_term_texts)} 条短期记忆")
    log_debug("PERSONAL", "事实详情:")
    for fact in fact_texts:
        log_debug("PERSONAL", f"  - {fact}")
    log_debug("PERSONAL", "短期记忆详情:")
    for mem in short_term_texts:
        log_debug("PERSONAL", f"  - {mem}")

    # ========== 3. 构建基础 System Prompt（不含情感） ==========
    base_system_prompt = f"""{self.system_prompt}

    【当前日期】{current_date}

    【当前用户信息】
    - 当前用户的身份标识是：{user_id}（你可以用这个称呼来指代用户）
    - 在对话中，“你”始终指代当前用户。

    【重要指代规则】
    - “你”始终指代当前用户（{user_id}）。
    - “我”指代香澄本人。
    - 当表达喜欢时，应使用“我喜欢你”，而不是“我喜欢户山香澄”。

    【可用工具】
    你拥有以下工具，可以在需要时自主调用：
    1. search_web: 搜索实时信息（天气、新闻、股价、最新动态等）
    2. control_pc: 操作电脑（打开应用、浏览器、创建文件等）

    【工具使用决策原则】
    - 如果用户需要实时数据（天气、新闻、股价、最新消息）→ 调用 search_web
    - 如果用户要求操作电脑（打开记事本、打开浏览器、新建文件）→ 调用 control_pc
    - 如果用户提到执行命令 → 调用 control_pc
    - 如果用户问的是角色知识、闲聊或你确定能回答的问题 → 不调用工具，直接回答
    - 你可以结合上下文自主决定是否调用工具

    """
    
    # 事实部分（L4）
    if fact_texts:
        base_system_prompt += "【关于你的事实】\n" + "\n".join(f"- {f}" for f in fact_texts) + "\n\n"

    # L5 角色事实
    role_facts = retrieval_result.get("role_facts", [])
    if role_facts:
        base_system_prompt += "【关于我】\n" + "\n".join(f"- {f}" for f in role_facts) + "\n\n"
    
    # 短期记忆部分（L2）
    if short_term_texts:
        base_system_prompt += "【相关记忆】\n" + "\n".join(f"- {t}" for t in short_term_texts) + "\n\n"
    
    if not fact_texts and not short_term_texts:
        base_system_prompt += "（记忆库中暂无相关信息，你可以直接回答或询问更多细节）\n\n"
    
    base_system_prompt += f"用户最新消息：{user_message}"

    # ========== 4. 情感注入 ==========
    full_system_prompt = inject_emotion_to_prompt(base_system_prompt, emotion)
    # 好感度注入（追加在情感之后）
    full_system_prompt = inject_affection_to_prompt(full_system_prompt, affection)

    # ========== 5. 构建对话消息 ==========
    chat_messages = [{"role": "system", "content": full_system_prompt}]
    
    # 从内存上下文取最近10轮
    history = self.conversation_history.get(user_id, [])
    for msg in history[-10:]:
        if msg.get("role") in ("user", "assistant"):
            chat_messages.append({"role": msg["role"], "content": msg["content"]})
    
    chat_messages.append({"role": "user", "content": user_message})

    # ========== 6. 调用主模型，带工具调用能力（不启用搜索fallback） ==========
    tools = [SEARCH_TOOL_OLLAMA, CONTROL_PC_TOOL_OLLAMA]
    result = self.main_adapter.chat_with_tools(
        messages=chat_messages,
        tools=tools,
        think=False
    )
    
    tool_calls = result.get("tool_calls", [])
    raw_content = result.get("content", "")

    # 如果 LLM 决定调用工具 → 返回 tool_calls，让 LangGraph 路由到 tools 节点
    if tool_calls:
        log_debug("PERSONAL", f"LLM 决定调用工具: {tool_calls}")
        # 注意：工具调用时，LLM 可能没有输出情感标签，我们保持情感不变
        return {
            "messages": [{"role": "assistant", "content": "", "tool_calls": tool_calls}],
            "iteration": iteration + 1,
            "image": image,
            "query_type": "PERSONAL"
        }
    
    # ========== 7. 解析情感状态 ==========
    clean_reply, new_emotion = parse_emotion_from_reply(raw_content)
    if new_emotion:
        self._save_emotion_state(user_id, new_emotion)
        emotion = new_emotion
        log_debug("PERSONAL", f"情感更新为: {emotion.primary} (强度 {emotion.intensity:.2f})")

    # 解析好感度
    clean_reply, new_affection = parse_affection_from_reply(clean_reply or raw_content)
    if new_affection:
        self._save_affection_state(user_id, role_id, new_affection)
        affection = new_affection
        
    else:
        # 如果未解析到新情感，保持原有情感（但可能衰减，可由LLM自行决定，我们暂不处理）
        log_debug("PERSONAL", "未解析到情感更新，保持当前情感")

    final_reply = clean_reply if clean_reply else "啊咧？香澄还没想好怎么回答呢…"
    
    # ========== 8. 更新内存上下文 ==========
    if user_id not in self.conversation_history:
        self.conversation_history[user_id] = []
    self.conversation_history[user_id].append({"role": "user", "content": user_message})
    self.conversation_history[user_id].append({"role": "assistant", "content": final_reply})

    log_time("PERSONAL 处理完成", _start)
    return {
        "messages": [{"role": "assistant", "content": final_reply}],
        "iteration": iteration + 1,
        "image": image,
        "query_type": "PERSONAL",
        "memory_context": memory_context,
        "facts": fact_texts,
        "_emotion": emotion  # 附加情感状态，便于调试
    }