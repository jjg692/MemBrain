"""
环境感知工具（harness 级，LLM 可主动调用）

把"主机的当下状态"做成 LLM 主动查询的能力，而不是只能被动地
被塞进 system prompt：

- get_current_tab
    通过 Chrome/Edge 的 DevTools 远程调试协议(CDP)读取"当前激活标签页"的
    标题+URL，让角色知道用户此刻在看什么。

    前置条件（用户需自行启动）：Chrome/Edge 以
        --remote-debugging-port=<port>
    启动；只有满足时才返回真实数据，否则按"不伪造"原则如实返回不可用。

- get_perception_summary
    当 LLM 想要比 system prompt 里更细/更新的一版"用户当下感知"时，
    主动拉取一次感知汇总（时序/情境/作息/情绪趋势）。

设计原则（与感知层一致）：
- 全本地、免费、无额外 LLM 调用；
- 读不到/未启用一律返回空/不可用，绝不伪造；
- 通过 ENVIRONMENT_SENSING_ENABLED 总开关控制（默认关闭，
  规避默认环境下用户没开远程调试端口时反复查询的失败噪音与隐私顾虑）。
"""

import json
import sys
import threading
import time

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from core.config import (
    ENVIRONMENT_SENSING_ENABLED,
    BROWSER_TAB_SENSING_ENABLED,
    PERCEPTION_SUMMARY_SENSING_ENABLED,
    BROWSER_DEBUG_PORT,
    BROWSER_SENSING_TIMEOUT,
    MAX_TAB_TITLE_CHARS,
)

# ===================== 浏览器当前标签页（CDP） =====================

#: 已被主动关闭/置空（避免读到"新标签页/空白页"当有效内容）
_NON_CONTENT_TITLE_HINTS = ("新标签页", "新滚动标签页", "", "chrome://newtab")

#: 注册后由 initializer 注入真正的 PerceptionManager（避免本模块引全局单例）
_perception_manager = None
#: 彩现给当前 user 的 user_id（工具调用时由注册的可调用包装器设置）
_perception_user_id = "default_user"
#: 低频率缓存，避免工具被高频调用时每次都打 CDP
_tab_cache = {"ts": 0.0, "text": ""}
_tab_lock = threading.RLock()


def configure_sensing(perception_manager=None, default_user_id: str = "default_user"):
    """由 AppInitializer 注入感知管理器（供 get_perception_summary 使用）。"""
    global _perception_manager, _perception_user_id
    _perception_manager = perception_manager
    _perception_user_id = default_user_id or "default_user"


def _clean_title(title: str) -> str:
    """去除标签页标题的常见噪音后缀，返回 `站点名` 部分。"""
    t = (title or "").strip()
    for sep in (" - ", " – ", " — ", " :: ", " | "):
        if sep in t:
            t = t.rsplit(sep, 1)[0]
    return t.strip()


def _is_content_tab(item: dict) -> bool:
    """判断 CDP 目标是否为`当前激活的内容标签页`。"""
    if not isinstance(item, dict):
        return False
    if item.get("type") != "page":
        return False
    if not item.get("active"):
        return False
    url = (item.get("url") or "")
    # 跳过浏览器内部页面 / 扩展页
    if url.startswith(("chrome://", "edge://", "about:", "devtools://", "chrome-extension://")):
        return False
    title = (item.get("title") or "").strip()
    if title in _NON_CONTENT_TITLE_HINTS:
        return False
    return True


