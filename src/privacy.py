"""统一隐私边界：采集许可、匿名事件、本地 OCR 地址和文本脱敏。"""
from __future__ import annotations

from i18n import t

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
            raise PrivacyError(t("privacy.{key} must be a list of non-empty strings "
                                 "(use [] when there are no rules)", key=key))
        if key.endswith("patterns"):
            for i, pattern in enumerate(values, 1):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    # 不回显正则或 YAML 原文，配置里可能含敏感信息。
                    raise PrivacyError(t("privacy.{key} entry {i} is not a valid regex",
                                         key=key, i=i)) from exc
    for key, default in (("store_screenshots", True), ("allow_llm", True)):
        if not isinstance(cfg.get(f"privacy.{key}", default), bool):
            raise PrivacyError(t("privacy.{key} must be true or false", key=key))
    if cfg.get("privacy.capture_scope", "active_window") not in ("active_window", "all_screens"):
        raise PrivacyError(t("privacy.capture_scope must be active_window or all_screens"))


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
        raise PrivacyError(t("The OCR HTTP URL must be a loopback http(s) address with no "
                             "credentials and no query parameters")) from exc
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
            raise PrivacyError(t("A title-blocking regex is invalid; content processing stopped")) from exc
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
            raise PrivacyError(t("A redaction regex is invalid; content processing stopped")) from exc
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


# ---------------------------------------------------------------- URL 归一化
# 1.3.0：来源链接。策略是「保留 query 但给敏感参数打码」，因为用户要的是"点得回去"，
# 而 token / session 这类参数一旦进报告就可能被分享出去。
_SECRET_PARAMS = {
    "token", "access_token", "refresh_token", "id_token", "authtoken",
    "sig", "signature", "password", "passwd", "pwd",
    "key", "apikey", "api_key", "secret", "client_secret",
    "session", "sessionid", "jsessionid", "sid",
    "code", "auth", "ticket", "sso_token", "cookie",
}
_TRACKING_EXACT = {
    "fbclid", "gclid", "dclid", "msclkid", "spm", "scm",
    "from", "ref", "ref_src", "share_token", "vd_source", "share_from",
}
_TRACKING_PREFIX = ("utm_",)
_REDACTED = "[REDACTED]"

# 形如随机串的参数值（长、含大小写与数字、无空格）疑似 token
_LOOKS_RANDOM = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z0-9_\-+/=]{32,}$")


def host_of(uri: str) -> str:
    try:
        return (urlsplit(uri or "").hostname or "").lower()
    except ValueError:
        return ""


def _split_query(uri: str) -> tuple[str, str, str]:
    """返回 (base, host, query)，base 为 scheme://host/path，不含 fragment。"""
    parts = urlsplit(uri or "")
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return "", "", ""
    base = f"{parts.scheme.lower()}://{parts.netloc}{parts.path or '/'}"
    return base, (parts.hostname or "").lower(), parts.query


def _is_tracking(name: str) -> bool:
    low = name.lower()
    return low in _TRACKING_EXACT or any(low.startswith(p) for p in _TRACKING_PREFIX)


def normalize_url(uri: str, cfg=None, *, keep_query: bool | None = None,
                  strip_tracking: bool | None = None) -> str:
    """规范化为可落盘的 URI：去 fragment、敏感参数打码、可选去追踪参数。

    - 非 http(s) 一律返回空串（javascript: / file:// 不走这条路，由 provider 分别处理）
    - keep_query=False 时整段 query 丢弃（分享版用）
    - 打码只动参数值，参数名保留，方便读者判断"这里原本有个 token"
    """
    base, _host, query = _split_query(uri)
    if not base:
        return ""
    if cfg is not None:
        if keep_query is None:
            keep_query = bool(cfg.get("url.keep_query", True))
        if strip_tracking is None:
            strip_tracking = bool(cfg.get("url.strip_tracking", False))
    if keep_query is None:
        keep_query = True
    if strip_tracking is None:
        strip_tracking = False
    if not keep_query or not query:
        return base

    out: list[str] = []
    for item in query.split("&"):
        if not item:
            continue
        name, _, value = item.partition("=")
        if not name:
            continue
        if strip_tracking and _is_tracking(name):
            continue
        if name.lower() in _SECRET_PARAMS and value:
            value = _REDACTED
        elif value and len(value) > 64 and _LOOKS_RANDOM.match(value):
            value = _REDACTED
        out.append(f"{name}={value}" if value else name)
    return base + ("?" + "&".join(out) if out else "")


def canonical_url(uri: str, cfg=None) -> str:
    """去重键：host + path + 排序后的参数（敏感值不参与，避免同页不同 token 被当成两页）。"""
    base, host, query = _split_query(uri)
    if not base:
        return ""
    pairs: list[tuple[str, str]] = []
    for item in query.split("&"):
        if not item:
            continue
        name, _, value = item.partition("=")
        if not name:
            continue
        if name.lower() in _SECRET_PARAMS or (value and len(value) > 64 and _LOOKS_RANDOM.match(value)):
            value = ""
        pairs.append((name.lower(), value))
    if not pairs:
        return base
    pairs.sort()
    tail = "&".join(f"{k}={v}" if v else k for k, v in pairs)
    return f"{base}?{tail}"
