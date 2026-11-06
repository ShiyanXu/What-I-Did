"""隐私边界自测：不需要模型 Key、不联网、不读真实记录。

与 `check_boundaries.py` 分工：
- `check_boundaries.py` 管功能边界（空关键词、日期越界、损坏 JSON…）
- 本脚本管"策略不能被绕过"（注入伪造凭据、关闭外发、名单收紧、
  无效配置、OCR 地址、裁剪失败、构建保护）

前置：无。所有样本都是内存里现造的合成事件，不写 `data/`；只有"无效配置"
一项会在临时目录写一个坏 YAML。返回码 0 表示全部通过。

    python scripts\\check_privacy.py
"""
from __future__ import annotations

import copy
import re
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import blocks as blocks_mod  # noqa: E402
import browse as browse_mod  # noqa: E402
import capture  # noqa: E402
import config as cfgmod  # noqa: E402
import digest  # noqa: E402
import llm  # noqa: E402
import paths  # noqa: E402
import privacy  # noqa: E402
import timeutil  # noqa: E402

DAY = "2026-10-18"

# 注入用的伪造凭据：值本身是假的，形态必须能被内置规则识别
FAKE_KEY = "sk-live" + "A1b2C3d4E5f6G7h8I9j0"
FAKE_PHONE = "13800138000"
FAKE_MAIL = "leak@example.com"
FAKE_ID = "110101199003071234"

results: list[tuple[str, str]] = []


def check(cond, label: str, detail: str = "") -> None:
    results.append((label, "ok" if cond else (detail or "断言失败")))


def expect_raises(fn, exc, label: str) -> None:
    try:
        fn()
    except exc:
        results.append((label, "ok"))
    except Exception as e:                      # noqa: BLE001
        results.append((label, f"错异常 {type(e).__name__}: {e}"))
    else:
        results.append((label, "没有报错"))


def make_cfg(**overrides) -> cfgmod.Config:
    """合成一份配置：默认值 + 指定覆盖，不读磁盘。"""
    data = copy.deepcopy(cfgmod.DEFAULTS)
    for dotted, value in overrides.items():
        cur = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
    return cfgmod.Config(data)


def start_ts(minutes: int):
    return timeutil.parse_day(DAY) + timedelta(hours=9, minutes=minutes)


def sample_events(count: int = 6, title: str = "", app: str = "Code.exe") -> list[dict]:
    """造一段连续 20 分钟的活动事件（供分块用）。"""
    out = []
    for i in range(count):
        ts = start_ts(i * 4)
        out.append({
            "v": 1, "ts": timeutil.iso(ts), "day": DAY, "seq": i + 1,
            "app": app, "title": title, "pid": 1000, "rect": [0, 0, 800, 600],
            "idle": 0, "state": "active", "kind": "tick", "content_policy": "allowed",
        })
    return out


# ---------------------------------------------------------------- 1 文本脱敏
def test_redact(cfg) -> None:
    raw = f"密钥 {FAKE_KEY} 手机 {FAKE_PHONE} 邮箱 {FAKE_MAIL} 身份证 {FAKE_ID}"
    out = privacy.redact(raw, cfg)
    for secret, name in ((FAKE_KEY, "api_key"), (FAKE_PHONE, "phone"),
                         (FAKE_MAIL, "email"), (FAKE_ID, "id_card")):
        check(secret not in out, f"redact 抹掉 {name}")
    check("[REDACTED:api_key]" in out, "redact 保留可读占位符")

    blank = privacy.redact("", cfg)
    check(blank == "", "redact 空串返回空串")
    check(privacy.redact(None, cfg) == "", "redact None 不抛异常")

    nested = privacy.redact_value({"a": [raw], "n": 3, "b": {"c": raw}}, cfg)
    check(FAKE_KEY not in str(nested) and nested["n"] == 3, "redact_value 递归且保留非字符串")


