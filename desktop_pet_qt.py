#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemBrain 桌面宠物 — Qt 版 (PySide6 + QWebEngineView)

用 Qt 实现真正的"桌面宠物"透明悬浮：QWidget 无边框 + 置顶 + 背景全透明，
WebEngine 加载 Live2D 立绘页。相比 pywebview，Qt 的 WA_TranslucentBackground
是成熟稳定的原生透明方案（pywebview 的 WebView2 透明是官方标记的 hack，易白底）。

能力：
- 浮动模式 (--pet / 默认)：透明小窗只显示角色立绘，可拖动满屏，只有角色浮在桌面
- 大窗模式 (--window)：不透明大窗加载完整聊天页 (chat.html 全部功能)，可全屏
- 双窗口模式 (--twin)：透明模型窗 + 独立聊天窗
- 系统托盘：显示/隐藏/模式切换/打开后台管理/退出

后端复用：模型页 /live2d?petmode=1、聊天页 /、后台 /admin（与 web 端共享同一 FastAPI + WS）。

用法:
    python desktop_pet_qt.py              # 浮动悬浮（透明，只有角色）
    python desktop_pet_qt.py --window     # 大窗聊天
    python desktop_pet_qt.py --twin       # 双窗口（透明模型 + 聊天）
    python desktop_pet_qt.py --backend-only