def _fetch_tabs_via_cdp(port: int) -> list:
    """经 CDP HTTP JSON 端点拉取所有目标；失败返回空列表（不伪造）。"""
    if requests is None:  # pragma: no cover
        return []
    url = f"http://127.0.0.1:{port}/json"
    resp = requests.get(url, timeout=BROWSER_SENSING_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def read_current_tab_raw() -> list:
    """读取当前激活标签页裸信息（不含缓存）。返回 [] 表示不可用。"""
    if not ENVIRONMENT_SENSING_ENABLED or not BROWSER_TAB_SENSING_ENABLED:
        return []
    try:
        return _fetch_tabs_via_cdp(BROWSER_DEBUG_PORT)
    except Exception:
        return []


def get_current_tab() -> str:
    """harness 工具：返回当前激活浏览器标签页的标题与链接（中文文本）。

    前置条件：Chrome/Edge 需以 --remote-debugging-port=<port> 启动（默认 9222），
    且 ENVIRONMENT_SENSING_ENABLED=true。读不到时如实返回不可用说明。
    """
    if not ENVIRONMENT_SENSING_ENABLED or not BROWSER_TAB_SENSING_ENABLED:
        return (
            "（浏览器感知未开启）标签页感知未开启，无法读取当前标签页。"
            "如需开启：设置 ENVIRONMENT_SENSING_ENABLED=true，并用 "
            "--remote-debugging-port 启动浏览器。"
        )
    # 低频率缓存：2 秒内复用，避免 LLM 连问时反复打 CDP
    now = time.time()
    with _tab_lock:
        if now - _tab_cache["ts"] < 2.0 and _tab_cache["text"]:
            return _tab_cache["text"]
    try:
        items = _fetch_tabs_via_cdp(BROWSER_DEBUG_PORT)
    except Exception:
        items = []
    if not items:
        text = (
            "（读取失败）未能连接浏览器调试端口。请确认浏览器已用 "
            f"--remote-debugging-port={BROWSER_DEBUG_PORT} 启动。"
        )
    else:
        active = [it for it in items if _is_content_tab(it)]
        if not active:
            text = "（读取为空）浏览器当前激活的不是内容页面，或没有可读取的标签页。"
        else:
            first = active[0]
            title = _clean_title(first.get("title", ""))
            url = first.get("url", "")
            lines = ["用户当前浏览器正打开："]
            lines.append(f"- 标题：{title[:MAX_TAB_TITLE_CHARS] if title else '（无标题）'}")
            if url:
                lines.append(f"- 链接：{url[:300]}")
            text = "\n".join(lines)
    with _tab_lock:
        _tab_cache.update({"ts": now, "text": text})
    return text


def get_perception_summary() -> str:
    """harness 工具：拉取一次用户当下感知汇总（时序/情境/作息/情绪趋势）。"""
    if not PERCEPTION_SUMMARY_SENSING_ENABLED:
        return "（感知摘要未开启）感知摘要工具已关闭。"
    pm = _perception_manager
    if pm is None:
        return "（感知不可用）感知层未启用。"
    try:
        text = pm.summarize(_perception_user_id)
        if not text:
            return "（感知为空）当前没有可汇总的感知信息。"
        return text
    except Exception as e:  # pragma: no cover
        return f"（感知失败）{e}"
def get_foreground_window() -> str:
    """harness 工具：返回用户当前聚焦的前台窗口（应用 + 标题），含失败兜底语义。

    用 Windows 原生 GetForegroundWindow。仅 Windows 且感知开关开启时可用；
    读不到则如实返回说明（不伪造）。
    """
    if not ENVIRONMENT_SENSING_ENABLED:
        return "（前台感知未开启）感知开关未开启，无法读取前台窗口。如需开启：设置 ENVIRONMENT_SENSING_ENABLED=true。"
    if sys.platform != "win32":
        return "（前台感知不可用）该功能仅支持 Windows。"
    try:
        from core.perception import foreground_window
        text = foreground_window()
        if not text:
            return "（读取为空）当前没有可读取的前台窗口标题。"
        return "用户当前前台窗口：" + text
    except Exception as e:  # pragma: no cover
        return f"（读取失败）{e}"




# ===================== 工具 Schema（供 LLM） =====================

GET_CURRENT_TAB_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_tab",
        "description": (
            "查看用户当前浏览器(Chrome/Edge)正在打开的标签页标题与链接，"
            "得知用户此刻在看什么。仅当浏览器开启远程调试端口且感知开关开启时可用；"
            "不可用时返回说明。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

GET_FOREGROUND_WINDOW_TOOL = {
    "type": "function",
    "function": {
        "name": "get_foreground_window",
        "description": (
            "查看用户当前正在用的前台应用及其窗口标题（不限浏览器，如写文档/看代码/看视频）。"
            "仅 Windows 且感知开关开启时可用；不可用时返回说明。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

GET_PERCEPTION_SUMMARY_TOOL = {
    "type": "function",
    "function": {
        "name": "get_perception_summary",
        "description": (
            "拉取一次当前用户的环境感知摘要（现在的时间/时段、所处的场景、"
            "作息规律、最近心情与好感度趋势、是否在线等）。当你需要比已有上下文"
            "更新的用户当下状态细节时可调用。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SENSING_TOOLS_SCHEMA = [GET_CURRENT_TAB_TOOL, GET_FOREGROUND_WINDOW_TOOL, GET_PERCEPTION_SUMMARY_TOOL]


def _get_current_tab_bound(user_id: str = "default_user") -> str:
    """带 user 的包装（工具注册用）。"""
    return get_current_tab()


def _get_foreground_window_bound(user_id: str = "default_user") -> str:
    """带 user 的包装（工具注册用）。"""
    return get_foreground_window()


def _get_perception_summary_bound(user_id: str = "default_user") -> str:
    """带 user 的包装（工具注册用）。"""
    global _perception_user_id
    _perception_user_id = user_id or "default_user"
    return get_perception_summary()


SENSING_TOOL_REGISTRY = {
    "get_current_tab": _get_current_tab_bound,
    "get_foreground_window": _get_foreground_window_bound,
    "get_perception_summary": _get_perception_summary_bound,
}
