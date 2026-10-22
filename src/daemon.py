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
            log.info("已拉起 tick 进程：%s", " ".join(cmd))
        return True
    except Exception as exc:
        if log:
            log.warning("拉起 tick 失败（%s），改为在常驻进程里直接跑", exc)
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


def _guard_allows(day: str, log) -> bool:
    """同一天别刷太频繁：5 分钟起步翻倍，最多 6 次；新进程重置一次机会。"""
    g = _load_guard()
    if g.get("boot") != BOOT_ID:
        g = {"boot": BOOT_ID, "days": {}}
    days = g.setdefault("days", {})
    rec = days.setdefault(day, {"attempts": 0, "ts": 0})
    if int(rec.get("attempts", 0)) >= MAX_ATTEMPTS:
        return False
    wait = min(3600, 300 * (2 ** min(int(rec.get("attempts", 0)), 3)))
    if time.time() - float(rec.get("ts", 0)) < wait:
        return False
    rec["attempts"] = int(rec.get("attempts", 0)) + 1
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
            log.warning("检查出报条件失败: %s", exc)
        return
    if not due:
        return

    day, why = due[0]
    if not _guard_allows(day, log):
        return
    if log:
        log.info("到点出报：%s（%s）", day, why)
    if not _spawn_tick(log):
        _run_tick_inline(log)


def run(cfg, log) -> int:
    cfg.require_valid()
    paths.ensure_dirs()
    with SingleInstance("WhatIDidDaemon") as lock:
        if not lock.acquired:
            if log:
                log.warning("已有采集进程在运行，本次退出")
            return 0

        sid, csid = winapi.session_id(), winapi.console_session_id()
        if sid != csid:
            log.warning("当前 Session=%s 与控制台 Session=%s 不一致，截图可能全黑"
                        "（计划任务必须勾选「只在用户登录时运行」）", sid, csid)

        log.info("daemon 启动 pid=%s session=%s", os.getpid(), sid)
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
                        _check_report_trigger(cfg, log)
                        retention.maybe_clean(cfg, log, CLEAN_STATE)
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
