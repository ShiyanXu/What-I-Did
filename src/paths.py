"""路径单一来源。所有模块都从这里取目录，禁止在别处拼路径。

设计要点（为了"拷到任意 Windows 机器就能跑"）
- 只读资源（模板、内置 OCR 模型）走 `_bundle_root()`：源码模式是项目根，
  PyInstaller 冻结后是 `sys._MEIPASS` 临时解包目录。
- 可写数据（配置、截图、日报）走 `ROOT`，按三级优先：
  `WID_HOME` 环境变量 → 程序所在目录（便携）→ `%LOCALAPPDATA%\\What-IDid`。
  程序目录不可写时（装在 Program Files、或从只读介质运行）自动降级，不会静默失败。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "What-IDid"
PORTABLE_MARKER = "portable.marker"   # 用户手动放置即强制便携模式（数据留在程序目录）


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _bundle_root() -> Path:
    """只读资源根：模板、内置模型、默认配置模板。"""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".wid_write_test_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_root() -> Path:
    env = os.environ.get("WID_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    exe_dir = _exe_dir()
    if (exe_dir / PORTABLE_MARKER).exists() and _writable(exe_dir):
        return exe_dir
    if _writable(exe_dir):
        # 绿色版默认把数据放在程序旁边，符合"解压即用"的直觉
        return exe_dir
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    fallback = Path(local) / APP_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


ROOT = _resolve_root()
BUNDLE = _bundle_root()

CONFIG_PATH = ROOT / "config.yaml"
CONFIG_DIR = ROOT / "config"
BINDINGS_PATH = CONFIG_DIR / "bindings.yaml"
API_KEY_PATH = CONFIG_DIR / "api_key.txt"

DATA = ROOT / "data"
SHARED = DATA / "_shared"
REPORTS = ROOT / "reports"
WEB = ROOT / "web"
WEB_DAY = WEB / "day"
LOGS = ROOT / "logs"
STATE = ROOT / "state"
DAY_STATE = STATE / "day"
LOCKS = STATE / "locks"
OCR_TMP = STATE / "ocr_tmp"

TEMPLATES = BUNDLE / "src" / "templates"
DEFAULT_CONFIG = BUNDLE / "config.default.yaml"
BUNDLED_MODELS = BUNDLE / "models"

PAUSE_FILE = STATE / "PAUSED"
HEARTBEAT = STATE / "heartbeat.json"
OBSIDIAN_INDEX = STATE / "obsidian_index.json"
REF_CACHE = SHARED / "refs.jsonl"


def self_command(*extra: str) -> list[str]:
    """拉起自己的命令行，源码模式与打包模式都正确。

    打包后 `ROOT` 只是数据目录，里面没有 wid.py，所以绝不能拼 `ROOT / "wid.py"`；
    冻结时直接复用 exe 本身（它带子命令）。
    """
    if is_frozen():
        return [sys.executable, *extra]
    entry = Path(__file__).resolve().parent.parent / "wid.py"
    return [sys.executable, str(entry), *extra]


def ensure_dirs() -> None:
    for p in (DATA, SHARED, REPORTS, WEB, WEB_DAY, LOGS, STATE, DAY_STATE, LOCKS, OCR_TMP, CONFIG_DIR):
        p.mkdir(parents=True, exist_ok=True)


def day_dir(day: str) -> Path:
    return DATA / day


def shots_dir(day: str) -> Path:
    return day_dir(day) / "shots"


def thumbs_dir(day: str) -> Path:
    return day_dir(day) / "thumbs"


def events_path(day: str) -> Path:
    return day_dir(day) / "events.jsonl"


def ocr_path(day: str) -> Path:
    return day_dir(day) / "ocr.jsonl"


def report_md(day: str) -> Path:
    return REPORTS / f"{day}.md"


def report_json(day: str) -> Path:
    return REPORTS / f"{day}.json"


def day_state(day: str) -> Path:
    return DAY_STATE / f"{day}.json"


def web_day(day: str) -> Path:
    return WEB_DAY / f"{day}.html"


def rel_to_root(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def describe() -> dict:
    return {
        "root": str(ROOT),
        "bundle": str(BUNDLE),
        "frozen": is_frozen(),
        "portable": ROOT == _exe_dir(),
        "config": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "templates": str(TEMPLATES),
        "templates_exists": TEMPLATES.exists(),
    }
