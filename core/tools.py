"""
工具定义：search_web + control_pc + 多源联网检索
提供给 LangGraph ToolNode 及 LLM 工具调用 Schema

search_web 内部实现“意图路由 + 回退链”，对调用方（LLM/L3）透明：
  - 意图轻量判定（规则，不花 LLM 调用）
  - 按候选源优先级逐源尝试，命中（有非空结果）即短路返回
  - 全部失败时诚实告知，不做无关降级
数据源：
  - 通用网页：DuckDuckGo（无 key）；若配置 BAIDU_API_KEY 则百度优先
  - 天气：Open-Meteo（免费无 key，含地理编码 + 实时/预报）
  - 百科/概念：Wikipedia（中文优先）
"""
import json
import subprocess
import sys
import urllib.parse
from typing import Optional

import requests

from core.config import BAIDU_API_KEY

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_TIMEOUT = 10


# ===================== 意图路由 =====================

# 天气触发词：命中即优先天气源
WEATHER_HINTS = [
    "天气", "气温", "温度", "降雨", "下雪", "雨", "雪", "℃", "度", "预报",
    "晴", "阴", "大风", "台风", "雾霾", "湿度", "weather",
]
# 百科触发词：命中即优先百科源
WIKI_HINTS = [
    "是什么", "什么是", "是谁", "谁", "简介", "背景", "历史", "概念",
    "定义", "由来", "含义", "哪个", "著名", "介绍", "百科",
]

# 中国大陆常见城市（用于天气本地化兜底，可扩充）
_CITY_MAP = {
    "北京": "beijing", "上海": "shanghai", "广州": "guangzhou",
    "深圳": "shenzhen", "杭州": "hangzhou", "成都": "chengdu",
    "武汉": "wuhan", "西安": "xian", "南京": "nanjing", "重庆": "chongqing",
    "天津": "tianjin", "苏州": "suzhou", "长沙": "changsha",
}


def _detect_intent(query: str) -> str:
    """返回 'weather' | 'wiki' | 'general'"""
    q = query.lower()
    for h in WEATHER_HINTS:
        if h in q:
            return "weather"
    for h in WIKI_HINTS:
        if h in q:
            return "wiki"
    return "general"


# ===================== search_web 主入口 =====================

def search_web(query: str) -> str:
    """
    联网搜索实时信息（天气、新闻、股价、最新动态等）。
    内部按意图路由 + 回退链选择数据源；查不到时诚实告知，不做无关降级。
    """
    query = (query or "").strip()
    if not query:
        return "（搜索不可用）搜索关键词不能为空。"

    intent = _detect_intent(query)

    # 按意图生成候选源顺序，命中即短路
    candidates = _candidates_for_intent(query, intent)

    for name, fn in candidates:
        try:
            result = fn(query)
            if result and not _is_failure(result):
                return result
        except Exception:
            # 单个源失败则继续回退下一个源
            continue
    return f"（搜索失败）未能获取“{query}”的搜索结果，请稍后再试。"


def _candidates_for_intent(query: str, intent: str) -> list:
    """按意图返回 [(源名, 可调用函数)] 优先级列表"""
    general = []
    if BAIDU_API_KEY:
        general.append(("baidu", _baidu_search))
    general.append(("duckduckgo", _ddg_search))
    general.append(("wikipedia", _wikipedia_search))  # 概念类问题通用兜底

    if intent == "weather":
        return [("openmeteo", lambda q: _weather_search(q)), *general]
    if intent == "wiki":
        return [("wikipedia", lambda q: _wikipedia_search(q)), *general]
    return general


def _is_failure(text: str) -> bool:
    """判断搜索结果是否为失败/空标记（用于回退链短路）"""
    if not text:
        return True
    return text.startswith(("（搜索不可用", "（搜索失败", "（搜索未返回结果", "搜索服务不可用"))


# ===================== 通用网页搜索 =====================

