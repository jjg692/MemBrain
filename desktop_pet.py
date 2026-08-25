#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemBrain 桌面宠物 — M1 客户端壳 (pywebview)

- 自动拉起后端（未运行时子进程启动 web_app.py）
- 无边框置顶小窗，加载现有聊天页面（复用 /ws/chat 全部能力：记忆/情感/工具/L3）
- 系统托盘：显示 / 隐藏 / 打开后台管理 / 退出（退出时关闭后端）

用法:
    python desktop_pet.py             # 启动桌面宠物
    python desktop_pet.py --backend-only   # 只启动后端（调试用）

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

def main():
    import webview

    # 窗口配置（M1：无边框置顶，可拖动；大小自定，后续可换 Live2D）
    window = webview.create_window(
        "MemBrain 桌面宠物",
        HOST_URL + "/",
        width=420,
        height=640,
        resizable=True,
        frameless=True,
        easy_drag=True,  # pywebview 无边框窗口整窗可拖
        on_top=True,
        confirm_close=False,
        background_color="#2b2f36",
    )

    def on_closing():
        # 托盘模式：允许关闭窗口=隐藏，除非用户选"退出"
        # 这里 window.close() 由托盘菜单控制; pywebview 关闭按钮在此无边框窗口默认隐藏
        pass

    window.events.closing += on_closing

    def run_tray():
        """托盘线程：显示/隐藏/后台/退出"""
        import pystray
        from PIL import Image, ImageDraw

        # 生成一个简单圆形托盘图标（16x16）
        icon_img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(icon_img)
        d.ellipse((4, 4, 60, 60), fill="#07c160")
        d.ellipse((22, 18, 42, 38), fill="#ffffff")
        d.arc((14, 22, 34, 54), 180, 360, fill="#ffffff", width=3)

        def show_action(icon, item):
            log("显示窗口")
            try:
                window.show()
            except Exception as e:
                log(f"show failed: {e}")

        def hide_action(icon, item):
            log("隐藏窗口")
            try:
                window.hide()
            except Exception as e:
                log(f"hide failed: {e}")

        def admin_action(icon, item):
            webbrowser.open(HOST_URL + "/admin")

        def chat_action(icon, item):
            webbrowser.open(HOST_URL + "/")

        def quit_action(icon, item):
            log("托盘退出")
            try:
                window.destroy()
            except Exception:
                pass
            stop_backend()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("打开聊天", chat_action),
            pystray.MenuItem("打开后台管理", admin_action),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("显示", show_action),
            pystray.MenuItem("隐藏", hide_action),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_action),
        )
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
