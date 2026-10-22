"""开发用：把 GUI 回归需要的样本环境一次准备好。

    python .build/seed_gui_home.py .tmp_wid_guiN

做四件事：初始化配置 → 造两天样本 → 打开审核模式并指向本地假模型 → 制造一份待审预览。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI_PY = sys.executable
EXE = ROOT / ".build" / "stage" / "What-IDid" / "What-IDid.exe"
DAYS = ("2026-10-18", "2026-10-19")


def run(args, home: Path, quiet: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ, WID_HOME=str(home), NO_PROXY="127.0.0.1,localhost")
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    return subprocess.run(args, cwd=str(ROOT), env=env, capture_output=quiet, text=True)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python .build/seed_gui_home.py <临时目录>")
        return 2
    home = Path(sys.argv[1]).resolve()
    if not home.name.startswith(".tmp_wid"):
        print("拒绝在非 .tmp_wid* 目录上造数据，避免动到真实记录")
        return 2
    home.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 初始化 {home}")
    r = run([str(EXE), "setup"], home)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        return 1

    print("[2/4] 造两天样本")
    for day in DAYS:
        r = run([GUI_PY, str(ROOT / "scripts" / "dev_fixture.py"), "--day", day, "--mock"], home)
        # 日志可能直接落到控制台而不进管道，所以按副作用判断，别看 stdout
        made = (home / "reports" / f"{day}.json").exists()
        if not made:
            print(r.stdout, r.stderr)
            return 1
        print(f"      {day} ok")

    print("[3/4] 打开审核模式，指向本地假模型")
    cfg = home / "config.yaml"
    text = cfg.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(\s*)review_mode:.*$", r"\1review_mode: true", text)
    if "review_mode" not in text:
        text = text.replace("report:", "report:\n  review_mode: true", 1)
    text = re.sub(r"(?m)^(\s*)base_url:.*$", r"\1base_url: http://127.0.0.1:8799/v1", text, count=1)
    cfg.write_text(text, encoding="utf-8")
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "api_key.txt").write_text("mock-key", encoding="utf-8")

    print("[4/4] 制造一份待审预览（不调用模型）")
    r = run([str(EXE), "report", "--date", DAYS[0], "--force"], home)
    if "awaiting_review" not in (r.stdout or ""):
        print(r.stdout, r.stderr)
        return 1
    print("      待审预览已生成")
    print(f"\n完成。接下来：\n  WID_HOME={home} {GUI_PY} .build/gui_regression.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
