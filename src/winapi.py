"""Windows 原生信息：DPI、前台窗口、进程名、空闲、锁屏、会话。

全部用 ctypes 实现，不依赖 pywin32。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_CMONITORS = 80

kernel32.GetTickCount.restype = wintypes.DWORD
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
try:
    # DPI_AWARENESS_CONTEXT 是句柄，默认 c_int 在 64 位下会被截断，必须声明成指针宽度。
    # Win10 1607 之前没有这两个函数，取不到就让 dpi_awareness() 走"无法判断"。
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
except AttributeError:
    pass
user32.SetProcessDPIAware.restype = wintypes.BOOL

try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    # 显式声明 restype：默认 int 在 64 位下拿不到 HRESULT 的完整语义，
    # 而 windll 不会因为 HRESULT 是失败值而抛异常，必须自己看返回值。
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness.restype = ctypes.c_long
except OSError:                                   # Win7 没有 shcore
    shcore = None

DPI_NAMES = {0: "未感知", 1: "系统级", 2: "逐显示器"}


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_dpi_done = False
_exe_cache: dict[int, str] = {}


def dpi_awareness() -> tuple[int, str]:
    """当前进程实际的 DPI 感知级别：(0 未感知 / 1 系统级 / 2 逐显示器, 可读名)。

    截图裁窗口靠的是「窗口坐标」和「屏幕像素」在同一个坐标系里。进程一旦停在未感知
    级别，多屏混合缩放下 GetWindowRect 返回的是被虚拟化过的逻辑坐标，裁出来就会
    差一个缩放倍数——所以这个值要能被 doctor 看见，而不是"设过了就当成功"。
    """
    try:
        value = int(user32.GetAwarenessFromDpiAwarenessContext(
            user32.GetThreadDpiAwarenessContext()))
    except Exception:                             # Win10 1607 之前没有这组 API
        return -1, "无法判断"
    return value, DPI_NAMES.get(value, f"未知({value})")


def set_dpi_aware() -> str:
    """必须在任何截图/取窗口坐标之前调用一次。返回最终生效的级别名。

    级别整个进程只能定一次：重复设置会返回 E_ACCESSDENIED（0x80070005），
    这是正常的，不代表失败。真正要防的是"第一次就没设上"——此时宁可显式报警，
    也不要让截图带着错误坐标继续跑。
    """
    global _dpi_done
    if _dpi_done:
        return dpi_awareness()[1]
    _dpi_done = True
    hr = -1
    if shcore is not None:
        try:
            hr = int(shcore.SetProcessDpiAwareness(2))   # PER_MONITOR_AWARE
        except Exception:
            hr = -1
    if hr != 0 and dpi_awareness()[0] == 0:
        # 还没被别人定死，退一步至少拿到系统级感知，别停在未感知
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    return dpi_awareness()[1]


def _exe_name(pid: int) -> str:
    if pid in _exe_cache:
        return _exe_cache[pid]
    name = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(32768)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h)
    if len(_exe_cache) > 400:
        _exe_cache.clear()
    _exe_cache[pid] = name
    return name


def foreground_info() -> dict:
    """当前前台窗口。取不到时返回空结构，绝不抛异常。"""
    set_dpi_aware()
    info = {"hwnd": 0, "title": "", "app": "", "pid": 0, "rect": (0, 0, 0, 0)}
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return info
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 2)
            user32.GetWindowTextW(hwnd, buf, length + 2)
            title = buf.value.strip()
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = window_rect(hwnd)
        info.update(hwnd=int(hwnd), title=title, pid=int(pid.value), app=_exe_name(int(pid.value)), rect=rect)
    except Exception:
        pass
    return info


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    return (0, 0, 0, 0)


def idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = int(kernel32.GetTickCount()) & 0xFFFFFFFF
    return max(0.0, float((tick - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0)


def is_locked() -> bool:
    """仅确认输入桌面为 Default 才视为未锁；无法判断时按锁屏保护。"""
    try:
        h = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not h:
            return True
        try:
            need = wintypes.DWORD(0)
            user32.GetUserObjectInformationW(h, UOI_NAME, None, 0, ctypes.byref(need))
            size = max(int(need.value), 64)
            buf = ctypes.create_unicode_buffer(size)
            if user32.GetUserObjectInformationW(h, UOI_NAME, buf, size * ctypes.sizeof(ctypes.c_wchar), ctypes.byref(need)):
                return buf.value.strip().lower() != "default"
            return True
        finally:
            user32.CloseDesktop(h)
    except Exception:
        return True


def screen_metrics() -> dict:
    set_dpi_aware()
    return {
        "monitors": int(user32.GetSystemMetrics(SM_CMONITORS)),
        "primary": (
            int(user32.GetSystemMetrics(SM_CXSCREEN)),
            int(user32.GetSystemMetrics(SM_CYSCREEN)),
        ),
        "virtual": (
            int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
        ),
    }


def session_id() -> int:
    sid = wintypes.DWORD(0)
    try:
        kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    except Exception:
        return -1
    return int(sid.value)


def console_session_id() -> int:
    """当前活动控制台会话（用于识别"计划任务跑进了 Session 0"这种坑）。"""
    try:
        kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
        return int(kernel32.WTSGetActiveConsoleSessionId())
    except Exception:
        return -1
