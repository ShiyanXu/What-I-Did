"""单实例锁：Windows 命名互斥量，进程崩溃自动释放。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183

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
    """

    def __init__(self, name: str):
        self.name = name if name.startswith(("Local\\", "Global\\")) else "Local\\" + name
        self._handle: int | None = None
        self.acquired = False

    def acquire(self) -> bool:
        handle = _kernel32.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        self._handle = handle
        self.acquired = bool(handle) and err != ERROR_ALREADY_EXISTS
        return self.acquired

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
