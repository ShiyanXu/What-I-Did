"""统一隐私边界：采集许可、匿名事件、本地 OCR 地址和文本脱敏。"""
from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit

DEFAULT_BLOCKED_APPS = ["KeePass.exe", "1Password.exe", "Bitwarden.exe", "KeePassXC.exe"]
DEFAULT_SKIP_OCR_APPS = ["WeChat.exe", "Weixin.exe", "Telegram.exe", "QQ.exe"]
DEFAULT_BLOCKED_TITLE_PATTERNS = [
    r"(?i)(password|密码|支付|银行|身份证|私钥|助记词|无痕|InPrivate|Incognito)"
]


class PrivacyError(RuntimeError):
    """策略不完整或禁止当前操作；不得静默放行。"""

# 这些永远脱敏，不受配置影响（密钥类泄露代价太高）
_ALWAYS = [
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED:api_key]"),
    (re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:token]"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "[REDACTED:id_card]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[REDACTED:phone]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"), "[REDACTED:email]"),
]


def _basename(app: str) -> str:
    return (app or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower()


def current_config(cfg):
    """文件配置在安全边界重新检查；内存配置保持原样。"""
    return cfg.current() if hasattr(cfg, "current") else cfg


def require_valid(cfg) -> None:
    error = getattr(cfg, "error", "")
    if error:
        raise PrivacyError(error)
    for key in ("blocked_apps", "skip_ocr_apps", "blocked_title_patterns", "redact_patterns"):
        values = cfg.get(f"privacy.{key}", [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise PrivacyError(f"privacy.{key} 必须是非空字符串列表（无规则时用 []）")
        if key.endswith("patterns"):
            for i, pattern in enumerate(values, 1):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    # 不回显正则或 YAML 原文，配置里可能含敏感信息。
                    raise PrivacyError(f"privacy.{key} 第 {i} 条正则无效") from exc
    for key, default in (("store_screenshots", True), ("allow_llm", True)):
        if not isinstance(cfg.get(f"privacy.{key}", default), bool):
            raise PrivacyError(f"privacy.{key} 必须是 true 或 false")
    if cfg.get("privacy.capture_scope", "active_window") not in ("active_window", "all_screens"):
        raise PrivacyError("privacy.capture_scope 只能是 active_window 或 all_screens")


def require_local_ocr_url(url: str) -> str:
    """只允许回环 HTTP(S)，避免配置或重定向把截图送到远端。"""
    try:
        if not isinstance(url, str):
            raise ValueError("invalid OCR endpoint type")
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        local = host.lower() == "localhost"
        if not local:
            local = ipaddress.ip_address(host).is_loopback
        if (not local or parsed.scheme not in ("http", "https") or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError("invalid OCR endpoint")
        _ = parsed.port  # 校验端口，禁止请求阶段才发现格式错误。
        if host.lower() == "localhost":
            # 不依赖 hosts/DNS 把 localhost 解析到哪里，直接固定到 IPv4 回环。
            port = f":{parsed.port}" if parsed.port is not None else ""
            url = parsed._replace(netloc=f"127.0.0.1{port}").geturl()
    except (TypeError, ValueError) as exc:
        raise PrivacyError("OCR HTTP 地址必须是无凭据、无查询参数的本机回环 http(s) 地址") from exc
    return url


def is_paused() -> bool:
    import paths
    import timeutil

    if not paths.PAUSE_FILE.exists():
        return False
    try:
        info = json.loads(paths.PAUSE_FILE.read_text(encoding="utf-8"))
        until = info.get("until") or ""
        return not until or timeutil.parse(until) > timeutil.now()
    except (OSError, ValueError, TypeError, AttributeError):
        return True  # 不可读/损坏的暂停标记不能被当作“恢复”。


def capture_reason(cfg, fg: dict, idle: float, locked: bool, force: bool = False) -> str:
    """force 仅跳过空闲；暂停、锁屏、名单和错误配置没有调试后门。"""
    try:
        require_valid(cfg)
    except PrivacyError:
        return "config_error"
    if is_paused():
        return "paused"
    if locked:
        return "locked"
    if not fg.get("app"):
        return "window_unavailable"
    if is_blocked_app(fg.get("app", ""), cfg):
        return "blocked_app"
    if is_blocked_title(fg.get("title", ""), cfg):
        return "blocked_title"
    if not force and idle >= float(cfg.get("capture.idle_pause_sec", 300)):
        return "idle"
    return ""


def anonymous_event(event: dict, reason: str) -> dict:
    out = {k: event[k] for k in ("v", "ts", "day", "seq", "idle") if k in event}
    out.update(state=reason, kind="idle", content_policy="none")
    return out


def sanitize_event(event: dict, cfg) -> dict:
    """采集与旧数据重读共用：仅时长的权限不能因以后移除名单而扩大。"""
    require_valid(cfg)
    state = event.get("state", "active")
    if state != "active" or event.get("content_policy") == "none":
        return anonymous_event(event, state if state != "active" else "blocked_app")
    app, title = event.get("app", ""), event.get("title", "")
    if is_blocked_app(app, cfg):
        return anonymous_event(event, "blocked_app")
    if is_blocked_title(title, cfg):
        return anonymous_event(event, "blocked_title")
    if should_skip_ocr(app, cfg) or event.get("content_policy") == "metadata_only":
        out = {k: event[k] for k in ("v", "ts", "day", "seq", "idle") if k in event}
        out.update(state="active", kind="tick", app=redact(app, cfg), title="",
                   content_policy="metadata_only")
        return out
    out = dict(event)
    out["app"], out["title"] = redact(app, cfg), redact(title, cfg)
    out["content_policy"] = "allowed"
    return out


def is_blocked_app(app: str, cfg) -> bool:
    name = _basename(app)
    if not name:
        return False
    for x in cfg.get("privacy.blocked_apps", []) or []:
        if _basename(x) == name:
            return True
    return False


def should_skip_ocr(app: str, cfg) -> bool:
    name = _basename(app)
    for x in cfg.get("privacy.skip_ocr_apps", []) or []:
        if _basename(x) == name:
            return True
    return False


def is_blocked_title(title: str, cfg) -> bool:
    if not title:
        return False
    for pat in cfg.get("privacy.blocked_title_patterns", []) or []:
        try:
            if re.search(pat, title):
                return True
        except re.error as exc:
            raise PrivacyError("标题屏蔽正则无效，已停止内容处理") from exc
    return False


def redact(text: str, cfg, extra_secrets: tuple[str, ...] = ()) -> str:
    require_valid(cfg)
    if not text:
        return ""
    out = text
    # 先替换完整密钥，避免后续正则只抹掉密钥的一部分而使精确匹配失效。
    for secret in extra_secrets:
        if secret:
            out = out.replace(secret, "[REDACTED:secret]")
    for pat, repl in _ALWAYS:
        out = pat.sub(repl, out)
    for pat in cfg.get("privacy.redact_patterns", []) or []:
        try:
            out = re.sub(pat, "[REDACTED]", out)
        except re.error as exc:
            raise PrivacyError("脱敏正则无效，已停止内容处理") from exc
    return out


def redact_value(value, cfg, extra_secrets: tuple[str, ...] = ()):
    """递归处理正文值，保留结构字段名和数据类型。"""
    if isinstance(value, str):
        return redact(value, cfg, extra_secrets)
    if isinstance(value, dict):
        return {k: redact_value(v, cfg, extra_secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, cfg, extra_secrets) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v, cfg, extra_secrets) for v in value)
    return value


def redact_count(cfg) -> int:
    return len(_ALWAYS) + len(cfg.get("privacy.redact_patterns", []) or [])
