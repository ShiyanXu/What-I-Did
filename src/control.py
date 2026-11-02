"""采集进程的启动/停止/暂停：CLI 与图形界面共用，避免两处判定逻辑走偏。"""
from __future__ import annotations

from i18n import join_list, t

import json

import paths
import status
import timeutil


def _is_ours(p) -> bool:
    """判断这个进程是不是「采集守护进程」。

    合并成单个 exe 之后名字已经不能区分了：界面和采集都叫 What-IDid.exe，
    只有命令行尾巴上的 daemon 子命令能分辨。误判的代价是把界面自己杀掉。
    """
    name = p.name().lower()
    try:
        cmd = [str(c).lower() for c in p.cmdline()]
    except Exception:
        cmd = []
    if not cmd:
        # 拿不到命令行时只认老版本的专属 exe 名，宁可放弃也不乱杀
        return name.startswith("whatididdaemon")
    if name.startswith("whatididdaemon"):
        return True
    if any("wid.py" in c for c in cmd):
        return "daemon" in cmd[1:]
    if name.startswith(("what-idid", "whatidid")):
        return "daemon" in cmd
    return False


def start() -> tuple[bool, str]:
    import autostart
    import config as cfgmod

    cfg = cfgmod.load()
    if cfg.error:
        return False, cfg.error

    hb = status.heartbeat()
    if hb["alive"]:
        return False, t("Capture is already running (pid={pid}, heartbeat {age}s ago)",
                 pid=hb["pid"], age=hb["age"])
    paths.ensure_dirs()
    try:
        pid = autostart.spawn_daemon()
    except Exception as exc:
        return False, t("Start failed: {e}", e=exc)
    return True, t("Capture process started, pid={pid}", pid=pid)


def _find_daemons() -> list:
    """扫一遍所有进程，找出真正的采集进程（界面进程会被 _is_ours 排除掉）。"""
    out = []
    try:
        import psutil

        for p in psutil.process_iter(["pid", "name"]):
            try:
                if _is_ours(p):
                    out.append(p)
            except Exception:
                continue
    except Exception:
        pass
    return out


def stop() -> tuple[bool, str]:
    pid = int(status.heartbeat()["pid"] or 0)
    targets = []
    if pid:
        try:
            import psutil

            if psutil.pid_exists(pid):
                p = psutil.Process(pid)
                if _is_ours(p):
                    targets.append(p)
        except Exception:
            pass
    if not targets:
        # 心跳里的 pid 可能是上一轮残留（被杀、崩溃），这时靠命令行兜底扫描，
        # 否则会出现「明明在采集，却说采集已退出」。
        targets = _find_daemons()
    if not targets:
        return True, t("The capture process is not running")

    killed = []
    for p in targets:
        try:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
            killed.append(str(p.pid))
        except Exception as exc:                     # noqa: BLE001
            return False, t("Failed to stop pid={pid}: {e}", pid=p.pid, e=exc)
    return True, t("Stopped the capture process, pid={pids}", pids=join_list(killed))


def pause(minutes: int = 0) -> tuple[bool, str]:
    from datetime import timedelta

    paths.ensure_dirs()
    until = timeutil.now() + timedelta(minutes=minutes) if minutes else None
    payload = {"paused_at": timeutil.iso(timeutil.now()),
               "until": timeutil.iso(until) if until else ""}
    paths.PAUSE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True, (t("Paused for {n} minutes; it resumes automatically", n=minutes) if minutes else t("Capture paused until you resume it"))


def resume() -> tuple[bool, str]:
    if paths.PAUSE_FILE.exists():
        paths.PAUSE_FILE.unlink()
        return True, t("Capture resumed")
    return True, t("Not paused right now")


def restart() -> tuple[bool, str]:
    ok, msg = stop()
    if not ok:
        return False, msg
    ok2, msg2 = start()
    return ok2, f"{msg}；{msg2}"