# ---------------------------------------------------------------- 2 采集端脱敏
def test_block_sanitize(cfg) -> None:
    events = sample_events(title=f"日报 - {FAKE_KEY} {FAKE_MAIL}")
    ocr_map = {"o1": {"id": "o1", "status": "ok",
                      "text": f"截图里出现的 {FAKE_PHONE} 和 {FAKE_ID}"}}
    events[0]["ocr"] = "o1"
    bl = blocks_mod.build_blocks(events, ocr_map, cfg)
    check(bool(bl), "合成事件能切出活动块")
    joined = " ".join(b.title + b.text for b in bl)
    check(FAKE_KEY not in joined and FAKE_MAIL not in joined, "块标题落盘前已脱敏")
    check(FAKE_PHONE not in joined and FAKE_ID not in joined, "块内 OCR 文本落盘前已脱敏")


# ---------------------------------------------------------------- 3 模型出口
def test_digest_exit(cfg) -> None:
    events = sample_events(title=f"修复登录 - {FAKE_KEY}")
    ocr_map = {"o1": {"id": "o1", "status": "ok", "text": f"联系 {FAKE_MAIL} / {FAKE_PHONE}"}}
    events[0]["ocr"] = "o1"
    bl = blocks_mod.build_blocks(events, ocr_map, cfg)
    stats = blocks_mod.summarize(bl)
    ws, we = timeutil.day_window(DAY, cfg.cutoff_hour)
    payload, meta = digest.build_payload(DAY, bl, stats, ws, we, [], cfg)
    check(FAKE_KEY not in payload, "模型输入不含密钥原文")
    check(FAKE_MAIL not in payload and FAKE_PHONE not in payload, "模型输入不含邮箱/手机号")
    check(meta["chars"] == len(payload), "meta.chars 与实际长度一致")

    # 引用标签同样要脱敏（refs 走同一条出口）
    refs = [{"ref_id": "r-1", "kind": "url", "label": f"文档 {FAKE_KEY}", "uri": "https://x"}]
    payload2, _ = digest.build_payload(DAY, bl, stats, ws, we, [], cfg, refs=refs)
    check(FAKE_KEY not in payload2, "模型输入不含引用标签里的密钥")


def test_request_guard(cfg) -> None:
    """关闭外发 / 配置无效时，请求数为零。"""
    calls = {"n": 0}
    real_post = llm.requests.Session.post

    def fake_post(self, *a, **kw):              # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("本不应发出请求")

    llm.requests.Session.post = fake_post
    try:
        off = make_cfg(**{"privacy.allow_llm": False})
        client = llm.LLMClient(off, None)
        check(not client.configured(), "关闭外发后 configured() 为 False")
        expect_raises(lambda: client.chat_json("s", "u"), llm.LLMError, "关闭外发后 chat_json 拒绝")
        check(calls["n"] == 0, "关闭外发后请求数为 0", f"实际 {calls['n']}")

        broken = cfgmod.Config(copy.deepcopy(cfgmod.DEFAULTS), error="配置无效（测试）")
        client2 = llm.LLMClient(broken, None)
        check(not client2.configured(), "配置无效时 configured() 为 False")
        expect_raises(lambda: client2.chat_json("s", "u"), llm.LLMError, "配置无效时拒绝请求")
        check(calls["n"] == 0, "配置无效时请求数为 0", f"实际 {calls['n']}")

        # 出口再脱敏：即使调用方把原文塞进 messages，也要在这一层被抹掉
        on_cfg = make_cfg(**{"llm.api_key_file": ""})
        on_cfg.set("llm.base_url", "https://example.invalid/v1")
        on_client = llm.LLMClient(on_cfg, None)
        guarded = on_client._private_payload({"messages": [{"role": "user", "content": f"x {FAKE_KEY}"}]})
        check(FAKE_KEY not in str(guarded), "HTTP 出口二次脱敏")
    finally:
        llm.requests.Session.post = real_post


