# agent/handlers/personal.py
"""
PERSONAL 分支处理器
负责：记忆检索 → 有记忆则直接回答，无记忆则让主模型自主决策是否搜索
"""
import time
from datetime import datetime
from core.logger import log_time, log_debug, log_router


def handle_personal(self, user_message: str, state: dict) -> dict:
    """
    只走记忆，但如果记忆为空，让主模型自主决策是否搜索
    """
    from core.tools import SEARCH_TOOL_OLLAMA
    
    _start = time.time()
    log_debug("PERSONAL", f"开始处理: {user_message[:30]}...")
    log_time("PERSONAL 开始", _start)
    
    image = state.get("image", None)
    user_id = state.get("user_id", "default_user")
    iteration = state.get("iteration", 0)

    # ========== 混合检索（短期+长期） ==========
    # 1. 检索短期记忆（最近对话原文）
    short_term_results = self.memory.search(
        query=user_message,
        user_id=user_id,
        threshold=0.4,        # 阈值稍低，召回更多
        n_results=3,
        where={"type": "short_term"}   # 只检索短期记忆
    )
    # 2. 检索长期记忆（摘要）
    long_term_results = self.memory.search(
        query=user_message,
        user_id=user_id,
        threshold=0.5,
        n_results=2,
        where={"type": "long_term"}    # 只检索长期记忆
    )
    # 3. 合并结果（去重，优先短期记忆）
    all_memories = []
    # 短期记忆优先
    for item in short_term_results.get("results", []):
        all_memories.append(item["document"])
    # 添加长期记忆（避免重复）
    for item in long_term_results.get("results", []):
        if item["document"] not in all_memories:
            all_memories.append(item["document"])
    
    # 构建 memory_context（与原有逻辑兼容）
    memory_parts = []
    if all_memories:
        memory_parts.append("【相关记忆】\n" + "\n".join(f"- {t}" for t in all_memories))
    # 也获取最近3条（保留原有逻辑）
    recent = self.memory.get_recent(user_id=user_id, n=3)
    if recent:
        memory_parts.append("【最近对话】\n" + "\n".join(f"- {d}" for d in recent))
    memory_context = "\n\n".join(memory_parts) if memory_parts else None
    
    log_debug("PERSONAL", f"检索到 {len(all_memories)} 条相关记忆")
    
    current_date = datetime.now().strftime("%Y年%m月%d日")

    # ========== 情况1：记忆库有信息，直接生成回复（不走搜索） ==========
    if memory_context:
        log_debug("PERSONAL", "记忆库有信息，直接生成回复")
        
        full_system_prompt = f"""{self.system_prompt}

        【当前日期】{current_date}

        {memory_context}

        用户最新消息：{user_message}
        """
        chat_messages = [{"role": "system", "content": full_system_prompt}]
        for msg in self.conversation_history[-10:]:
            chat_messages.append({"role": msg["role"], "content": msg["content"]})
        chat_messages.append({"role": "user", "content": user_message})

        final_reply = self._generate_with_main_model(chat_messages, image)

        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": final_reply})

        log_time("PERSONAL 处理完成", _start)
        return {
            "messages": [{"role": "assistant", "content": final_reply}],
            "iteration": iteration + 1,
            "image": image,
            "query_type": "PERSONAL"
        }

    # ========== 情况2：记忆库为空，让主模型自主决策是否搜索 ==========
    log_router("记忆库为空，主模型自主决策是否搜索")
    
    full_system_prompt = f"""{self.system_prompt}

    【当前日期】{current_date}

    用户问了一个涉及个人历史的问题，但记忆库中没有相关信息。
    如果你知道答案，可以直接回答。
    如果你不确定，可以调用 search_web 工具搜索相关信息。

    用户最新消息：{user_message}
    """

    chat_messages = [{"role": "system", "content": full_system_prompt}]
    for msg in self.conversation_history[-10:]:
        chat_messages.append({"role": msg["role"], "content": msg["content"]})
    chat_messages.append({"role": "user", "content": user_message})

    result = self.main_adapter.chat_with_tools(
        messages=chat_messages,
        tools=[SEARCH_TOOL_OLLAMA]
    )

    tool_calls = result.get("tool_calls", [])
    content = result.get("content", "")

    if tool_calls:
        # 模型决定搜索
        search_query = tool_calls[0].get("function", {}).get("arguments", {}).get("query", user_message)
        log_router(f"主模型决定搜索: {search_query}")
        result_search = self._force_search(search_query, state)
        result_search["query_type"] = "PERSONAL"
        log_time("PERSONAL 处理完成（搜索）", _start)
        return result_search
    else:
        # 模型认为自己知道，直接回答
        log_router("主模型决定直接回答")
        final_reply = content or "啊咧？香澄还没想好怎么回答呢…让我们再试一次吧！"
        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": final_reply})
        log_time("PERSONAL 处理完成（直接回答）", _start)
        return {
            "messages": [{"role": "assistant", "content": final_reply}],
            "iteration": iteration + 1,
            "image": image,
            "query_type": "PERSONAL"
        }