"""报告期反查本机浏览器历史，把访问记录匹配到活动块，得到"这一块在看哪个网页"。

三条硬规矩：

1. **只在报告期跑**（21:00 起的批处理），不在 20 秒采样里做。采集期读库会放大 IO 与隐私面，
   而历史库被浏览器独占，频繁读要反复拷贝。
2. **只读，且不搬家**：先把 `History` 连同 `-wal` / `-shm` 拷到 `state/tmp/`，用 `mode=ro` 打开，
   读完即删。`data/` 里只留归一化后的 URL，绝不落全量历史。
3. **任何环节失败都静默降级**：没有库、被锁、被加密、表结构不认识，一律记 debug 并跳过，
   出报流程不受影响。

匹配窗必须向前回溯，这是实测出来的：`visits` 只记「导航」，一篇读 30 分钟的文章只留开头那一次访问，
而块可能从任意时刻起头。用 ±30 秒窗，实测只有 40% 的浏览器块能命中；向前回溯 60 分钟能到 86%。
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import paths
import privacy
from blocks import BROWSER_APPS, is_article_title

# Chrome / Edge 的时间基准：1601-01-01 起的微秒
_EPOCH_DELTA = 11644473600

_BROWSER_DIRS = {
    "chrome": ("Google/Chrome", "Chrome"),
    "edge": ("Microsoft/Edge", "Edge"),
}


@dataclass
class Visit:
    ts: float
    url: str
    title: str
    browser: str = ""


def is_browser(app: str) -> bool:
    return (app or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower() in BROWSER_APPS


# ------------------------------------------------------------------ 历史库发现
def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def _profile_dirs(cfg) -> list[tuple[str, Path]]:
    """返回 [(浏览器名, 该 profile 的 History 路径)]。profiles=auto 时扫所有 Profile 目录。"""
    want = [str(b).lower() for b in (cfg.get("browse.history.browsers") or ["chrome", "edge"])]
    wanted = str(cfg.get("browse.history.profiles") or "auto")
    out: list[tuple[str, Path]] = []
    base = _local_appdata()
    for name in want:
        vendor = _BROWSER_DIRS.get(name)
        if not vendor:
            continue  # firefox 本期不实现（见需求 §15 D-5）
        user_data = base / vendor[0] / "User Data"
        if not user_data.is_dir():
            continue
        if wanted and wanted != "auto":
            targets = [user_data / p for p in re.split(r"[;,]", wanted) if p.strip()]
        else:
            targets = [d for d in user_data.iterdir()
                       if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile "))]
        for d in targets:
            hist = d / "History"
            if hist.is_file():
                out.append((name, hist))
    return sorted(out, key=lambda x: str(x[1]))


def _copy_db(src: Path, dst_dir: Path, timeout: float) -> Path | None:
    """把 History 与 wal/shm 拷出来。拷不动（被独占/没权限）就返回 None。"""
    dst = dst_dir / f"{src.parent.name}_{src.name}_{os.getpid()}"
    try:
        deadline = time.time() + max(0.5, timeout)
        shutil.copy2(src, dst)
        for suffix in ("-wal", "-shm"):
            side = src.with_name(src.name + suffix)
            if side.is_file():
                shutil.copy2(side, dst.with_name(dst.name + suffix))
        if time.time() > deadline:
            dst.unlink(missing_ok=True)
            return None
        return dst
    except OSError:
        dst.unlink(missing_ok=True)
        return None


def _chrome_ts(value: float) -> float:
    return float(value) / 1_000_000 - _EPOCH_DELTA


def _query_chrome(db: Path, since: float, until: float) -> list[Visit]:
    sql = ("SELECT v.visit_time, u.url, u.title FROM visits v JOIN urls u ON u.id = v.url "
           "WHERE v.visit_time >= ? AND v.visit_time <= ?")
    lo = int((since + _EPOCH_DELTA) * 1_000_000)
    hi = int((until + _EPOCH_DELTA) * 1_000_000)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(sql, (lo, hi)).fetchall()
    finally:
        con.close()
    return [Visit(_chrome_ts(t), str(u or ""), str(tt or "")) for t, u, tt in rows]


def _cleanup(db: Path) -> None:
    for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def load_visits(cfg, day_start: float, day_end: float, log=None) -> list[Visit]:
    """读出当天（含回溯余量）的全部访问记录。失败一律返回已读到的部分。"""
    lookback = float(cfg.get("browse.history.lookback_sec", 3600))
    ahead = float(cfg.get("browse.history.ahead_sec", 30))
    since, until = day_start - lookback, day_end + ahead
    timeout = float(cfg.get("browse.history.copy_timeout_sec", 5))

    visits: list[Visit] = []
    tmp_dir = paths.browse_tmp()
    for name, hist in _profile_dirs(cfg):
        db = _copy_db(hist, tmp_dir, timeout)
        if db is None:
            if log:
                log.debug("browse: cannot copy %s history; skipped", name)
            continue
        try:
            got = _query_chrome(db, since, until)
        except sqlite3.DatabaseError:
            if log:
                log.debug("browse: %s history is unreadable (locked/encrypted); skipped", name)
            got = []
        except Exception as exc:  # noqa: BLE001 —— 任何异常都不能影响出报
            if log:
                log.debug("browse: %s history query failed (%s); skipped", name, type(exc).__name__)
            got = []
        finally:
            _cleanup(db)
        for v in got:
            v.browser = name
        visits.extend(got)

    # 同一秒内的重复访问去重（同 URL 同标题）
    seen: set[tuple[int, str]] = set()
    uniq: list[Visit] = []
    for v in sorted(visits, key=lambda x: x.ts):
        key = (int(v.ts), v.url)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(v)
    return uniq


# ------------------------------------------------------------------ 标题相似度
def _norm_title(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_.\-]{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = {"".join(pair) for pair in zip(cjk, cjk[1:])} if len(cjk) > 1 else set(cjk)
    return words | bigrams


def title_similarity(a: str, b: str) -> float:
    """标题相似度。词/字二元组重合率与序列相似度取大，兼顾中英文。"""
    x, y = _norm_title(a), _norm_title(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    tx, ty = _tokens(x), _tokens(y)
    jac = len(tx & ty) / len(tx | ty) if (tx and ty) else 0.0
    seq = difflib.SequenceMatcher(None, x, y).ratio()
    score = max(jac, seq)
    # 一个标题是另一个的前缀/子串（站点名拼接差异）：按长度比例打折后仍算较高
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    if short and short in long:
        score = max(score, 0.85 * len(short) / len(long) + 0.1)
    return min(1.0, score)


def _confidence(sim: float) -> float:
    if sim >= 0.8:
        return 0.95
    if sim >= 0.6:
        return 0.85
    return 0.7


MIN_SIM = 0.55  # 低于这个相似度宁可不给链接：错链比没链更糟


def match_block(block, visits: list[Visit], cfg) -> list[dict]:
    """给一个块找候选 URL。

    两级匹配，区别对待"块内发生的导航"与"块开始前就已打开的页面"：

    - **块内**（`[start - ahead, end + ahead]`）：用户在这一块里切过页面，允许多条，最多 `max_urls_per_block` 条；
    - **块开始前**（回溯窗口内）：只取**最近一条**标题对得上的。回溯 60 分钟是为了救"块开始时页面早已打开"
      的情况，如果把窗口内所有访问都收进来，会把一小时前看过的无关页面也挂到这一块上。
    """
    lookback = max(1.0, float(cfg.get("browse.history.lookback_sec", 3600)))
    ahead = max(0.0, float(cfg.get("browse.history.ahead_sec", 30)))
    cap = int(cfg.get("browse.history.max_urls_per_block", 3))
    start = block.start.timestamp()
    end_ts = block.end.timestamp()

    def _score(v: Visit) -> tuple[float, float]:
        sim = title_similarity(block.title, v.title)
        gap = max(0.0, start - v.ts)
        return 0.6 * sim + 0.4 * (1.0 - min(1.0, gap / lookback)), sim

    inner: list[tuple[float, float, Visit]] = []
    before: list[tuple[float, float, Visit]] = []
    for v in visits:
        if v.ts < start - lookback or v.ts > end_ts + ahead:
            continue
        score, sim = _score(v)
        if sim < MIN_SIM:
            continue
        (inner if v.ts >= start - ahead else before).append((score, sim, v))

    inner.sort(key=lambda x: (-x[0], -x[2].ts))
    picked = inner[:cap]
    if not picked and before:
        # 只认最近的一次：更早的访问属于上一块
        before.sort(key=lambda x: -x[2].ts)
        picked = [before[0]]
    else:
        best = picked[0][0] if picked else 0.0
        # 块内的其它候选只在分数接近最优时才保留，避免把侧栏里的其它标签页也算进来
        picked = [p for p in picked if p[0] >= best - 0.12] or picked[:1]
    if not picked:
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for score, sim, v in picked:
        uri = privacy.normalize_url(v.url, cfg)
        if not uri:
            continue
        canon = privacy.canonical_url(uri, cfg) or uri
        if canon in seen:
            continue
        seen.add(canon)
        out.append({
            "block_id": block.id,
            "uri": uri,
            "canonical": canon,
            "title": (v.title or "")[:200],
            "source": "history",
            "browser": v.browser,
            "confidence": round(_confidence(sim), 2),
            "similarity": round(sim, 3),
            "score": round(score, 3),
            "ts": int(start),
            "visit_ts": round(v.ts, 1),
        })
        if len(out) >= cap:
            break
    return out


# ------------------------------------------------------------------ 地址栏兜底
_ADDR_LIKE = re.compile(r"^(?:https?://)?([a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/\S*)?$", re.I)


def address_bar_url(block, ocr_map: dict, cfg) -> str:
    """从窗口顶部条带的 OCR 行里认地址栏。

    只认**有行坐标**的记录：没有 `lines` 的旧数据直接跳过，不去猜。
    """
    if not cfg.get("browse.address_bar.enabled", True):
        return ""
    ratio = float(cfg.get("browse.address_bar.top_ratio", 0.15))
    for oid in reversed(block.ocr_ids or []):
        rec = ocr_map.get(oid) or {}
        lines = rec.get("lines") or []
        height = float(rec.get("height") or 0)
        if not lines or height <= 0:
            continue
        for item in lines:
            try:
                y1 = float(item.get("y1", 0))
            except (TypeError, ValueError):
                continue
            if y1 > ratio * height:
                continue
            text = str(item.get("text") or "").strip()
            if not text or len(text) > 300:
                continue
            if _ADDR_LIKE.match(text):
                return privacy.normalize_url(
                    text if text.lower().startswith("http") else "https://" + text, cfg)
    return ""


_DOMAIN_IN_TEXT = re.compile(
    r"(?:网页由|由)\s*([a-z0-9-]+(?:\.[a-z0-9-]+)+)\s*(?:提供)?", re.I)


def domain_hint(block, cfg, text: str = "") -> str:
    """内置浏览器只有域名时用：域名必须来自 OCR 原文，不能由标题推断。"""
    if not cfg.get("browse.domain_only.enabled", True):
        return ""
    body = text or getattr(block, "text", "") or ""
    if not body:
        return ""
    m = _DOMAIN_IN_TEXT.search(body)
    if not m:
        return ""
    host = m.group(1).lower()
    if "." not in host or host.endswith((".jpg", ".png", ".gif")):
        return ""
    return privacy.domain_source(host)


# ------------------------------------------------------------------ 主流程
def enrich(cfg, day: str, blocks: list, ocr_map: dict | None = None, log=None) -> dict[str, list[dict]]:
    """给每个块补 `urls`，并把结果整份写进 data/<day>/urls.jsonl。

    返回 block_id -> [记录]。任何异常都要吞掉（FR-1.8）。
    """
    history_on = bool(cfg.get("browse.history.enabled", False))
    addr_on = bool(cfg.get("browse.address_bar.enabled", True))
    if not history_on and not addr_on:
        _write_urls(day, [])
        return {}

    try:
        if history_on:
            day_start = _day_bounds(day)[0]
            day_end = _day_bounds(day)[1]
            visits = load_visits(cfg, day_start, day_end, log)
            if log:
                log.info("browse: %d history visits in range; matching %d blocks",
                         len(visits), len(blocks))
        else:
            visits = []
    except Exception as exc:  # noqa: BLE001
        if log:
            log.warning("browse: history lookup failed (%s); continuing without URLs",
                        type(exc).__name__)
        visits = []

    result: dict[str, list[dict]] = {}
    flat: list[dict] = []
    for b in blocks:
        found: list[dict] = []
        try:
            if visits and is_browser(b.app):
                found = match_block(b, visits, cfg)
        except Exception:  # noqa: BLE001
            found = []
        # 历史库没命中时，才退到地址栏（顺序很重要：OCR 识别率远低于历史库）
        if not found and addr_on and ocr_map and is_browser(b.app):
            try:
                uri = address_bar_url(b, ocr_map, cfg)
            except Exception:  # noqa: BLE001
                uri = ""
            if uri:
                found = [{
                    "block_id": b.id, "uri": uri, "canonical": privacy.canonical_url(uri, cfg) or uri,
                    "title": (b.title or "")[:200], "source": "address_bar",
                    "confidence": 0.6, "ts": int(b.start.timestamp()),
                }]
        # 微信/飞书内置浏览器不写系统历史：退到"仅域名"，域名必须来自 OCR 原文。
        # 微信默认在 skip_ocr_apps 里、拿不到标题与 OCR，这条实际对飞书生效。
        if not found and is_article_title(b.title):
            try:
                uri = domain_hint(b, cfg)
            except Exception:  # noqa: BLE001
                uri = ""
            if uri:
                found = [{
                    "block_id": b.id, "uri": uri, "canonical": uri,
                    "title": (b.title or "")[:200], "source": "domain",
                    "confidence": 0.5, "ts": int(b.start.timestamp()),
                }]
        if found:
            result[b.id] = found
            flat.extend(found)

    _write_urls(day, flat)
    return result


def _day_bounds(day: str) -> tuple[float, float]:
    """一天的起止时间戳。窗口起点跟 app.day_cutoff_hour 一致（默认 04:00）。"""
    from datetime import datetime, timedelta

    d = datetime.strptime(day, "%Y-%m-%d")
    start = d
    end = d + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _write_urls(day: str, records: list[dict]) -> None:
    """整份覆盖写：报告期一天可能跑多次，覆盖才能保证重跑一致、不叠加。"""
    path = paths.urls_path(day)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass


def load_urls(day: str) -> list[dict]:
    path = paths.urls_path(day)
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("uri"):
                    out.append(rec)
    except OSError:
        return []
    return out


def forget_urls(day: str, before_ts: float | None = None) -> int:
    """forget --minutes 用：删掉该时刻之后的记录（与事件流同日窗口）。"""
    records = load_urls(day)
    if not records:
        return 0
    if before_ts is None:
        _write_urls(day, [])
        return len(records)
    keep = [r for r in records if float(r.get("ts") or 0) < before_ts]
    removed = len(records) - len(keep)
    if removed:
        _write_urls(day, keep)
    return removed
