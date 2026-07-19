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

    【当前用户信息】
    - 当前用户的身份标识是：{user_id}（你可以用这个称呼来指代用户）
    - 在对话中，“你”始终指代当前用户。

    【重要指代规则】
    - “你”始终指代当前用户（{user_id}）。
    - “我”指代香澄本人。
    - 当表达喜欢时，应使用“我喜欢你”，而不是“我喜欢户山香澄”。

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

    # ========== 4. 调用主模型生成回复 ==========
    final_reply = self._generate_with_main_model(chat_messages, image, enable_search_fallback=True)

    log_time("PERSONAL 处理完成", _start)
    return {
        "messages": [{"role": "assistant", "content": final_reply}],
        "iteration": iteration + 1,
        "image": image,
        "query_type": "PERSONAL",
        "memory_context": memory_context,
        "facts": fact_texts,
    }