def _ddg_search(query: str, limit: int = 5) -> str:
    """DuckDuckGo HTML 搜索（无 key），解析标题/链接/摘要"""
    import re
    from html import unescape

    params = {"q": query, "kl": "cn-zh", "ia": "web"}
    resp = requests.get(
        "https://html.duckduckgo.com/html/", params=params, timeout=_TIMEOUT,
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
    )
    resp.raise_for_status()
    html = resp.text
    # 提取结果块
    results = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    )
    if not results:
        return ""
    lines = [f"关于“{query}”的搜索结果："]
    for i, (url, title, snippet) in enumerate(results[:limit], 1):
        title = unescape(re.sub(r"<[^>]+>", "", title)).strip()
        snippet = unescape(re.sub(r"<[^>]+>", "", snippet)).strip()
        lines.append(f"{i}. {title}" + "\n   " + snippet[:150] + "\n   " + url)
    return "\n".join(lines)


# ===================== 天气源（Open-Meteo） =====================

def _geocode_city(city: str) -> Optional[str]:
    """地理编码：优先内置城市表，其次 Open-Meteo geocoding API，返回 lat,lon"""
    key = city.strip()
    en = _CITY_MAP.get(key)
    if en:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": key, "count": 1, "language": "zh", "format": "json"},
            timeout=_TIMEOUT,
        )
        data = r.json()
        res = (data.get("results") or [])
        if res:
            return f"{res[0]['latitude']},{res[0]['longitude']}"
    return None


def _weather_search(query: str) -> str:
    """Open-Meteo 天气：从 query 提取城市，返回实时与预报"""
    import re
    # 提取城市：内置表直接命中，否则取第一个疑似城市词（忽略常用天气词）
    city = None
    for c in _CITY_MAP:
        if c in query:
            city = c
            break
    if not city:
        for c in _CITY_MAP:
            if query.startswith(c) or f"天气{c}" in f" {query}":
                city = c
                break
    if not city:
        # 兜底：尝试把"XX天气"里的 XX 当城市名
        m = re.match(r"^([一-龥]{2,4})(?:今天|明天|后天|的)?天气", query)
        if m:
            city = m.group(1)
    if not city:
        return ""
    coords = _geocode_city(city)
    if not coords:
        return ""
    lat, lon = coords.split(",")
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "current_weather": "true",
            "hourly": "temperature_2m,relativehumidity_2m,precipitation,weathercode",
            "timezone": "auto",
            "forecast_days": 2,
        },
        timeout=_TIMEOUT,
    )
    data = r.json()
    cur = data.get("current_weather") or {}
    temp = cur.get("temperature")
    code = cur.get("weathercode")
    desc = _wmocode_description(code)
    lines = [f"{city} 当前天气：{desc}，气温 {temp}°C（Open-Meteo 实时）"]
    # 未来 24h 每小时简况
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    precip = hourly.get("precipitation") or []
    next_temps = []
    for i in range(0, min(24, len(times)), 3):
        h = times[i][11:16]
        t = temps[i] if i < len(temps) else "?"
        next_temps.append(f"{h} {t}°C")
    if next_temps:
        lines.append("未来24小时气温（每3小时）：" + " · ".join(next_temps))
    return "\n".join(lines)


def _wmocode_description(code) -> str:
    """WMO weather code -> 中文描述"""
    code = code if code is not None else -1
    table = {
        0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
        45: "雾", 48: "雾凇", 51: "毛毛雨", 53: "小雨", 55: "中雨",
        61: "小阵雨", 63: "中阵雨", 65: "大阵雨", 71: "小雪",
        73: "中雪", 75: "大雪", 80: "小阵雨", 81: "中阵雨", 82: "暴风雨",
        95: "雷暴", 96: "雷暴伴冰雹",
    }
    return table.get(code, f"未知({code})")


# ===================== 百科源（Wikipedia） =====================

