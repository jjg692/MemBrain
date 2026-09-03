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

# 中国大陆常见城市 -> 精确坐标（lat,lon）。**直接存坐标**，绕过 Open-Meteo
# 地理编码 API 对中文地名的不可靠解析（例：它把"长沙"错匹配到重庆边界的同名地，
# 导致天气显示的是别处数据）。用精确坐标调预报接口即稳定。
_CITY_MAP = {
    "北京": "39.9042,116.4074",
    "上海": "31.2304,121.4737",
    "广州": "23.1291,113.2644",
    "深圳": "22.5431,114.0579",
    "杭州": "30.2741,120.1551",
    "成都": "30.5728,104.0668",
    "武汉": "30.5928,114.3055",
    "西安": "34.3416,108.9398",
    "南京": "32.0603,118.7969",
    "重庆": "29.5630,106.5516",
    "天津": "39.3434,117.3616",
    "苏州": "31.2989,120.5853",
    "长沙": "28.2282,112.9388",
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


# 明确的"设提醒"触发词（供 tool_fallback 兜底守卫使用）。
# 刻意**保守**：只匹配明确的"提醒/设提醒/定时"等表述，
# 不包含裸"记"字（否则"记得/记忆"等闲聊会被误判成要设提醒）。
REMIND_HINTS = ["提醒", "设提醒", "别忘", "到点", "定时", "设个提醒", "帮我记一下"]


def _detect_remind(query: str) -> bool:
    """是否明确表达了「要设提醒」的意图（供 git 兜底守卫强制注入 remind_me）。"""
    q = (query or "").lower()
    return any(h in q for h in REMIND_HINTS)


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
    """由内置城市表返回精确坐标 "lat,lon"，不调外部地理编码 API。

    之前依赖 Open-Meteo geocoding API 按中文名解析，但该接口对多字同名
    城市匹配不可靠（如"长沙"被匹配到重庆边界），导致天气数据错误。改为
    内置坐标表直接命中，稳定且无外部依赖。
    """
    key = city.strip()
    coord = _CITY_MAP.get(key)
    if coord:
        return coord if "," in coord else None
    return None


def _weather_search(query: str) -> str:
    """Open-Meteo 天气：从 query 提取城市（内置精确坐标），返回实时 + 今/明天预报。

    改进（相比旧版）：
    - 城市坐标来自内置 _CITY_MAP（精确坐标），不再调用不可靠的 Open-Meteo
      geocoding 中文解析，修复"长沙查到重庆"等错误。
    - 用 daily 接口取今天/明天的**最高/最低温 + 降水概率**，按天组织输出；
      用户问"明天"时能给出对应日期的预报，而非旧版"当前时刻起的前8个3小时间隔"。
    """
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
        # 兜底：尝试把"XX天气"里的 XX 当城市名（可能不在内置表 → 下面坐标查不到则诚实返回）
        m = re.match(r"^([一-龥]{2,4})(?:今天|明天|后天|的)?天气", query)
        if m:
            city = m.group(1)
    if not city:
        return ""
    coords = _geocode_city(city)
    if not coords:
        return f"（暂不支持该城市）暂时只支持部分中国大陆城市天气，请换个常见城市试试。"
    lat, lon = coords.split(",")
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "current_weather": "true",
            "hourly": "temperature_2m,precipitation,weathercode",
            "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=_TIMEOUT,
    )
    data = r.json()
    cur = data.get("current_weather") or {}
    temp = cur.get("temperature")
    code = cur.get("weathercode")
    desc = _wmocode_description(code)
    lines = [f"{city} 当前天气：{desc}，气温 {temp}°C（Open-Meteo 实时）"]

    # 今天 / 明天的 daily 预报
    daily = data.get("daily") or {}
    d_times = daily.get("time") or []
    d_codes = daily.get("weathercode") or []
    d_max = daily.get("temperature_2m_max") or []
    d_min = daily.get("temperature_2m_min") or []
    d_pop = daily.get("precipitation_probability_max") or []
    day_labels = ["今天", "明天", "后天"]
    if d_times:
        for i in range(min(3, len(d_times))):
            label = day_labels[i] if i < len(day_labels) else f"{d_times[i][5:]}"
            ddesc = _wmocode_description(d_codes[i]) if i < len(d_codes) else ""
            hi = d_max[i] if i < len(d_max) else "?"
            lo = d_min[i] if i < len(d_min) else "?"
            pop = d_pop[i] if i < len(d_pop) else None
            pop_str = f"，降水概率 {pop}%" if pop is not None else ""
            lines.append(f"{label}（{d_times[i]}）：{ddesc}，{lo}~{hi}°C{pop_str}")

    # 未来逐 3 小时气温简况（自当前之后 12 小时，跨今天/明天）
    hourly = data.get("hourly") or {}
    h_times = hourly.get("time") or []
    h_temps = hourly.get("temperature_2m") or []
    cur_iso = cur.get("time") or ""
    hourly_bits = []
    for i in range(len(h_times)):
        ht = h_times[i]
        if cur_iso and ht <= cur_iso:
            continue
        try:
            if int(ht[11:13]) % 3 == 0:
                hourly_bits.append(f"{ht[5:16]} {h_temps[i]}°C")
        except Exception:
            pass
        if len(hourly_bits) >= 6:
            break
    if hourly_bits:
        lines.append("未来逐3小时气温：" + " · ".join(hourly_bits))
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

