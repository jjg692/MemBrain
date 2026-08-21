"""
工具定义：search_web + control_pc
提供给 LangGraph ToolNode 及 LLM 工具调用 Schema
"""
import json
import subprocess
import sys
import urllib.parse
from typing import Optional

import requests

from core.config import BAIDU_API_KEY


# ===================== 工具实现 =====================

def search_web(query: str) -> str:
    """
    联网搜索实时信息（天气、新闻、股价、最新动态等）。
    使用百度 API；未配置 Key 时降级为 Bilibili 热搜（仅供演示）。
    """
    content = None
    if BAIDU_API_KEY:
        try:
            content = _baidu_search(query)
        except Exception as e:
            content = None
    if not content:
        content = _bilibili_popular(query)
    return content


def control_pc(command: str, target: Optional[str] = None) -> str:
    """
    控制本地电脑：打开应用/浏览器/文件/执行命令（占位实现）。
    command: open_app | open_browser | open_file | run_command
    target: 应用名 / URL / 文件路径 / 命令
    """
    system = sys.platform
    try:
        if command == "open_browser" and target:
            url = target if target.startswith("http") else f"https://{target}"
            if system == "win32":
                subprocess.Popen(["cmd", "/c", "start", "", url])
            elif system == "darwin":
                subprocess.Popen(["open", url])
            else:
                subprocess.Popen(["xdg-open", url])
            return f"已在浏览器打开: {url}"
        elif command == "open_app" and target:
            if system == "win32":
                subprocess.Popen(["cmd", "/c", "start", "", target])
            elif system == "darwin":
                subprocess.Popen(["open", "-a", target])
            else:
                subprocess.Popen([target])
            return f"已打开应用: {target}"
        elif command == "open_file" and target:
            if system == "win32":
                subprocess.Popen(["cmd", "/c", "start", "", target])
            elif system == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
            return f"已打开文件: {target}"
        elif command == "run_command" and target:
            result = subprocess.run(target, shell=True, capture_output=True, text=True, timeout=15)
            return f"命令输出: {result.stdout[:500] or result.stderr[:500]}"
        else:
            return "参数不完整：需要 command（open_app/open_browser/open_file/run_command）和 target。"
    except Exception as e:
        return f"PC 控制失败: {e}"


def _baidu_search(query: str) -> str:
    """百度 web 搜索 API（简化调用，需登记 apikey）"""
    url = "https://aip.baidubce.com/rest/2.0/solution/v1/web_search"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = urllib.parse.urlencode({"q": query, "apikey": BAIDU_API_KEY})
    resp = requests.post(url, headers=headers, data=data, timeout=10)
    data = resp.json()
    results = (data.get("result") or [])[:5]
    if not results:
        return f"（搜索未返回结果）关于“{query}”：抱歉，暂时没有找到相关内容。"
    lines = [f"关于“{query}”的搜索结果："]
    for idx, item in enumerate(results, 1):
        title = item.get("title", "")
        detail = item.get("abstract", "") or item.get("content", "")
        lines.append(f"{idx}. {title}")
        if detail:
            lines.append(f"   {detail[:120]}")
    return "\n".join(lines)


def _bilibili_popular(query: str) -> str:
    """降级实现：Bilibili 热门榜单（演示联网能力）"""
    try:
        resp = requests.get("https://api.bilibili.com/x/web-interface/popular", timeout=8, headers={
            "User-Agent": "Mozilla/5.0"
        })
        data = resp.json()
        items = (data.get("data") or {}).get("list", [])[:5]
        lines = [f"（搜索服务降级）Bilibili 热门（与“{query}”相关推荐）："]
        for i, it in enumerate(items, 1):
            lines.append(f"{i}. {it.get('title', '')}")
        return "\n".join(lines)
    except Exception as e:
        return f"搜索服务不可用: {e}"


# ===================== 工具 Schema（供 Ollama 使用） =====================

SEARCH_TOOL_OLLAMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "搜索实时信息，包括天气、新闻、股价、最新动态等。当你需要联网或获取最新、实时数据时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的问题或关键词"},
            },
            "required": ["query"],
        },
    },
}

CONTROL_PC_TOOL_OLLAMA = {
    "type": "function",
    "function": {
        "name": "control_pc",
        "description": "控制本地电脑：打开浏览器、打开应用、打开文件或执行命令。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": ["open_app", "open_browser", "open_file", "run_command"],
                    "description": "操作类型",
                },
                "target": {"type": "string", "description": "应用名 / URL / 文件路径 / 命令"},
            },
            "required": ["command"],
        },
    },
}

ALL_TOOLS = [SEARCH_TOOL_OLLAMA, CONTROL_PC_TOOL_OLLAMA]

# name -> 可调用函数
TOOL_REGISTRY = {
    "search_web": search_web,
    "control_pc": control_pc,
}


def execute_tool(name: str, arguments: dict) -> str:
    """根据工具名执行并返回结果字符串"""
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return f"未知工具: {name}"
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            arguments = {}
        return fn(**arguments)
    except Exception as e:
        return f"工具 {name} 执行失败: {e}"
