"""
Agent 状态定义（LangGraph State）
使用 add_messages reducer，支持 ReAct 工具循环消息累积
"""
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """LangGraph 图传递的状态"""
    messages: Annotated[List[AnyMessage], add_messages]  # 对话消息（累积）
    user_id: str                            # 用户标识
    role_id: str                            # 角色标识
    image: Optional[str]                    # 可选图片（base64）
    iteration: int                          # 迭代计数（防死循环）
    system_prompt: Optional[str]            # 构建好的 system prompt
    room_context: Optional[str]             # 群聊 L0 上下文（可选）
    facts: Optional[List[str]]              # 检索到的 L4 事实
    role_facts: Optional[List[str]]         # 检索到的 L5 角色事实
    emotion: Optional[Dict[str, Any]]       # 情感状态
    affection: Optional[Dict[str, Any]]     # 好感度（6维）
