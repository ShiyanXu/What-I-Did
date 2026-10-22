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

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


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
        print(f"\nWhat-I-Did 环境自检  {timeutil.now():%Y-%m-%d %H:%M:%S}\n" + "-" * 78)
        for level, name, detail in rows:
            print(f"{level} {_pad(name, 12)} {detail}")
        print("-" * 78)
        s = doctor.summary(rows)
        print(f"结果：{s['ok']} 正常 / {s['warn']} 提醒 / {s['fail']} 失败")

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
            print(f"{FAIL} 图片不存在: {image_path}")
            return 1
    elif not args.screen:
        print("请加 --screen 或 --image <路径>")
        return 2

    info = ocr.probe(cfg)
    print(f"引擎配置={info['mode']}  Umi存在={info['umi_exe_exists']}  "
          f"HTTP:{info['umi_port']} 在听={info['umi_open']}")
    res = (ocr.recognize_once(cfg, image_path, timeout=float(cfg.get("ocr.timeout_sec", 90)))
           if image_path is not None else ocr.recognize_screen(cfg))
    print(f"引擎={res['engine'] or '-'}  状态={res['status']}  耗时={res['ms']}ms")
    if res["error"]:
        print(f"{FAIL} {res['error']}")
    text = res["text"] or ""
    if args.raw:
        print("-" * 60)
        print(text)
    else:
        preview = text[:600].replace("\n", " / ")
        print("-" * 60)
        print(f"字符数={len(text)}")
        print(f"预览：{preview or '(空)'}")
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
    log.info("前台采集启动（Ctrl+C 停止）")
    return _run_collector(cfg, log, duration=args.duration)


