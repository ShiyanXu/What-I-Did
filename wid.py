#!/usr/bin/env python
"""What-I-Did 统一入口。

    python wid.py doctor            环境自检
    python wid.py collect --once    采集一帧（调试）
    python wid.py collect           前台常驻采集（Ctrl+C 停）
    python wid.py daemon            无窗口常驻（计划任务用）
    python wid.py report --date 2026-10-19
    python wid.py tick              计划任务入口（判断该给哪几天出报）
    python wid.py status            看当前状态
    python wid.py gui               打开图形界面
    python wid.py ocr-test --screen 测一次本地 OCR
    python wid.py pause --minutes 60 / resume
    python wid.py open              打开看板
    python wid.py forget --minutes 30   遗忘最近 30 分钟（不可逆）
    python wid.py manual add --start 10:00 --end 11:00 --label "线下会议"
    python wid.py review show       查看发送前审核的待发内容
    python wid.py search 关键词 --days 30
    python wid.py weekly            生成本周汇总
    python wid.py export --date 2026-10-19  导出安全分享版
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import i18n                         # noqa: E402
from i18n import join_list, t, tn   # noqa: E402  （要先把 src 加进 sys.path）

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


def _apply_locale() -> None:
    """CLI 的输出语言跟配置走；读不到配置就用默认（英文）。"""
    code = i18n.DEFAULT_LOCALE
    try:
        import config as cfgmod

        code = cfgmod.load().get("ui.language", i18n.DEFAULT_LOCALE)
    except Exception:
        pass
    i18n.set_locale(code)


def _pad(text: str, width: int) -> str:
    """按终端显示宽度补齐：CJK 算 2 格，否则中英混排会歪。"""
    import unicodedata

    w = sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)
    return text + " " * max(0, width - w)


# ---------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    """检查逻辑放在 src/doctor.py，图形界面「诊断」页用的是同一份。"""
    import doctor
    import timeutil

    rows = doctor.run()
    if args.json:
        print(json.dumps([{"level": l.strip("[] "), "name": n, "detail": d} for l, n, d in rows],
                         ensure_ascii=False, indent=2))
    else:
        print(t("\nWhat-I-Did environment checks  {ts}\n",
  ts=timeutil.now().strftime("%Y-%m-%d %H:%M:%S")) + "-" * 78)
        for level, name, detail in rows:
            print(f"{level} {_pad(name, 12)} {detail}")
        print("-" * 78)
        s = doctor.summary(rows)
        print(t("Result: {ok} ok / {warn} warnings / {fail} failed",
  ok=s["ok"], warn=s["warn"], fail=s["fail"]))

    if args.full:
        return cmd_ocr_test(argparse.Namespace(screen=True, image=None, raw=False))
    return 1 if [r for r in rows if r[0] == doctor.FAIL] else 0


# ---------------------------------------------------------------- ocr-test
def cmd_ocr_test(args) -> int:
    import config as cfgmod
    import ocr
    import paths

    cfg = cfgmod.load()
    cfg.require_valid()
    paths.ensure_dirs()
    image_path: Path | None = None
    if args.image:
        image_path = Path(args.image)
        if not image_path.exists():
            print(t("{tag} Image not found: {p}", tag=FAIL, p=image_path))
            return 1
    elif not args.screen:
        print(t("Pass --screen or --image <path>"))
        return 2

    info = ocr.probe(cfg)
    print(t("engine mode={mode}  umi={umi}  http:{port} listening={listen}",
  mode=info["mode"], umi=info["umi_exe_exists"],
  port=info["umi_port"], listen=info["umi_open"]))
    res = (ocr.recognize_once(cfg, image_path, timeout=float(cfg.get("ocr.timeout_sec", 90)))
           if image_path is not None else ocr.recognize_screen(cfg))
    print(t("engine={engine}  status={status}  {ms}ms",
  engine=res["engine"] or "-", status=res["status"], ms=res["ms"]))
    if res["error"]:
        print(f"{FAIL} {res['error']}")
    text = res["text"] or ""
    if args.raw:
        print("-" * 60)
        print(text)
    else:
        preview = text[:600].replace("\n", " / ")
        print("-" * 60)
        print(t("chars={n}", n=len(text)))
        print(t("Preview: {p}", p=preview or t("(empty)")))
    return 0 if res["status"] == "ok" else 1


# ---------------------------------------------------------------- 采集
def cmd_collect(args) -> int:
    import config as cfgmod
    import logging_setup
    import paths
    import timeutil
    from capture import Collector

    cfg = cfgmod.load()
    paths.ensure_dirs()
    log = logging_setup.setup("collector", console=True)
    if args.once:
        col = Collector(cfg, log)
        ev = col.sample(force=True)
        print(json.dumps(ev, ensure_ascii=False, indent=2))
        col.shutdown()
        return 0
    log.info(t("Foreground capture started (Ctrl+C to stop)"))
    return _run_collector(cfg, log, duration=args.duration)


def _run_collector(cfg, log, duration: float = 0) -> int:
    from capture import Collector

    col = Collector(cfg, log)
    started = time_now()
    try:
        col.run(duration=duration)
    except KeyboardInterrupt:
        log.info(t("Interrupted"))
    finally:
        col.shutdown()
    log.info(t("Capture finished after %.0fs"), time_now() - started)
    return 0


def time_now() -> float:
    import time

    return time.time()


# ---------------------------------------------------------------- daemon
def cmd_daemon(args) -> int:
    import config as cfgmod
    import daemon
    import logging_setup
    import paths

    cfg = cfgmod.load()
    paths.ensure_dirs()
    log = logging_setup.setup("daemon", console=False)
    return daemon.run(cfg, log)


# ---------------------------------------------------------------- report
def cmd_report(args) -> int:
    import config as cfgmod
    import logging_setup
    import paths
    import report as report_mod
    import timeutil

    cfg = cfgmod.load(reload=True)
    paths.ensure_dirs()
    log = logging_setup.setup("report", console=True)
    day = args.date or timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)
    res = report_mod.generate(day, cfg, log, reason=args.reason,
                              force=args.force, open_browser=(not args.no_open))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res.get("status") == "awaiting_review":
        print(t("Queued for review; nothing is sent until you confirm: {exe} review show --date {day}",
  exe=Path(sys.argv[0]).name, day=day))
    return 0 if res.get("status") in ("ok", "skipped", "degraded", "empty", "busy",
                                      "nodata", "awaiting_review") else 1


def cmd_tick(args) -> int:
    import config as cfgmod
    import logging_setup
    import paths
    import tick

    cfg = cfgmod.load(reload=True)
    paths.ensure_dirs()
    log = logging_setup.setup("tick", console=True)
    return tick.run(cfg, log)


# ---------------------------------------------------------------- status
def cmd_status(args) -> int:
    import config as cfgmod
    import paths
    import timeutil

    cfg = cfgmod.load()
    paths.ensure_dirs()
    now = timeutil.now()
    day = timeutil.workday_of(now, cfg.cutoff_hour)
    out = {"now": timeutil.iso(now), "workday": day}

    if paths.HEARTBEAT.exists():
        try:
            out["heartbeat"] = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            out["heartbeat"] = None
    else:
        out["heartbeat"] = None

    ev = paths.events_path(day)
    shots = 0
    lines = 0
    if ev.exists():
        try:
            with ev.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip():
                        lines += 1
        except OSError:
            pass
    sd = paths.shots_dir(day)
    if sd.exists():
        shots = sum(1 for _ in sd.glob("*.jpg"))
    out["today"] = {"events": lines, "shots": shots}

    try:
        usage = shutil.disk_usage(str(paths.ROOT))
        out["disk_free_gb"] = round(usage.free / 1024 ** 3, 1)
    except Exception:
        pass

    st = paths.day_state(day)
    out["day_state"] = json.loads(st.read_text(encoding="utf-8")) if st.exists() else None
    out["paused"] = paths.PAUSE_FILE.exists()
    out["api_key"] = cfg.has_api_key()
    # 配置失效时采集与外发已经停了，这里必须把原因带出来，否则 status 看起来一切正常
    out["privacy_error"] = getattr(cfg, "error", "")

    reports = sorted(paths.REPORTS.glob("*.md"))[-5:]
    out["recent_reports"] = [p.stem for p in reports]

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- misc
def cmd_pause(args) -> int:
    import control

    ok, msg = control.pause(args.minutes)
    print(msg)
    return 0 if ok else 1


def cmd_resume(args) -> int:
    import control

    ok, msg = control.resume()
    print(msg)
    return 0 if ok else 1


def cmd_open(args) -> int:
    import os
    import paths
    import timeutil
    import config as cfgmod

    cfg = cfgmod.load()
    day = args.date or timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)
    target = paths.web_day(day)
    if not target.exists():
        target = paths.WEB / "index.html"
    if not target.exists():
        print(t("No dashboard yet. Run: python wid.py report"))
        return 1
    os.startfile(str(target))  # noqa: S606
    print(t("Opened {target}", target=target))
    return 0


def cmd_clean(args) -> int:
    import config as cfgmod
    import logging_setup
    import retention

    cfg = cfgmod.load()
    log = logging_setup.setup("clean", console=True)
    summary = retention.clean(cfg, log, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") == "ok" else 1


# ---------------------------------------------------------------- 遗忘 / 补录 / 审核
def _confirm(prompt: str) -> bool | None:
    """危险操作确认：只有显式 y 才算同意，回车视为取消（与 _ask 的默认相反）。"""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return None
    except Exception:
        return None
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        print()
        return False
    return ans in ("y", "yes")


def cmd_forget(args) -> int:
    """删除最近 N 分钟或某一整天的原始与派生数据。不可逆。"""
    import config as cfgmod
    import forget as forget_mod
    import logging_setup

    if bool(args.minutes) == bool(args.day):
        print(t("Pick one: --minutes N (last N minutes) or --day YYYY-MM-DD (whole day)"))
        return 2
    scope = t("the last {n} minutes", n=args.minutes) if args.minutes else t("the whole day {day}", day=args.day)
    if not args.yes:
        ans = _confirm(t("This permanently deletes events, screenshots, OCR and derived reports for "
  "{scope}. Continue? [y/N] ", scope=scope))
        if ans is None:
            print(t("Non-interactive: add --yes to confirm (planned deletion: {scope})", scope=scope))
            return 2
        if not ans:
            print(t("Cancelled; nothing was deleted"))
            return 2

    cfg = cfgmod.load()
    log = logging_setup.setup("forget", console=True)
    try:
        summary = (forget_mod.forget_minutes(args.minutes, cfg, log) if args.minutes
                   else forget_mod.forget_day(args.day, cfg, log))
    except (ValueError, RuntimeError) as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(t("Deleted. The dashboard index is refreshed; content already sent to the model service cannot be recalled."))
    return 0


def cmd_manual(args) -> int:
    """人工修订与线下补录：写 data/<day>/manual.json，下次出报时套用。"""
    import config as cfgmod
    import manual as manual_mod
    import paths
    import timeutil

    paths.ensure_dirs()
    cfg = cfgmod.load()
    day = args.date or timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)
    action = args.action

    if action == "show":
        print(json.dumps(manual_mod.load(day), ensure_ascii=False, indent=2))
        return 0
    try:
        if action == "add":
            if not (args.start and args.end and args.label):
                print(t("add needs --start HH:MM --end HH:MM --label TEXT"))
                return 2
            manual_mod.add_offline(day, args.start, args.end, args.label, args.note or "")
        elif action == "override":
            if not args.seg:
                print(t("override needs --seg seg-03"))
                return 2
            manual_mod.set_override(day, args.seg, args.verb or "", args.label or "", args.note or "")
        elif action == "notes":
            if args.text is None:
                print(t("notes needs --text"))
                return 2
            manual_mod.set_notes(day, args.text)
        else:
            print(t("Unknown action; use add / override / notes / show"))
            return 2
    except ValueError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(t("Written to {path}", path=manual_mod.path(day)))
    print(t("The next report picks it up automatically: {exe} report --date {day} --force",
  exe=Path(sys.argv[0]).name, day=day))
    return 0


def cmd_review(args) -> int:
    """发送前审核：查看待发内容、确认外发、丢弃预览。"""
    import config as cfgmod
    import logging_setup
    import paths
    import report as report_mod
    import timeutil

    cfg = cfgmod.load(reload=True)
    paths.ensure_dirs()
    day = args.date or timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)
    path = report_mod.review_path(day)

    if args.action == "show":
        if not path.exists():
            print(t("No queued content for {day}. Run: {exe} report --date {day}",
  day=day, exe=Path(sys.argv[0]).name))
            return 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(t("{tag} Cannot read the queued file: {e}", tag=FAIL, e=exc))
            return 1
        payload = str(data.get("payload") or "")
        print(t("Date     : {v}", v=data.get("day", day)))
        print(t("Generated: {v}", v=data.get("generated_at", "")))
        print(t("Coverage : {v}", v=data.get("coverage", "")))
        print(t("Target   : {v} @ {u}", v=data.get("model", ""), u=data.get("base_url", "")))
        print(t("Chars    : {v}", v=data.get("payload_chars", len(payload))))
        print(t("Preview  : {v}", v=path))
        print("-" * 70)
        limit = max(200, int(args.chars))
        print(payload[:limit] + (t("\n…(truncated; adjust with --chars or open the preview file)")
                                 if len(payload) > limit else ""))
        return 0

    if args.action == "approve":
        if not path.exists():
            print(t("No queued content for {day}; run report first to build a preview", day=day))
            return 1
        if not cfg.get("report.review_mode", False):
            print(t("review_mode is off; approving still generates the report normally"))
        log = logging_setup.setup("review", console=True)
        res = report_mod.generate(day, cfg, log, reason="review", force=True,
                                  open_browser=False, approved=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("status") in ("ok", "degraded", "empty", "skipped") else 1

    if args.action == "discard":
        report_mod.discard_review(day)
        print(t("Discarded the queued content for {day} (nothing was sent)", day=day))
        return 0

    print(t("Unknown action; use show / approve / discard"))
    return 2


# ---------------------------------------------------------------- 检索 / 汇总 / 分享
def cmd_search(args) -> int:
    """跨日检索：只在本地扫描，不联网。"""
    import config as cfgmod
    import paths
    import search as search_mod

    paths.ensure_dirs()
    cfg = cfgmod.load()
    kinds = tuple(k.strip() for k in str(args.kind or "").split(",") if k.strip()) or search_mod.KINDS
    try:
        result = search_mod.search(args.query, days=args.days, cfg=cfg,
                                  limit=args.limit, kinds=kinds)
    except ValueError as exc:
        print(f"{FAIL} {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(search_mod.render_text(result))
    return 0 if result["count"] else 1


def cmd_weekly(args) -> int:
    """周汇总：只聚合已生成的日报，不重新调用模型。"""
    import config as cfgmod
    import paths
    import weekly as weekly_mod

    paths.ensure_dirs()
    cfg = cfgmod.load()
    try:
        data = weekly_mod.weekly(anchor=args.anchor, cfg=cfg, out=args.out,
                                 start=args.start, end=args.end)
    except ValueError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(t("{start} - {end}: {days} days with a report, {minutes:.0f} minutes of activity",
  start=data["start"], end=data["end"], days=data["counted_days"],
  minutes=data["total_minutes"]))
    if data["missing"]:
        print(t("Missing: {items}", items=join_list(data["missing"])))
    print(t("Written to {path}", path=data["path"]))
    return 0


def cmd_export(args) -> int:
    """安全分享版：去截图、去 OCR 原文、去内部锚点与本机路径。"""
    import config as cfgmod
    import export_safe
    import paths
    import timeutil

    paths.ensure_dirs()
    cfg = cfgmod.load()
    day = args.date or timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)
    try:
        res = export_safe.export_day(day, cfg, out=args.out,
                                     include_evidence=not args.no_evidence)
    except (ValueError, export_safe.ExportError) as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(t("Exported the shareable copy of {day} ({chars} chars): {path}",
  day=res["day"], chars=res["chars"], path=res["path"]))
    print(t("Removed: {items}", items=join_list(res["removed"])))
    return 0


def cmd_init(args) -> int:
    import config as cfgmod
    import paths

    paths.ensure_dirs()
    actions = cfgmod.ensure_default()
    print(t("Data dir: {p}", p=paths.ROOT) + (t("(portable)") if paths.describe()["portable"] else t("(user folder)")))
    print(t("Config  : {p}", p=paths.CONFIG_PATH) + (t("(created)") if actions.get("config") else t("(already there)")))
    print(t("API Key : {path} (paste the key here, or set WID_LLM_API_KEY)",
  path=paths.API_KEY_PATH))
    print(t("Bindings: {path}", path=paths.BINDINGS_PATH))
    print(t("\nNext steps:"))
    print(t("  1) Set an API key (or import it from an env var)"))
    print(t("  2) wid.py doctor            environment checks"))
    print(t("  3) wid.py autostart --install   start with Windows"))
    return 0


def cmd_start(args) -> int:
    """拉起后台采集（脱离当前窗口，关掉控制台也不会停）。"""
    import autostart
    import paths
    import time
    import timeutil

    paths.ensure_dirs()
    hb = None
    if paths.HEARTBEAT.exists():
        try:
            hb = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            hb = None
    if hb and hb.get("pid"):
        age = _pid_age_seconds(int(hb["pid"]))
        if age is not None and age < 120:
            print(t("Already running (pid={pid}, heartbeat {age}s ago); no need to start it again",
  pid=hb["pid"], age=age))
            return 0

    pid = autostart.spawn_daemon()
    print(t("Background process started, pid={pid}; waiting for its first heartbeat …", pid=pid))
    deadline = time.time() + 12
    while time.time() < deadline:
        time.sleep(1)
        if not paths.HEARTBEAT.exists():
            continue
        try:
            cur = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            continue
        if int(cur.get("pid") or 0) == pid:
            print(t("Capture started: {app}, {n} screenshots today",
  app=cur.get("app") or "-", n=cur.get("shots_today", 0)))
            print(t("Data dir: {p}", p=paths.ROOT))
            return 0
    print(t("No heartbeat within 12 seconds; the process may have failed to start. Try:"))
    log = max(paths.LOGS.glob("daemon-*.log"), key=lambda p: p.stat().st_mtime, default=None)
    print(t("  1) Check the log: {log}", log=log))
    print(t("  2) Run it in the foreground to see the error: {exe} daemon",
  exe=Path(sys.argv[0]).name))
    return 1


def cmd_stop(args) -> int:
    """停掉后台采集进程（按心跳里的 pid，不误杀别的进程）。"""
    import control

    ok, msg = control.stop()
    print(msg)
    return 0 if ok else 1


def _pid_age_seconds(pid: int) -> int | None:
    """进程存在则返回心跳文件的滞后秒数，不存在返回 None。"""
    try:
        import psutil

        if not psutil.pid_exists(pid):
            return None
        import paths
        import timeutil

        if not paths.HEARTBEAT.exists():
            return None
        hb = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
        ts = timeutil.parse(hb.get("ts")) if hb.get("ts") else None
        if ts is None:
            return None
        return max(0, int((timeutil.now() - ts).total_seconds()))
    except Exception:
        return None


def _ask(prompt: str, default_yes: bool = True) -> bool | None:
    """读一个 y/n。非交互（管道、双击无控制台）时返回 None，调用方按"待办"处理。"""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return None
    except Exception:
        return None
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        print()
        return None
    if ans in ("", "y", "yes"):
        return True
    if ans in ("n", "no"):
        return False
    return default_yes


def cmd_setup(args) -> int:
    """首次运行引导：建目录、生成配置、注册自启、拉起后台。"""
    import config as cfgmod
    import paths

    actions = cfgmod.ensure_default()
    cfg = cfgmod.load(reload=True)
    paths.ensure_dirs()

    me = Path(sys.executable) if paths.is_frozen() else Path(__file__).resolve()
    print("\n" + "=" * 66)
    print(t("  What-I-Did  first-run setup"))
    print("=" * 66)
    print(t("  Program  : {p}", p=me))
    print(t("  Data dir : {p}", p=paths.ROOT))
    print(t("  Config   : {p}", p=paths.CONFIG_PATH))
    if actions.get("config"):
        print(t("             (generated with defaults; edit it directly)"))

    # --- Key
    key_path = paths.API_KEY_PATH
    print()
    if cfg.has_api_key():
        print(t("  [done] Model API key configured"))
    else:
        print(t("  [todo] No model API key yet"))
        print(t("         Paste the key into: {p}", p=key_path))
        print(t("         or set the WID_LLM_API_KEY env var (takes precedence)"))
        print(t("         Default model: {m} @ {u}",
  m=cfg.get("llm.model"), u=cfg.get("llm.base_url")))
        print(t("         It works without one, but reports degrade to a plain timeline."))

    # --- 自启
    import autostart

    try:
        st = autostart.status()
    except Exception:
        st = {"installed": False, "kind": "", "command": ""}
    name = me.name
    print()
    if st.get("installed"):
        print(t("  [done] Autostart registered ({kind})", kind=st.get("kind")))
    else:
        ans = _ask(t("  [ask] Start automatically at logon? [Y/n] "))
        if ans is True:
            print("         " + autostart.install())
            st = autostart.status()
        elif ans is False:
            print(t("         Skipped. Later you can run: {name} autostart --install", name=name))
        else:
            print(t("  [todo] Autostart is not registered"))
            print(t("         Run: {name} autostart --install", name=name))
    if st.get("command"):
        print(t("         Autostart command: {cmd}", cmd=st["command"]))

    # --- 立刻开始采集
    print()
    running = False
    try:
        if paths.HEARTBEAT.exists():
            hb = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
            pid = int(hb.get("pid") or 0)
            age = _pid_age_seconds(pid) if pid else None
            running = age is not None and age < 120
    except Exception:
        running = False
    if running:
        print(t("  [done] Background capture is running"))
    else:
        ans = _ask(t("  [ask] Start background capture now? [Y/n] "))
        if ans is True:
            pid = autostart.spawn_daemon()
            print(t("         Started pid={pid}; run status in a few seconds to check the heartbeat",
  pid=pid))
        elif ans is False:
            print(t("         Skipped. Later you can run: {name} start", name=name))
        else:
            print(t("  [todo] Start capture: {name} start", name=name))

    print()
    print(t("  Verify with: {name} doctor", name=name))
    print("=" * 66 + "\n")
    return 0


def cmd_gui(args) -> int:
    import gui

    return gui.main(getattr(args, "shot", None), getattr(args, "page", None),
                    getattr(args, "day", None))


def cmd_autostart(args) -> int:
    import autostart
    import config as cfgmod

    if args.remove:
        print(autostart.uninstall())
        return 0
    if args.status:
        st = autostart.status()
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0 if st["installed"] else 1
    print(autostart.install(cfgmod.load(reload=True)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="What-IDid", description=t("What-I-Did automatic work log and daily reports"))
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("doctor", help=t("Environment checks"))
    d.add_argument("--json", action="store_true")
    d.add_argument("--full", action="store_true", help=t("Also run a real OCR pass"))
    d.set_defaults(func=cmd_doctor)

    o = sub.add_parser("ocr-test", help=t("Test local OCR"))
    o.add_argument("--screen", action="store_true")
    o.add_argument("--image")
    o.add_argument("--raw", action="store_true")
    o.set_defaults(func=cmd_ocr_test)

    c = sub.add_parser("collect", help=t("Capture (foreground, for debugging)"))
    c.add_argument("--once", action="store_true")
    c.add_argument("--duration", type=float, default=0)
    c.set_defaults(func=cmd_collect)

    dm = sub.add_parser("daemon", help=t("Resident capture (no window)"))
    dm.set_defaults(func=cmd_daemon)

    st_ = sub.add_parser("start", help=t("Start capture in the background"))
    st_.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", help=t("Stop background capture"))
    sp.set_defaults(func=cmd_stop)

    r = sub.add_parser("report", help=t("Generate the report for one day"))
    r.add_argument("--date")
    r.add_argument("--reason", default="manual")
    r.add_argument("--force", action="store_true")
    r.add_argument("--no-open", action="store_true")
    r.set_defaults(func=cmd_report)

    tk = sub.add_parser("tick", help=t("Scheduled-task entry point"))
    tk.set_defaults(func=cmd_tick)

    s = sub.add_parser("status", help=t("Current status"))
    s.set_defaults(func=cmd_status)

    pa = sub.add_parser("pause", help=t("Pause capture"))
    pa.add_argument("--minutes", type=int, default=0)
    pa.set_defaults(func=cmd_pause)

    re_ = sub.add_parser("resume", help=t("Resume capture"))
    re_.set_defaults(func=cmd_resume)

    op = sub.add_parser("open", help=t("Open the dashboard"))
    op.add_argument("--date")
    op.set_defaults(func=cmd_open)

    cl = sub.add_parser("clean", help=t("Delete expired data"))
    cl.add_argument("--dry-run", action="store_true")
    cl.set_defaults(func=cmd_clean)

    i = sub.add_parser("init", help=t("Create folders and a placeholder config"))
    i.set_defaults(func=cmd_init)

    su = sub.add_parser("setup", help=t("First-run guide: create the config and list what is left to do"))
    su.set_defaults(func=cmd_setup)

    au = sub.add_parser("autostart", help=t("Register or remove autostart (no admin needed)"))
    au.add_argument("--install", action="store_true", help=t("Register autostart (default)"))
    au.add_argument("--remove", action="store_true", help=t("Remove autostart"))
    au.add_argument("--status", action="store_true", help=t("Show the current status"))
    au.set_defaults(func=cmd_autostart)

    fg_ = sub.add_parser("forget", help=t("Forget the last N minutes or a whole day (irreversible)"))
    fg_.add_argument("--minutes", type=int, default=0, help=t("How many recent minutes"))
    fg_.add_argument("--day", help=t("Forget a whole day, as YYYY-MM-DD"))
    fg_.add_argument("--yes", action="store_true", help=t("Skip the interactive confirmation (for scripts and scheduled tasks)"))
    fg_.set_defaults(func=cmd_forget)

    ma = sub.add_parser("manual", help=t("Manual entries and corrections"))
    ma.add_argument("action", choices=["add", "override", "notes", "show"],
                    help=t("add an offline activity; override a segment label; notes for the day; show them"))
    ma.add_argument("--date", help=t("YYYY-MM-DD, defaults to today (04:00 cutoff)"))
    ma.add_argument("--start", help=t("for add: start time HH:MM"))
    ma.add_argument("--end", help=t("for add: end time HH:MM"))
    ma.add_argument("--label", help=t("for add/override: the label"))
    ma.add_argument("--seg", help=t("for override: seg-03"))
    ma.add_argument("--verb", help=t("for override: coding/debugging/testing/designing/reading/writing/meeting/communicating/operating/admin"))
    ma.add_argument("--note", help=t("Note"))
    ma.add_argument("--text", help=t("for notes: the note text"))
    ma.set_defaults(func=cmd_manual)

    rv = sub.add_parser("review", help=t("Pre-send review: show, approve or discard queued content"))
    rv.add_argument("action", choices=["show", "approve", "discard"])
    rv.add_argument("--date", help=t("YYYY-MM-DD, defaults to today"))
    rv.add_argument("--chars", type=int, default=2000, help=t("How many characters to print for show"))
    rv.set_defaults(func=cmd_review)

    se = sub.add_parser("search", help=t("Search titles, screen text and reports across days (local only)"))
    se.add_argument("query", nargs="+", help=t("Keywords; when several are given, all must match"))
    se.add_argument("--days", type=int, default=14, help=t("How many days back, 1-365"))
    se.add_argument("--kind", help=t("Subset of title,ocr,report; all by default"))
    se.add_argument("--limit", type=int, default=200, help=t("Maximum hits overall"))
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=cmd_search)

    wk = sub.add_parser("weekly", help=t("Weekly summary: aggregate the generated reports"))
    wk.add_argument("--anchor", help=t("Use the calendar week containing this day; defaults to today"))
    wk.add_argument("--start", help=t("Custom start date, YYYY-MM-DD"))
    wk.add_argument("--end", help=t("Custom end date, YYYY-MM-DD"))
    wk.add_argument("--out", help=t("Output filename (written under reports/weekly/)"))
    wk.set_defaults(func=cmd_weekly)

    ex = sub.add_parser("export", help=t("Export a shareable report (no screenshots, source text or local paths)"))
    ex.add_argument("--date", help=t("YYYY-MM-DD, defaults to today"))
    ex.add_argument("--no-evidence", action="store_true", help=t("Drop the evidence fields as well"))
    ex.add_argument("--out", help=t("Output filename or absolute path (must stay inside reports)"))
    ex.set_defaults(func=cmd_export)

    g = sub.add_parser("gui", help=t("Open the graphical interface"))
    g.add_argument("--shot", help=t("[dev] screenshot the window itself to this path, then exit"))
    g.add_argument("--page", help=t("[dev] page to show before the screenshot: dash/reports/settings/diag"))
    g.add_argument("--day", help=t("[dev] day to select on the Reports page before the screenshot"))
    g.set_defaults(func=cmd_gui)
    return p


def _ensure_console() -> bool:
    """窗口化 exe 里 sys.stdout/stderr 是 None（pythonw 语义）。

    合并成单个 exe 之后，命令行要用同一个文件：有父控制台就 Attach 过去，
    没有就自己开一个。attached=False 说明命令是在资源管理器里直接跑的，
    这时 AllocConsole 会弹一个控制台窗口，命令结束随进程一起消失。
    """
    if os.name != "nt":
        return False
    if sys.stdout is not None and sys.stderr is not None:
        return False
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        attached = bool(k32.AttachConsole(-1))          # ATTACH_PARENT_PROCESS
        if not attached and not k32.AllocConsole():
            return False
    except Exception:
        return False
    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        except OSError:
            pass
        return True
    except OSError:
        return False


def _fix_console_encoding() -> None:
    """打包成 exe 后 stdout 默认走系统代码页（GBK），中文会变乱码。

    同时处理两种情况：真实控制台（切 65001）和管道/重定向（强制 UTF-8）。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 一个 exe 顶三个角色：双击进界面（无控制台），带参数当命令行用。
    # daemon 是后台进程，不能给它挂控制台，其余子命令先把控制台接回来。
    if argv and argv[0] != "daemon":
        _ensure_console()
    # 剩下的情况（界面/守护）不需要输出，兜一个 devnull，免得 print 炸
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    _fix_console_encoding()

    _apply_locale()
    parser = build_parser()
    if not argv:
        # 老版本的无窗口守护 exe 双击时进采集循环（升级后兼容一下）
        if "daemon" in Path(sys.argv[0]).stem.lower():
            args = parser.parse_args(["daemon"])
            return int(args.func(args) or 0)
        # 双击主程序：直接开界面（缺配置时界面里会有提示）
        return cmd_gui(argparse.Namespace(shot=None, page=None, day=None))
    try:
        args = parser.parse_args(argv)
        if not getattr(args, "func", None):
            parser.print_help()
            return 2
        return int(args.func(args) or 0)
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        try:
            sys.stdout and sys.stdout.flush()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
