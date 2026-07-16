# agent/handlers/__init__.py
"""
Handlers 模块
导出各类型处理器
"""
from .personal import handle_personal
from .realtime import force_search
from .result import handle_result_node

__all__ = [
    "handle_personal",
    "force_search",
    "handle_hybrid",
    "handle_result_node"
]