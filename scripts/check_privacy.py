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
        check(bool(cfg2.error) and "正则" in cfg2.error, "非法隐私正则使配置失效",
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [f"{k}: {v}" for k, v in results if v != "ok"]
    print(f"共 {len(results)} 项，失败 {len(failed)} 项")
    for key, val in results:
        print(("  ok  " if val == "ok" else "  FAIL") + f" {key}" + ("" if val == "ok" else f" -> {val}"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
