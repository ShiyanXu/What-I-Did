"""项目周汇总：把一周的日报聚成一篇可读的回顾。

定位与日报不同：
- 日报回答"今天做了什么"，周汇总回答"这周整体推到哪了、什么在反复卡"；
- 只聚合已经生成的日报，不重新调用模型——周报里的每句话都能在某个日报里找到出处；
- 没有日报的那天会被明确标成"缺报"，而不是当作"没工作"。

输出是 Markdown：`reports/weekly/<起始日>_<结束日>.md`。
"""
from __future__ import annotations

from i18n import join_list, t, tn

import json
from datetime import timedelta

import paths
import timeutil

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MAX_ITEMS = 40          # 单类条目上限，避免一周刷出几百条把汇总淹掉


def week_range(anchor: str | None = None, cfg=None) -> tuple[str, str]:
    """返回本周的 (周一, 周日)，按"工作日"口径（凌晨算前一天）。"""
    if cfg is None:
        import config as cfgmod

        cfg = cfgmod.load()
    if anchor:
        base = timeutil.parse_day(anchor)
    else:
        base = timeutil.parse_day(timeutil.workday_of(timeutil.now(), cfg.cutoff_hour))
    monday = base - timedelta(days=base.weekday())
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def _days_between(start: str, end: str) -> list[str]:
    lo, hi = timeutil.parse_day(start), timeutil.parse_day(end)
    if hi < lo:
        raise ValueError(t("End date is earlier than start date"))
    if (hi - lo).days > 62:
        raise ValueError(t("A summary can span at most 62 days; split longer ranges"))
    out, cur = [], lo
    while cur <= hi:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _load_day(day: str) -> dict | None:
    p = paths.report_json(day)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_broken": True, "day": day}
    return data if isinstance(data, dict) else {"_broken": True, "day": day}


def _texts(items, key: str = "what") -> list[str]:
    out = []
    for it in items or []:
        if isinstance(it, dict):
            val = str(it.get(key) or it.get("observation") or it.get("item") or "").strip()
        else:
            val = str(it or "").strip()
        if val:
            out.append(val)
    return out


