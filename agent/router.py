# agent/router.py
from typing import Dict, Optional
from core.config import MEMORY_DEBUG


def classify_query(self, user_message: str, rewrite_context: Optional[Dict] = None, role_context: Optional[str] = None) -> str:
    """
    自治路由：完全废除路由判断，所有请求走 PERSONAL，由 LLM 自主决策。
    """
    if MEMORY_DEBUG:
        print(f"[Router] 全部走 PERSONAL (LLM 自主决策)")
    return "PERSONAL"