# ---------------------------------------------------------------- 4 采集许可
def test_capture_reason(cfg) -> None:
    fg = {"app": "Code.exe", "title": "main.py", "rect": (0, 0, 800, 600)}
    check(privacy.capture_reason(cfg, fg, 0, False) == "", "正常前台允许采集")
    check(privacy.capture_reason(cfg, fg, 10_000, False) == "idle", "空闲返回 idle")
    check(privacy.capture_reason(cfg, fg, 10_000, False, force=True) == "",
          "force 只跳过空闲")
    check(privacy.capture_reason(cfg, fg, 0, True) == "locked", "锁屏返回 locked")
    check(privacy.capture_reason(cfg, fg, 0, True, force=True) == "locked",
          "force 不能绕过锁屏")
    check(privacy.capture_reason(cfg, {}, 0, False) == "window_unavailable", "无前台窗口")

    blocked = make_cfg(**{"privacy.blocked_apps": ["KeePass.exe"]})
    fg2 = {"app": "C:\\Tools\\KeePass.exe", "title": "vault", "rect": (0, 0, 800, 600)}
    check(privacy.capture_reason(blocked, fg2, 0, False) == "blocked_app", "名单应用被屏蔽")
    check(privacy.capture_reason(blocked, fg2, 0, False, force=True) == "blocked_app",
          "debug force 不能绕过名单")

    titled = make_cfg(**{"privacy.blocked_title_patterns": [r"(?i)password"]})
    fg3 = {"app": "chrome.exe", "title": "My Password Vault", "rect": (0, 0, 800, 600)}
    check(privacy.capture_reason(titled, fg3, 0, False) == "blocked_title", "标题正则命中")

    broken = cfgmod.Config(copy.deepcopy(cfgmod.DEFAULTS), error="配置无效（测试）")
    check(privacy.capture_reason(broken, fg, 0, False) == "config_error", "配置无效停止采集")

    event = {"v": 1, "ts": timeutil.iso(start_ts(0)), "day": DAY, "seq": 1,
             "app": "KeePass.exe", "title": "vault", "idle": 0, "state": "active"}
    anon = privacy.anonymous_event(event, "blocked_app")
    check(anon["content_policy"] == "none", "匿名事件标记 content_policy=none")
    check("title" not in anon and "app" not in anon, "匿名事件不含应用与标题")


# ---------------------------------------------------------------- 5 名单收紧后旧数据
def test_old_data_policy(cfg) -> None:
    strict = make_cfg(**{"privacy.skip_ocr_apps": ["WeChat.exe"]})
    old = sample_events(count=5, title="客户群消息", app="WeChat.exe")
    sanitized = [privacy.sanitize_event(e, strict) for e in old]
    check(all(e.get("content_policy") == "metadata_only" for e in sanitized),
          "仅时长名单：旧事件标 metadata_only")
    check(all(not e.get("title") for e in sanitized), "仅时长名单：标题被清空")

    bl = blocks_mod.build_blocks(old, {}, strict)
    check(bool(bl) and all(b.title == "" for b in bl), "重分块不再恢复标题")
    check(all(b.content_policy == "metadata_only" for b in bl), "块保留 metadata_only 标记")

    # 幂等：再跑一遍不会因为 content_policy 已存在而被当成 allowed
    again = privacy.sanitize_event(sanitized[0], strict)
    check(again.get("content_policy") == "metadata_only" and not again.get("title"),
          "sanitize_event 幂等")

    # 同一段数据去掉名单也不能"复活"内容（content_policy=none 优先）
    loose = make_cfg(**{"privacy.skip_ocr_apps": []})
    frozen = privacy.sanitize_event({"v": 1, "ts": timeutil.iso(start_ts(0)), "day": DAY,
                                     "seq": 1, "app": "WeChat.exe", "title": "客户群消息",
                                     "state": "active", "content_policy": "none"}, loose)
    check(not frozen.get("title"), "已判定无内容的事件不会因移除名单而恢复")


