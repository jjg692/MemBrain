"""
单元测试：web 查询调用 / search_web 意图路由 / 助手核心工具
================================================
- search_web：天气 / 百科 / 通用 的意图路由；回退链；诚实失败
- execute_tool：按名称执行
- assistant_tools：提醒 / 时间 / 文件沙箱（越权拒绝）
"""
import pytest


# ===================== 意图路由 =====================

def test_detect_intent_weather():
    from core.tools import _detect_intent
    assert _detect_intent("北京今天天气怎么样") == "weather"
    assert _detect_intent("上海 气温多少度") == "weather"


def test_detect_intent_wiki():
    from core.tools import _detect_intent
    assert _detect_intent("什么是黑洞") == "wiki"
    assert _detect_intent("爱因斯坦是谁") == "wiki"


def test_detect_intent_general():
    from core.tools import _detect_intent
    assert _detect_intent("帮我搜一下最新的人工智能新闻") == "general"


def test_weather_candidates_dedupe(monkeypatch):
    """天气意图：Open-Meteo 优先 + 通用源（wiki 兜底去重）。"""
    from core.tools import _candidates_for_intent
    names = [n for n, _ in _candidates_for_intent("北京天气", "weather")]
    assert "openmeteo" == names[0]
    # 由于配置无 BAIDU key，通用源不含 baidu
    assert "baidu" not in names


# ===================== search_web 数据源（mock 网络） =====================

def _fake_requests(monkeypatch, responses):
    """把 requests.get/post 换成脚本化响应。responses: {'http://x': text_or_exc}"""
    import core.tools as T

    class FakeResp:
        def __init__(self, payload, ok=True, raise_err=None):
            self._payload = payload
            self._ok = ok
            self._raise = raise_err
        def raise_for_status(self):
            if self._raise:
                raise self._raise
        @property
        def text(self):
            return self._payload if isinstance(self._payload, str) else ""
        def json(self):
            return self._payload if isinstance(self._payload, (dict, list)) else {}

    def fake_get(url, *a, **k):
        for key, val in responses.items():
            if url.startswith(key):
                if isinstance(val, Exception):
                    raise val
                return FakeResp(val)
        raise RuntimeError(f"unexpected get url: {url}")

    def fake_post(url, *a, **k):
        for key, val in responses.items():
            if url.startswith(key):
                return FakeResp(val)
        raise RuntimeError(f"unexpected post url: {url}")

    monkeypatch.setattr(T.requests, "get", fake_get)
    monkeypatch.setattr(T.requests, "post", fake_post)


def test_search_web_weather_success(monkeypatch):
    """天气查询命中 Open-Meteo：返回天气描述，不回退到通用源。"""
    from core.tools import search_web, _geocode_city
    monkeypatch.setattr("core.tools._CITY_MAP", {"北京": "beijing"})
    _fake_requests(monkeypatch, {
        "https://geocoding-api.open-meteo.com": {"results": [{"latitude": 39.9, "longitude": 116.4}]},
        "https://api.open-meteo.com": {
            "current_weather": {"temperature": 24.5, "weathercode": 0},
            "hourly": {"time": ["2026-01-01T09:00", "2026-01-01T12:00", "2026-01-01T15:00"],
                       "temperature_2m": [20, 24, 28],
                       "precipitation": [0, 0, 0.1]},
        },
    })
    result = search_web("北京天气怎么样")
    assert "北京" in result
    assert "24.5" in result or "晴" in result


def test_search_web_empty_query():
    from core.tools import search_web
    assert "不能为空" in search_web("")


def test_search_web_all_sources_fail_honest(monkeypatch):
    """所有源都失败：诚实告知，不做无关降级。"""
    from core.tools import search_web, _ddg_search, _wikipedia_search
    _fake_requests(monkeypatch, {})  # 任何请求都抛 unexpected
    result = search_web("随便搜点什么")
    assert "搜索失败" in result or "未能获取" in result


# ===================== execute_tool 按名执行 =====================

