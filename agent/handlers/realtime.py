# agent/handlers/realtime.py
"""
REALTIME 分支处理器
负责：直接触发搜索，不经过工具模型判断
"""
import time
from core.logger import log_time, log_router


def force_search(self, query: str, state: dict) -> dict:
    """
    直接触发搜索，不经过工具模型判断
    """
    _start = time.time()
    log_router(f"强制搜索: {query[:50]}...")
    
    iteration = state.get("iteration", 0)
    forced_tool_call = {
        "function": {
            "name": "search_web",
            "arguments": {"query": query}
        },
        "id": f"call_{int(time.time())}"
    }
    response_dict = {
        "role": "assistant",
        "content": "",
        "tool_calls": [forced_tool_call]
    }
    
    log_time("强制搜索完成", _start)
    return {
        "messages": [response_dict],
        "iteration": iteration + 1,
        "query_type": state.get("query_type", "REALTIME")
    }