def _wikipedia_search(query: str) -> str:
    """Wikipedia 中文搜索，返回摘要（无 key）"""
    # 尝试直接按词条名搜索（去搭配词）
    import re
    clean = re.sub(r"(是什么|什么是|谁|的|呢|简介|介绍|[？?])", "", query).strip()
    if not clean:
        clean = query
    # 先搜页标题
    s = requests.get(
        "https://zh.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": 3, "utf8": 1,
        },
        timeout=_TIMEOUT, headers={"User-Agent": _UA},
    )
    s.raise_for_status()
    hits = ((s.json().get("query") or {}).get("search")) or []
    if not hits:
        return ""
    title = hits[0]["title"]
    # 抓取该词条摘要
    e = requests.get(
        "https://zh.wikipedia.org/w/api.php",
        params={
            "action": "query", "prop": "extracts", "explaintext": 1,
            "exintro": 1, "titles": title, "format": "json", "redirects": 1,
        },
        timeout=_TIMEOUT, headers={"User-Agent": _UA},
    )
    e.raise_for_status()
    pages = ((e.json().get("query") or {}).get("pages")) or {}
    for page in pages.values():
        extract = page.get("extract", "").strip()
        if extract:
            lines = [f"【{page.get('title', title)}】"]
            lines.append(extract[:600])
            url = f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(title)}"
            lines.append(f"来源：{url}")
            return "\n".join(lines)
    return ""


# ===================== 百度（可选，需 key） =====================

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


# ===================== 本机控制（control_pc） =====================

def control_pc(command: str, target: Optional[str] = None) -> str:
    """
    控制本地电脑：打开应用/浏览器/文件/执行命令。
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


# ===================== Bilibili 热门（独立给 L3 使用） =====================

def fetch_bilibili_popular(query: str = "", limit: int = 5) -> list:
    """
    独立抓取 Bilibili 热门榜单（演示联网能力）。
    与 search_web 解耦：直接返回原始条目列表，供 L3 主动信息采集等场景直接调用。
    返回：[{"title": str, "uri": str}, ...]；请求失败时返回空列表。
    """
    try:
        resp = requests.get("https://api.bilibili.com/x/web-interface/popular", timeout=8, headers={
            "User-Agent": _UA
        })
        data = resp.json()
        items = (data.get("data") or {}).get("list", [])[:limit]
        return [
            {
                "title": it.get("title", ""),
                "uri": it.get("uri", "") or it.get("bvid", ""),
            }
            for it in items
        ]
    except Exception:
        return []


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

# 助手核心工具（提醒/时间/文件）——从 core.assistant_tools 引入
try:
    from core.assistant_tools import (
        ASSISTANT_TOOLS_SCHEMA, ASSISTANT_TOOL_REGISTRY, ensure_workspace,
    )
    ensure_workspace()
    _ASSISTANT_AVAILABLE = True
except Exception:
    _ASSISTANT_AVAILABLE = False

ALL_TOOLS = [SEARCH_TOOL_OLLAMA, CONTROL_PC_TOOL_OLLAMA] + (
    ASSISTANT_TOOLS_SCHEMA if _ASSISTANT_AVAILABLE else []
)

# name -> 可调用函数
TOOL_REGISTRY = {
    "search_web": search_web,
    "control_pc": control_pc,
}
if _ASSISTANT_AVAILABLE:
    TOOL_REGISTRY.update(ASSISTANT_TOOL_REGISTRY)

# MCP 客户端骨架：动态发现并注册外部工具（游戏 MCP 等）。
# 无 MCP 配置或 server 启动失败时静默降级，不影响既有功能。
try:
    from core.mcp_client import get_mcp_manager, McpError
    _mcp = get_mcp_manager()
    _mcp.load()
    if _mcp.servers:
        ALL_TOOLS.extend(_mcp.schemas())
        for tname in _mcp.tool_names():
            def _mk(tname):
                def _call(arguments=None, **kw):
                    return _mcp.call(tname, arguments or kw)
                return _call
            TOOL_REGISTRY[tname] = _mk(tname)
except Exception as e:
    from core.logger import log_error
    log_error("MCP", f"MCP 注册失败（跳过）: {e}")
    _mcp = None


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
