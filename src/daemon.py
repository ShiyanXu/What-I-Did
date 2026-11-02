"""常驻采集守护：单实例、看门狗、21:00 兜底触发、每日清理。"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import capture
import paths
import tick as tick_mod
import retention
import timeutil
import winapi
from locking import SingleInstance

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
TRIGGER_GUARD = paths.STATE / "auto_trigger.json"
CLEAN_STATE = paths.STATE / "last_clean.txt"

MAX_ATTEMPTS = 6          # 同一天最多自动尝试几次（新进程会重置）
BOOT_ID = f"{os.getpid()}-{int(time.time())}"


def pythonw() -> str:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def _load_guard() -> dict:
    try:
        return json.loads(TRIGGER_GUARD.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_guard(data: dict) -> None:
    try:
        TRIGGER_GUARD.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _spawn_tick(log) -> bool:
    """把 tick 拉成独立进程（隔离失败，不让出报把采集拖死）。

    打包后 `paths.self_command` 返回 exe 自己，源码模式才带 wid.py。
    """
    cmd = paths.self_command("tick")
    try:
        subprocess.Popen(
            cmd,
            cwd=str(paths.ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        )
        if log:
            log.info("Spawned the tick process: %s", " ".join(cmd))
        return True
    except Exception as exc:
        if log:
            log.warning("Could not spawn tick (%s); running it inside the daemon instead", exc)
        return False


def _run_tick_inline(log) -> None:
    """兜底：拉不起子进程时，在后台线程里直接出报。

    出报是网络 IO 为主（模型调用），单独线程不会阻塞采样；
    report 自己有单实例锁，不会和别处的 tick 打架。
    """
    def work() -> None:
        try:
            import config as cfgmod

            cfg = cfgmod.load(reload=True)
            tick_mod.run(cfg, log, limit=2)
        except Exception:
            log.exception("内联 tick 失败")

    threading.Thread(target=work, name="tick-inline", daemon=True).start()


def _guard_allows(day: str, why: str, log) -> bool:
    """同一天别刷太频繁：5 分钟起步翻倍，最多 6 次；有新版产出就重新计数。

    计的是「连续无进展的尝试」：`state` 里的 revision 一旦上涨，说明上一轮真出了
    新版，配额归零。旧实现只看拉起次数，21:00 起每 90 分钟一版增量正好 6 次，
    到 04:00 补终版时配额已耗尽，一次失手就整天不再重试（2026-11-01 即此）。

    终版额外开一道口子：它是唯一要交付的版本，配额用完后仍按 1 小时退避继续试，
    宁可多试几次也不能整天缺终版。
    """
    g = _load_guard()
    if g.get("boot") != BOOT_ID:
        g = {"boot": BOOT_ID, "days": {}}
    days = g.setdefault("days", {})
    rec = days.setdefault(day, {"attempts": 0, "ts": 0, "rev": 0})

    rev = tick_mod.revision_of(day)
    if rev > int(rec.get("rev", 0)):
        rec["attempts"] = 0
        rec["rev"] = rev

    attempts = int(rec.get("attempts", 0))
    if attempts >= MAX_ATTEMPTS and why != tick_mod.WHY_FINAL:
        return False

    wait = min(3600, 300 * (2 ** min(attempts, 3)))
    if time.time() - float(rec.get("ts", 0)) < wait:
        return False
    rec["attempts"] = attempts + 1
    rec["ts"] = time.time()
    # 只留最近 14 天，防止文件无限长
    cutoff = timeutil.workday_of(timeutil.now(), 4)
    keep = sorted(days)[-14:]
    g["days"] = {k: days[k] for k in keep if k <= cutoff or k == day}
    _save_guard(g)
    return True


def _check_report_trigger(cfg, log) -> None:
    """21:00 兜底：即使出报计划任务没装成，常驻进程也能把日报出了。

    判断交给 tick.pending，它会把"今天没出"和"昨天错过"一起算进来，
    所以晚上没开机、第二天才开，也能补出来。
    """
    try:
        due = tick_mod.pending(cfg, timeutil.now(), days=2)
    except Exception as exc:
        if log:
            log.warning("Failed to check the report condition: %s", exc)
        return
    if not due:
        return

    # 逐个看：最新那天的配额用完时，别把后面还在等的老日子一起饿死
    for day, why in due:
        if not _guard_allows(day, why, log):
            continue
        if log:
            log.info("Report due: %s (%s)", day, tick_mod.why_text(why))
        if not _spawn_tick(log):
            _run_tick_inline(log)
        return


def run(cfg, log) -> int:
    cfg.require_valid()
    paths.ensure_dirs()
    with SingleInstance("WhatIDidDaemon") as lock:
        if not lock.acquired:
            if log:
                log.warning("Another recorder is already running; exiting")
            return 0

        sid, csid = winapi.session_id(), winapi.console_session_id()
        if sid != csid:
            log.warning("Session=%s does not match the console session %s; "
                        "screenshots may come out black (the scheduled task must run "
                        "only when the user is logged on)", sid, csid)

        log.info("daemon started pid=%s session=%s", os.getpid(), sid)
        col = capture.Collector(cfg, log)
        col.start()

        stop = threading.Event()
        last_progress = [time.time()]
        max_seconds = float(os.environ.get("WID_DAEMON_MAX_SECONDS", "0") or 0)
        started = time.time()

        def watchdog() -> None:
            while not stop.wait(30):
                idle = time.time() - last_progress[0]
                if idle > 120:
                    log.error("看门狗：主循环 %.0fs 无进展，主动退出让计划任务重启", idle)
                    os._exit(75)

        threading.Thread(target=watchdog, name="watchdog", daemon=True).start()

        next_at = time.time()
        last_bg = 0.0
        try:
            while True:
                now = time.time()
                if max_seconds and now - started >= max_seconds:
                    log.info("达到测试时长上限，退出")
                    break
                if now >= next_at:
                    try:
                        col.sample()
                    except Exception as exc:
                        log.exception("采样异常: %s", exc)
                    last_progress[0] = time.time()
                    jitter = random.uniform(-col.jitter, col.jitter) if col.jitter else 0
                    next_at = now + max(5.0, col.interval + jitter)
                if now - last_bg >= 30:
                    last_bg = now
                    try:
                        # 先清理再出报：两者共用 WhatIDid.Report 锁，反过来的话
                        # 刚拉起的 tick 会在启动的 1 秒里撞上清理，直接躺平成 busy。
                        retention.maybe_clean(cfg, log, CLEAN_STATE)
                        _check_report_trigger(cfg, log)
                    except Exception as exc:
                        log.warning("后台任务异常: %s", exc)
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("收到中断信号")
        finally:
            stop.set()
            col.shutdown()
            log.info("daemon 退出")
    return 0
