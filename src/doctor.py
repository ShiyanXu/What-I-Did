"""环境自检：CLI `doctor` 与图形界面共用同一套判定，避免两边结论不一致。"""
from __future__ import annotations

import json
import os
import shutil
import sys

import paths
import privacy
import timeutil

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


def run(cfg=None) -> list[tuple[str, str, str]]:
    """执行全部检查，返回 (level, name, detail) 列表。不抛异常。"""
    import config as cfgmod

    paths.ensure_dirs()
    cfg = cfg or cfgmod.load()
    rows: list[tuple[str, str, str]] = []

    def add(level: str, name: str, detail: str) -> None:
        rows.append((level, name, detail))

    if getattr(cfg, "error", ""):
        add(FAIL, "配置/隐私", cfg.error)
    else:
        add(OK, "配置/隐私", "策略有效；" + ("允许脱敏文本外发" if cfg.get("privacy.allow_llm", True)
                                                else "仅本地模式，模型请求已禁用"))

    # 1. python
    v = sys.version_info
    add(OK if v >= (3, 9) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro} @ {sys.executable}")

    # 2. 依赖
    missing = []
    for mod in ("PIL", "numpy", "yaml", "jinja2", "requests"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    add(OK if not missing else FAIL, "依赖", "全部就绪" if not missing else "缺少: " + ", ".join(missing))

    # 3. 目录可写
    info_root = paths.describe()
    try:
        probe = paths.STATE / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        kind = "便携版" if info_root["portable"] else "用户目录"
        add(OK, "数据目录", f"可写（{kind}）{paths.ROOT}")
    except Exception as exc:
        add(FAIL, "数据目录", f"不可写: {exc}")

    # 4. 磁盘
    try:
        usage = shutil.disk_usage(str(paths.ROOT))
        free_gb = usage.free / 1024 ** 3
        need = float(cfg.get("capture.min_free_gb", 3))
        add(OK if free_gb >= need else WARN, "磁盘", f"{paths.ROOT.drive} 剩余 {free_gb:.1f} GB（阈值 {need:.0f} GB）")
    except Exception as exc:
        add(WARN, "磁盘", str(exc))

    # 常规自检只读屏幕指标，不为了诊断悄悄抓全屏；实测走受保护的 OCR 入口。
    try:
        import winapi

        winapi.set_dpi_aware()
        m = winapi.screen_metrics()
        add(OK, "显示器", f"{m['monitors']} 屏 虚拟桌面 {m['virtual'][2]}x{m['virtual'][3]}（未抓屏）")
        level, name = winapi.dpi_awareness()
        if level == 2:
            add(OK, "DPI", f"逐显示器感知（截图坐标与窗口坐标同一坐标系）")
        elif level == 1:
            add(WARN, "DPI", "只有系统级感知：多屏不同缩放时窗口坐标可能被虚拟化，"
                             "截图会裁偏；建议把缩放设成一致后重启程序")
        elif level == 0:
            add(FAIL, "DPI", "进程仍是未感知状态：截图尺寸会与窗口范围不一致，已拒绝抓屏")
        else:
            add(WARN, "DPI", f"无法判断当前感知级别（{name}）；截图一致性请在屏幕 OCR 实测中确认")
    except Exception as exc:
        add(FAIL, "显示器", f"读取屏幕指标失败: {exc}")

    # 7. 前台窗口
    try:
        import winapi

        fg = winapi.foreground_info()
        reason = privacy.capture_reason(cfg, fg, 0, winapi.is_locked(), force=True)
        if reason:
            add(WARN, "前台窗口", f"{reason}：按隐私策略隐藏窗口信息")
        else:
            safe = privacy.sanitize_event({**fg, "state": "active"}, cfg)
            add(OK, "前台窗口", f"{safe.get('app') or '?'} | {safe.get('title', '')[:50] or '(标题已隐藏/为空)'}")
    except Exception as exc:
        add(FAIL, "前台窗口", str(exc))

    # 8. 空闲/锁屏
    try:
        import winapi

        idle = winapi.idle_seconds()
        locked = winapi.is_locked()
        add(OK, "空闲/锁屏", f"空闲 {int(idle)}s，{'已锁屏' if locked else '未锁屏'}")
    except Exception as exc:
        add(WARN, "空闲/锁屏", str(exc))

    # 9. 会话
    try:
        import winapi

        sid, csid = winapi.session_id(), winapi.console_session_id()
        add(OK if sid == csid else WARN, "会话",
            f"进程 Session={sid}，控制台 Session={csid}" + ("" if sid == csid else "（不一致会导致截图全黑）"))
    except Exception as exc:
        add(WARN, "会话", str(exc))

    # 10. OCR
    try:
        import ocr

        info = ocr.probe(cfg)
        mode = str(info["mode"] or "auto")
        parts = [f"引擎={mode}"]
        parts.append("内置 RapidOCR 就绪" if info["rapidocr_ready"] else "内置 RapidOCR 缺失")
        if info["umi_open"]:
            parts.append("Umi 端口在听")
        elif info["umi_exe_exists"]:
            parts.append("Umi 已装但未运行")
        usable = info["rapidocr_ready"] or info["umi_open"] or info["umi_exe_exists"]
        level = OK if usable and mode != "none" else (WARN if mode == "none" else FAIL)
        add(level, "OCR", "，".join(parts))
    except Exception as exc:
        add(FAIL, "OCR", str(exc))

    # 11. 代理
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    add(OK if not proxy or "127.0.0.1" in proxy else WARN, "代理",
        f"{proxy or '未设置'}（本机请求已强制绕过代理）")

    # 12. 模型 Key
    add(OK if cfg.has_api_key() else WARN, "模型 Key",
        "已配置" if cfg.has_api_key() else
        f"未配置（填 {paths.API_KEY_PATH} 或设 WID_LLM_API_KEY，缺省时出报降级为时间线版）")

    # 13. 心跳
    try:
        if paths.HEARTBEAT.exists():
            hb = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
            ts = timeutil.parse(hb.get("ts", ""))
            age = (timeutil.now() - ts).total_seconds()
            level = OK if age < 180 else WARN
            add(level, "采集心跳", f"{int(age)}s 前（{hb.get('state', '?')}）")
        else:
            add(WARN, "采集心跳", "还没有心跳，采集器可能从未启动")
    except Exception as exc:
        add(WARN, "采集心跳", str(exc))

    # 14. 自启动
    try:
        import autostart

        state = autostart.status()
        if state["installed"]:
            add(OK, "自启动", f"已注册 → {state['command'][:70]}")
        else:
            add(WARN, "自启动", "未注册（界面「设置 → 开机自启」，或 What-IDid.exe autostart --install）")
    except Exception as exc:
        add(WARN, "自启动", str(exc))

    # 15. 出报定时任务：跟采集任务解耦，采集停了也照样出报
    try:
        import autostart

        state = autostart.status()
        if state.get("report_task"):
            add(OK, "出报定时", f"计划任务 {state['report_task_name']} 已注册"
                                f"（每天 {int(cfg.get('report.trigger_hour', 21)):02d}:00 起每 20 分钟重试、"
                                f"登录补跑、次日终版）")
        else:
            add(WARN, "出报定时", "没有出报计划任务：只能靠常驻进程到点兜底。"
                                  "在界面「设置 → 开机自启」里重新注册可一并创建")
    except Exception as exc:
        add(WARN, "出报定时", str(exc))

    # 16. 出报状态：现在有没有"该出没出"的日子
    try:
        import tick as tick_mod

        now = timeutil.now()
        due = tick_mod.pending(cfg, now, days=2)
        if due:
            add(WARN, "出报状态", "待生成：" + "、".join(f"{d}（{w}）" for d, w in due[:3]))
        else:
            add(OK, "出报状态", f"没有待生成的日报（当前工作日 "
                                f"{timeutil.workday_of(now, cfg.cutoff_hour)}）")
    except Exception as exc:
        add(WARN, "出报状态", str(exc))

    return rows


def summary(rows: list[tuple[str, str, str]]) -> dict:
    fails = [r for r in rows if r[0] == FAIL]
    warns = [r for r in rows if r[0] == WARN]
    return {"total": len(rows), "ok": len(rows) - len(fails) - len(warns),
            "warn": len(warns), "fail": len(fails)}
