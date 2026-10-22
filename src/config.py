"""配置加载：有效配置合并默认值；损坏配置显式报错并关闭内容处理。"""
from __future__ import annotations

import copy
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

import paths
import privacy

DEFAULTS: dict[str, Any] = {
    "app": {"day_cutoff_hour": 4, "timezone": "Asia/Shanghai"},
    "capture": {
        "interval_sec": 20,
        "jitter_sec": 3,
        "max_width": 1920,
        "jpeg_quality": 62,
        "thumb_width": 480,
        "keyframe_sec": 300,
        "idle_pause_sec": 300,
        "pause_on_locked": True,
        "min_free_gb": 3,
        "min_save_gap_sec": 90,
    },
    "dedupe": {
        "hamming_threshold": 4,
        "cell_grid": [12, 8],
        "cell_diff_dup": 0.02,
        "cell_diff_major": 0.22,
    },
    "ocr": {
        "engine": "auto",
        "umi_exe": "",
        "rapidocr": {"max_side": 1600, "min_score": 0.5},
        "warmup_on_start": True,
        "crop_active_window": True,
        "min_interval_same_window_sec": 45,
        "periodic_sample_sec": 180,
        "max_per_minute": 6,
        "max_per_day": 1200,
        "max_chars_per_shot": 6000,
        "timeout_sec": 90,
        "recover_probe_sec": 600,
        "http_url": "http://127.0.0.1:1224/api/ocr",
    },
    "privacy": {
        "store_screenshots": True,
        "capture_scope": "active_window",
        "allow_llm": True,
        "blocked_apps": list(privacy.DEFAULT_BLOCKED_APPS),
        "skip_ocr_apps": list(privacy.DEFAULT_SKIP_OCR_APPS),
        "blocked_title_patterns": list(privacy.DEFAULT_BLOCKED_TITLE_PATTERNS),
        "redact_patterns": [],
    },
    "retention": {"shots_days": 7, "events_days": 30, "report_excerpt_days": None,
                  "keyframes_days": 30, "max_total_gb": 20, "clean_hour": 4},
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_file": "config/api_key.txt",
        "api_key_env": "WID_LLM_API_KEY",
        "timeout_sec": 180,
        "max_retries": 3,
        "input_char_budget": 24000,
        "max_calls_per_report": 12,
        "temperature": 0.2,
    },
    "report": {
        "trigger_hour": 21,
        "catchup_days": 7,
        "min_active_minutes": 20,
        "open_browser": True,
        "review_mode": False,
        "max_upgrade_attempts": 3,
        "degraded_retry_hours": 12,
        "gap_threshold_min": 10,
        "max_block_min": 30,
        "max_session_min": 60,
        "refresh_gap_min": 90,
    },
    "obsidian": {"enabled": False, "vault_dir": ""},
    "refs": {
        "enabled": True,
        "providers": ["local_file", "obsidian", "url"],
        "allow_unresolved_links": False,
        "cache_ttl_days": 30,
        "resolve_timeout_sec": 15,
        "search_roots": [],
        "vault_dir": "",
        "vault_name": "",
        "kb": {"enabled": False, "scheme": "kb", "base_url": "", "url_template": ""},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(ValueError):
    """无效配置不能静默降级成没有隐私保护的默认配置。"""


class Config:
    def __init__(self, data: dict, raw_text: str = "", *, error: str = "", source_path: Path | None = None):
        self.data = data
        self.raw_text = raw_text
        self.error = error
        self.source_path = source_path

    def current(self) -> "Config":
        if self.source_path == paths.CONFIG_PATH:
            return load()
        return self

    def require_valid(self) -> None:
        if self.error:
            raise ConfigError(self.error)
        privacy.require_valid(self)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self.data
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    # 常用快捷方式
    @property
    def cutoff_hour(self) -> int:
        return int(self.get("app.day_cutoff_hour", 4))

    def api_key(self) -> str:
        if self.error:
            return ""
        env_name = self.get("llm.api_key_env", "WID_LLM_API_KEY")
        if env_name:
            v = os.environ.get(env_name, "").strip()
            if v:
                return v
        rel = self.get("llm.api_key_file", "")
        if rel:
            p = Path(rel)
            if not p.is_absolute():
                p = paths.ROOT / p
            try:
                return p.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""

    def has_api_key(self) -> bool:
        return bool(self.api_key())


_CACHE: Config | None = None
_CACHE_STAMP: tuple | None = None


def _parse_config(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"（第 {mark.line + 1} 行）" if mark is not None else ""
        raise ConfigError(f"配置 YAML 无法解析{where}，已停止采集与模型外发") from exc
    if not isinstance(data, dict) or not data:
        raise ConfigError("配置必须是非空 YAML 对象，已停止采集与模型外发")
    for section in DEFAULTS:
        if section in data and not isinstance(data[section], dict):
            raise ConfigError(f"配置段 {section} 必须是对象")

    def check_types(values: dict, defaults: dict, prefix: str = "") -> None:
        for key, value in values.items():
            if key not in defaults:
                continue
            expected = defaults[key]
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(expected, dict):
                if not isinstance(value, dict):
                    raise ConfigError(f"{name} 必须是对象")
                check_types(value, expected, name)
            elif expected is not None:
                if type(expected) in (int, float):
                    valid = type(value) in (int, float) and math.isfinite(value)
                else:
                    valid = isinstance(value, type(expected))
                if not valid:
                    raise ConfigError(f"{name} 的配置类型无效")

    check_types(data, DEFAULTS)
    merged = _deep_merge(DEFAULTS, data)
    cfg = Config(merged)
    privacy.require_valid(cfg)
    privacy.require_local_ocr_url(cfg.get("ocr.http_url", ""))
    if cfg.get("ocr.engine") not in ("auto", "umi_http", "umi_cli", "rapidocr", "none"):
        raise ConfigError("ocr.engine 不是支持的引擎")

    def number(key: str, minimum: float, maximum: float | None = None, integer: bool = False) -> None:
        value = cfg.get(key)
        valid = type(value) is int if integer else type(value) in (int, float)
        if (not valid or not math.isfinite(value) or value < minimum
                or (maximum is not None and value > maximum)):
            raise ConfigError(f"{key} 必须是有效范围内的{'整数' if integer else '数字'}")

    for key in ("app.day_cutoff_hour", "report.trigger_hour", "retention.clean_hour"):
        number(key, 0, 23, integer=True)
    for key in ("retention.shots_days", "retention.events_days", "ocr.max_per_day", "ocr.max_per_minute"):
        number(key, 0, integer=True)
    number("report.max_upgrade_attempts", 1, integer=True)
    if cfg.get("retention.report_excerpt_days") is not None:
        number("retention.report_excerpt_days", 0, integer=True)
    for key in ("capture.interval_sec", "capture.idle_pause_sec", "ocr.timeout_sec",
                "ocr.recover_probe_sec", "llm.timeout_sec", "retention.max_total_gb",
                "refs.cache_ttl_days", "report.degraded_retry_hours"):
        number(key, 0.001)
    for key in ("capture.jitter_sec", "capture.min_free_gb", "capture.min_save_gap_sec",
                "capture.keyframe_sec", "ocr.min_interval_same_window_sec", "ocr.periodic_sample_sec",
                "report.min_active_minutes", "report.refresh_gap_min"):
        number(key, 0)
    for key in ("capture.max_width", "capture.thumb_width", "ocr.max_chars_per_shot",
                "ocr.rapidocr.max_side", "llm.max_retries", "llm.input_char_budget"):
        number(key, 1, integer=True)
    number("capture.jpeg_quality", 1, 100, integer=True)
    number("ocr.rapidocr.min_score", 0, 1)
    grid = cfg.get("dedupe.cell_grid")
    if len(grid) != 2 or any(type(v) is not int or v <= 0 for v in grid):
        raise ConfigError("dedupe.cell_grid 必须是两个正整数")
    for key in ("refs.providers", "refs.search_roots"):
        if any(not isinstance(value, str) for value in cfg.get(key)):
            raise ConfigError(f"{key} 必须是字符串列表")
    for key in ("ocr.crop_active_window", "capture.pause_on_locked", "report.review_mode"):
        if not isinstance(cfg.get(key), bool):
            raise ConfigError(f"{key} 必须是 true 或 false")
    return merged


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _has_existing_runtime() -> bool:
    """只看文件存在性，不读私人正文；缺配置不能被误判成全新安装。"""
    return (paths.HEARTBEAT.exists() or any(paths.DATA.glob("????-??-??"))
            or any(paths.REPORTS.glob("*.json"))
            or (paths.API_KEY_PATH.exists() and paths.API_KEY_PATH.stat().st_size > 0))


def ensure_default(log=None) -> dict:
    """首次运行落地配置。绿色版拷到新机器后，双击即可用，不需要手工建文件。"""
    if not paths.CONFIG_PATH.exists() and _has_existing_runtime():
        raise ConfigError("已有运行数据但配置文件丢失，已停止内容处理；请恢复并检查原配置")
    paths.ensure_dirs()
    actions: dict[str, str] = {}

    if not paths.CONFIG_PATH.exists():
        src = paths.DEFAULT_CONFIG
        try:
            if src.exists():
                paths.CONFIG_PATH.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                actions["config"] = str(paths.CONFIG_PATH)
            else:
                paths.CONFIG_PATH.write_text(
                    "# 自动生成的最小配置（未找到随包模板）\n" + yaml.safe_dump(
                        DEFAULTS, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
                actions["config"] = str(paths.CONFIG_PATH)
        except OSError as exc:
            actions["config_error"] = str(exc)

    if not paths.API_KEY_PATH.exists():
        try:
            paths.API_KEY_PATH.write_text("", encoding="utf-8")
            actions["api_key"] = str(paths.API_KEY_PATH)
        except OSError:
            pass

    if not paths.BINDINGS_PATH.exists():
        try:
            paths.BINDINGS_PATH.write_text(
                "# 手工引用绑定：把某个项目/标题固定挂到某份文档上\n"
                "# - match: {title_contains: \"需求评审\"}\n"
                "#   uri: \"https://doc.example.com/xxx\"\n"
                "#   label: \"需求评审纪要\"\n[]\n",
                encoding="utf-8")
        except OSError:
            pass

    if log and actions.get("config"):
        log.info("首次运行：已生成配置 %s", actions["config"])
    return actions


# ---------------------------------------------------------------- 写回配置
_PLAIN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-/@:]*$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n"}


def _fmt_scalar(value: Any, prefer_quote: bool = False) -> str:
    """写成 YAML 标量。能用裸值就用裸值，保持和原文件同一副样子。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt_scalar(v, True) for v in value) + "]"
    text = str(value)
    if not text:
        return '""'
    if not prefer_quote and _PLAIN_RE.match(text) and text.lower() not in _RESERVED:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _find_comment(rest: str) -> int:
    """返回行内注释 # 的下标（引号里的不算），没有则 -1。"""
    quote = ""
    i = 0
    while i < len(rest):
        ch = rest[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or rest[i - 1] in " \t"):
            return i
        i += 1
    return -1


def _set_scalar(lines: list[str], section: str, key: str, value: Any) -> list[str]:
    """在 section 段内就地替换 key 的值。

    - 行内注释原地保留，且对齐到原来的列；
    - 列表写成块状（`- "x"`），并清掉旧的块状列表子行；
    - 找不到就插到段尾。全程不动其它行。
    """
    head_re = re.compile(rf"^{re.escape(section)}:\s*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if head_re.match(line)), None)
    if start is None:
        block = [f"{section}:", f"  {key}: {_fmt_scalar(value)}"]
        return lines + [""] + block

    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = i
            break

    row = re.compile(rf"^([ \t]+){re.escape(key)}:[ \t]*(.*)$")
    for i in range(start + 1, end):
        m = row.match(lines[i])
        if not m:
            continue
        indent = m.group(1)
        colon = len(indent) + len(key) + 1
        rest = lines[i][colon:]
        h = _find_comment(rest)
        comment = rest[h:] if h >= 0 else ""
        col = (colon + h) if h >= 0 else -1
        old_val = (rest[:h] if h >= 0 else rest).strip()
        prefer_quote = old_val.startswith(('"', "'"))

        if isinstance(value, (list, tuple)):
            # 清掉旧的块状列表子行（缩进更深且以 - 开头）
            j = i + 1
            while j < end:
                nxt = lines[j]
                if not nxt.strip():
                    break
                deeper = (len(nxt) - len(nxt.lstrip())) > len(indent)
                if not deeper or not nxt.lstrip().startswith("-"):
                    break
                j += 1
            if j > i + 1:
                del lines[i + 1:j]
            if not value:
                body = f"{indent}{key}: []"
                if comment:
                    body += (" " * max(1, col - len(body)) if col > len(body) else " ") + comment
                lines[i] = body
            else:
                head = f"{indent}{key}:"
                if comment:
                    head += (" " * max(1, col - len(head)) if col > len(head) else " ") + comment
                block = [head] + [f"{indent}  - {_fmt_scalar(x, True)}" for x in value]
                lines[i:i + 1] = block
            return lines

        body = f"{indent}{key}: {_fmt_scalar(value, prefer_quote)}"
        if comment:
            if col > len(body):
                body += " " * (col - len(body))
            else:
                body += " "
            body += comment
        lines[i] = body
        return lines

    pos = end
    while pos > start + 1 and not lines[pos - 1].strip():
        pos -= 1
    if isinstance(value, (list, tuple)) and value:
        block = [f"  {key}:"] + [f"    - {_fmt_scalar(x, True)}" for x in value]
        lines[pos:pos] = block
    else:
        lines.insert(pos, f"  {key}: {_fmt_scalar(value)}")
    return lines


def save_values(updates: dict[str, Any]) -> list[str]:
    """把 {section.key: value} 写回 config.yaml。只改动目标行的值，注释、空行、顺序全部保留。

    GUI 保存设置走这里，避免 yaml.dump 把整份带中文注释的配置重排成机器味。
    """
    if paths.CONFIG_PATH.exists():
        text = paths.CONFIG_PATH.read_text(encoding="utf-8")
        _parse_config(text)  # 不用安全占位值覆盖用户的损坏配置，先修复原文件。
    elif _has_existing_runtime() or (_CACHE is not None and _CACHE.source_path == paths.CONFIG_PATH):
        raise ConfigError("原配置已丢失，拒绝用表单占位值替代；请先恢复原配置")
    elif paths.DEFAULT_CONFIG.exists():
        text = paths.DEFAULT_CONFIG.read_text(encoding="utf-8")
    else:
        text = yaml.safe_dump(DEFAULTS, allow_unicode=True, sort_keys=False)

    lines = text.splitlines()
    written: list[str] = []
    for dotted, value in updates.items():
        parts = dotted.split(".")
        if len(parts) != 2:
            raise ValueError(f"只支持 section.key 形式：{dotted}")
        lines = _set_scalar(lines, parts[0], parts[1], value)
        written.append(dotted)

    new_text = "\n".join(lines).rstrip() + "\n"
    _parse_config(new_text)
    paths.ensure_dirs()
    _write_atomic(paths.CONFIG_PATH, new_text)
    load(reload=True)
    return written


def save_api_key(key: str) -> None:
    """写入 Key 文件。环境变量优先，所以这里只负责落盘。"""
    paths.ensure_dirs()
    text = (key or "").strip()
    _write_atomic(paths.API_KEY_PATH, text + ("\n" if text else ""))
    load(reload=True)


def load(reload: bool = False) -> Config:
    global _CACHE, _CACHE_STAMP
    try:
        stat = paths.CONFIG_PATH.stat()
        stamp = (str(paths.CONFIG_PATH), stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = (str(paths.CONFIG_PATH), None, None)
    if _CACHE is not None and not reload and _CACHE_STAMP == stamp:
        return _CACHE
    text = ""
    try:
        if not paths.CONFIG_PATH.exists():
            if _CACHE is not None and _CACHE.source_path == paths.CONFIG_PATH:
                raise ConfigError("运行中的配置文件已丢失，已停止采集与模型外发")
            ensure_default()
        text = paths.CONFIG_PATH.read_text(encoding="utf-8")
        data = _parse_config(text)
        _CACHE = Config(data, text, source_path=paths.CONFIG_PATH)
    except (OSError, UnicodeError, ConfigError, privacy.PrivacyError) as exc:
        # 保留可供 GUI 展示的结构，但不能凭这些占位值继续采集、OCR 或发送。
        safe = copy.deepcopy(DEFAULTS)
        safe["privacy"].update(store_screenshots=False, allow_llm=False)
        safe["ocr"]["engine"] = "none"
        error = (str(exc) if isinstance(exc, (ConfigError, privacy.PrivacyError))
                 else "无法读取配置文件，已停止采集与模型外发")
        _CACHE = Config(safe, text, error=error, source_path=paths.CONFIG_PATH)
    # 使用读前的签名；若读取期间发生编辑，下一次调用会重新加载而不是缓存旧内容。
    _CACHE_STAMP = stamp
    return _CACHE
