"""免管理员权限的自启动 + 出报定时任务。

三条路，优先计划任务：
1. 用户级计划任务：普通账户即可建，支持"登录即启 + 隐藏窗口 + 崩溃重启 +
   不限执行时长"，是最稳的形态。**注册有两条路**——优先 ScheduledTasks 模块的
   `Register-ScheduledTask`（只走 RPC），失败再退回 `schtasks /create`；有些安全
   策略把 `schtasks.exe` 直接列进程序黑名单，连执行都不给，此时只有前者能用。
2. 注册表 `HKCU\\...\\Run`：计划任务被安全软件拦掉时的兜底。
   缺点是无法崩溃重启。
3. 出报另有独立任务 `WhatIDid-Report`：每天 21:00 起每 20 分钟跑一次 tick（幂等），
   外加登录后 3 分钟补跑、次日 04:05 出终版。**它不依赖采集进程是否在跑**，
   所以哪怕托盘里退出了采集，日报照样会出。
"""
from __future__ import annotations

from i18n import t

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import paths

TASK_NAME = "WhatIDid-Recorder"
REPORT_TASK_NAME = "WhatIDid-Report"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "What-IDid"

CREATE_NO_WINDOW = 0x08000000


def _self_command(*extra: str) -> list[str]:
    """把自己重新拉起（daemon / tick）的命令行。

    合并成单个 exe 之后不再有专属的无窗口孪生程序：主程序本身就没有控制台，
    带上子命令启动即可，一样不会弹黑框。
    """
    return paths.self_command(*extra)


def _command_line(*extra: str) -> str:
    return subprocess.list2cmdline(_self_command(*extra))


def _user_id() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    if domain and user:
        return f"{domain}\\{user}"
    return user or os.environ.get("USERPROFILE", "")


def _exec_block(*extra: str) -> str:
    cmd = _self_command(*extra)
    exe = cmd[0]
    args = subprocess.list2cmdline(cmd[1:]) if len(cmd) > 1 else ""
    return (f"    <Exec>\n"
            f"      <Command>{exe}</Command>\n"
            f"      <Arguments>{args}</Arguments>\n"
            f"      <WorkingDirectory>{paths.ROOT}</WorkingDirectory>\n"
            f"    </Exec>")


_SETTINGS = """  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>{wake}</WakeToRun>
    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>{restart}</Count>
    </RestartOnFailure>
  </Settings>
"""

_SHELL = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{desc}</Description>
  </RegistrationInfo>
  <Triggers>
{triggers}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
{settings}
  <Actions Context="Author">
{actions}
  </Actions>
