"""渲染：一份 Markdown 归档 + 一个零依赖的静态看板。"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import jinja2
import yaml

import blocks as blocks_mod
import behavior as behavior_mod
import paths
import timeutil

CAT_COLOR = {
    "dev": "#2563eb",
    "doc": "#0d9488",
    "meet": "#d97706",
    "comm": "#7c3aed",
    "web": "#64748b",
    "other": "#94a3b8",
}
CAT_LABEL = blocks_mod.CATEGORY_LABEL

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _env(autoescape: bool = True) -> jinja2.Environment:
    """统一的模板环境。

    注意 `trim_blocks=True`：它会吃掉块标签**后面**那个换行。所以在 Markdown 模板里
    每一行的末尾必须是字面文本或 `{{ }}`，绝不能以 `{% endif %}` 结尾，否则整份列表
    会被拼成一行（老版本的归档 Markdown 就栽在这上面）。
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(paths.TEMPLATES)),
        autoescape=autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["minutes"] = lambda v: f"{int(v)} 分"
    env.filters["pct"] = lambda v: f"{float(v):.1f}%"
    return env


def weekday_of(day: str) -> str:
    return WEEKDAYS[timeutil.parse_day(day).weekday()]


def behavior_verbs() -> dict:
    return behavior_mod.VERBS


# ---------------------------------------------------------------- Markdown
_TABLE_ROW = re.compile(r"^\|")
_LIST_ITEM = re.compile(r"^([-*] |\d+\. )")


def _fix_md(text: str) -> str:
    """补回被 trim_blocks 吃掉的空行，并压掉多余空行。

    Markdown 里标题、引用（callout）、表格前面必须有空行，否则会被上一段吞掉
    （`> [!warning]` 粘在列表后面会变成列表的一部分，Obsidian 里就不显示成 callout 了）。
    """
    out: list[str] = []
    prev_table = prev_item = False
    for ln in text.split("\n"):
        cur_table = bool(_TABLE_ROW.match(ln))
        cur_item = bool(_LIST_ITEM.match(ln))
        need_blank = bool(ln.strip()) and bool(out) and out[-1].strip() != ""
        if need_blank:
            if ln.startswith("#") or (ln.startswith(">") and not out[-1].startswith(">")):
                pass                      # 标题/引用（引用内的续行不算）：一定要空行
            elif cur_table and not prev_table:
                pass                      # 表格开始：要空行
            elif cur_item and not prev_item and not prev_table:
                pass                      # 列表开始：要空行
            else:
                need_blank = False
        if need_blank:
            out.append("")
        if ln.strip() == "" and out and out[-1].strip() == "":
            continue                      # 连续空行只留一个
        out.append(ln)
        prev_table, prev_item = cur_table, cur_item
    return "\n".join(out).strip("\n") + "\n"


