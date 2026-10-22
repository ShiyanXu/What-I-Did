"""日志：文件滚动 + 控制台。Windows 控制台编码兜底。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

import paths

_LOGGERS: dict[str, logging.Logger] = {}


def _fix_console_encoding() -> None:
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def setup(component: str = "app", level: int | str = logging.INFO, console: bool = True) -> logging.Logger:
    if component in _LOGGERS:
        return _LOGGERS[component]
    paths.ensure_dirs()
    _fix_console_encoding()

    logger = logging.getLogger(f"wid.{component}")
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S")

    logfile = paths.LOGS / f"{component}-{datetime.now():%Y%m}.log"
    fh = RotatingFileHandler(logfile, maxBytes=5 * 1024 * 1024, backupCount=4, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if console and sys.stderr is not None:
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _LOGGERS[component] = logger
    return logger


def get(component: str = "app") -> logging.Logger:
    return _LOGGERS.get(component) or setup(component)
