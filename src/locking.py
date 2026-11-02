"""单实例锁：Windows 命名互斥量，进程崩溃自动释放。"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183

# 等待模式下的轮询间隔：对方多为「清理 1 秒」这类短任务，200ms 足够细
POLL_SEC = 0.2

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class SingleInstance:
    """用法::

        with SingleInstance("WhatIDid.Daemon") as lock:
            if not lock.acquired:
                return 1
            ...

    `wait` 秒内反复尝试，对方释放就接手；0 表示立刻放弃（默认，保持旧行为）。

    为什么需要等：清理与出报共用 `WhatIDid.Report`，两者在 daemon 的同一个
    30 秒轮次里先后启动，子进程启动约需 1 秒，正好撞上清理持锁的那 1 秒。
    只判一次「有没有人持有」会把毫秒级错位当成「有人正在出报」，于是整晚的
    终版就这么丢掉（2026-11-01 的 11-01 日报即此）。
    """

    def __init__(self, name: str, wait: float = 0.0):
        self.name = name if name.startswith(("Local\\", "Global\\")) else "Local\\" + name
        self.wait = max(0.0, float(wait))
        self._handle: int | None = None
        self.acquired = False

    def acquire(self) -> bool:
        deadline = time.monotonic() + self.wait
        while True:
            handle = _kernel32.CreateMutexW(None, False, self.name)
            err = ctypes.get_last_error()
            if handle and err != ERROR_ALREADY_EXISTS:
                self._handle = handle
                self.acquired = True
                return True
            if handle:                       # 别人拿着，先放开句柄免得漏
                _kernel32.CloseHandle(handle)
            self._handle = None
            self.acquired = False
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(POLL_SEC, left))

    def release(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
            self.acquired = False

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