def write_text_atomic(out: Path, text: str) -> None:
    """单文件原子替换；失败不把现有报告截断成半页。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def write_markdown(payload: dict) -> Path:
    env = _env(autoescape=False)
    tpl = env.get_template("report.md.j2")
    text = tpl.render(
        d=payload,
        day=payload["day"],
        weekday=payload.get("weekday", ""),
        cat_label=CAT_LABEL,
        verb_label={k: v["label"] for k, v in behavior_verbs().items()},
    )
    out = paths.report_md(payload["day"])
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(out, _fix_md(text))
    return out


# ---------------------------------------------------------------- HTML
def _strip_segments(payload: dict) -> list[dict]:
    """按"工作日窗口"（默认 04:00→次日 04:00）铺 24 小时，跨零点不会错位。"""
    cov = payload.get("coverage") or {}
    try:
        ws = timeutil.parse(cov.get("start_iso", ""))
        we = timeutil.parse(cov.get("end_iso", ""))
    except Exception:
        return []
    span = max(1.0, (we - ws).total_seconds())
    segs = []
    for b in payload.get("blocks", []):
        try:
            start = timeutil.parse(b["start"])
            end = timeutil.parse(b["end"])
        except Exception:
            continue
        s = (start - ws).total_seconds()
        e = (end - ws).total_seconds()
        s = min(max(0.0, s), span)
        e = min(max(s + 60.0, e), span)
        segs.append({
            "left": round(s / span * 100, 3),
            "width": max(0.2, round((e - s) / span * 100, 3)),
            "color": CAT_COLOR.get(b.get("category", "other"), CAT_COLOR["other"]),
            "title": f"{b['start'][11:16]}–{b['end'][11:16]} {b.get('title', '')}",
            "id": b.get("id", ""),
        })
    return segs


def _strip_ticks(payload: dict) -> list[str]:
    cov = payload.get("coverage") or {}
    try:
        ws = timeutil.parse(cov.get("start_iso", ""))
        we = timeutil.parse(cov.get("end_iso", ""))
    except Exception:
        return []
    labels = []
    for i in range(4):
        t = ws + timedelta(hours=6 * i)
        labels.append(t.strftime("%H:%M"))
    labels.append(we.strftime("%H:%M") + "⁺")
    return labels


def _seg_window(payload: dict, it: dict) -> tuple[datetime, datetime] | None:
    """行为段起止。优先 ISO；旧数据只有 HH:MM 时按工作日窗口推真实日期。"""
    day = payload.get("day", "")
    cov = payload.get("coverage") or {}
    try:
        ws = timeutil.parse(cov.get("start_iso", ""))
    except Exception:
        return None
    if it.get("start_iso") and it.get("end_iso"):
        try:
            return timeutil.parse(it["start_iso"]), timeutil.parse(it["end_iso"])
        except Exception:
            pass
    try:
        start = timeutil.parse(f"{day}T{it['start']}:00+08:00")
        end = timeutil.parse(f"{day}T{it['end']}:00+08:00")
    except Exception:
        return None
    # HH:MM 早于窗口起点（默认 04:00）说明它属于次日：00:30 是"当晚"而不是"当天凌晨"
    if start.date() == ws.date() and start.time() < ws.time():
        start += timedelta(days=1)
        end += timedelta(days=1)
    if end <= start:
        end += timedelta(days=1)  # 跨午夜：结束顺延一天
    return start, end


def _behavior_segments(payload: dict) -> list[dict]:
    """行为时间轴用：按"行为动词"上色，跟活动块的分类色是两套。"""
    cov = payload.get("coverage") or {}
    behaviors = ((payload.get("report") or {}).get("behaviors")) or []
    try:
        ws = timeutil.parse(cov.get("start_iso", ""))
        we = timeutil.parse(cov.get("end_iso", ""))
    except Exception:
        return []
    span = max(1.0, (we - ws).total_seconds())
    segs = []
    for it in behaviors:
        pair = _seg_window(payload, it)
        if pair is None:
            continue
        start, end = pair
        s = min(max(0.0, (start - ws).total_seconds()), span)
        e = min(max(s + 60.0, (end - ws).total_seconds()), span)
        color = behavior_mod.VERBS.get(it.get("verb", ""), behavior_mod.VERBS["admin"])["color"]
        segs.append({
            "left": round(s / span * 100, 3),
            "width": max(0.2, round((e - s) / span * 100, 3)),
            "color": color,
            "title": f"{it['start']}–{it['end']} {it.get('label', '')}",
            "id": it.get("seg_id", ""),
        })
    return segs


def _ref_map(payload: dict) -> dict:
    return {r["ref_id"]: r for r in payload.get("refs", [])}


def render_day(payload: dict) -> Path:
    env = _env(autoescape=True)
    tpl = env.get_template("day.html.j2")
    day = payload["day"]
    blocks = payload.get("blocks", [])
    data_root = paths.DATA / day
    for b in blocks:
        # 截图有保留期，过期被清理后必须让缩略图优雅消失而不是留一个裂图
        b["_thumb"] = ""
        b["_full"] = ""
        for rel in (b.get("shots") or [])[:1]:
            thumb_rel = rel.replace("shots/", "thumbs/")
            if (data_root / thumb_rel).exists():
                b["_thumb"] = f"../../data/{day}/{thumb_rel}"
                b["_full"] = f"../../data/{day}/{rel}" if (data_root / rel).exists() else b["_thumb"]
            elif (data_root / rel).exists():
                b["_thumb"] = f"../../data/{day}/{rel}"
                b["_full"] = b["_thumb"]
            break
        b["_color"] = CAT_COLOR.get(b.get("category", "other"), CAT_COLOR["other"])
        b["_cat_label"] = CAT_LABEL.get(b.get("category", "other"), "其他")
    refs = _ref_map(payload)
    behaviors = ((payload.get("report") or {}).get("behaviors")) or []
    html = tpl.render(
        d=payload,
        day=day,
        weekday=payload.get("weekday", ""),
        blocks=blocks,
        refs=refs,
        refs_list=payload.get("refs", []),
        segs=_strip_segments(payload),
        bsegs=_behavior_segments(payload),
        behaviors=behaviors,
        rhythm=(payload.get("stats") or {}).get("rhythm") or {},
        verbs=behavior_mod.VERBS,
        ticks=_strip_ticks(payload),
        cat_label=CAT_LABEL,
        extra_head="../assets/board.css",
    )
    out = paths.web_day(day)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(out, html)
    return out


def render_index(cfg) -> Path:
    env = _env(autoescape=True)
    tpl = env.get_template("index.html.j2")
    today = timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)

    reports: list[dict] = []
    for p in sorted(paths.REPORTS.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rep = data.get("report") or {}
        reports.append({
            "day": data.get("day", p.stem),
            "weekday": data.get("weekday", ""),
            "headline": rep.get("headline", ""),
            "status": data.get("status", ""),
            "minutes": (data.get("stats") or {}).get("active_minutes", 0),
            "blocks": (data.get("stats") or {}).get("block_count", 0),
            "acc": len(rep.get("accomplishments") or []),
            "confidence": rep.get("confidence", ""),
            "revision": data.get("revision", 1),
        })
    reports.sort(key=lambda r: r["day"], reverse=True)

    # 最近 30 天热力条
    heat = []
    today_dt = timeutil.parse_day(today)
    by_day = {r["day"]: r for r in reports}
    for i in range(29, -1, -1):
        d = (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        r = by_day.get(d)
        minutes = r["minutes"] if r else 0
        if minutes >= 300:
            level = 4
        elif minutes >= 180:
            level = 3
        elif minutes >= 60:
            level = 2
        elif minutes > 0:
            level = 1
        else:
            level = 0
        heat.append({"day": d, "minutes": minutes, "level": level, "has": bool(r),
                     "missing": (not r) and d < today and d > "2026-01-01"})

    heartbeat = None
    if paths.HEARTBEAT.exists():
        try:
            heartbeat = json.loads(paths.HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            heartbeat = None
    hb_age = None
    if heartbeat and heartbeat.get("ts"):
        try:
            hb_age = int((timeutil.now() - timeutil.parse(heartbeat["ts"])).total_seconds())
        except Exception:
            hb_age = None
    try:
        import shutil

        free_gb = round(shutil.disk_usage(str(paths.ROOT)).free / 1024 ** 3, 1)
    except Exception:
        free_gb = None

    html = tpl.render(
        reports=reports,
        today=today,
        heat=heat,
        heartbeat=heartbeat,
        hb_age=hb_age,
        free_gb=free_gb,
        has_key=cfg.has_api_key(),
        paused=paths.PAUSE_FILE.exists(),
        model=cfg.get("llm.model", ""),
        now=timeutil.now(),
        extra_head="assets/board.css",
    )
    out = paths.WEB / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(out, html)
    return out


def write_assets() -> None:
    """样式内联进模板更简单，这里只保证 assets 目录存在（将来放图标）。"""
    (paths.WEB / "assets").mkdir(parents=True, exist_ok=True)