def _dedupe(items: list[str], limit: int = _MAX_ITEMS) -> list[str]:
    """按前 40 字去重：同一件事在不同天被写成两种说法时只留一条。"""
    seen, out = set(), []
    for item in items:
        key = item[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def collect(start: str, end: str, cfg=None) -> dict:
    """聚合 [start, end] 的日报。缺失、损坏、降级都分别记录，不混入统计。"""
    if cfg is None:
        import config as cfgmod

        cfg = cfgmod.load()
    days = _days_between(start, end)

    rows: list[dict] = []
    missing: list[str] = []
    broken: list[str] = []
    degraded: list[str] = []
    total_minutes = 0.0
    total_blocks = 0
    by_category: dict[str, float] = {}
    by_app: dict[str, float] = {}
    highlights: list[str] = []
    friction: list[str] = []
    pending: list[str] = []
    tomorrow: list[str] = []

    for day in days:
        data = _load_day(day)
        if data is None:
            has_events = paths.events_path(day).exists() and paths.events_path(day).stat().st_size > 40
            rows.append({"day": day, "status": "missing", "active_minutes": 0,
                         "block_count": 0,
                         "headline": t("Captured, but no report yet") if has_events else t("No records")})
            if has_events:
                missing.append(day)
            continue
        if data.get("_broken"):
            broken.append(day)
            rows.append({"day": day, "status": "broken", "active_minutes": 0,
                         "block_count": 0, "headline": t("Report file is corrupted")})
            continue

        rep = data.get("report") or {}
        stats = data.get("stats") or {}
        status = str(data.get("status") or "")
        minutes = float(stats.get("active_minutes") or 0)
        total_minutes += minutes
        total_blocks += int(stats.get("block_count") or 0)
        if status == "degraded":
            degraded.append(day)
        for key, val in (stats.get("by_category") or {}).items():
            by_category[key] = by_category.get(key, 0.0) + float(val or 0)
        for key, val in (stats.get("by_app") or {}).items():
            by_app[key] = by_app.get(key, 0.0) + float(val or 0)

        highlights.extend(_texts(rep.get("highlights")))
        friction.extend(_texts(rep.get("friction")))
        pending.extend(_texts(rep.get("in_progress") or rep.get("blockers")))
        tomorrow.extend(_texts(rep.get("tomorrow"), key="item"))

        rows.append({
            "day": day,
            "status": status or "unknown",
            "active_minutes": minutes,
            "block_count": int(stats.get("block_count") or 0),
            "headline": str(rep.get("headline") or "").strip(),
            "revision": int(data.get("revision") or 1),
        })

    counted = [r for r in rows if r["status"] not in ("missing", "broken")]
    return {
        "start": start,
        "end": end,
        "rows": rows,
        "counted_days": len(counted),
        "missing": missing,
        "broken": broken,
        "degraded": degraded,
        "total_minutes": round(total_minutes, 1),
        "daily_avg_minutes": round(total_minutes / len(counted), 1) if counted else 0.0,
        "total_blocks": total_blocks,
        "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
        "by_app": dict(sorted(by_app.items(), key=lambda x: -x[1])[:10]),
        "highlights": _dedupe(highlights),
        "friction": _dedupe(friction),
        "pending": _dedupe(pending),
        "tomorrow": _dedupe(tomorrow),
    }


def _hm(minutes: float) -> str:
    total = int(round(minutes))
    h, m = divmod(total, 60)
    return f"{h}{t(' h')} {m}{t(' min')}" if h else f"{m}{t(' min')}"


def render_md(data: dict, cat_label: dict | None = None) -> str:
    """渲染成 Markdown。统计口径与日报一致：都按"有效活动"。"""
    labels = cat_label or {}
    start, end = data["start"], data["end"]
    lines = [t("# {start} – {end} weekly summary", start=start, end=end), ""]

    # ---- 概览
    lines.append(t("## This week at a glance"))
    lines.append("")
    bits = [tn(data["counted_days"], "{n} day with a report", "{n} days with a report")]
    if data["total_minutes"]:
        bits.append(t("Active time totals {dur}", dur=_hm(data["total_minutes"])))
        bits.append(t("Daily average {dur}", dur=_hm(data["daily_avg_minutes"])))
    if data["total_blocks"]:
        bits.append(t("{n} activity blocks", n=data["total_blocks"]))
    lines.append("- " + join_list(bits))
    if data["degraded"]:
        lines.append(t("- {n} of them are the degraded timeline version (model not involved); "
                       "the narrative is less reliable", n=len(data["degraded"])))
    if data["missing"]:
        lines.append(tn(len(data["missing"]),
                        "- {n} day without a report: {days}",
                        "- {n} days without a report: {days}",
                        days=join_list(data["missing"])))
    if data["broken"]:
        lines.append(tn(len(data["broken"]),
                        "- {n} day with a corrupted file: {days}",
                        "- {n} days with a corrupted file: {days}",
                        days=join_list(data["broken"])))
    if data["by_category"]:
        cats = join_list(f"{labels.get(k, k)} {_hm(v)}" for k, v in data["by_category"].items())
        lines.append(t("- Time breakdown: {cats}", cats=cats))
    if data["by_app"]:
        apps = join_list(f"{k} {_hm(v)}" for k, v in data["by_app"].items())
        lines.append(t("- Top apps: {apps}", apps=apps))
    lines.append("")

    # ---- 每日
    lines.append(t("## Day by day"))
    lines.append("")
    lines.append(t("| Date | Weekday | Active time | Blocks | Status | Headline |"))
    lines.append("|---|---|---|---|---|---|")
    for row in data["rows"]:
        try:
            wd = t(WEEKDAYS[timeutil.parse_day(row["day"]).weekday()])
        except Exception:
            wd = ""
        status = {"ok": t("Model"), "degraded": t("Timeline"), "empty": t("Empty day"),
                  "missing": t("Missing"), "broken": t("Broken")}.get(
                      row["status"], row["status"] or "-")
        head = (row.get("headline") or "").replace("|", "／")[:60]
        lines.append(f"| {row['day']} | {wd} | {_hm(row['active_minutes']) if row['active_minutes'] else '-'} | "
                     f"{row['block_count'] or '-'} | {status} | {head} |")
    lines.append("")

    # ---- 进展 / 卡点
    for title, items, hint in (
        (t("Today's wins"), data["highlights"],
         t("All taken from that day's report; not reviewed one by one")),
        (t("Recurring friction"), data["friction"],
         t("Only worth changing the process when it shows up twice or more")),
        (t("Unfinished, continue next week"), data["pending"],
         t("From the in-progress and blocked items in the reports")),
        (t("Could be scheduled next week"), data["tomorrow"],
         t("From each day's suggestions for tomorrow, deduplicated")),
    ):
        if not items:
            continue
        lines.append(t("## {title}", title=title))
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
        lines.append(t("> {hint}", hint=hint))
        lines.append("")

    # ---- 口径说明
    lines.append(t("## About the numbers"))
    lines.append("")
    lines.append(t("- \"Active time\" is the wall-clock span of activity blocks minus paused, "
                   "locked and blocked intervals. It is a local measurement, not real work effort."))
    lines.append(t("- Screen records only prove that something was viewed or operated, never "
                   "that nothing was produced; sitting and reading can both look like low activity."))
    lines.append(t("- A weekly summary only aggregates reports that already exist; to fill in "
                   "a missing day, regenerate that day's report first, then summarize again."))
    lines.append("")
    return "\n".join(lines)


def weekly(anchor: str | None = None, cfg=None, out: str | None = None,
           start: str | None = None, end: str | None = None) -> dict:
    """生成周汇总文件。给了 start/end 就按区间，否则按 anchor 所在自然周。

    `out` 可以是文件名（写到 reports/weekly/ 下）或绝对的 .md 路径；
    只允许写到 reports 目录内，避免误把汇总写到别处覆盖文件。
    """
    from pathlib import Path

    if cfg is None:
        import config as cfgmod

        cfg = cfgmod.load()
    if start or end:
        if not (start and end):
            raise ValueError(t("start and end must be given together"))
        for value in (start, end):
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError(t("Date must be YYYY-MM-DD"))
            try:
                timeutil.parse_day(value)
            except ValueError as exc:
                raise ValueError(t("Date must be YYYY-MM-DD, got \"{value}\"", value=value)) from exc
    else:
        start, end = week_range(anchor, cfg)

    data = collect(start, end, cfg)
    md = render_md(data, blocks_label())

    base = paths.REPORTS / "weekly"
    if out:
        raw = str(out).strip()
        if not raw:
            raise ValueError(t("Output name cannot be empty"))
        target = Path(raw) if raw.lower().endswith(".md") and Path(raw).is_absolute() else base / (
            raw if raw.lower().endswith(".md") else f"{raw}.md")
        try:
            target.resolve().relative_to(paths.REPORTS.resolve())
        except ValueError as exc:
            raise ValueError(t("The weekly summary can only be written inside the reports folder")) from exc
    else:
        target = base / f"{start}_{end}.md"

    import render as render_mod

    render_mod.write_text_atomic(target, md)
    data["path"] = str(target)
    return data


def blocks_label() -> dict:
    """分类显示名从 render 取，保持与日报/看板同一套说法，且跟随当前语言。"""
    try:
        import render as render_mod

        return {key: render_mod.cat_name(key) for key in render_mod.CAT_NAME}
    except Exception:
        return {}
