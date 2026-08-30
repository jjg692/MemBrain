#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemBrain 桌面宠物 — M1 客户端壳 (pywebview)

- 自动拉起后端（未运行时子进程启动 web_app.py）
- 大窗模式（默认）：Live2D 立绘页（全窗口立绘 + 底部聊天气泡；复用 /ws/chat 全部能力），可全屏
- 浮动模式（--pet / PET_MODE=floating）：透明小窗只包住模型，无边框置顶、整窗=模型大小，
  "拖模型=拖窗口=移动模型本身"，模型可满屏/移动到屏幕任意位置甚至窗口外/其他应用之上
- 窗口可全屏（大窗模式托盘菜单切换）
- 系统托盘：显示 / 隐藏 / 全屏 / 打开后台管理 / 退出（退出时关闭后端）

用法:
    python desktop_pet.py                    # 启动桌面宠物（大窗 Live2D 立绘页，可全屏）
    python desktop_pet.py --pet              # 透明小窗只包模型（拖动=移动模型，可跨界）
    python desktop_pet.py --twin             # 双窗口：透明模型窗 + 独立对话窗
    python desktop_pet.py --transparent      # 兼容写法，等价 --pet
    python desktop_pet.py --chat             # 聊天页（旧行为）
    python desktop_pet.py --backend-only     # 只启动后端（调试用）

依赖:
    pip install pywebview pystray
"""
import os
import sys
import time
import subprocess
import threading
import webbrowser

import requests

# 项目根目录
ROOT = os.path.dirname(os.path.abspath(__file__))
HOST_URL = "http://127.0.0.1:8000"

# 主窗口页面：默认 Live2D 立绘页；--chat / PET_PAGE=chat 切聊天页
PET_PAGE = os.getenv("PET_PAGE", "live2d").strip().lower()
# 窗口模式：
#   window  -> 大窗立绘（默认：立绘+底部聊天 UI，可全屏；模型在窗口内拖动）
#   floating-> 透明小窗只包模型（PET_MODE=floating 或 --pet）：无边框透明、整窗=模型大小，
#               拖动即移动模型本身，可满屏/跨界移动；气泡就近跟随
PET_MODE = os.getenv("PET_MODE", "window").strip().lower()
# 透明浮动模式（兼容旧参数 --transparent / PET_TRANSPARENT=1 -> floating）
PET_TRANSPARENT = os.getenv("PET_TRANSPARENT", "").strip().lower() in ("1", "true", "yes", "on")
LIVE2D_PAGE_URL = HOST_URL + "/live2d"
CHAT_PAGE_URL = HOST_URL + "/"

_backend_proc: subprocess.Popen | None = None


# ===================== 后端管理 =====================

def backend_alive(timeout: float = 1.0) -> bool:
    """探测后端健康检查"""
    try:
        r = requests.get(HOST_URL + "/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def wait_backend(seconds: float = 30.0) -> bool:
    """等待后端就绪"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if backend_alive(0.5):
            return True
        time.sleep(0.5)
    return False


def start_backend() -> bool:
    """启动后端子进程（若尚未运行）"""
    global _backend_proc
    if backend_alive():
        return True
    if _backend_proc is not None and _backend_proc.poll() is None:
        return False  # 已经在启动中/已启动但未就绪
    log("启动后端: python web_app.py")
    # 用 pythonw 避免弹控制台窗口（Windows）
    exe = sys.executable
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        env = dict(os.environ)
        env["MEMBRAIN_NO_BROWSER"] = "1"
        _backend_proc = subprocess.Popen(
            [exe, os.path.join(ROOT, "web_app.py")],
            cwd=ROOT,
            env=env,
            creationflags=flags,
            stdout=subprocess.DEVNULL if sys.platform == "win32" else None,
            stderr=subprocess.DEVNULL if sys.platform == "win32" else None,
        )
    except Exception as e:
        log(f"启动后端失败: {e}")
        return False
    return wait_backend()


def stop_backend():
    """退出时关闭由本壳启动的后端子进程（不影响外部已启动的后端）"""
    global _backend_proc
    if _backend_proc is not None and _backend_proc.poll() is None:
        try:
            _backend_proc.terminate()
            try:
                _backend_proc.wait(timeout=5)
            except Exception:
                _backend_proc.kill()
        except Exception:
            pass
    _backend_proc = None


