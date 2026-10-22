"""开发用：DPI / 多屏 采集坐标一致性实测。

    WID_HOME=.tmp_wid_pack "C:/.../Python310/python.exe" .build/verify_dpi.py

做法：自己开一个已知尺寸的窗口当靶子（不截用户正在看的画面），
按采集器真实路径抓一次，再比对「抓到的像素尺寸」和「窗口矩形」。
两者只要差一个缩放倍数，就说明进程的 DPI 感知级别不对——这正是多屏混合
缩放时窗口被裁错、OCR 只识别到半个界面的原因。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
from PIL import ImageGrab  # noqa: E402

import capture  # noqa: E402
import config as cfgmod  # noqa: E402
import paths  # noqa: E402
import vision  # noqa: E402
import winapi  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


user32 = ctypes.windll.user32
shcore = ctypes.windll.shcore

MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int,
                                    ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
shcore.GetDpiForMonitor.restype = ctypes.c_long


def monitor_list() -> list[dict]:
    out: list[dict] = []

    def cb(hmon, _hdc, _lprc, _lp):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dx, dy = wintypes.UINT(0), wintypes.UINT(0)
            hr = shcore.GetDpiForMonitor(wintypes.HMONITOR(hmon), 0,
                                         ctypes.byref(dx), ctypes.byref(dy))
            out.append({
                "device": mi.szDevice,
                "rect": (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom),
                "primary": bool(mi.dwFlags & 1),
                "dpi": int(dx.value) if hr == 0 else 0,
            })
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


def awareness() -> tuple[int, str]:
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
        val = int(user32.GetAwarenessFromDpiAwarenessContext(ctx))
        return val, {0: "UNAWARE", 1: "SYSTEM_AWARE", 2: "PER_MONITOR_AWARE"}.get(val, f"?{val}")
    except Exception as exc:                        # 老系统没有这个 API
        return -1, f"不可用({exc})"


def main() -> int:
    print(f"数据目录：{paths.ROOT}")

    print("\n[1] 进程 DPI 感知")
    before = awareness()
    winapi.set_dpi_aware()
    after = awareness()
    print(f"       调用前 {before[1]} → 调用后 {after[1]}")
    check("采集路径会切到逐显示器感知", after[0] == 2, after[1])

    print("\n[2] 显示器与缩放")
    mons = monitor_list()
    metrics = winapi.screen_metrics()
    for i, m in enumerate(mons):
        w = m["rect"][2] - m["rect"][0]
        h = m["rect"][3] - m["rect"][1]
        print(f"       [{i}] {m['device']} 主屏={m['primary']} {w}x{h} dpi={m['dpi']} "
              f"缩放={m['dpi'] / 96:.2f}")
    print(f"       GetSystemMetrics: {metrics}")
    dpi_set = {m["dpi"] for m in mons}
    check("至少枚举到 1 台显示器", len(mons) >= 1, f"{len(mons)} 台")
    check("枚举数量与系统计数一致", metrics["monitors"] == len(mons),
          f"{len(mons)} vs {metrics['monitors']}")
    multi = len(mons) > 1
    mixed = len(dpi_set) > 1
    print(f"       多屏={multi}　混合缩放={mixed}　DPI 集合={sorted(dpi_set)}")

    print("\n[3] 采集坐标一致性（自建靶窗口，不截用户画面）")
    import tkinter as tk

    cfg = cfgmod.load(reload=True)

    def probe_on(rect_x: int, rect_y: int, tag: str, expect_content: bool = True) -> None:
        win = tk.Tk()
        win.title(f"What-IDid DPI 靶窗口 {tag}")
        win.geometry(f"640x420+{rect_x}+{rect_y}")
        win.configure(bg="#123456")
        tk.Label(win, text="DPI PROBE", bg="#123456", fg="#ffffff",
                 font=("Consolas", 28, "bold")).pack(expand=True)
        win.update()
        win.lift()
        try:
            win.focus_force()
        except Exception:
            pass
        win.update()
        # 等窗口真正合成出来，否则副屏上可能抓到还没画内容的黑帧
        time.sleep(0.5)
        win.update()
        hwnd = int(win.winfo_id())
        rect = winapi.window_rect(hwnd)
        print(f"       {tag}: rect={rect} 尺寸={rect[2] - rect[0]}x{rect[3] - rect[1]}")
        fg = {"hwnd": hwnd, "title": "DPI 靶窗口", "app": "python.exe", "pid": 0, "rect": rect}
        try:
            img, origin = capture.grab_frame(cfg, fg)
            want = (rect[2] - rect[0], rect[3] - rect[1])
            check(f"{tag}：抓到的像素尺寸 = 窗口矩形尺寸", img.size == want, f"{img.size} vs {want}")
            arr = np.asarray(img.convert("L"))
            if expect_content:
                check(f"{tag}：画面不是全黑（没被挡住）", float(arr.mean()) > 1.0,
                      f"均值 {arr.mean():.1f}")
            else:
                # 这块输出整屏没画面（RDP/虚拟显示常见），像素内容无从校验，
                # 但"尺寸对不对"才是坐标可信的判据，继续跑。
                print(f"       {tag}：该屏整屏无画面（均值 {arr.mean():.1f}），跳过内容校验")
            crop = vision.crop_region(img, rect, origin)
            check(f"{tag}：按窗口矩形再裁尺寸不变", crop is not None and crop.size == want,
                  f"{getattr(crop, 'size', None)}")
            if crop is not None:
                diff = abs(crop.size[0] - img.size[0])
                check(f"{tag}：无缩放倍数偏差", diff <= 2, f"差 {diff}px")
        except Exception as exc:
            check(f"{tag}：活动窗口截图成功", False, f"{type(exc).__name__}: {exc}")
        finally:
            win.destroy()

    primary = next((m for m in mons if m["primary"]), mons[0] if mons else None)
    secondary = next((m for m in mons if not m["primary"]), None)
    if primary:
        probe_on(primary["rect"][0] + 120, primary["rect"][1] + 120, "主屏")
    if secondary is not None:
        # 关键：第二块屏的 x 不是 0，裁切时 origin 偏移一步算错就会裁到隔壁屏
        sx1, sy1, sx2, sy2 = secondary["rect"]
        try:
            live = np.asarray(ImageGrab.grab(bbox=(sx1, sy1, sx2, sy2),
                                             all_screens=True).convert("L")).mean()
        except Exception:
            live = 0.0
        print(f"       副屏整屏均值 {live:.1f}")
        probe_on(secondary["rect"][0] + 80, secondary["rect"][1] + 80, "副屏",
                 expect_content=live > 1.0)

    print("\n[4] 全屏scope：虚拟桌面尺寸一致")
    try:
        cfg2 = cfgmod.load(reload=True)
        cfg2.set("privacy.capture_scope", "all_screens")
        shot = ImageGrab.grab(all_screens=True)
        vx, vy, vw, vh = metrics["virtual"]
        check("全屏截图尺寸 = 虚拟桌面尺寸", shot.size == (vw, vh), f"{shot.size} vs {(vw, vh)}")
        check("虚拟桌面原点可解析", isinstance(vx, int) and isinstance(vy, int), f"origin=({vx},{vy})")
        if secondary is not None:
            sx1, sy1, sx2, sy2 = secondary["rect"]
            area = np.asarray(shot.crop((sx1, sy1, sx2, sy2)).convert("L"))
            print(f"       副屏整屏灰度均值 {area.mean():.1f}（0 表示这块输出本身没有画面）")
    except Exception as exc:
        check("全屏截图成功", False, f"{type(exc).__name__}: {exc}")

    print("\n[5] 混合缩放的兜底：坐标一旦对不上，宁可跳过也不存错图")
    try:
        from PIL import Image

        real_grab = capture.ImageGrab.grab
        # 模拟"进程停在未感知级别"时最容易出现的症状：抓到的是被缩放过的区域
        capture.ImageGrab.grab = lambda *a, **k: Image.new("RGB", (960, 630), (18, 52, 86))
        try:
            capture.grab_frame(cfg, {"rect": (0, 0, 640, 420)})
            raised = False
            detail = "没有抛异常——错图会被存下来"
        except Exception as exc:
            raised = type(exc).__name__ == "PrivacyError"
            detail = f"{type(exc).__name__}: {exc}"
        finally:
            capture.ImageGrab.grab = real_grab
        check("尺寸不一致时拒绝保存截图", raised, detail)
    except Exception as exc:
        check("尺寸不一致时拒绝保存截图", False, f"{type(exc).__name__}: {exc}")

    print("\n[6] 结论")
    if not multi:
        print("       本机只有 1 台显示器，无法实测「跨屏拖动 + 不同缩放」。")
        print("       上面验证的是坐标一致性本身（尺寸差一个缩放倍数就会 FAIL），")
        print("       换到多屏机器上执行同一个脚本即可得到那部分的结论。")
    elif not mixed:
        print(f"       本机 {len(mons)} 屏但缩放比例一致（都是 {sorted(dpi_set)} DPI）：")
        print("       跨屏裁切的 origin 偏移已在副屏实测通过；混合缩放场景未覆盖。")
    else:
        print(f"       本机是多屏混合缩放（DPI {sorted(dpi_set)}），[3] 两屏尺寸一致")
        print("       即说明窗口坐标没有被虚拟化，裁切正确。")

    ok_n = sum(1 for ok, _, _ in RESULTS if ok)
    bad = [r for r in RESULTS if not r[0]]
    print("\n" + "=" * 58)
    print(f"共 {len(RESULTS)} 项：通过 {ok_n}，失败 {len(bad)}")
    for _, name, detail in bad:
        print(f"  FAIL  {name}  {detail}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
