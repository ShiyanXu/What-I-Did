"""系统托盘图标（纯 ctypes 调 Shell_NotifyIcon，不引入 pystray / pywin32）。

为什么自己写：
- 项目铁律是运行时不新增 pip 依赖，界面用 tkinter，托盘就自己调 Win32 三件套。

为什么托盘窗口要跑在**独立线程**里（踩过的坑）：
- 把隐藏窗口建在 tk 主线程、让 tk 的 mainloop 顺便泵它的消息，看起来最省事，
  但 tkinter 调 Tcl_DoOneEvent 时会释放 GIL 并换掉线程状态，此时 DispatchMessage
  分发到我们的 ctypes 回调，ctypes 再想 PyGILState_Ensure 拿回 GIL 就撞上
  「PyEval_RestoreThread: the current Python thread state is NULL」直接崩进程。
- 所以托盘线程自己 GetMessage/DispatchMessage，回调只往 queue 里塞事件；
  tk 主线程用 after() 轮询 queue 再派发，谁都不碰别人的线程。
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import wintypes
from pathlib import Path

# ---------------------------------------------------------------- Win32 常量
WM_NULL = 0x0000
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 20                    # 自定义回调消息

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_GRAYED = 0x0001

TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON = 49
SM_CYSMICON = 50
NIIF_INFO = 0x00000001

_is_nt = os.name == "nt"

if _is_nt:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        # shell32 用 cbSize 判断结构版本，5.0 的结构就够（不需要 NIM_SETVERSION）
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
        ]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                                   wintypes.LPCWSTR]
    user32.TrackPopupMenuEx.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                        ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
    user32.TrackPopupMenuEx.restype = ctypes.c_int
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT

    shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                       ctypes.POINTER(wintypes.HICON),
                                       ctypes.POINTER(wintypes.HICON), wintypes.UINT]
    shell32.ExtractIconExW.restype = wintypes.UINT
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                          ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class Tray:
    """托盘图标 + 右键菜单。

    set_menu([(文本, 命令号, 是否可用)])   —— 文本传 "-" 表示分隔线（主线程调用）
    on_command(id) / on_activate()        —— 一律在主线程被调用
    poll()                                —— 主线程周期性调用，用来派发事件
    """

    def __init__(self, root, icon_path=None, tip="What-IDid",
                 on_command=None, on_activate=None):
        self.root = root
        self.icon_path = Path(icon_path) if icon_path else None
        self.tip = (tip or "")[:127]
        self.on_command = on_command or (lambda cid: None)
        self.on_activate = on_activate or (lambda: None)

        self._items: list[tuple] = []
        self._events: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = None
        self._tid = 0
        self._hwnd = None
        self._hicon = None
        self._proc = None
        self._taskbar_msg = 0
        self._installed = False
        self.last_error = ""

    # ------------------------------------------------------------ 对外
    @property
    def installed(self) -> bool:
        return self._installed

    def install(self) -> bool:
        if self._installed:
            return True
        if not _is_nt:
            self.last_error = "非 Windows"
            return False
        self._thread = threading.Thread(target=self._thread_main, name="wid-tray",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(4.0)
        return self._installed

    def remove(self) -> None:
        if self._installed:
            try:
                self._notify(NIM_DELETE)
            except Exception:
                pass
            self._installed = False
        tid = self._tid
        if tid:
            try:
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def set_menu(self, items) -> None:
        self._items = list(items or [])

    def set_tip(self, text: str) -> None:
        text = (text or "")[:127]
        if text == self.tip or not self._installed:
            return
        self.tip = text
        try:
            self._notify(NIM_MODIFY)
        except Exception:
            pass

    def notify(self, title: str, text: str) -> None:
        """气泡提示（Win10 上是右下角 toast）。"""
        if not self._installed:
            return
        try:
            nid = self._nid(NIF_INFO)
            nid.szInfoTitle = (title or "")[:63]
            nid.szInfo = (text or "")[:255]
            nid.dwInfoFlags = NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    def poll(self) -> None:
        """主线程调用：把托盘线程收到的事件派发出去。"""
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "cmd":
                    self.on_command(payload)
                elif kind == "activate":
                    self.on_activate()
            except Exception:
                pass

    # ------------------------------------------------------------ 托盘线程
    def _thread_main(self) -> None:
        try:
            self._tid = int(kernel32.GetCurrentThreadId())
            self._create_window()
            self._hicon = self._load_icon()
            if not self._notify(NIM_ADD):
                self.last_error = "Shell_NotifyIcon 注册失败"
                self._ready.set()
                return
            self._installed = True
            self._remember_hwnd()
            self._ready.set()

            msg = wintypes.MSG()
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:                     # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._ready.set()
            try:
                if self._installed:
                    self._notify(NIM_DELETE)
            except Exception:
                pass
            self._installed = False
            try:
                if self._hwnd:
                    user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
            try:
                if self._hicon:
                    user32.DestroyIcon(self._hicon)
            except Exception:
                pass

    def _create_window(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        name = f"WhatIDidTray{id(self):x}"
        self._proc = WNDPROC(self._wndproc)          # 必须留引用，被 GC 掉会崩
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):                 # 1410 = 类已存在
                raise OSError(f"RegisterClassW 失败 code={err}")

        # 不传 WS_VISIBLE：建出来就是隐藏的，不会在任务栏留按钮
        self._hwnd = user32.CreateWindowExW(0, name, "WhatIDidTray", 0,
                                            0, 0, 0, 0, None, None, hinst, None)
        if not self._hwnd:
            raise OSError(f"CreateWindowExW 失败 code={ctypes.get_last_error()}")
        self._taskbar_msg = user32.RegisterWindowMessageW("TaskbarCreated")

    def _load_icon(self):
        cx = user32.GetSystemMetrics(SM_CXSMICON) or 16
        cy = user32.GetSystemMetrics(SM_CYSMICON) or 16
        if self.icon_path and self.icon_path.exists():
            h = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON, cx, cy,
                                  LR_LOADFROMFILE)
            if h:
                return h
        # 退路：直接从自己的 exe 里抠（打包时 ico 已作为资源嵌入）
        big = wintypes.HICON()
        small = wintypes.HICON()
        try:
            import sys

            if shell32.ExtractIconExW(sys.executable, 0,
                                      ctypes.byref(big), ctypes.byref(small), 1):
                if big:
                    user32.DestroyIcon(big)
                if small:
                    return small
        except Exception:
            pass
        return None

    def _nid(self, flags: int) -> "NOTIFYICONDATAW":
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self.tip
        return nid

    def _notify(self, action: int) -> bool:
        if not self._hwnd:
            return False
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        return bool(shell32.Shell_NotifyIconW(action, ctypes.byref(nid)))

    def _remember_hwnd(self) -> None:
        """把窗口句柄写到 state/tray.hwnd：既是排障线索，也让自动化测试能投递消息。"""
        try:
            import paths

            p = paths.STATE / "tray.hwnd"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(int(self._hwnd)), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------ 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = int(lparam) & 0xFFFF
                if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._popup()                    # 本线程弹菜单（需要本线程的消息循环）
                elif ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._events.put(("activate", None))
                return 0
            if msg == WM_CLOSE:
                return 0                             # 这个隐藏窗口不该被关掉
            if self._taskbar_msg and msg == self._taskbar_msg:
                self._re_add()                       # 资源管理器重启后图标会被清掉
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _re_add(self) -> None:
        try:
            self._notify(NIM_ADD)
            self._notify(NIM_MODIFY)
        except Exception:
            pass

    def _popup(self) -> None:
        """弹出右键菜单。TrackPopupMenuEx 阻塞到菜单关闭，然后只投递命令号。"""
        items = list(self._items)
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        chosen = 0
        try:
            for label, cid, enabled in items:
                if label in (None, "-", ""):
                    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                    continue
                flags = MF_STRING if enabled else MF_STRING | MF_GRAYED
                user32.AppendMenuW(hmenu, flags, int(cid), label)
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # 先抢前台，否则点菜单外面菜单不消失（托盘菜单的经典坑）
            user32.SetForegroundWindow(self._hwnd)
            chosen = user32.TrackPopupMenuEx(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, self._hwnd, None)
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(hmenu)
        if chosen:
            self._events.put(("cmd", int(chosen)))


def available() -> bool:
    return _is_nt