def test_execute_tool_search_web_empty(monkeypatch):
    """execute_tool 按名调用 search_web（空关键词）。"""
    from core.tools import execute_tool
    assert "不能为空" in execute_tool("search_web", {"query": ""})


def test_execute_tool_unknown():
    from core.tools import execute_tool
    assert "未知工具" in execute_tool("no_such_tool", {})


def test_execute_tool_control_pc_no_target(monkeypatch):
    """control_pc 缺 target 时应返回参数不完整提示（不真正执行）。"""
    from core.tools import execute_tool
    result = execute_tool("control_pc", {"command": "open_browser"})
    assert "参数不完整" in result


# ===================== 助手核心工具 =====================

def test_get_current_time_format():
    from core.assistant_tools import get_current_time
    out = get_current_time()
    assert "现在：" in out
    assert "时段" in out


def test_write_read_list_file_sandbox(tmp_path, monkeypatch):
    """文件沙箱：工作区内可读写，可列目录。"""
    from core import assistant_tools as AT
    ws = tmp_path / "ws"
    monkeypatch.setattr(AT, "_WORKSPACE", ws)
    ws.mkdir(parents=True, exist_ok=True)

    assert "已写入" in AT.write_file("notes.txt", "你好香澄")
    out = AT.read_file("notes.txt")
    assert "你好香澄" in out
    listing = AT.list_files(".")
    assert "notes.txt" in listing


def test_read_file_traversal_rejected(tmp_path, monkeypatch):
    """路径穿越 / 越界：应被沙箱拒绝。"""
    from core import assistant_tools as AT
    ws = tmp_path / "ws"
    monkeypatch.setattr(AT, "_WORKSPACE", ws)
    ws.mkdir(parents=True, exist_ok=True)
    # 写入一个工作区外的文件
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    assert "拒绝" in AT.read_file(str(outside))
    assert "拒绝" in AT.read_file("../secret.txt")


def test_write_file_uploads_denied(tmp_path, monkeypatch):
    """uploads 目录只读：写入应被拒绝。"""
    from core import assistant_tools as AT
    ws = tmp_path / "ws"
    up = tmp_path / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(AT, "_WORKSPACE", ws)
    monkeypatch.setattr(AT, "_UPLOADS", up)
    assert "拒绝写入" in AT.write_file(str(up / "x.txt"), "hack")


# ===================== 提醒工具 =====================

def test_remind_me_requires_text(tmp_path, monkeypatch):
    """remind_me：缺 text 拒绝。"""
    import core.assistant_tools as AT
    from core.reminder import ReminderStore
    monkeypatch.setattr(AT, "_reminder_store",
                        lambda: ReminderStore(tmp_path / "rem.json"))
    assert "未创建" in AT.remind_me("", "2026-12-31 10:00")


def test_remind_me_requires_when_or_repeat(tmp_path, monkeypatch):
    """remind_me：既无 when 也无 repeat 拒绝。"""
    import core.assistant_tools as AT
    from core.reminder import ReminderStore
    monkeypatch.setattr(AT, "_reminder_store",
                        lambda: ReminderStore(tmp_path / "rem.json"))
    assert "未创建" in AT.remind_me("记得喝水", "")


def test_remind_me_create_list_cancel(tmp_path, monkeypatch):
    """提醒全流程：创建 -> 列出 -> 取消。"""
    import core.assistant_tools as AT
    from core.reminder import ReminderStore
    store = ReminderStore(tmp_path / "rem.json")
    monkeypatch.setattr(AT, "_reminder_store", lambda: store)

    out = AT.remind_me("明天下午开会", "2026-12-31 14:00", user_id="u1")
    assert "已为你设置提醒" in out
    lst = AT.list_reminders("u1")
    assert "明天下午开会" in lst
    items = store.list("u1", include_done=False)
    assert items
    rid = items[0]["id"]
    assert "已取消" in AT.cancel_reminder(rid, "u1")
    assert store.list("u1", include_done=False) == []