# ---------------------------------------------------------------- 6 OCR 地址
def test_ocr_url() -> None:
    ok = privacy.require_local_ocr_url("http://127.0.0.1:1224/api/ocr")
    check(ok == "http://127.0.0.1:1224/api/ocr", "回环地址通过")
    local = privacy.require_local_ocr_url("http://localhost:1224/api/ocr")
    check(local.startswith("http://127.0.0.1:1224"), "localhost 固定到 127.0.0.1", local)
    for url, label in (
        ("http://192.168.1.9:1224/api/ocr", "非回环地址被拒"),
        ("https://ocr.example.com/api/ocr", "公网域名被拒"),
        ("http://user:pw@127.0.0.1:1224/api/ocr", "带凭据的地址被拒"),
        ("http://127.0.0.1:1224/api/ocr?x=1", "带查询参数的地址被拒"),
        ("ftp://127.0.0.1:1224/api/ocr", "非 http(s) 被拒"),
        ("not-a-url", "非法地址被拒"),
    ):
        expect_raises(lambda u=url: privacy.require_local_ocr_url(u),
                      privacy.PrivacyError, label)


# ---------------------------------------------------------------- 7 无效配置
def test_invalid_config(tmp: Path) -> None:
    original = cfgmod.paths.CONFIG_PATH
    cfgmod.paths.CONFIG_PATH = tmp / "config.yaml"
    cfgmod._CACHE = None
    cfgmod._CACHE_STAMP = None
    try:
        cfgmod.paths.CONFIG_PATH.write_text("privacy:\n  blocked_apps: [\n", encoding="utf-8")
        cfg = cfgmod.load(reload=True)
        check(bool(cfg.error), "坏 YAML 使配置进入错误态")
        check(cfg.get("privacy.store_screenshots") is False, "错误态关闭截图")
        check(cfg.get("privacy.allow_llm") is False, "错误态关闭外发")
        check(cfg.get("ocr.engine") == "none", "错误态关闭 OCR")
        check(cfg.api_key() == "", "错误态不返回 Key")

        cfgmod.paths.CONFIG_PATH.write_text(
            "privacy:\n  blocked_title_patterns: ['(']\n", encoding="utf-8")
        cfg2 = cfgmod.load(reload=True)
        # 提示语跟着界面语言走，中英都要认
        msg = str(cfg2.error or "")
        check(bool(msg) and ("正则" in msg or "regex" in msg.lower()),
              "非法隐私正则使配置失效",
              cfg2.error)
    finally:
        cfgmod.paths.CONFIG_PATH = original
        cfgmod._CACHE = None
        cfgmod._CACHE_STAMP = None


def test_config_ranges() -> None:
    text = (ROOT / "config.default.yaml").read_text(encoding="utf-8")
    cfgmod._parse_config(text)
    check(True, "默认配置可解析")
    for bad, label in (
        (text.replace("review_mode: false", 'review_mode: "false"'), "review_mode 类型错误被拒"),
        (text.replace("max_upgrade_attempts: 3", "max_upgrade_attempts: 0"), "max_upgrade_attempts<1 被拒"),
        (text.replace("capture_scope: active_window", "capture_scope: full"), "capture_scope 非法值被拒"),
        (text.replace('http_url: "http://127.0.0.1:1224/api/ocr"',
                      'http_url: "http://10.0.0.2:1224/api/ocr"'), "非回环 OCR 地址被拒"),
    ):
        # 这两项由 privacy.require_valid / require_local_ocr_url 拦下，抛的是 PrivacyError
        expect_raises(lambda t=bad: cfgmod._parse_config(t),
                      (cfgmod.ConfigError, privacy.PrivacyError), label)


# ---------------------------------------------------------------- 8 裁剪失败
def test_grab_no_fallback(cfg) -> None:
    real_grab = capture.ImageGrab.grab
    calls = {"n": 0}

    def counting_grab(*a, **kw):                # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("裁剪失败时不应抓屏")

    capture.ImageGrab.grab = counting_grab
    try:
        expect_raises(lambda: capture.grab_frame(cfg, {"rect": (10, 10, 40, 40)}),
                      privacy.PrivacyError, "窗口过小时拒绝截图")
        expect_raises(lambda: capture.grab_frame(cfg, {}),
                      privacy.PrivacyError, "窗口矩形缺失时拒绝截图")
        check(calls["n"] == 0, "拒绝截图时一次都没抓屏", f"实际 {calls['n']} 次")
    finally:
        capture.ImageGrab.grab = real_grab