依赖: pip install PySide6
"""
import os
import sys
import time
import subprocess
import threading

import requests

# 项目根目录
ROOT = os.path.dirname(os.path.abspath(__file__))
HOST_URL = "http://127.0.0.1:8000"

PET_MODE = os.getenv("PET_MODE", "floating").strip().lower()

_backend_proc: subprocess.Popen | None = None


# ===================== 后端管理（复用 pywebview 版逻辑） =====================

def backend_alive(timeout: float = 1.0) -> bool:
    try:
        return requests.get(HOST_URL + "/health", timeout=timeout).status_code == 200
    except Exception:
        return False


def wait_backend(seconds: float = 30.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if backend_alive(0.5):
            return True
        time.sleep(0.5)
    return False


def start_backend() -> bool:
    global _backend_proc
    if backend_alive():
        return True
    if _backend_proc is not None and _backend_proc.poll() is None:
        return False
    exe = sys.executable
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        env = dict(os.environ)
        env["MEMBRAIN_NO_BROWSER"] = "1"
        _backend_proc = subprocess.Popen(
            [exe, os.path.join(ROOT, "web_app.py")],
            cwd=ROOT, env=env, creationflags=flags,
            stdout=subprocess.DEVNULL if sys.platform == "win32" else None,
            stderr=subprocess.DEVNULL if sys.platform == "win32" else None,
        )
    except Exception as e:
        log(f"启动后端失败: {e}")
        return False
    return wait_backend()


def stop_backend():
    global _backend_proc
    if _backend_proc is not None and _backend_proc.poll() is None:
        try:
            _backend_proc.terminate()
            _backend_proc.wait(timeout=5)
        except Exception:
            try: _backend_proc.kill()
            except Exception: pass
    _backend_proc = None


def log(msg: str):
    print(f"[PetQt] {msg}", flush=True)


# ===================== Qt 窗口 =====================

def _screen_size():
    from PySide6.QtWidgets import QApplication
    if not QApplication.instance():
        # 不创建 app，使用主屏幕
        from PySide6.QtGui import QGuiApplication
        pass
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    if screen:
        g = screen.availableGeometry()
        return g.width(), g.height()
    return 1280, 800


def build_windows():
    """创建窗口。返回 (主窗口, 可选副窗口)。"""
    from PySide6.QtCore import Qt, QUrl, QEvent, QObject
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QSystemTrayIcon, QMenu
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage

    sw, sh = _screen_size()
    mode = PET_MODE

    # 可拖动的无边框悬浮窗：用 Windows 原生 WM_NCHITTEST + HTCAPTION 实现拖拽。
    # 这是最可靠的方式——不依赖 Qt 的鼠标事件传递（Qt WebEngine 在 Windows 上会
    # 把左键原生消息先给 Chromium，Qt 的 mousePress/全局过滤器收不到真实左键拖拽），
    # 而是让 Windows 窗口管理器直接把整窗当"标题栏"来拖动，绝对可靠。
    try:
        from ctypes import c_int
        WM_NCHITTEST = 0x0084
        HTCAPTION = 0x0002
        HTCLIENT = 0x0001
    except Exception:
        WM_NCHITTEST = HTCLIENT = HTCAPTION = 0

    class DraggableWindow(QWidget):
        """透明悬浮窗：鼠标事件正常进 WebView（HTCLIENT）。
        窗口拖动改由页面手势识别后经 petHost.dragBy 驱动（见 attach_pethost），
        这样点击能触发页面交互、悬停能显示手型，而不是被 HTCAPTION 整窗吞掉。
        """
        def nativeEvent(self, eventType, message):
            try:
                if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
                    from ctypes import wintypes
                    msg = wintypes.MSG.from_address(int(message))
                    if msg.message == WM_NCHITTEST:
                        # 返回 HTCLIENT：让所有鼠标事件进入页面（点击/悬停/手型正常）。
                        # 拖动由页面手势识别 + petHost.dragBy 完成。
                        return (True, HTCLIENT)
            except Exception:
                pass
            return super().nativeEvent(eventType, message)

    # ---- QWebChannel 桥：让页面里的滚轮缩放直接调整宿主窗口大小 ----
    # 方案A：窗口是唯一基准。滚轮缩放 → JS 通过 petHost.resizeWindow(w,h) 改窗口尺寸，
    # 画布 CSS 是 100vw/100vh 会自动跟随窗口拉伸，于是模型与窗口按同比例缩放，
    # 窗口永远贴合模型，不再出现"模型缩小、窗口不变"的多余透明区。
    def attach_pethost(view, win):
        from PySide6.QtCore import QObject, Slot, QTimer
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QCursor
        from PySide6.QtWebChannel import QWebChannel

        class PetHost(QObject):
            def __init__(self, win):
                super().__init__()
                self.win = win
            @Slot(int, int)
            def resizeWindow(self, w, h):
                # 钳制到合理范围，避免窗口过小/过大
                w = max(90, min(_screen_size()[0], int(w)))
                h = max(140, min(_screen_size()[1], int(h)))
                self.win.resize(w, h)
                log(f"resizeWindow -> {w}x{h}")
            @Slot(float, float)
            def setCursor(self, dx, dy):
                # 页面已能收到鼠标时由页面自己驱动；此槽为兜底
                pass
            @Slot(int, int)
            def dragBy(self, dx, dy):
                # 页面手势识别出"拖动"后，按位移增量移动窗口（HTCLIENT 下由页面驱动拖动）
                # 边界：限制窗口中心保持在屏幕内，保证任何时候窗口都有大半可见，不会拖丢。
                try:
                    g = self.win.geometry()
                    sw, sh = _screen_size()
                    # 窗口中心在 [0, sw]/[0, sh] 内 → 窗口必然有大半在屏内
                    cx = g.x() + g.width() / 2 + int(dx)
                    cy = g.y() + g.height() / 2 + int(dy)
                    cx = max(0, min(sw, int(cx)))
                    cy = max(0, min(sh, int(cy)))
                    nx = cx - g.width() / 2
                    ny = cy - g.height() / 2
                    self.win.move(int(nx), int(ny))
                except Exception:
                    pass
            @Slot(float, float)
            def cropToChar(self, fx, fy):
                # 把窗口裁剪到角色实际渲染尺寸（fx,fy = 角色宽/高占窗口比例），
                # 使窗口=角色轮廓、无外部多余一圈；并让角色底部贴任务栏、保持相对右下角。
                try:
                    cg = self.win.geometry()
                    sw, sh = _screen_size()
                    new_w = max(60, int(cg.width() * fx))
                    new_h = max(80, int(cg.height() * fy))
                    # 保持右下角偏移：右距维持当前，底部贴任务栏（工作区底-新高）
                    right_gap = sw - (cg.x() + cg.width())
                    new_x = max(0, sw - new_w - right_gap)
                    new_y = max(0, sh - new_h)
                    log(f"cropToChar fx={fx:.3f} fy={fy:.3f} -> {new_w}x{new_h} @ ({new_x},{new_y})")
                    self.win.setGeometry(new_x, new_y, new_w, new_h)
                except Exception as e:
                    log(f"cropToChar error: {e}")

        host = PetHost(win)
        channel = QWebChannel(view.page())
        channel.registerObject("petHost", host)
        view.page().setWebChannel(channel)

        # 兜底视线跟随：Qt 侧每 40ms 轮询全局光标相对本窗位置并推给页面。
        # 透明/不接受的焦点窗里 Chromium 可能不把 mousemove 交给页面，
        # 从 Qt 侧主动上报光标位置能保证眼睛一定跟随（问题1）。
        def poll_cursor():
            try:
                if not win.isVisible():
                    return
                cg = win.geometry()
                pos = QCursor.pos()  # 全局坐标
                # 相对窗口客户区中心，归一化到 [-1,1]
                w = max(1, cg.width()); h = max(1, cg.height())
                dx = max(-1.0, min(1.0, (pos.x() - (cg.x() + w / 2)) / (w / 2)))
                dy = max(-1.0, min(1.0, (pos.y() - (cg.y() + h / 2)) / (h / 2)))
                js = ("window.__petCursor && window.__petCursor(%f,%f);" % (dx, dy))
                view.page().runJavaScript(js)
            except Exception:
                pass

        # 窗口显示后再启动光标轮询
        def start_poll():
            poll_timer = QTimer(win)
            poll_timer.timeout.connect(poll_cursor)
            poll_timer.start(40)
            win._petCursorTimer = poll_timer

        win._start_poll = start_poll
        win._poll_cursor = poll_cursor

        def inject(_ok=None):
            js = """
            (function(){
              try{
                if(!window.qt || !qt.webChannelTransport){ return; }
                if(window.Live2DHost && window.__petCursor){ return; } // 已注入
                var s = document.createElement('script');
                s.src = 'qrc:///qtwebchannel/qwebchannel.js';
                s.onload = function(){
                  try{
                    new QWebChannel(qt.webChannelTransport, function(ch){
                      window.Live2DHost = ch.objects.petHost;
                      // 页面侧的视线入口：Qt 每帧这里拿到归一化光标，驱动眼睛
                      window.__petCursor = function(dx, dy){
                        if(window.Live2D && window.Live2D._setCursor){
                          window.Live2D._setCursor(dx, dy);
                        }
                      };
                      window.__setCursorBridge = true;
                      var evt = new Event('petHostReady');
                      window.dispatchEvent(evt);
                    });
                  }catch(e){}
                };
                document.head.appendChild(s);
              }catch(e){}
            })();
            """
            try:
                view.page().runJavaScript(js)
            except Exception as e:
                log(f"petHost 注入失败: {e}")

        view.loadFinished.connect(inject)
        win._petChannel = channel
        win._petHost = host
        return host

    # 透明的页面容器：WA_TranslucentBackground 让 QWidget 背景全透明
    def make_view(url, w, h, transparent, title):
        if transparent:
            win = DraggableWindow()
        else:
            win = QWidget()
        win.setWindowTitle(title)
        if transparent:
            win.setWindowFlags(
                Qt.FramelessWindowHint |
                Qt.WindowStaysOnTopHint |
                Qt.WindowDoesNotAcceptFocus
            )
            win.setAttribute(Qt.WA_TranslucentBackground, True)
            win.setAttribute(Qt.WA_ShowWithoutActivating, True)
        else:
            win.setWindowFlags(
                Qt.WindowStaysOnTopHint | Qt.Window
            )
        win.resize(w, h)

        lay = QVBoxLayout(win)
        lay.setContentsMargins(0, 0, 0, 0)
        view = QWebEngineView(win)
        # 透明背景必须同时设置页面背景透明
        page = view.page()
        page.setBackgroundColor(Qt.transparent if transparent else Qt.white)
        lay.addWidget(view)

        win._view = view
        # 透明窗（浮动/双子模型窗）挂接 QWebChannel 桥
        if transparent:
            attach_pethost(view, win)

        # 拦截 target="_blank" 链接：Qt WebEngine 默认阻止新窗口，点"后台管理 →"
        # 之类的新标签会无反应。改为在当前窗口内导航打开。
        def inline_links(_ok=None):
            js = r"""
            (function(){
              if(window.__inlineLinks) return;
              window.__inlineLinks = true;
              document.addEventListener('click', function(e){
                var t = e.target;
                var a = null;
                while(t && t !== document){
                  if(t.tagName === 'A'){ a = t; break; }
                  t = t.parentElement;
                }
                if(a && (a.target === '_blank' || a.getAttribute('target') === '_blank')){
                  e.preventDefault(); e.stopPropagation();
                  try{ window.location.href = a.href; }catch(err){}
                }
              }, true);
            })();
            """
            try:
                view.page().runJavaScript(js)
            except Exception:
                pass
        view.loadFinished.connect(inline_links)

        view.page().load(QUrl(url))
        return win

    main_win = None
    sub_win = None

    # 双窗口架构（所有模式统一）：
    #   - pet_win：透明悬浮宠物窗，只显示 Live2D 模型（视觉/动作/表情/口型）
    #   - ui_win ：独立普通窗口，承载聊天 / 后台管理（可交互、可拖动、可调大小）
    # 这样透明悬浮窗与"完整可交互应用"不再冲突（透明窗不接受焦点、无法承载复杂交互）。
    # 宠物窗默认大小 291x486（原 448x747 缩至 65%；模型随窗口等比缩小，保持 aspect≈0.60）。
    # 默认位置：相对工作区右下角的像素偏移定位——大小固定，位置按偏移在当前屏幕摆放。
    #   RIGHT_OFF = 距工作区右缘 31px（取自已手动调整好的位置）
    #   BOTTOM_OFF = 距工作区底 0px（底部紧贴任务栏上沿，脚底位置不变）
    # 这样任意分辨率下都贴右 31px、贴底，模型不因分辨率改变而移位/出屏。
    PET_W, PET_H = 291, 486
    PET_RIGHT_OFF, PET_BOTTOM_OFF = 31, 0
    pet_x = max(0, sw - PET_W - PET_RIGHT_OFF)
    pet_y = max(0, sh - PET_H - PET_BOTTOM_OFF)
    main_win = make_view(HOST_URL + "/live2d?petmode=1", PET_W, PET_H, True, "MemBrain 宠物")
    main_win.move(pet_x, pet_y)
    uiw = max(760, int(sw * 0.34))
    uih = max(560, int(sh * 0.72))
    sub_win = make_view(HOST_URL + "/", uiw, uih, False, "MemBrain 对话")
    sub_win.move(sw - uiw - 30, int(sh * 0.08))
    # 标记哪个是腹 明宠物窗、哪个是交互窗，供托盘逻辑区分
    main_win._is_pet = True
    sub_win._is_ui = True
    return main_win, sub_win


def main():
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
    from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction

    global PET_MODE
    if "--window" in sys.argv:
        PET_MODE = "window"
    if "--twin" in sys.argv:
        PET_MODE = "twin"
    if "--pet" in sys.argv:
        PET_MODE = "floating"

    # 必须先有 QApplication 再创建 WebEngine 窗口（WebEngine 需要 app 实例）
    app = QApplication(sys.argv)
    app.setApplicationName("MemBrainPet")

    main_win, sub_win = build_windows()

    # ---- 托盘 ----
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setBrush(QColor("#07c160"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(22, 16, 22, 22)
    p.end()
    tray = QSystemTrayIcon(QIcon(pix))
    tray.setToolTip("MemBrain 桌面宠物")
    menu = QMenu()

    def act(name, fn):
        a = QAction(name, menu)
        a.triggered.connect(fn)
        menu.addAction(a)
        return a

    def show_all():
        if main_win: main_win.show()
        if sub_win: sub_win.show()

    def hide_all():
        if main_win: main_win.hide()
        if sub_win: sub_win.hide()

    if PET_MODE == "twin":
        act("对话窗→聊天", lambda: sub_win._view.load(QUrl(HOST_URL + "/")))
        act("对话窗→后台管理", lambda: sub_win._view.load(QUrl(HOST_URL + "/admin")))
    elif getattr(main_win, "_is_pet", False) and getattr(sub_win, "_is_ui", False):
        # 双窗口：交互窗切换聊天/后台，宠物窗只显示模型
        act("切换到聊天", lambda: sub_win._view.load(QUrl(HOST_URL + "/")))
        act("切换到后台管理", lambda: sub_win._view.load(QUrl(HOST_URL + "/admin")))
    else:
        # 大窗模式：后台管理在主窗口内打开
        act("后台管理", lambda: main_win._view.load(QUrl(HOST_URL + "/admin")))
        act("回到聊天", lambda: main_win._view.load(QUrl(HOST_URL + "/")))
    menu.addSeparator()
    act("显示", show_all)
    act("隐藏", hide_all)
    menu.addSeparator()
    act("退出", lambda: (stop_backend(), app.quit()))
    tray.setContextMenu(menu)
    tray.show()

    # 后端就绪
    if not backend_alive():
        if not start_backend():
            log("后端启动失败")
            sys.exit(1)

    main_win.show()
    if sub_win: sub_win.show()

    # 宠物窗（透明悬浮）启动光标轮询定时器（Qt→页面 兜底视线跟随）
    if getattr(main_win, "_is_pet", False) and getattr(main_win, "_start_poll", None):
        main_win._start_poll()

    log(f"模式 {PET_MODE} 启动完成")
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--backend-only" in sys.argv:
        os.chdir(ROOT)
        from web_app import app
        import uvicorn
        from core.config import HOST, PORT
        uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
    else:
        main()