# 环境感知工具（LLM 主动查询）：当前浏览器标签页 / 感知摘要。
# 由 ENVIRONMENT_SENSING_ENABLED 总开关控制（默认关闭）。仅开关开启时才注册暴露给 LLM，
# 且 get_current_tab 需浏览器开启远程调试端口，读不到时如实返回不可用（不伪造）。
try:
    from core.config import ENVIRONMENT_SENSING_ENABLED
except Exception:
    ENVIRONMENT_SENSING_ENABLED = False
if ENVIRONMENT_SENSING_ENABLED:
    try:
        from core.sensing import SENSING_TOOLS_SCHEMA, SENSING_TOOL_REGISTRY
        ALL_TOOLS = ALL_TOOLS + list(SENSING_TOOLS_SCHEMA)
        TOOL_REGISTRY.update(SENSING_TOOL_REGISTRY)
    except Exception:
        pass


# Live2D 身体表达（B 方案）：LLM 主动指挥身体。
# 仅在 LIVE2D_BODY_MODE=B 时注册 express_body 工具；C 方案无需工具（由内核自动映射）。
try:
    from core.config import LIVE2D_BODY_MODE
    from core.body_tools import EXPRESS_BODY_TOOL, EXPRESS_BODY_REGISTRY
    if LIVE2D_BODY_MODE == "B":
        ALL_TOOLS = ALL_TOOLS + [EXPRESS_BODY_TOOL]
        TOOL_REGISTRY.update(EXPRESS_BODY_REGISTRY)
except Exception:
    pass

# MCP 客户端骨架：动态发现并注册外部工具（游戏 MCP 等）。
# 受 STARDEW_MCP_ENABLED 总开关控制（默认关闭，规避他人拉取无游戏/无环境也受影响）。
# 关闭时完全不启动 MCP server、不注册工具（LLM 看不到）；开启时才加载。
# 即便开启但 server 启动失败，也 try/except 静默降级，不影响既有功能。
_mcp = None
try:
    from core.config import STARDEW_MCP_ENABLED
except Exception:
    STARDEW_MCP_ENABLED = False
if STARDEW_MCP_ENABLED:
    try:
        from core.mcp_client import get_mcp_manager, McpError
        _mcp = get_mcp_manager()
        _mcp.load()
        if _mcp.servers:
            ALL_TOOLS.extend(_mcp.schemas())
            # 记录每个 MCP 工具的原始描述（供 docstring 复用，避免 LangGraph 因缺描述失败）
            _schema_by_name = {s["function"]["name"]: s["function"].get("description", "")
                               for s in _mcp.schemas()}
            for tname in _mcp.tool_names():
                def _mk(tname):
                    desc = _schema_by_name.get(tname, tname)
                    def _call(arguments=None, **kw):
                        """调用外部 MCP 工具（星露谷游戏等）。"""
                        return _mcp.call(tname, arguments or kw)
                    # 覆盖 docstring 为原始工具描述，LangChain tool() 依赖它
                    _call.__doc__ = desc or "调用外部 MCP 工具。"
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
