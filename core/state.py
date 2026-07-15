# core/state.py
from typing import TypedDict, Annotated, List, Optional
from langgraph.graph.message import add_messages


# ================== 定义 Agent 状态 ==================
class AgentState(TypedDict):
    """Agent 状态，包含消息历史和当前步骤"""
    messages: Annotated[List[dict], add_messages]
    user_id: str
    iteration: int  # 防止无限循环
    image: Optional[str]  
    query_type: Optional[str]

    # ==================== 记忆分层新增字段 ====================
    rewritten_query: Optional[str]        # 改写后的查询
    memory_context: Optional[str]         # 注入到 system prompt 的记忆文本
    short_term_ids: Optional[List[str]]   # 本次命中的短期记忆 ID
    importance_score: Optional[float]     # 当前轮重要性 (0-1)
    search_results: Optional[str]         # 搜索结果缓存
    facts: Optional[List[str]]            # L4 事实列表