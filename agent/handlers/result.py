# agent/handlers/result.py
"""
搜索结果处理器
负责：从 LangGraph 状态中提取搜索结果，生成最终回复
"""
import time
from datetime import datetime
from core.logger import log_time, log_debug


def handle_result_node(self, state: dict) -> dict:
    """处理搜索结果，生成最终回复（不经过路由）"""
    _start = time.time()
    log_debug("RESULT", "开始处理搜索结果")
    
    image = state.get("image", None)
    user_id = state.get("user_id", "default_user")
    iteration = state.get("iteration", 0)
    
    # 提取用户原始消息
    messages = state["messages"]
    user_message = ""
    for msg in reversed(messages):
        if hasattr(msg, 'type') and msg.type == 'human':
            user_message = msg.content
            break
    
    # 提取搜索结果
    search_result = "暂无搜索结果"
    for msg in reversed(messages):
        if hasattr(msg, 'type') and msg.type == 'tool':
            search_result = msg.content
            break
    
    current_date = datetime.now().strftime("%Y年%m月%d日")

    # ========== 提取搜索词（用于调试和上下文） ==========
    search_query = state.get("search_query", "未知")

    full_system_prompt = f"""{self.system_prompt}

    【当前日期】{current_date}

    【搜索词】{search_query}

    【搜索结果】（来自联网搜索，可信度高）
    {search_result}

    【回答规则】
    1. 如果搜索结果中包含了用户提到的实体（如游戏名、人名），请基于搜索结果回答。
    2. 如果搜索结果中没有找到相关信息，请如实告知用户，不要编造。
    3. 如果搜索结果不完整，可以告知用户信息有限。

    【日期校验规则】
    1. 当前真实日期是：{current_date}
    2. 如果搜索结果中的日期与当前日期不符，请忽略搜索结果中的日期，使用当前日期。

    用户最新消息：{user_message}
    """

    chat_messages = [{"role": "system", "content": full_system_prompt}]
    for msg in self.conversation_history[-10:]:
        chat_messages.append({"role": msg["role"], "content": msg["content"]})
    chat_messages.append({"role": "user", "content": user_message})

    final_reply = self._generate_with_main_model(chat_messages, image)

    self.conversation_history.append({"role": "user", "content": user_message})
    self.conversation_history.append({"role": "assistant", "content": final_reply})

    log_time("RESULT 处理完成", _start)
    return {
        "messages": [{"role": "assistant", "content": final_reply}],
        "iteration": iteration + 1,
        "image": image,
        "query_type": state.get("query_type", "REALTIME") 
    }