# ---------------------------------------------------------------- 9 构建保护
def test_build_guard(tmp: Path) -> None:
    try:
        import build_dist
    except Exception as exc:                    # noqa: BLE001
        results.append(("导入 build_dist", f"跳过：{type(exc).__name__}"))
        return

    with_data = tmp / "portable"
    (with_data / "config").mkdir(parents=True)
    (with_data / "config" / "api_key.txt").write_text("x", encoding="utf-8")
    (with_data / "What-IDid.exe").write_bytes(b"x")
    (with_data / "_internal").mkdir()
    expect_raises(lambda: build_dist._layout(with_data), build_dist.BuildSafetyError,
                  "含运行数据的旧目录拒绝覆盖")

    clean = tmp / "clean"
    clean.mkdir()
    (clean / "What-IDid.exe").write_bytes(b"x")
    (clean / "_internal").mkdir()
    (clean / "_internal" / "a.dll").write_bytes(b"abc")
    build_dist._layout(clean)
    check(True, "纯产物目录允许布局检查")
    expect_raises(lambda: build_dist.verify_artifact(clean), build_dist.BuildSafetyError,
                  "无安全清单的旧版拒绝自动清理")

    # 有清单但内容被改过：不能当成"可清理的纯产物"
    import json as _json

    listed = tmp / "listed"
    shutil.copytree(clean, listed)
    (listed / build_dist.MANIFEST).write_text(
        _json.dumps(build_dist._inventory(listed), ensure_ascii=False), encoding="utf-8")
    build_dist.verify_artifact(listed)
    check(True, "清单一致的产物通过校验")
    (listed / "_internal" / "a.dll").write_bytes(b"tampered")
    expect_raises(lambda: build_dist.verify_artifact(listed), build_dist.BuildSafetyError,
                  "内部文件被改动后拒绝自动清理")

    incomplete = tmp / "incomplete"
    incomplete.mkdir()
    (incomplete / "What-IDid.exe").write_bytes(b"")
    (incomplete / "_internal").mkdir()
    expect_raises(lambda: build_dist._layout(incomplete), build_dist.BuildSafetyError,
                  "空 exe 视为不完整产物")


def test_excerpt_policy() -> None:
    """报告里的原始 OCR 摘录有独立保留期：0 表示不写，null 跟随事件保留期。"""
    import retention

    now = timeutil.parse_day(DAY) + timedelta(hours=22)
    zero = make_cfg(**{"retention.report_excerpt_days": 0})
    check(not retention.keep_report_excerpts(DAY, zero, now), "0 天不写摘录")

    follow = make_cfg(**{"retention.report_excerpt_days": None, "retention.events_days": 30})
    check(retention.keep_report_excerpts(DAY, follow, now), "null 跟随事件期：当天保留")
    old = (timeutil.parse_day(DAY) - timedelta(days=40)).strftime("%Y-%m-%d")
    check(not retention.keep_report_excerpts(old, follow, now), "超过事件期不再保留")

    three = make_cfg(**{"retention.report_excerpt_days": 3})
    four_days_ago = (timeutil.parse_day(DAY) - timedelta(days=4)).strftime("%Y-%m-%d")
    check(not retention.keep_report_excerpts(four_days_ago, three, now), "自定义 3 天生效")
    check(retention.report_excerpt_days(three) == 3, "report_excerpt_days 读值正确")


