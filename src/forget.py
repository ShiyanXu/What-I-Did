"""“遗忘”机制：删除最近 N 分钟或某一整天的原始与派生数据。

- 只删本工具管理的数据：事件、截图、缩略图、OCR、日报 JSON/MD、页面、日状态；
  manual.json 是用户亲写的内容，整日遗忘时一并删除，分钟级遗忘时保留。
- 删除是不可逆的；已经发送给模型服务的内容无法撤回——脱敏与发送前审核才是第一道防线。
- 派生数据（日报/页面）在原始数据被删后一并移除，下次出报不会再包含被遗忘的内容。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import paths
import timeutil

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _derived_paths(day: str) -> list[Path]:
    return [
        paths.report_json(day),
        paths.report_md(day),
        paths.web_day(day),
        paths.day_state(day),
        paths.DAY_STATE / f"{day}-review.json",
        paths.REPORTS / "share" / f"{day}-分享版.md",
    ]


_WEEKLY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.md$")


def _drop_weekly(days: list[str], removed: list[str]) -> None:
    """周汇总覆盖了被遗忘的日期时一并删掉。

    只认 `<起始日>_<结束日>.md` 这种自动命名；用户自定义文件名的汇总无法判断覆盖范围，
    留着比误删安全，但要在日志里说清楚。
    """
    if not days:
        return
    folder = paths.REPORTS / "weekly"
    if not folder.exists():
        return
    targets = set(days)
    for p in folder.glob("*.md"):
        m = _WEEKLY_RE.match(p.name)
        if not m:
            continue
        start, end = m.group(1), m.group(2)
        if any(start <= d <= end for d in targets):
            try:
                p.unlink()
                removed.append(paths.rel_to_root(p))
            except OSError:
                pass


def _drop_derived(day: str, removed: list[str]) -> None:
    for p in _derived_paths(day):
        try:
            if p.exists():
                p.unlink()
                removed.append(paths.rel_to_root(p))
        except OSError:
            pass


def _filter_events(day: str, lo, hi, removed: dict) -> int:
    """删掉 [lo, hi] 区间内的事件及其截图/缩略图，返回删除条数。"""
    p = paths.events_path(day)
    if not p.exists():
        return 0
    keep_lines: list[str] = []
    dropped = 0
    with p.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
                ts = timeutil.parse(rec.get("ts", ""))
                in_range = lo <= ts <= hi
            except Exception:
                keep_lines.append(s)
                continue
            if not in_range:
                keep_lines.append(s)
                continue
            dropped += 1
            for rel in (rec.get("img"),):
                if not rel:
                    continue
                img = paths.day_dir(day) / rel
                thumb = paths.thumbs_dir(day) / img.name
                for target in (img, thumb):
                    try:
                        if target.exists():
                            target.unlink()
                            removed["shots"] += 1
                    except OSError:
                        pass
    if dropped:
        render_atomic(p, "\n".join(keep_lines) + ("\n" if keep_lines else ""))
        removed["events"] += dropped
    return dropped


def _filter_ocr(day: str, lo, hi, removed: dict) -> int:
    p = paths.ocr_path(day)
    if not p.exists():
        return 0
    keep_lines: list[str] = []
    dropped = 0
    with p.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
                ts = timeutil.parse(timeutil.iso(
                    __import__("datetime").datetime.fromtimestamp(float(rec.get("ts", 0)), timeutil.TZ)))
                in_range = lo <= ts <= hi
            except Exception:
                keep_lines.append(s)
                continue
            if in_range:
                dropped += 1
            else:
                keep_lines.append(s)
    if dropped:
        render_atomic(p, "\n".join(keep_lines) + ("\n" if keep_lines else ""))
        removed["ocr"] += dropped
    return dropped


def render_atomic(path: Path, text: str) -> None:
    import render

    render.write_text_atomic(path, text)


def _span_days(lo, hi, cutoff_hour: int) -> list[str]:
    days = []
    cur = lo.astimezone(timeutil.TZ).date()
    end = hi.astimezone(timeutil.TZ).date()
    while cur <= end and len(days) < 40:
        probe = timeutil.parse(f"{cur.isoformat()}T12:00:00+08:00")
        day = timeutil.workday_of(probe, cutoff_hour)
        if day not in days:
            days.append(day)
        cur += timedelta(days=1)
    return sorted(days)


def forget_minutes(minutes: int, cfg, log=None) -> dict:
    """删除最近 N 分钟的原始数据 + 受影响天的派生数据。"""
    minutes = int(minutes)
    if minutes <= 0 or minutes > 7 * 24 * 60:
        raise ValueError("minutes 应在 1 到 10080（一周）之间")
    now = timeutil.now()
    lo = now - timedelta(minutes=minutes)
    removed = {"events": 0, "shots": 0, "ocr": 0, "days": [], "derived": []}
    for day in _span_days(lo, now, cfg.cutoff_hour):
        changed = _filter_events(day, lo, now, removed)
        changed += _filter_ocr(day, lo, now, removed)
        if changed:
            _drop_derived(day, removed["derived"])
            removed["days"].append(day)
    _drop_weekly(removed["days"], removed["derived"])
    _refresh_index(cfg)
    if log:
        log.info("遗忘最近 %d 分钟：删事件 %d / 截图 %d / OCR %d，涉及 %s",
                 minutes, removed["events"], removed["shots"], removed["ocr"],
                 "、".join(removed["days"]) or "无")
    return removed


def forget_day(day: str, cfg, log=None) -> dict:
    """删除某一整天的一切（原始 + 派生 + 人工备注）。"""
    if not _DAY_RE.match(day or ""):
        raise ValueError("day 应为 YYYY-MM-DD")
    removed = {"days": [day], "events": 0, "shots": 0, "ocr": 0, "derived": []}
    dd = paths.day_dir(day)
    if dd.exists():
        try:
            shutil.rmtree(dd)
            removed["derived"].append(paths.rel_to_root(dd))
        except OSError as exc:
            raise RuntimeError(f"删除 {dd} 失败：{exc}") from exc
    _drop_derived(day, removed["derived"])
    _drop_weekly([day], removed["derived"])
    _refresh_index(cfg)
    if log:
        log.info("遗忘 %s：已删除 %s", day, "、".join(removed["derived"]))
    return removed


def _refresh_index(cfg) -> None:
    try:
        import render

        render.render_index(cfg)
    except Exception:
        pass  # 索引刷新失败不影响遗忘本身；下次出报会重建