def _run_collector(cfg, log, duration: float = 0) -> int:
    from capture import Collector

    col = Collector(cfg, log)
    started = time_now()
    try:
        col.run(duration=duration)
    except KeyboardInterrupt:
        log.info("收到中断")
    finally:
        col.shutdown()
    log.info("采集结束，运行 %.0fs", time_now() - started)
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
        print(f"已生成待审内容，确认后才外发：{Path(sys.argv[0]).name} review show --date {day}")
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
        print("还没有生成看板，先运行：python wid.py report")
        return 1
    os.startfile(str(target))  # noqa: S606
    print(f"已打开 {target}")
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
        print("请二选一：--minutes N（最近 N 分钟）或 --day YYYY-MM-DD（整天）")
        return 2
    scope = f"最近 {args.minutes} 分钟" if args.minutes else f"{args.day} 一整天"
    if not args.yes:
        ans = _confirm(f"将永久删除{scope}的事件、截图、OCR 与派生日报，无法恢复。继续？[y/N] ")
        if ans is None:
            print(f"非交互场景请加 --yes 明确确认（计划删除：{scope}）")
            return 2
        if not ans:
            print("已取消，什么都没删")
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
    print("已删除。看板索引已刷新；已经发给模型服务的内容无法撤回。")
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
                print("add 需要 --start HH:MM --end HH:MM --label 文字")
                return 2
            manual_mod.add_offline(day, args.start, args.end, args.label, args.note or "")
        elif action == "override":
            if not args.seg:
                print("override 需要 --seg seg-03")
                return 2
            manual_mod.set_override(day, args.seg, args.verb or "", args.label or "", args.note or "")
        elif action == "notes":
            if args.text is None:
                print("notes 需要 --text")
                return 2
            manual_mod.set_notes(day, args.text)
        else:
            print("未知操作，可用：add / override / notes / show")
            return 2
    except ValueError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(f"已写入 {manual_mod.path(day)}")
    print(f"下次生成日报会自动套用：{Path(sys.argv[0]).name} report --date {day} --force")
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
            print(f"{day} 没有待审内容。先运行：{Path(sys.argv[0]).name} report --date {day}")
            return 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{FAIL} 待审文件读不出来：{exc}")
            return 1
        payload = str(data.get("payload") or "")
        print(f"日期   : {data.get('day', day)}")
        print(f"生成于 : {data.get('generated_at', '')}")
        print(f"覆盖   : {data.get('coverage', '')}")
        print(f"目标   : {data.get('model', '')} @ {data.get('base_url', '')}")
        print(f"字数   : {data.get('payload_chars', len(payload))}")
        print(f"预览文件: {path}")
        print("-" * 70)
        limit = max(200, int(args.chars))
        print(payload[:limit] + ("\n…（已截断，用 --chars 调整或直接打开预览文件）"
                                 if len(payload) > limit else ""))
        return 0

    if args.action == "approve":
        if not path.exists():
            print(f"{day} 没有待审内容，先运行 report 生成预览")
            return 1
        if not cfg.get("report.review_mode", False):
            print("当前未开启 review_mode；确认外发仍会正常生成日报")
        log = logging_setup.setup("review", console=True)
        res = report_mod.generate(day, cfg, log, reason="review", force=True,
                                  open_browser=False, approved=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("status") in ("ok", "degraded", "empty", "skipped") else 1

    if args.action == "discard":
        report_mod.discard_review(day)
        print(f"已丢弃 {day} 的待审内容（不会外发任何内容）")
        return 0

    print("未知操作，可用：show / approve / discard")
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
    print(f"{data['start']} – {data['end']}：有日报 {data['counted_days']} 天，"
          f"有效活动 {data['total_minutes']:.0f} 分钟")
    if data["missing"]:
        print(f"缺报：{'、'.join(data['missing'])}")
    print(f"已写入 {data['path']}")
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
    print(f"已导出 {res['day']} 分享版（{res['chars']} 字）：{res['path']}")
    print(f"已去除：{'、'.join(res['removed'])}")
    return 0


def cmd_init(args) -> int:
    import config as cfgmod
    import paths

    paths.ensure_dirs()
    actions = cfgmod.ensure_default()
    print(f"数据目录: {paths.ROOT}" + ("（便携模式）" if paths.describe()["portable"] else "（用户目录）"))
    print(f"配置文件: {paths.CONFIG_PATH}" + ("（已生成）" if actions.get("config") else "（已存在）"))
    print(f"API Key : {paths.API_KEY_PATH}（把 Key 粘进去，或设环境变量 WID_LLM_API_KEY）")
    print(f"引用绑定: {paths.BINDINGS_PATH}")
    print("\n下一步：")
    print("  1) 填 API Key（或 import 环境变量）")
    print("  2) wid.py doctor            自检")
    print("  3) wid.py autostart --install   开机自启")
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
            print(f"已在运行（pid={hb['pid']}，{age} 秒前还有心跳），不必重复启动")
            return 0

    pid = autostart.spawn_daemon()
    print(f"已拉起后台进程 pid={pid}，等待它写出第一份心跳 …")
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
            print(f"采集已开始：{cur.get('app') or '-'}，今日截图 {cur.get('shots_today', 0)} 张")
            print(f"数据目录：{paths.ROOT}")
            return 0
    print("12 秒内没等到心跳，可能启动失败了。排查：")
    log = max(paths.LOGS.glob("daemon-*.log"), key=lambda p: p.stat().st_mtime, default=None)
    print(f"  1) 看日志：{log}")
    print(f"  2) 前台跑一次看报错：{Path(sys.argv[0]).name} daemon")
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
    print("  What-I-Did  首次运行准备")
    print("=" * 66)
    print(f"  程序位置 : {me}")
    print(f"  数据目录 : {paths.ROOT}")
    print(f"  配置文件 : {paths.CONFIG_PATH}")
    if actions.get("config"):
        print("             （已按默认值生成，可直接编辑）")

    # --- Key
    key_path = paths.API_KEY_PATH
    print()
    if cfg.has_api_key():
        print("  [完成] 大模型 Key 已配置")
    else:
        print("  [待办] 还没配大模型 Key")
        print(f"         把 Key 粘进：{key_path}")
        print("         或设环境变量 WID_LLM_API_KEY（优先级更高）")
        print(f"         默认模型：{cfg.get('llm.model')} @ {cfg.get('llm.base_url')}")
        print("         不配也能用，日报会降级为纯时间线版。")

    # --- 自启
    import autostart

    try:
        st = autostart.status()
    except Exception:
        st = {"installed": False, "kind": "", "command": ""}
    name = me.name
    print()
    if st.get("installed"):
        print(f"  [完成] 开机自启已注册（{st.get('kind')}）")
    else:
        ans = _ask("  [询问] 让程序随登录自动启动？[Y/n] ")
        if ans is True:
            print("         " + autostart.install())
            st = autostart.status()
        elif ans is False:
            print(f"         已跳过。以后可运行：{name} autostart --install")
        else:
            print("  [待办] 还没注册开机自启")
            print(f"         运行：{name} autostart --install")
    if st.get("command"):
        print(f"         自启命令：{st['command']}")

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
        print("  [完成] 后台采集已在运行")
    else:
        ans = _ask("  [询问] 现在就启动后台采集？[Y/n] ")
        if ans is True:
            pid = autostart.spawn_daemon()
            print(f"         已启动 pid={pid}，几秒后可用 status 确认心跳")
        elif ans is False:
            print(f"         已跳过。以后可运行：{name} start")
        else:
            print(f"  [待办] 启动采集：{name} start")

    print()
    print(f"  复核：{name} doctor")
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
    p = argparse.ArgumentParser(prog="What-IDid", description="What-I-Did 自动工作记录与日报")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--json", action="store_true")
    d.add_argument("--full", action="store_true", help="附带跑一次真实 OCR")
    d.set_defaults(func=cmd_doctor)

    o = sub.add_parser("ocr-test", help="测试本地 OCR")
    o.add_argument("--screen", action="store_true")
    o.add_argument("--image")
    o.add_argument("--raw", action="store_true")
    o.set_defaults(func=cmd_ocr_test)

    c = sub.add_parser("collect", help="采集（前台调试）")
    c.add_argument("--once", action="store_true")
    c.add_argument("--duration", type=float, default=0)
    c.set_defaults(func=cmd_collect)

    dm = sub.add_parser("daemon", help="常驻采集（无窗口）")
    dm.set_defaults(func=cmd_daemon)

    st_ = sub.add_parser("start", help="在后台启动采集")
    st_.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", help="停止后台采集")
    sp.set_defaults(func=cmd_stop)

    r = sub.add_parser("report", help="生成某天日报")
    r.add_argument("--date")
    r.add_argument("--reason", default="manual")
    r.add_argument("--force", action="store_true")
    r.add_argument("--no-open", action="store_true")
    r.set_defaults(func=cmd_report)

    t = sub.add_parser("tick", help="计划任务入口")
    t.set_defaults(func=cmd_tick)

    s = sub.add_parser("status", help="当前状态")
    s.set_defaults(func=cmd_status)

    pa = sub.add_parser("pause", help="暂停采集")
    pa.add_argument("--minutes", type=int, default=0)
    pa.set_defaults(func=cmd_pause)

    re_ = sub.add_parser("resume", help="恢复采集")
    re_.set_defaults(func=cmd_resume)

    op = sub.add_parser("open", help="打开看板")
    op.add_argument("--date")
    op.set_defaults(func=cmd_open)

    cl = sub.add_parser("clean", help="清理过期数据")
    cl.add_argument("--dry-run", action="store_true")
    cl.set_defaults(func=cmd_clean)

    i = sub.add_parser("init", help="初始化目录与配置占位")
    i.set_defaults(func=cmd_init)

    su = sub.add_parser("setup", help="首次运行引导：生成配置并列出待办")
    su.set_defaults(func=cmd_setup)

    au = sub.add_parser("autostart", help="注册/移除开机自启（无需管理员）")
    au.add_argument("--install", action="store_true", help="注册自启动（默认行为）")
    au.add_argument("--remove", action="store_true", help="移除自启动")
    au.add_argument("--status", action="store_true", help="查看当前状态")
    au.set_defaults(func=cmd_autostart)

    fg_ = sub.add_parser("forget", help="遗忘最近 N 分钟或某一整天的数据（不可逆）")
    fg_.add_argument("--minutes", type=int, default=0, help="最近多少分钟")
    fg_.add_argument("--day", help="整天遗忘，格式 YYYY-MM-DD")
    fg_.add_argument("--yes", action="store_true", help="跳过交互确认（脚本/计划任务用）")
    fg_.set_defaults(func=cmd_forget)

    ma = sub.add_parser("manual", help="人工补录与修订")
    ma.add_argument("action", choices=["add", "override", "notes", "show"],
                    help="add 补录线下活动；override 改行为段标签；notes 加当天备注；show 查看")
    ma.add_argument("--date", help="YYYY-MM-DD，默认今天（按 04:00 分界）")
    ma.add_argument("--start", help="add 用：开始时间 HH:MM")
    ma.add_argument("--end", help="add 用：结束时间 HH:MM")
    ma.add_argument("--label", help="add/override 用：名称")
    ma.add_argument("--seg", help="override 用：seg-03")
    ma.add_argument("--verb", help="override 用：coding/debugging/testing/designing/reading/"
                                   "writing/meeting/communicating/operating/admin")
    ma.add_argument("--note", help="备注")
    ma.add_argument("--text", help="notes 用：当天备注正文")
    ma.set_defaults(func=cmd_manual)

    rv = sub.add_parser("review", help="发送前审核：查看/确认/丢弃待发内容")
    rv.add_argument("action", choices=["show", "approve", "discard"])
    rv.add_argument("--date", help="YYYY-MM-DD，默认今天")
    rv.add_argument("--chars", type=int, default=2000, help="show 时打印多少字")
    rv.set_defaults(func=cmd_review)

    se = sub.add_parser("search", help="跨日检索标题/屏幕文字/日报（只在本机扫描）")
    se.add_argument("query", nargs="+", help="关键词，多个词取全部命中")
    se.add_argument("--days", type=int, default=14, help="回溯天数，1–365")
    se.add_argument("--kind", help="title,ocr,report 的子集，默认全部")
    se.add_argument("--limit", type=int, default=200, help="总命中上限")
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=cmd_search)

    wk = sub.add_parser("weekly", help="周汇总：聚合已生成的日报")
    wk.add_argument("--anchor", help="以哪天所在的自然周为准，默认今天")
    wk.add_argument("--start", help="自定义起始日 YYYY-MM-DD")
    wk.add_argument("--end", help="自定义结束日 YYYY-MM-DD")
    wk.add_argument("--out", help="输出文件名（写到 reports/weekly/ 下）")
    wk.set_defaults(func=cmd_weekly)

    ex = sub.add_parser("export", help="导出可对外分享的日报（去截图/原文/本机路径）")
    ex.add_argument("--date", help="YYYY-MM-DD，默认今天")
    ex.add_argument("--no-evidence", action="store_true", help="连证据字段一起去掉")
    ex.add_argument("--out", help="输出文件名或绝对路径（只能写在 reports 内）")
    ex.set_defaults(func=cmd_export)

    g = sub.add_parser("gui", help="打开图形界面")
    g.add_argument("--shot", help="【开发用】启动后把窗口自身截图存到该路径并退出")
    g.add_argument("--page", help="【开发用】截图前切到哪个页面：dash/reports/settings/diag")
    g.add_argument("--day", help="【开发用】截图前在日报页选中哪一天")
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