def test_source_links(cfg) -> None:
    """1.3.0：来源链接不能把敏感参数带进报告，也不能把历史库搬进数据目录。"""
    masked = privacy.normalize_url("https://ex.com/p?token=abc&q=hi#frag", cfg)
    check("token=[REDACTED]" in masked, "source link: secret param is masked", masked)
    check("frag" not in masked, "source link: fragment is dropped", masked)
    check("q=hi" in masked, "source link: benign param is kept", masked)

    for name in ("session", "password", "code", "signature", "access_token"):
        got = privacy.normalize_url(f"https://ex.com/p?{name}=VALUE", cfg)
        check("VALUE" not in got, f"source link: {name} value removed", got)

    long_token = "Aa1" * 40
    got = privacy.normalize_url(f"https://ex.com/p?k={long_token}", cfg)
    check(long_token not in got, "source link: long random value is masked", got)

    check(privacy.normalize_url("javascript:alert(1)", cfg) == "",
          "source link: non-http scheme is rejected")
    check("?" not in privacy.normalize_url("https://ex.com/p?a=1", cfg, keep_query=False),
          "source link: share version drops the query")

    one = privacy.canonical_url("https://ex.com/p?a=1&token=AAA", cfg)
    two = privacy.canonical_url("https://ex.com/p?token=BBB&a=1", cfg)
    check(one and one == two, "source link: canonical ignores secret values and order", f"{one} vs {two}")

    # 历史库只允许出现在 state/tmp 与浏览器原目录下，绝不能落进 data/
    check(str(paths.browse_tmp()).startswith(str(paths.STATE)),
          "source link: history copies live under state/", str(paths.browse_tmp()))
    src = (ROOT / "src" / "browse.py").read_text(encoding="utf-8")
    check("DATA" not in src or "paths.DATA" not in src,
          "source link: browse.py never writes into the data folder")

    check(privacy.domain_source("mp.weixin.qq.com") == "https://mp.weixin.qq.com/",
          "source link: domain-only source is built from the host")
    check(privacy.domain_source("") == "", "source link: empty host yields no link")

    check(blocks_mod.classify("WeChat.exe", "某文章 - 微信公众平台") == "web",
          "source link: WeChat article page is classified as web")
    check(blocks_mod.classify("WeChat.exe", "与张三的聊天") == "comm",
          "source link: WeChat chat window stays comm")
    # 飞书同时在 MEET_APPS 里，改判若写在 MEET 分支之后会永远不生效
    check(blocks_mod.classify("Feishu.exe", "项目方案 - 飞书文档") == "web",
          "source link: Feishu doc page is classified as web")
    check(blocks_mod.classify("Feishu.exe", "项目资源协调 - 飞书") == "meet",
          "source link: Feishu meeting window stays meet")

    # 地址栏通道只认带行坐标的记录；没有 lines 的旧数据必须跳过而不是猜
    import types

    fake = types.SimpleNamespace(ocr_ids=["o1"], text="", title="x", app="chrome.exe")
    got = browse_mod.address_bar_url(
        fake, {"o1": {"lines": [{"y0": 20, "y1": 40, "text": "https://ex.com/a?token=abc"}],
                      "height": 800}}, cfg)
    check(got.startswith("https://ex.com/a") and "abc" not in got,
          "source link: address bar line is read and masked", got)
    check(browse_mod.address_bar_url(fake, {"o1": {"text": "no coords"}}, cfg) == "",
          "source link: records without line coordinates are skipped")
    low = types.SimpleNamespace(ocr_ids=["o1"], text="", title="x", app="chrome.exe")
    check(browse_mod.address_bar_url(
        low, {"o1": {"lines": [{"y0": 700, "y1": 760, "text": "https://ex.com/a"}],
                     "height": 800}}, cfg) == "",
          "source link: a URL outside the top strip is not an address bar")

    hint = types.SimpleNamespace(text="网页由 mp.weixin.qq.com 提供", title="x")
    check(browse_mod.domain_hint(hint, cfg) == "https://mp.weixin.qq.com/",
          "source link: domain hint comes from the OCR text", browse_mod.domain_hint(hint, cfg))
    check(browse_mod.domain_hint(types.SimpleNamespace(text="", title="x"), cfg) == "",
          "source link: no OCR text means no domain hint")