</Task>
"""


# ---------------------------------------------------------------- 计划任务
def _task_xml() -> str:
    """常驻采集：登录即起，崩溃每分钟重启，不限时长。"""
    return _SHELL.format(
        desc=t("What-I-Did work log recorder (per-user, no admin rights needed)"),
        triggers=("    <LogonTrigger>\n"
                  "      <Enabled>true</Enabled>\n"
                  f"      <UserId>{_user_id()}</UserId>\n"
                  "    </LogonTrigger>\n"
                  "    <BootTrigger>\n"
                  "      <Enabled>false</Enabled>\n"
                  "    </BootTrigger>"),
        user=_user_id(),
        settings=_SETTINGS.format(wake="false", limit="PT0S", restart=999),
        actions=_exec_block("daemon"),
    )


def _report_task_xml(cfg=None) -> str:
    """出报：每天 trigger_hour 起每 20 分钟跑一次 tick，登录后 3 分钟补跑一次，
    次日 04:05 再跑一次出终版。tick 本身幂等，多余触发不会重复出报。"""
    if cfg is None:
        import config as cfgmod

        cfg = cfgmod.load()
    hour = int(cfg.get("report.trigger_hour", 21))
    cutoff = int(cfg.get("app.day_cutoff_hour", 4))

    today = datetime.now().date()
    at_trigger = datetime.combine(today, datetime.min.time()).replace(hour=hour)
    at_final = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=cutoff, minute=5)

    triggers = "\n".join([
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        "      <Delay>PT3M</Delay>\n"
        f"      <UserId>{_user_id()}</UserId>\n"
        "    </LogonTrigger>",
        # 21:00 起每 20 分钟一次，持续 2 小时：网络抖动/模型限流都能自愈
        "    <CalendarTrigger>\n"
        f"      <StartBoundary>{at_trigger:%Y-%m-%dT%H:%M:%S}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByDay>\n"
        "        <DaysInterval>1</DaysInterval>\n"
        "      </ScheduleByDay>\n"
        "      <Repetition>\n"
        "        <Interval>PT20M</Interval>\n"
        "        <Duration>PT2H</Duration>\n"
        "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
        "      </Repetition>\n"
        "    </CalendarTrigger>",
        # 次日 04:05：这一天窗口关了，补一版收尾终版
        "    <CalendarTrigger>\n"
        f"      <StartBoundary>{at_final:%Y-%m-%dT%H:%M:%S}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByDay>\n"
        "        <DaysInterval>1</DaysInterval>\n"
        "      </ScheduleByDay>\n"
        "    </CalendarTrigger>",
    ])
    return _SHELL.format(
        desc=t("What-I-Did daily report (retries from {hour:02d}:00, catch-up at logon, "
                "final version the next day)", hour=hour),
        triggers=triggers,
        user=_user_id(),
        settings=_SETTINGS.format(wake="true", limit="PT2H", restart=3),
        actions=_exec_block("tick"),
    )


def _run_ps(script: str, timeout: int = 30):
    """跑一段 PowerShell（-EncodedCommand，免得跟引号/中文打架）。"""
    import base64

    body = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script
    enc = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW,
    )


def _ps_error(r) -> str:
    return ((r.stderr or "") + (r.stdout or "")).strip() or t("PowerShell call failed")


def _create_task_ps(name: str, xml: str) -> str:
    """走 ScheduledTasks 模块。`schtasks.exe` 会被部分安全策略直接拦掉（连执行都不给），
    而 `Register-ScheduledTask` 是 cmdlet，只走 RPC，通常还活着。"""
    import tempfile as _tf

    tmp = Path(_tf.gettempdir()) / f"whatidid_{name}.xml"
    tmp.write_text(xml, encoding="utf-16")
    path = str(tmp).replace("'", "''")
    try:
        r = _run_ps(
            "$ErrorActionPreference='Stop';"
            f"$x=Get-Content -Raw -LiteralPath '{path}';"
            # 声明里的 encoding 跟字符串形式不匹配，注册前去掉
            "$x=$x -replace '^\\s*<\\?xml[^>]*\\?>','';"
            f"Register-ScheduledTask -TaskName '{name}' -Xml $x -Force | Out-Null;"
            "'OK'"
        )
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0 or "OK" not in (r.stdout or ""):
        raise RuntimeError(_ps_error(r)[:300])
    return name


def _delete_task_ps(name: str) -> str:
    r = _run_ps(
        "$ErrorActionPreference='Stop';"
        f"Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false;"
        "'OK'"
    )
    if r.returncode != 0 or "OK" not in (r.stdout or ""):
        raise RuntimeError(_ps_error(r)[:300])
    return name


def _task_exists_ps(name: str) -> bool:
    r = _run_ps(
        f"if (Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue) {{'YES'}} else {{'NO'}}",
        timeout=20,
    )
    if r.returncode != 0:
        raise RuntimeError(_ps_error(r)[:200])
    return "YES" in (r.stdout or "")


def _create_task(name: str, xml: str) -> str:
    """两条路都试：先 cmdlet（能绕开 schtasks 黑名单），失败再退回 schtasks。"""
    errors = []
    try:
        return _create_task_ps(name, xml)
    except Exception as exc:
        errors.append(f"Register-ScheduledTask: {exc}")
    tmp = Path(tempfile.gettempdir()) / f"whatidid_{name}.xml"
    # schtasks 要求 UTF-16 编码的 XML
    tmp.write_text(xml, encoding="utf-16")
    try:
        r = subprocess.run(
            ["schtasks", "/Create", "/TN", name, "/XML", str(tmp), "/F"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        # 安全策略把 schtasks.exe 列进黑名单时，连进程都起不来
        errors.append(f"schtasks: {exc}")
        raise RuntimeError(t("; ").join(errors)[:400]) from exc
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        errors.append("schtasks: " + (r.stderr or r.stdout or t("creation failed")).strip())
        raise RuntimeError(t("; ").join(errors)[:400])
    return name


def _delete_task(name: str) -> str:
    try:
        return _delete_task_ps(name)
    except Exception as exc:
        first = str(exc)
    try:
        r = subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise RuntimeError(
            t("Register-ScheduledTask: {first}; schtasks: {second}", first=first,
              second=exc)[:400]) from exc
    if r.returncode != 0:
        raise RuntimeError(
            t("Register-ScheduledTask: {first}; schtasks: {second}", first=first,
              second=(r.stderr or r.stdout or t("deletion failed")).strip())[:400])
    return name


def task_exists(name: str = TASK_NAME) -> bool:
    try:
        return _task_exists_ps(name)
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", name],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
        return False
    return r.returncode == 0


def report_task_exists() -> bool:
    try:
        return task_exists(REPORT_TASK_NAME)
    except Exception:
        return False


# ---------------------------------------------------------------- 注册表兜底
def _run_read():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            try:
                val, _ = winreg.QueryValueEx(k, RUN_VALUE)
                return val
            except FileNotFoundError:
                return None
    except OSError:
        return None


def _run_write(cmd: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)


def _run_delete() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- 对外
def install(cfg=None) -> str:
    """注册采集 + 出报两条计划任务，采集失败退注册表。返回人话说明。"""
    msgs: list[str] = []

    # 1) 常驻采集
    try:
        _create_task(TASK_NAME, _task_xml())
        msgs.append(t("Capture task {name} created (starts at logon, hidden window, "
                      "restarts after a crash)", name=TASK_NAME))
        # 计划任务成功后清掉可能残留的 Run 项，避免双启
        if _run_read():
            _run_delete()
            msgs.append(t("Removed the stale registry startup entry"))
    except Exception as exc:
        detail = str(exc)[:120]
        try:
            _run_write(_command_line("daemon"))
            msgs.append(t("Scheduled task creation failed ({detail}); fell back to a registry "
                          "startup entry (note: it cannot restart after a crash)",
                          detail=detail))
        except Exception as exc2:
            msgs.append(t("Both the scheduled task and the registry entry failed: "
                          "{first} / {second}", first=detail, second=exc2))

    # 2) 出报定时：与采集任务解耦，采集停了也照样出报
    try:
        _create_task(REPORT_TASK_NAME, _report_task_xml(cfg))
        hour = int((cfg.get("report.trigger_hour", 21) if cfg else 21))
        msgs.append(t("Report task {name} created (retries every 20 minutes from "
                      "{hour:02d}:00, catch-up at logon, final version the next day)",
                      name=REPORT_TASK_NAME, hour=hour))
    except Exception as exc:
        msgs.append(t("Report task creation failed ({detail}); the resident process will "
                      "trigger reports when the time comes, but nothing is generated "
                      "while capture is not running", detail=str(exc)[:120]))

    return t("; ").join(msgs)


def uninstall() -> str:
    done = []
    for name in (TASK_NAME, REPORT_TASK_NAME):
        try:
            if task_exists(name):
                _delete_task(name)
                done.append(t("{name} deleted", name=name))
        except Exception as exc:
            done.append(t("Failed to delete {name}: {e}", name=name, e=exc))
    try:
        if _run_read():
            _run_delete()
            done.append(t("Registry startup entry removed"))
    except Exception as exc:
        done.append(t("Failed to remove the registry entry: {e}", e=exc))
    return t("; ").join(done) or t("No startup entry found")


def status() -> dict:
    try:
        would = _command_line("daemon")
    except Exception:
        would = ""
    out = {
        "installed": False, "kind": "", "command": would, "task_name": TASK_NAME,
        "report_task": False, "report_task_name": REPORT_TASK_NAME,
        "report_command": _command_line("tick"),
    }
    out["report_task"] = report_task_exists()
    try:
        if task_exists(TASK_NAME):
            out.update(installed=True, kind="scheduled_task")
            return out
    except Exception:
        pass
    try:
        val = _run_read()
        if val:
            out.update(installed=True, kind="registry_run", command=val)
    except Exception:
        pass
    return out


def spawn_daemon() -> int:
    """立刻把守护进程拉起来（脱离当前控制台），返回 pid。"""
    cmd = _self_command("daemon")
    flags = 0
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        if not paths.is_frozen():
            flags |= CREATE_NO_WINDOW
    p = subprocess.Popen(
        cmd, cwd=str(paths.ROOT), creationflags=flags,
        # 必须全部断开：否则子进程继承父进程的管道，调用方会一直等管道关闭
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return int(p.pid)