# ===================== 日志 =====================

def log(msg: str):
    print(f"[Pet] {msg}", flush=True)


# ===================== 入口 =====================

def _screen_size():
    """返回主屏幕工作区尺寸（用于默认窗口大小与全屏）"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)   # SM_CXSCREEN
        h = user32.GetSystemMetrics(1)   # SM_CYSCREEN
        return int(w), int(h)
    except Exception:
        return 1280, 800


def main():
    import webview

    # ---- 参数解析 ----
    global PET_PAGE, PET_MODE, PET_TRANSPARENT
    if "--live2d" in sys.argv:
        PET_PAGE = "live2d"
    if "--chat" in sys.argv:
        PET_PAGE = "chat"
    if "--pet" in sys.argv or "--transparent" in sys.argv or PET_TRANSPARENT:
        PET_MODE = "floating"
    if "--twin" in sys.argv or PET_MODE == "twin":
        PET_MODE = "twin"
    page_url = LIVE2D_PAGE_URL if PET_PAGE == "live2d" else CHAT_PAGE_URL

    sw, sh = _screen_size()
    floating = PET_MODE == "floating" and PET_PAGE == "live2d"
    twin = PET_MODE == "twin" and PET_PAGE == "live2d"
    chat_window = None

    # ---- 双窗口：模型窗(floating watcher) + 独立对话窗(sender) ----
    if twin:
        float_w = max(240, int(sw * 0.28))
        float_h = max(360, int(sh * 0.62))
        model_url = LIVE2D_PAGE_URL + "?petmode=1"
        model_win = webview.create_window(
            "MemBrain 宠物",
            model_url,
            width=float_w, height=float_h,
            resizable=False, frameless=True, easy_drag=True,
            on_top=True, transparent=True, confirm_close=False,
            background_color="#000000",
        )
        # 对话窗：加载完整 web 版聊天页（联系人/搜索/昵称/群聊/历史/角色切换全部功能），
        # 与模型窗(floating watcher)共享同一会话；桌面与 web 共用同一后端，无需重复迁移。
        chatw = max(760, int(sw * 0.34))
        chath = max(560, int(sh * 0.72))
        chat_url = HOST_URL + "/"   # web 完整聊天页 chat.html
        chat_win = webview.create_window(
            "MemBrain 对话",
            chat_url,
            width=chatw, height=chath,
            x=sw - chatw - 30, y=int(sh * 0.08),
            resizable=True, frameless=False, easy_drag=True,
            on_top=True, confirm_close=False,
            background_color="#f5f6fa",
        )
        window = model_win  # 主引用用于托盘（显示/隐藏/退出）
        chat_window = chat_win

    elif floating:
        # ---- 浮动小窗：透明、无边框、只包模型 ----
        # 窗口大小接近模型身高；工程上常用 ~0.6 屏高。整窗可拖 => 拖模型=拖窗口
        float_w = max(240, int(sw * 0.28))
        float_h = max(360, int(sh * 0.62))
        if "?" not in page_url:
            page_url = page_url + "?petmode=1"
        else:
            page_url = page_url + "&petmode=1"
        window = webview.create_window(
            "MemBrain 宠物",
            page_url,
            width=float_w,
            height=float_h,
            resizable=False,
            frameless=True,
            easy_drag=True,          # 关键：整窗即模型，拖动=移动模型
            on_top=True,
            transparent=True,        # 背景全透明（由 transparent 控制），只有模型可见
            confirm_close=False,
            background_color="#000000",  # pywebview 仅接受 6 位 hex
        )
    else:
        # ---- 大窗模式（默认立绘页 / 聊天页）：可全屏 ----
        # 窗口大小按屏幕自适应
        if PET_PAGE == "live2d":
            win_w = max(520, int(sw * 0.55))
            win_h = max(560, int(sh * 0.75))
        else:
            win_w = max(520, int(sw * 0.4))
            win_h = max(600, int(sh * 0.85))
        transparent = bool(PET_TRANSPARENT)
        if transparent and PET_PAGE == "live2d" and "?" not in page_url:
            page_url = page_url + "?transparent=1"
        window = webview.create_window(
            "MemBrain 桌面宠物",
            page_url,
            width=win_w,
            height=win_h,
            resizable=True,
            frameless=True,
            easy_drag=True,          # 无边框整窗可拖
            on_top=True,
            transparent=transparent,
            confirm_close=False,
            background_color="#2b2f36" if not transparent else "#000000",
        )

    def on_closing():
        pass

    window.events.closing += on_closing

    def run_tray():
        """托盘线程：显示/隐藏/全屏/后台/退出"""
        import pystray
        from PIL import Image, ImageDraw

        # 生成一个简单圆形托盘图标（64x64）
        icon_img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(icon_img)
        d.ellipse((4, 4, 60, 60), fill="#07c160")
        d.ellipse((22, 18, 42, 38), fill="#ffffff")
        d.arc((14, 22, 34, 54), 180, 360, fill="#ffffff", width=3)

        def show_action(icon, item):
            log("显示窗口")
            try:
                window.show()
                if chat_window is not None: chat_window.show()
            except Exception as e:
                log(f"show failed: {e}")

        def hide_action(icon, item):
            log("隐藏窗口")
            try:
                window.hide()
                if chat_window is not None: chat_window.hide()
            except Exception as e:
                log(f"hide failed: {e}")

        def toggle_fullscreen_action(icon, item):
            log("切换全屏")
            try:
                window.toggle_fullscreen()
            except Exception as e:
                log(f"fullscreen failed: {e}")

        # 双窗口模式下，后台管理直接在对话窗内打开（不弹外部浏览器）
        def admin_action(icon, item):
            if twin and chat_window is not None:
                log("打开后台管理（对话窗内）")
                try:
                    chat_window.load_url(HOST_URL + "/admin")
                except Exception as e:
                    log(f"admin load failed: {e}")
            else:
                webbrowser.open(HOST_URL + "/admin")

        def show_chat_action(icon, item):
            # 把对话窗切回完整聊天页
            if twin and chat_window is not None:
                log("对话窗切回聊天")
                try:
                    chat_window.load_url(HOST_URL + "/")
                except Exception as e:
                    log(f"chat load failed: {e}")
            else:
                webbrowser.open(HOST_URL + "/")

        def chat_action(icon, item):
            webbrowser.open(HOST_URL + "/")

        def live2d_action(icon, item):
            webbrowser.open(HOST_URL + "/live2d")

        def reload_action(icon, item):
            log("刷新窗口")
            try:
                window.load_url(page_url)
            except Exception as e:
                log(f"reload failed: {e}")

        def quit_action(icon, item):
            log("托盘退出")
            try:
                window.destroy()
                if chat_window is not None:
                    try: chat_window.destroy()
                    except Exception: pass
            except Exception:
                pass
            stop_backend()
            icon.stop()

        # 浮动/双窗口模式没有全屏概念；大窗模式才有
        menu_items = []
        if twin:
            # 双窗口：后台/聊天都在对话窗内切换
            menu_items += [
                pystray.MenuItem("对话窗→聊天", show_chat_action),
                pystray.MenuItem("对话窗→后台管理", admin_action),
            ]
        else:
            menu_items += [
                pystray.MenuItem("打开聊天", chat_action),
            ]
            menu_items.append(pystray.MenuItem("打开后台管理", admin_action))
        menu_items += [
            pystray.MenuItem("打开 Live2D 宠物", live2d_action),
            pystray.MenuItem("刷新当前页", reload_action),
        ]
        if not floating and not twin:
            menu_items.append(pystray.MenuItem("全屏/还原", toggle_fullscreen_action))
        menu_items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("显示", show_action),
            pystray.MenuItem("隐藏", hide_action),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_action),
        ]
        menu = pystray.Menu(*menu_items)
        icon = pystray.Icon("membrain_pet", icon_img, "MemBrain 桌面宠物", menu)
        icon.run()

    # 后端就绪后启动 GUI
    if not backend_alive():
        log("后端未运行，尝试启动...")
        if not start_backend():
            log("后端启动失败，请检查 web_app.py 与依赖")
            sys.exit(1)
        log("后端就绪")

    # 托盘放后台线程（与 GUI 主循环共存）
    threading.Thread(target=run_tray, daemon=True).start()

    # 启动 GUI
    webview.start()


if __name__ == "__main__":
    if "--backend-only" in sys.argv:
        # 只启动后端，便于调试
        os.chdir(ROOT)
        from web_app import app
        import uvicorn
        from core.config import HOST, PORT
        uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
    else:
        main()
