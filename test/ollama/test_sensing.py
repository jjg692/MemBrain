"""
单元测试：环境感知工具（LLM 主动查询）
================================================
覆盖 core/sensing 的关键纯函数与可调用路径：
  - _is_content_tab：CDP 目标的"当前激活内容页"过滤（chrome:// / 新标签页 / 非 page 等噪音剔除）
  - _clean_title：标签页标题噪音后缀清理
  - get_current_tab：在**关闭**时的诚实返回（不伪造）
  - get_current_tab：在**开启**且存在假 CDP 端点时返回真实标题/链接
  - get_perception_summary：无感知管理器时诚实返回
  - SENSING_TOOLS_SCHEMA / SENSING_TOOL_REGISTRY 的键与名字匹配
"""
import json
import threading
import http.server
import socketserver

import core.sensing as s
from core.config import ENVIRONMENT_SENSING_ENABLED


def _start_cdp(items):
    """启动一个返回固定 JSON 的本地假 CDP /json 端点，返回端口。"""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(items).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _reset_cache():
    s._tab_cache["ts"] = 0.0
    s._tab_cache["text"] = ""


def test_is_content_tab_filters_noise():
    """内容页判定：chrome://、新标签页、非 page、空标题一律剔除；
    不把 active 当硬性条件（独立 user-data-dir 调试实例 active 常为空）。"""
    assert s._is_content_tab({"type": "page", "url": "https://a.com/x",
                              "title": "标题 - 站", "active": True}) is True
    assert s._is_content_tab({"type": "page", "url": "chrome://settings",
                              "title": "设置", "active": True}) is False
    assert s._is_content_tab({"type": "page", "url": "https://a.com",
                              "title": "新标签页", "active": True}) is False
    # active 不再是硬性条件：独立调试实例里 active 常为空，只要是可读内容页即可
    assert s._is_content_tab({"type": "page", "url": "https://a.com",
                              "title": "标题", "active": False}) is True
    assert s._is_content_tab({"type": "other", "url": "https://a.com",
                              "title": "t", "active": True}) is False
    assert s._is_content_tab({"type": "page", "url": "about:blank",
                              "title": "", "active": True}) is False


def test_clean_title_strips_site_suffix():
    assert s._clean_title("我的主页 - Bilibili") == "我的主页"
    assert s._clean_title("文章标题 – 网站") == "文章标题"
    assert s._clean_title("无分隔标题") == "无分隔标题"


def test_get_current_tab_disabled_returns_honest():
    """感知开关关闭时，不伪造内容，而是如实返回'未开启'说明。"""
    # 强制关闭（即便环境把开关开了也要隔离本测试）
    s_orig = s.ENVIRONMENT_SENSING_ENABLED
    s.ENVIRONMENT_SENSING_ENABLED = False
    try:
        out = s.get_current_tab()
        assert "未开启" in out
        assert "远程调试" in out or "remote-debugging" in out
    finally:
        s.ENVIRONMENT_SENSING_ENABLED = s_orig


def test_get_current_tab_enabled_with_fake_cdp(monkeypatch):
    """开启且连上 CDP 时，返回激活内容标签页的标题与链接；忽略副页/内部页。"""
    if not s.requests:
        pytest.skip("requests 不可用")
    _reset_cache()
    items = [
        {"type": "page", "url": "https://www.bilibili.com/video/av123",
         "title": "测试视频 - 哔哩哔哩", "active": True},
        {"type": "page", "url": "https://example.com/other",
         "title": "其他页面", "active": False},
        {"type": "page", "url": "chrome://newtab", "title": "新标签页", "active": False},
    ]
    srv = _start_cdp(items)
    try:
        orig_flag = s.ENVIRONMENT_SENSING_ENABLED
        orig_port = s.BROWSER_DEBUG_PORT
        s.ENVIRONMENT_SENSING_ENABLED = True
        s.BROWSER_DEBUG_PORT = srv.server_address[1]
        try:
            out = s.get_current_tab()
            assert "测试视频" in out
            assert "av123" in out
            assert "其他页面" not in out   # 非激活页不读
        finally:
            s.ENVIRONMENT_SENSING_ENABLED = orig_flag
            s.BROWSER_DEBUG_PORT = orig_port
    finally:
        srv.shutdown()


