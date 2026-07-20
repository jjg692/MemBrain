# core/tools.py
import os
import requests
from langchain_core.tools import tool

from core.config import BAIDU_API_KEY
from core.pc_control import execute_pc_task


# ================== 工具定义（Function Calling） ==================
# 1. Ollama 原生格式（JSON Schema）
SEARCH_TOOL_OLLAMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "当用户询问实时信息，如新闻、天气、最新动态、或你无法确定的问题时，使用此工具进行联网搜索。注意：如果用户提到相对时间（如'今天'），请结合上下文中的当前日期来确定具体搜索日期。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，应该简洁明确，例如'北京天气'、'2026年奥斯卡获奖名单'"
                }
            },
            "required": ["query"]
        }
    }
}


# ================== 搜索 API ==================
def search_baidu_api(query: str, max_results: int = 3) -> str:
    """使用百度AI搜索官方API（每天100次免费）"""
    print(f"[搜索] 开始搜索{query} ")
    if not BAIDU_API_KEY:
        return "百度API Key未配置，请检查.env文件"
    
    url = "https://qianfan.baidubce.com/v2/ai_search/web_search"
    headers = {
        "Authorization": f"Bearer {BAIDU_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "messages": [{"content": query, "role": "user"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": max_results}]
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        if data.get("error_code"):
            return f"搜索失败：{data.get('error_msg')}"
        results = data.get("references", [])
        if not results:
            return "没有搜到相关信息。"
        output = [f"{r.get('title', '无标题')}: {r.get('content', '')}" for r in results[:max_results]]
        return "\n".join(output)
    except Exception as e:
        return f"搜索失败：{e}"


# ================== 定义工具（复用现有搜索） ==================
@tool
def search_web(query: str) -> str:
    """当用户询问实时信息、新闻、天气、最新动态时，使用此工具进行联网搜索。
    Args:
        query: 搜索关键词
    """
    return search_baidu_api(query)  # 直接调用，不再从 web_app 导入


@tool
def control_pc(task: str) -> str:
    """执行 Windows 自动化任务（打开应用、操作浏览器、新建文件等）"""
    return execute_pc_task(task)