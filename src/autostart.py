"""免管理员权限的自启动 + 出报定时任务。

三条路，优先计划任务：
1. 用户级计划任务（`schtasks /create` 不带 /RL HIGHEST）：普通账户即可建，
   支持"登录即启 + 隐藏窗口 + 崩溃重启 + 不限执行时长"，是最稳的形态。
2. 注册表 `HKCU\\...\\Run`：计划任务被安全软件拦掉时的兜底。
   缺点是无法崩溃重启。
3. 出报另有独立任务 `WhatIDid-Report`：每天 21:00 起每 20 分钟跑一次 tick（幂等），
   外加登录后 3 分钟补跑、次日 04:05 出终版。**它不依赖采集进程是否在跑**，
   所以哪怕托盘里退出了采集，日报照样会出。
"""
from __future__ import annotations

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
        desc="What-I-Did 工作过程自动记录（用户级，无需管理员）",
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
        desc="What-I-Did 日报生成（每天 21:00 起重试、登录补跑、次日终版）",
        triggers=triggers,
        user=_user_id(),
        settings=_SETTINGS.format(wake="true", limit="PT2H", restart=3),
        actions=_exec_block("tick"),
    )


def _create_task(name: str, xml: str) -> str:
    tmp = Path(tempfile.gettempdir()) / f"whatidid_{name}.xml"
    # schtasks 要求 UTF-16 编码的 XML
    tmp.write_text(xml, encoding="utf-16")
    try:
        r = subprocess.run(
            ["schtasks", "/Create", "/TN", name, "/XML", str(tmp), "/F"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW,
        )
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "schtasks 创建失败").strip())
    return name


def _delete_task(name: str) -> str:
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", name, "/F"],
        capture_output=True, text=True, timeout=20,
        encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "schtasks 删除失败").strip())
    return name


def task_exists(name: str = TASK_NAME) -> bool:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", name],
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW,
    )
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
        msgs.append(f"采集任务 {TASK_NAME} 已创建（登录即启动，隐藏窗口，崩溃自动重启）")
        # 计划任务成功后清掉可能残留的 Run 项，避免双启
        if _run_read():
            _run_delete()
            msgs.append("已清除旧的注册表启动项")
    except Exception as exc:
        detail = str(exc)[:120]
        try:
            _run_write(_command_line("daemon"))
            msgs.append(f"计划任务创建失败（{detail}），已改用注册表启动项"
                        f"（注意：此方式无法崩溃自启）")
        except Exception as exc2:
            msgs.append(f"计划任务与注册表都失败：{detail} / {exc2}")

    # 2) 出报定时：与采集任务解耦，采集停了也照样出报
    try:
        _create_task(REPORT_TASK_NAME, _report_task_xml(cfg))
        hour = int((cfg.get("report.trigger_hour", 21) if cfg else 21))
        msgs.append(f"出报任务 {REPORT_TASK_NAME} 已创建（每天 {hour:02d}:00 起每 20 分钟重试、"
                    f"登录补跑、次日终版）")
    except Exception as exc:
        msgs.append(f"出报任务创建失败（{str(exc)[:120]}）：日报改由常驻进程在到点时兜底触发，"
                    f"但采集进程没跑时不会自动出报")

    return "；".join(msgs)


def uninstall() -> str:
    done = []
    for name in (TASK_NAME, REPORT_TASK_NAME):
        try:
            if task_exists(name):
                _delete_task(name)
                done.append(f"{name} 已删除")
        except Exception as exc:
            done.append(f"删除 {name} 失败：{exc}")
    try:
        if _run_read():
            _run_delete()
            done.append("注册表启动项已删除")
    except Exception as exc:
        done.append(f"删除注册表项失败：{exc}")
    return "；".join(done) or "没有找到已注册的自启动项"


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