def test_config_nested_write() -> None:
    """设置页的三个开关是三层键（browse.history.enabled），写盘必须支持多层。

    回归背景：`save_values` 曾只接受 `section.key` 两段，三层直接抛 ValueError，
    界面上点保存就会失败——而这条路径没有别的测试覆盖。
    """
    import yaml

    src = ["browse:", "  address_bar:            # 说明", "    enabled: true",
           "url:", "  keep_query: true"]
    out = cfgmod._set_path(list(src), ["browse", "history", "enabled"], True)
    data = yaml.safe_load("\n".join(out))
    check((data.get("browse", {}).get("history", {}) or {}).get("enabled") is True,
          "config write: three-level key is written", "\n".join(out))
    check(any("说明" in line for line in out), "config write: sibling comment survives")
    check(data.get("browse", {}).get("address_bar", {}).get("enabled") is True,
          "config write: sibling value untouched")

    out2 = cfgmod._set_path(list(src), ["url", "keep_query"], False)
    check(yaml.safe_load("\n".join(out2))["url"]["keep_query"] is False,
          "config write: two-level key still works")

    out3 = cfgmod._set_path(list(src), ["brand", "new", "deep"], 5)
    check(yaml.safe_load("\n".join(out3))["brand"]["new"]["deep"] == 5,
          "config write: a missing section is appended as a whole tree")

    try:
        cfgmod._set_path(list(src), ["lonely"], 1)
    except ValueError:
        check(True, "config write: a one-part path is rejected")
    else:
        check(False, "config write: a one-part path is rejected", "no error raised")


def test_chat_evidence() -> None:
    """聊天窗口不能自己变成"我沟通过了"。

    回归背景：11-05 的日报把"只看了群会话列表"写成"扫群消息"、并据此推出
    "等别人确认排期"的待跟进。提示词里原本就有"别人的消息一律不算"，但没管到
    behaviors 的 label/detail 和 followups/tomorrow，兜底路径还把 comm 直接映射成
    communicating。两边一起补：这里锁住本地兜底，下面锁住提示词里的硬要求。
    """
    import behavior as behavior_mod

    sess = behavior_mod.Session(
        id="s1",
        start=timeutil.parse("2026-11-05T12:29:46+08:00"),
        end=timeutil.parse("2026-11-05T12:35:18+08:00"),
    )
    sess.category = "comm"
    sess.titles = ["迪普AP5000-2C 中磊交流群"]
    check(behavior_mod.guess_verb(sess) == "reading",
          "chat evidence: a chat window falls back to reading, not communicating",
          behavior_mod.guess_verb(sess))
    check(behavior_mod.local_fill(sess)["verb"] == "reading",
          "chat evidence: the local filler does not claim a conversation")

    # 标题里只有"群/消息"时也不能猜成沟通
    sess2 = behavior_mod.Session(
        id="s2",
        start=timeutil.parse("2026-11-05T18:33:49+08:00"),
        end=timeutil.parse("2026-11-05T19:14:26+08:00"),
    )
    sess2.category = "other"
    sess2.titles = ["迪普四款产品ODM 2.0项目需求817"]
    check(behavior_mod.guess_verb(sess2) != "communicating",
          "chat evidence: a title mentioning a group is never communicating")

    prompt = digest.build_system_prompt()
    check("看群消息，未发言" in prompt,
          "chat evidence: the prompt prescribes the read-only wording")
    check("追不到就删掉" in prompt,
          "chat evidence: followups may not come from other people's messages")
    check("未发言" in digest._USER_SCHEMA,
          "chat evidence: the schema asks the model to say it did not speak")


def main() -> int:
    cfg = make_cfg()
    tmp = Path(tempfile.mkdtemp(prefix="wid-privacy-"))
    try:
        test_redact(cfg)
        test_block_sanitize(cfg)
        test_digest_exit(cfg)
        test_request_guard(cfg)
        test_capture_reason(cfg)
        test_old_data_policy(cfg)
        test_ocr_url()
        test_invalid_config(tmp)
        test_config_ranges()
        test_excerpt_policy()
        test_grab_no_fallback(cfg)
        test_build_guard(tmp)
        test_source_links(cfg)
        test_config_nested_write()
        test_chat_evidence()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [f"{k}: {v}" for k, v in results if v != "ok"]
    print(f"共 {len(results)} 项，失败 {len(failed)} 项")
    for key, val in results:
        print(("  ok  " if val == "ok" else "  FAIL") + f" {key}" + ("" if val == "ok" else f" -> {val}"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
