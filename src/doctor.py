"""环境自检：CLI `doctor` 与图形界面共用同一套判定，避免两边结论不一致。"""
from __future__ import annotations

from i18n import join_list, t

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
        add(FAIL, t("Config / privacy"), cfg.error)
    else:
        add(OK, t("Config / privacy"), t("Policy OK; ") + (t("redacted text may be sent")
                                     if cfg.get("privacy.allow_llm", True)
                                     else t("local-only, model requests are disabled")))

    # 1. python
    v = sys.version_info
    # 3.10 是硬要求：vision.py 用了 int.bit_count()。写成 3.9 会让 3.9 机器上 doctor 报绿、
    # 到采集阶段才炸，比直接报红更糟（见 PROGRESS 已知问题）。
    add(OK if v >= (3, 10) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro} @ {sys.executable}")

    # 2. 依赖
    missing = []
    for mod in ("PIL", "numpy", "yaml", "jinja2", "requests"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    add(OK if not missing else FAIL, t("Dependencies"), t("Everything present") if not missing else t("Missing: ") + ", ".join(missing))

    # 3. 目录可写
    info_root = paths.describe()
    try:
        probe = paths.STATE / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        kind = t("portable") if info_root["portable"] else t("user folder")
        add(OK, t("Data folder"), t("Writable ({kind}) {path}", kind=kind, path=paths.ROOT))
    except Exception as exc:
        add(FAIL, t("Data folder"), t("Not writable: {e}", e=exc))

    # 4. 磁盘
    try:
        usage = shutil.disk_usage(str(paths.ROOT))
        free_gb = usage.free / 1024 ** 3
        need = float(cfg.get("capture.min_free_gb", 3))
        add(OK if free_gb >= need else WARN, t("Disk"), t("{drive} has {free:.1f} GB free (threshold {need:.0f} GB)",
            drive=paths.ROOT.drive, free=free_gb, need=need))
    except Exception as exc:
        add(WARN, t("Disk"), str(exc))

    # 常规自检只读屏幕指标，不为了诊断悄悄抓全屏；实测走受保护的 OCR 入口。
    try:
        import winapi

        winapi.set_dpi_aware()
        m = winapi.screen_metrics()
        add(OK, t("Displays"), t("{n} monitors, virtual desktop {w}x{h} (not captured)",
               n=m["monitors"], w=m["virtual"][2], h=m["virtual"][3]))
        level, name = winapi.dpi_awareness()
        if level == 2:
            add(OK, "DPI", t("Per-monitor aware (captures and window rects share one coordinate space)"))
        elif level == 1:
            add(WARN, "DPI", t("System-level aware only: with mixed scaling the window rect can be "
                          "virtualized and captures get cropped; set one scaling factor and "
                          "restart"))
        elif level == 0:
            add(FAIL, "DPI", t("Still DPI-unaware: capture size would not match the window rect, "
                    "so capturing is refused"))
        else:
            add(WARN, "DPI", t("Cannot tell the current awareness level ({name}); confirm capture "
                          "consistency with the OCR test", name=name))
    except Exception as exc:
        add(FAIL, t("Displays"), t("Cannot read screen metrics: {e}", e=exc))

    # 7. 前台窗口
    try:
        import winapi

        fg = winapi.foreground_info()
        reason = privacy.capture_reason(cfg, fg, 0, winapi.is_locked(), force=True)
        if reason:
            add(WARN, t("Foreground window"), t("{reason}: window details hidden by the privacy policy", reason=reason))
        else:
            safe = privacy.sanitize_event({**fg, "state": "active"}, cfg)
            add(OK, t("Foreground window"), f"{safe.get('app') or '?'} | "
                  + (safe.get("title", "")[:50] or t("(title hidden or empty)")))
    except Exception as exc:
        add(FAIL, t("Foreground window"), str(exc))

    # 8. 空闲/锁屏
    try:
        import winapi

        idle = winapi.idle_seconds()
        locked = winapi.is_locked()
        add(OK, t("Idle / lock"), t("idle {n}s, {state}", n=int(idle),
                     state=t("screen locked") if locked else t("screen unlocked")))
    except Exception as exc:
        add(WARN, t("Idle / lock"), str(exc))

    # 9. 会话
    try:
        import winapi

        sid, csid = winapi.session_id(), winapi.console_session_id()
        add(OK if sid == csid else WARN, t("Session"),
            t("process session {a}, console session {b}", a=sid, b=csid)
            + ("" if sid == csid else t(" (a mismatch makes captures come out black)")))
    except Exception as exc:
        add(WARN, t("Session"), str(exc))

    # 10. OCR
    try:
        import ocr

        info = ocr.probe(cfg)
        mode = str(info["mode"] or "auto")
        parts = [t("engine={mode}", mode=mode)]
        parts.append(t("bundled RapidOCR ready") if info["rapidocr_ready"] else t("bundled RapidOCR missing"))
        if info["umi_open"]:
            parts.append(t("Umi port is listening"))
        elif info["umi_exe_exists"]:
            parts.append(t("Umi installed but not running"))
        usable = info["rapidocr_ready"] or info["umi_open"] or info["umi_exe_exists"]
        level = OK if usable and mode != "none" else (WARN if mode == "none" else FAIL)
        add(level, "OCR", join_list(parts))
    except Exception as exc:
        add(FAIL, "OCR", str(exc))

    # 11. 代理
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    add(OK if not proxy or "127.0.0.1" in proxy else WARN, t("Proxy"),
        t("{proxy} (local requests bypass the proxy)", proxy=proxy or t("not set")))

    # 12. 模型 Key
    add(OK if cfg.has_api_key() else WARN, t("Model API key"),
        t("Configured") if cfg.has_api_key() else
        t("Not configured (set {path} or the WID_LLM_API_KEY env var; without it "
          "reports degrade to a timeline)", path=paths.API_KEY_PATH))

    # 13. 心跳
    try:
        if paths.HEARTBEAT.exists():
            hb = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
            ts = timeutil.parse(hb.get("ts", ""))
            age = (timeutil.now() - ts).total_seconds()
            level = OK if age < 180 else WARN
            add(level, t("Capture heartbeat"), t("{n}s ago ({state})", n=int(age), state=hb.get("state", "?")))
        else:
            add(WARN, t("Capture heartbeat"), t("No heartbeat yet; the collector may never have started"))
    except Exception as exc:
        add(WARN, t("Capture heartbeat"), str(exc))

    # 14. 自启动
    try:
        import autostart

        state = autostart.status()
        if state["installed"]:
            # 命令行可能很长：截断要留省略号，并保留尾巴（exe 路径与参数都在后半段）
            cmd = str(state["command"])
            shown = cmd if len(cmd) <= 70 else "…" + cmd[-68:]
            add(OK, t("Autostart"), t("Registered → {cmd}", cmd=shown))
        else:
            add(WARN, t("Autostart"), t("Not registered (Settings → Autostart, or What-IDid.exe autostart --install)"))
    except Exception as exc:
        add(WARN, t("Autostart"), str(exc))

    # 15. 出报定时任务：跟采集任务解耦，采集停了也照样出报
    try:
        import autostart

        state = autostart.status()
        if state.get("report_task"):
            add(OK, t("Report schedule"), t("Scheduled task {name} registered (retries every 20 minutes from "
                                  "{hour:02d}:00, a catch-up at logon, and a final version the next day)",
                                  name=state["report_task_name"],
                                  hour=int(cfg.get("report.trigger_hour", 21))))
        else:
            add(WARN, t("Report schedule"), t("No report task: only the resident process can trigger it. Re-register "
                                  "from Settings → Autostart to create it"))
    except Exception as exc:
        add(WARN, t("Report schedule"), str(exc))

    # 16. 出报状态：现在有没有"该出没出"的日子
    try:
        import tick as tick_mod

        now = timeutil.now()
        due = tick_mod.pending(cfg, now, days=2)
        if due:
            add(WARN, t("Report status"), t("Due: ") + join_list(f"{d} ({w})" for d, w in due[:3]))
        else:
            add(OK, t("Report status"), t("No report pending (current workday {day})",
                                day=timeutil.workday_of(now, cfg.cutoff_hour)))
    except Exception as exc:
        add(WARN, t("Report status"), str(exc))

    return rows


def summary(rows: list[tuple[str, str, str]]) -> dict:
    fails = [r for r in rows if r[0] == FAIL]
    warns = [r for r in rows if r[0] == WARN]
    return {"total": len(rows), "ok": len(rows) - len(fails) - len(warns),
            "warn": len(warns), "fail": len(fails)}
