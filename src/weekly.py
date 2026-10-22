"""项目周汇总：把一周的日报聚成一篇可读的回顾。

定位与日报不同：
- 日报回答"今天做了什么"，周汇总回答"这周整体推到哪了、什么在反复卡"；
- 只聚合已经生成的日报，不重新调用模型——周报里的每句话都能在某个日报里找到出处；
- 没有日报的那天会被明确标成"缺报"，而不是当作"没工作"。

输出是 Markdown：`reports/weekly/<起始日>_<结束日>.md`。
"""
from __future__ import annotations

import json
from datetime import timedelta

import paths
import timeutil

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
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
        raise ValueError("结束日期早于开始日期")
    if (hi - lo).days > 62:
        raise ValueError("汇总区间最多 62 天，更长请分次")
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
                         "block_count": 0, "headline": "有采集记录但还没出报" if has_events else "无记录"})
            if has_events:
                missing.append(day)
            continue
        if data.get("_broken"):
            broken.append(day)
            rows.append({"day": day, "status": "broken", "active_minutes": 0,
                         "block_count": 0, "headline": "日报文件损坏"})
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
    return f"{h} 小时 {m} 分" if h else f"{m} 分钟"


def render_md(data: dict, cat_label: dict | None = None) -> str:
    """渲染成 Markdown。统计口径与日报一致：都按"有效活动"。"""
    labels = cat_label or {}
    start, end = data["start"], data["end"]
    lines = [f"# {start} – {end} 工作周汇总", ""]

    # ---- 概览
    lines.append("## 这周概览")
    lines.append("")
    bits = [f"有日报 {data['counted_days']} 天"]
    if data["total_minutes"]:
        bits.append(f"有效活动合计 {_hm(data['total_minutes'])}")
        bits.append(f"日均 {_hm(data['daily_avg_minutes'])}")
    if data["total_blocks"]:
        bits.append(f"{data['total_blocks']} 个活动块")
    lines.append("- " + "，".join(bits))
    if data["degraded"]:
        lines.append(f"- 其中 {len(data['degraded'])} 天是降级时间线版（模型未参与），叙述可信度低")
    if data["missing"]:
        lines.append(f"- 缺报 {len(data['missing'])} 天：{'、'.join(data['missing'])}")
    if data["broken"]:
        lines.append(f"- 文件损坏 {len(data['broken'])} 天：{'、'.join(data['broken'])}")
    if data["by_category"]:
        cats = "、".join(f"{labels.get(k, k)} {_hm(v)}" for k, v in data["by_category"].items())
        lines.append(f"- 时间分布：{cats}")
    if data["by_app"]:
        apps = "、".join(f"{k} {_hm(v)}" for k, v in data["by_app"].items())
        lines.append(f"- 主要应用：{apps}")
    lines.append("")

    # ---- 每日
    lines.append("## 每日一览")
    lines.append("")
    lines.append("| 日期 | 星期 | 有效活动 | 块 | 状态 | 标题 |")
    lines.append("|---|---|---|---|---|---|")
    for row in data["rows"]:
        try:
            wd = WEEKDAYS[timeutil.parse_day(row["day"]).weekday()]
        except Exception:
            wd = ""
        status = {"ok": "模型", "degraded": "时间线", "empty": "空日",
                  "missing": "缺报", "broken": "损坏"}.get(row["status"], row["status"] or "-")
        head = (row.get("headline") or "").replace("|", "／")[:60]
        lines.append(f"| {row['day']} | {wd} | {_hm(row['active_minutes']) if row['active_minutes'] else '-'} | "
                     f"{row['block_count'] or '-'} | {status} | {head} |")
    lines.append("")

    # ---- 进展 / 卡点
    for title, items, hint in (
        ("关键进展", data["highlights"], "全部摘自当天日报，未逐条复核"),
        ("反复出现的卡点", data["friction"], "出现两次以上才值得改流程"),
        ("未完成，下周继续", data["pending"], "来自日报里的进行中与卡住项"),
        ("下周可以安排", data["tomorrow"], "来自每天给的明日建议，已去重"),
    ):
        if not items:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
        lines.append(f"> {hint}")
        lines.append("")

    # ---- 口径说明
    lines.append("## 数据说明")
    lines.append("")
    lines.append("- 「有效活动」= 活动块的墙钟跨度扣除暂停/锁屏/屏蔽区间，是本机观测口径，不等于实际工作投入。")
    lines.append("- 屏幕记录只能证明「看过、操作过」，不能反证「没产出」；静坐与阅读都可能被算成低活跃。")
    lines.append("- 周汇总只聚合已生成的日报；想补某天的内容，先对那天重新生成日报再汇总。")
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
            raise ValueError("start 与 end 需要同时给出")
        for value in (start, end):
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError("日期格式应为 YYYY-MM-DD")
            try:
                timeutil.parse_day(value)
            except ValueError as exc:
                raise ValueError(f"日期格式应为 YYYY-MM-DD，收到「{value}」") from exc
    else:
        start, end = week_range(anchor, cfg)

    data = collect(start, end, cfg)
    md = render_md(data, blocks_label())

    base = paths.REPORTS / "weekly"
    if out:
        raw = str(out).strip()
        if not raw:
            raise ValueError("输出名不能为空")
        target = Path(raw) if raw.lower().endswith(".md") and Path(raw).is_absolute() else base / (
            raw if raw.lower().endswith(".md") else f"{raw}.md")
        try:
            target.resolve().relative_to(paths.REPORTS.resolve())
        except ValueError as exc:
            raise ValueError("周汇总只能写到 reports 目录内") from exc
    else:
        target = base / f"{start}_{end}.md"

    import render as render_mod

    render_mod.write_text_atomic(target, md)
    data["path"] = str(target)
    return data


def blocks_label() -> dict:
    """分类中文名从 blocks 模块取，保持与日报/看板同一套说法。"""
    try:
        import blocks as blocks_mod

        return blocks_mod.CATEGORY_LABEL
    except Exception:
        return {}
