"""状态快照：CLI `status` 与图形界面共用，集中判定"采集在不在跑、今天有多少数据"。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import paths
import timeutil

ALIVE_WINDOW_SEC = 180      # 心跳超过这个秒数就认为采集已经停了


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------------------------------------------------------------- 心跳
def heartbeat() -> dict:
    hb = read_json(paths.HEARTBEAT, {}) or {}
    pid = int(hb.get("pid") or 0)
    age = None
    if pid:
        try:
            import psutil

            if psutil.pid_exists(pid):
                ts = timeutil.parse(hb["ts"]) if hb.get("ts") else None
                if ts is not None:
                    age = max(0, int((timeutil.now() - ts).total_seconds()))
        except Exception:
            age = None
    return {
        "raw": hb,
        "pid": pid,
        "age": age,
        "alive": age is not None and age <= ALIVE_WINDOW_SEC,
        "state": hb.get("state", ""),
        "day": hb.get("day", ""),
    }


def pause_info() -> dict | None:
    """返回暂停信息；已过期（到了 until）则视作未暂停。"""
    if not paths.PAUSE_FILE.exists():
        return None
    data = read_json(paths.PAUSE_FILE)
    if not isinstance(data, dict) or not data:
        return {"until": "", "error": "暂停标记不可读/为空，按暂停处理"}
    until = data.get("until") or ""
    if until:
        try:
            if timeutil.parse(until) <= timeutil.now():
                return None
        except Exception:
            return {"until": "", "error": "暂停时间无效，按暂停处理"}
    return data


# ---------------------------------------------------------------- 数据量
_COUNT_CACHE: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {}
_SIZE_CACHE: dict[str, tuple[float, float]] = {}
_RPT_CACHE: dict[str, tuple[tuple[int, int], dict | None]] = {}
_INDEX_CACHE: dict = {"key": None, "val": []}


def day_counts(day: str) -> dict:
    """今日事件条数与截图张数（按 mtime+size 缓存，界面 3 秒轮询不会反复扫盘）。"""
    ev = paths.events_path(day)
    sd = paths.shots_dir(day)
    try:
        st = ev.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = (0, 0)
    cached = _COUNT_CACHE.get(day)
    if cached and cached[0] == key:
        return {"events": cached[1][0], "shots": cached[1][1]}

    events = 0
    if key[1]:
        try:
            with ev.open("r", encoding="utf-8", errors="ignore") as f:
                events = sum(1 for line in f if line.strip())
        except OSError:
            events = 0
    shots = sum(1 for _ in sd.glob("*.jpg")) if sd.exists() else 0
    _COUNT_CACHE[day] = (key, (events, shots))
    return {"events": events, "shots": shots}


def day_size_mb(day: str) -> float:
    """当日目录体积（30 秒缓存：界面每 3 秒刷一次，不值得反复 rglob 上千个文件）。"""
    import time

    hit = _SIZE_CACHE.get(day)
    now = time.time()
    if hit and now - hit[0] < 30:
        return hit[1]
    d = paths.day_dir(day)
    total = 0
    if d.exists():
        for p in d.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    mb = round(total / 1024 ** 2, 1)
    _SIZE_CACHE[day] = (now, mb)
    return mb


def disk_free_gb() -> float | None:
    try:
        return round(shutil.disk_usage(str(paths.ROOT)).free / 1024 ** 3, 1)
    except Exception:
        return None


# ---------------------------------------------------------------- 日报索引
def reports_index() -> list[dict]:
    """reports/*.json 的轻量索引，按日期倒序。按文件 mtime 缓存，避免重复解析。"""
    if not paths.REPORTS.exists():
        return []
    files = sorted(paths.REPORTS.glob("*.json"), reverse=True)
    key = []
    for p in files:
        try:
            st = p.stat()
            key.append((p.name, st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((p.name, 0, 0))
    key_t = tuple(key)
    if _INDEX_CACHE["key"] == key_t:
        return _INDEX_CACHE["val"]

    out: list[dict] = []
    for p in files:
        d = read_json(p, {}) or {}
        r = d.get("report") or {}
        stats = d.get("stats") or {}
        out.append({
            "day": d.get("day") or p.stem,
            "weekday": d.get("weekday", ""),
            "headline": r.get("headline") or d.get("reason") or "",
            "status": d.get("status", ""),
            "confidence": r.get("confidence", ""),
            "active_minutes": stats.get("active_minutes", 0) or 0,
            "block_count": stats.get("block_count", 0) or 0,
            "generated_at": d.get("generated_at", ""),
            "revision": d.get("revision", 1),
            "has_html": paths.web_day(p.stem).exists(),
        })
    _INDEX_CACHE["key"] = key_t
    _INDEX_CACHE["val"] = out
    return out


def load_report(day: str) -> dict | None:
    p = paths.report_json(day)
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    hit = _RPT_CACHE.get(day)
    if hit and hit[0] == key:
        return hit[1]
    data = read_json(p)
    _RPT_CACHE[day] = (key, data)
    return data


# ---------------------------------------------------------------- 实时行为轴
_LIVE_CACHE: dict[str, tuple] = {}


def live_behaviors(day: str, cfg) -> list[dict]:
    """今天还没出报时，用本地规则先铺一条行为轴。

    按 events.jsonl / ocr.jsonl 的 mtime+size 缓存：界面 3 秒轮询，只有新数据落盘
    才会重新切块，正常一次 50ms 左右，不会卡。
    """
    if getattr(cfg, "error", ""):
        return []
    key = [json.dumps(cfg.get("privacy", {}), ensure_ascii=False, sort_keys=True)]
    for p in (paths.events_path(day), paths.ocr_path(day)):
        try:
            st = p.stat()
            key.append((st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((0, 0))
    key.append((cfg.get("report.gap_threshold_min", 10), cfg.get("report.max_session_min", 60)))
    cached = _LIVE_CACHE.get(day)
    if cached and cached[0] == tuple(key):
        return cached[1]

    import behavior
    import blocks

    try:
        events = blocks.load_events(paths.events_path(day))
        if not events:
            out: list[dict] = []
        else:
            ocr = blocks.load_ocr(paths.ocr_path(day))
            bl = blocks.build_blocks(events, ocr, cfg)
            sessions = behavior.build_sessions(bl, cfg)
            out = behavior.merge([], sessions)          # 本地标签，没有模型
    except Exception:
        out = []
    _LIVE_CACHE[day] = (tuple(key), out)
    return out


# ---------------------------------------------------------------- 出报计划
def report_schedule(cfg, day: str | None = None) -> dict:
    """出报计划与"该出没出"的状态，给概览页和 doctor 共用。

    `due` 来自 tick.pending，跟 daemon 的 21:00 兜底、计划任务用的是同一套判断，
    所以界面上显示"待生成"就一定是真的待生成。
    """
    from datetime import timedelta

    now = timeutil.now()
    today = day or timeutil.workday_of(now, cfg.cutoff_hour)
    hour = int(cfg.get("report.trigger_hour", 21))
    trigger = timeutil.trigger_at(today, cfg.cutoff_hour, hour)
    next_at = trigger if now < trigger else timeutil.trigger_at(
        (timeutil.parse_day(today) + timedelta(days=1)).strftime("%Y-%m-%d"), cfg.cutoff_hour, hour)

    due: list[tuple[str, str]] = []
    try:
        import tick

        due = tick.pending(cfg, now, days=2)
    except Exception:
        due = []

    return {
        "today": today,
        "trigger_hour": hour,
        "trigger_at": timeutil.iso(trigger),
        "next_at": timeutil.iso(next_at),
        "passed": now >= trigger,
        "due": due,
        "overdue": bool(due),
        "overdue_day": due[0][0] if due else "",
        "overdue_why": due[0][1] if due else "",
    }


# ---------------------------------------------------------------- 待审内容
def pending_review(day: str) -> dict:
    """有没有一份"等用户确认才外发"的内容。

    只读元信息，不读 payload 正文：界面每 3 秒轮询一次，不该把几千字的输入反复搬进内存。
    正文由需要展示的页面按需自己读。
    """
    p = paths.DAY_STATE / f"{day}-review.json"
    if not p.exists():
        return {}
    data = read_json(p, {}) or {}
    if not isinstance(data, dict):
        return {"error": "待审文件不可读"}
    return {
        "day": data.get("day") or day,
        "generated_at": data.get("generated_at", ""),
        "coverage": data.get("coverage", ""),
        "chars": int(data.get("payload_chars") or 0),
        "model": data.get("model", ""),
        "base_url": data.get("base_url", ""),
        "path": str(p),
    }


# ---------------------------------------------------------------- 汇总
def snapshot(cfg) -> dict:
    now = timeutil.now()
    day = timeutil.workday_of(now, cfg.cutoff_hour)
    hb = heartbeat()
    pause = pause_info()
    counts = day_counts(day)
    rep = load_report(day)
    stats = (rep or {}).get("stats") or {}
    return {
        "now": timeutil.iso(now),
        "workday": day,
        "heartbeat": hb,
        "paused": pause,
        "running": hb["alive"] and not pause and not getattr(cfg, "error", ""),
        "privacy_error": getattr(cfg, "error", ""),
        "allow_llm": cfg.get("privacy.allow_llm", True) and not getattr(cfg, "error", ""),
        "interval_sec": int(float(hb["raw"].get("interval_sec") or cfg.get("capture.interval_sec", 20))),
        "events": counts["events"],
        "shots": counts["shots"],
        "day_size_mb": round(day_size_mb(day), 1),
        "disk_free_gb": disk_free_gb(),
        "has_key": cfg.has_api_key(),
        "today_report": {
            "exists": bool(rep),
            "headline": ((rep or {}).get("report") or {}).get("headline", ""),
            "status": (rep or {}).get("status", ""),
            "active_minutes": stats.get("active_minutes", 0) or 0,
            "block_count": stats.get("block_count", 0) or 0,
        },
        "reports": reports_index(),
        "report": report_schedule(cfg, day),
        "review": pending_review(day),
    }
