"""
PERSONAL 分支处理器
- 接入混合检索（L2 + L4）
- 事实优先注入
"""
import time
from datetime import datetime
from core.logger import log_time, log_debug, log_router
from core.config import MEMORY_DEBUG
from typing import Optional
from agent.handlers.realtime import force_search


def handle_personal(self, user_message: str, state: dict, role_id: Optional[str] = None) -> dict:
    """PERSONAL 分支：记忆检索 + 主模型生成"""
    _start = time.time()
    log_debug("PERSONAL", f"开始处理: {user_message[:30]}...")
    
    image = state.get("image", None)
    user_id = state.get("user_id", "default_user")
    iteration = state.get("iteration", 0)
    current_date = datetime.now().strftime("%Y年%m月%d日")

    # ========== 综合检索（L5 角色事实 + L4 事实 + L2 短期记忆） ==========
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
    # ===== 新增详细打印 =====
    log_debug("PERSONAL", "事实详情:")
    for fact in fact_texts:
        log_debug("PERSONAL", f"  - {fact}")
    log_debug("PERSONAL", "短期记忆详情:")
    for mem in short_term_texts:
        log_debug("PERSONAL", f"  - {mem}")

    # ========== 构建 System Prompt（事实优先） ==========
    full_system_prompt = f"""{self.system_prompt}

    【当前日期】{current_date}

    """
    
    # 事实部分（L4）
    if fact_texts:
        full_system_prompt += "【关于你的事实】\n" + "\n".join(f"- {f}" for f in fact_texts) + "\n\n"

    # 新增：L5 角色事实
    role_facts = retrieval_result.get("role_facts", [])
    if role_facts:
        full_system_prompt += "【关于我】\n" + "\n".join(f"- {f}" for f in role_facts) + "\n\n"
    
    # 短期记忆部分（L2）
    if short_term_texts:
        full_system_prompt += "【相关记忆】\n" + "\n".join(f"- {t}" for t in short_term_texts) + "\n\n"
    
    if not fact_texts and not short_term_texts:
        full_system_prompt += "（记忆库中暂无相关信息，你可以直接回答或询问更多细节）\n\n"
    
    full_system_prompt += f"用户最新消息：{user_message}"

    # ========== 构建对话消息 ==========
    chat_messages = [{"role": "system", "content": full_system_prompt}]
    
    # 从内存上下文取最近10轮
    history = self.conversation_history.get(user_id, [])
    for msg in history[-10:]:
        if msg.get("role") in ("user", "assistant"):
            chat_messages.append({"role": msg["role"], "content": msg["content"]})
    
    chat_messages.append({"role": "user", "content": user_message})

    # ========== 4. 调用主模型生成回复（带“不确定”检测） ==========
    # 这里在 system prompt 里多加一句指令，让 LLM 在不确定时输出标记
    chat_messages[0]["content"] += "\n\n【重要指令】如果你不确定答案，请在回复中包含「@@@UNCERTAIN@@@」。"

    # ========== 主模型生成回复 ==========
    final_reply = self._generate_with_main_model(chat_messages, image)

    # ========== 5. 检测 LLM 是否不确定 ==========
    if "@@@UNCERTAIN@@@" in final_reply:
        # 移除标记
        final_reply = final_reply.replace("@@@UNCERTAIN@@@", "").strip()
        log_router("PERSONAL 分支：LLM 表示不确定，走搜索兜底")
        # 触发搜索
        return force_search(self, user_message, state)
    

    log_time("PERSONAL 处理完成", _start)
    return {
        "messages": [{"role": "assistant", "content": final_reply}],
        "iteration": iteration + 1,
        "image": image,
        "query_type": "PERSONAL",
        "memory_context": memory_context,
        "facts": fact_texts,
    }