def test_get_current_tab_unreachable_port():
    """连不上端口时如实返回读取失败，不伪造。"""
    _reset_cache()
    orig_flag = s.ENVIRONMENT_SENSING_ENABLED
    orig_port = s.BROWSER_DEBUG_PORT
    s.ENVIRONMENT_SENSING_ENABLED = True
    s.BROWSER_DEBUG_PORT = 1  # 不可能有服务的端口
    try:
        out = s.get_current_tab()
        assert "读取失败" in out
    finally:
        s.ENVIRONMENT_SENSING_ENABLED = orig_flag
        s.BROWSER_DEBUG_PORT = orig_port


def test_get_perception_summary_without_manager():
    """无感知管理器时如实返回不可用。"""
    orig = s._perception_manager
    s._perception_manager = None
    try:
        out = s.get_perception_summary()
        assert "感知" in out and ("未启用" in out or "不可用" in out)
    finally:
        s._perception_manager = orig


def test_sensing_tool_schema_and_registry_consistent():
    """Schema 里的工具名与注册表函数一一对应。"""
    schema_names = {t["function"]["name"] for t in s.SENSING_TOOLS_SCHEMA}
    reg_names = set(s.SENSING_TOOL_REGISTRY.keys())
    assert schema_names == reg_names == {"get_current_tab", "get_foreground_window", "get_perception_summary"}


def test_cdp_connection_error_returns_empty_no_raise():
    """CDP 连接失败时 read_current_tab_raw 返回空列表而不抛异常。"""
    orig_flag = s.ENVIRONMENT_SENSING_ENABLED
    orig_port = s.BROWSER_DEBUG_PORT
    s.ENVIRONMENT_SENSING_ENABLED = True
    s.BROWSER_DEBUG_PORT = 1
    try:
        assert s.read_current_tab_raw() == []
    finally:
        s.ENVIRONMENT_SENSING_ENABLED = orig_flag
        s.BROWSER_DEBUG_PORT = orig_port



def test_get_foreground_window_disabled_honest():
    """总开关关闭时，get_foreground_window 如实返回未开启说明。"""
    orig = s.ENVIRONMENT_SENSING_ENABLED
    s.ENVIRONMENT_SENSING_ENABLED = False
    try:
        out = s.get_foreground_window()
        assert "未开启" in out
    finally:
        s.ENVIRONMENT_SENSING_ENABLED = orig


def test_get_foreground_window_reads_real_on_win(monkeypatch):
    """Windows 且开启时，get_foreground_window 返回前台窗口文本（打桩感知）。"""
    from core import perception as P
    monkeypatch.setattr(P, "foreground_window", lambda: "code：测试编辑器")
    orig = s.ENVIRONMENT_SENSING_ENABLED
    s.ENVIRONMENT_SENSING_ENABLED = True
    try:
        out = s.get_foreground_window()
        assert "测试编辑器" in out
        assert "前台窗口" in out
    finally:
        s.ENVIRONMENT_SENSING_ENABLED = orig


def test_tab_switch_off_returns_unavailable(monkeypatch):
    """BROWSER_TAB_SENSING_ENABLED=False 时 get_current_tab 返回未开启（隐私粒度）。"""
    orig_e = s.ENVIRONMENT_SENSING_ENABLED
    orig_t = s.BROWSER_TAB_SENSING_ENABLED
    s.ENVIRONMENT_SENSING_ENABLED = True
    s.BROWSER_TAB_SENSING_ENABLED = False
    try:
        out = s.get_current_tab()
        assert "未开启" in out
    finally:
        s.ENVIRONMENT_SENSING_ENABLED = orig_e
        s.BROWSER_TAB_SENSING_ENABLED = orig_t


def test_perception_summary_switch_off_returns_closed(monkeypatch):
    """PERCEPTION_SUMMARY_SENSING_ENABLED=False 时 get_perception_summary 返回未开启（独立开关）。"""
    orig = s.PERCEPTION_SUMMARY_SENSING_ENABLED
    s.PERCEPTION_SUMMARY_SENSING_ENABLED = False
    try:
        out = s.get_perception_summary()
        assert "未开启" in out
    finally:
        s.PERCEPTION_SUMMARY_SENSING_ENABLED = orig


def test_master_and_sub_switches_default_true():    # noqa
    """总开关与三个子开关默认均为 True。"""
    from core.config import (ENVIRONMENT_SENSING_ENABLED, BROWSER_TAB_SENSING_ENABLED,
                             FOREGROUND_SENSING_ENABLED, PERCEPTION_SUMMARY_SENSING_ENABLED)
    assert ENVIRONMENT_SENSING_ENABLED is True
    assert BROWSER_TAB_SENSING_ENABLED is True
    assert FOREGROUND_SENSING_ENABLED is True
    assert PERCEPTION_SUMMARY_SENSING_ENABLED is True