"""注册 / 卸载 Windows 计划任务（命令行入口，实际逻辑在 src/autostart.py）。

留这个脚本只是为了在没装界面、或想先看 XML 时用。真正的注册逻辑只有一份：
`src/autostart.py`，打包后 `What-IDid.exe autostart` 走的是同一条路。

用法（在项目根目录）：
    python scripts/install_tasks.py            # 注册两条任务：采集 + 出报
    python scripts/install_tasks.py --dry-run  # 只打印 XML，不注册
    python scripts/install_tasks.py --uninstall
    python scripts/install_tasks.py --status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import autostart  # noqa: E402
import config as cfgmod  # noqa: E402
import paths  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印将注册的 XML")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    cfg = cfgmod.load(reload=True)
    print(f"程序目录 : {paths.ROOT}")
    print(f"可执行   : {paths.self_command('daemon')[0]}")
    print(f"运行账户 : {autostart._user_id()}")

    if args.status:
        print(json.dumps(autostart.status(), ensure_ascii=False, indent=2))
        return 0

    if args.uninstall:
        print(autostart.uninstall())
        return 0

    if args.dry_run:
        for name, xml in ((autostart.TASK_NAME, autostart._task_xml()),
                          (autostart.REPORT_TASK_NAME, autostart._report_task_xml(cfg))):
            print(f"\n===== {name} =====")
            print(xml)
        print("\n（dry-run，未注册）")
        return 0

    print(autostart.install(cfg))
    print("\n自检要点：")
    print("  1. 两条任务都必须是「只在用户登录时运行」，否则截图会全黑")
    print("  2. WhatIDid-Recorder 的执行时限应为「无限」（常驻采集）")
    print("  3. WhatIDid-Report 是 21:00 起每 20 分钟一次 + 登录补跑 + 次日 04:05 终版")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
