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
        # 归档与网页用同一套阅读顺序：成果 → 待办 → 主线 → 过程 → 附录
        rv=reading_view(payload),
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


# ---------------------------------------------------------------- 阅读层派生
# 页面要回答的是"今天推进了什么、还差什么"，不是"采集到了多少块"。
# 下面这些函数把已落盘的 report 归一成阅读结构，全部本地计算、结果可复现；
# 模型没给新字段的旧日报按文本推断，也能按新排版渲染。
# 状态词表与判定只有 behavior.py 一份，写入（validate）与展示（这里）共用。
OUTCOME_STATUS = behavior_mod.OUTCOME_STATUS
outcome_status = behavior_mod.outcome_status

EVIDENCE_SHOTS = 3          # 正文最多几张截图：证据不足就别硬塞
FOLLOWUP_LIMIT = 4          # 待办与卡点最多几条
GAP_MIN_MINUTES = 20        # 缺口小于这个数不值得提
GAP_MERGE_MINUTES = 10      # 两次休息挨得这么近，当成一次


def _hm_offset(hm: str, anchor_min: int) -> int:
    """HH:MM -> 相对工作日窗口起点的分钟偏移，跨零点不会排错。"""
    try:
        m = int(str(hm)[:2]) * 60 + int(str(hm)[3:5])
    except (TypeError, ValueError):
        return anchor_min
    return m + 1440 if m < anchor_min else m


def _shot_score(b: dict) -> int:
    """挑"值得看"的截图：有文字、有错误/版本/命令线索、停留够久的块优先。"""
    text = f"{b.get('title') or ''} {b.get('text') or ''}"
    score = 0
    if b.get("text"):
        score += 1
    if re.search(r"(?i)错误|失败|异常|超时|not connected|timeout|error|exception|版本|version|"
                 r"config|配置|命令|\$ |show |display |v\d+\.\d+", text):
        score += 2
    try:
        if int(b.get("minutes") or 0) >= 3:
            score += 1
    except (TypeError, ValueError):
        pass
    return score


def _pick_shot(payload: dict, block_ids: list[str]) -> dict:
    by_id = {b.get("id"): b for b in payload.get("blocks") or []}
    best, best_score = None, -1
    for bid in block_ids:
        b = by_id.get(bid)
        if not b or not b.get("_thumb"):
            continue
        score = _shot_score(b)
        if score > best_score:
            best, best_score = b, score
    if not best:
        return {}
    return {"thumb": best["_thumb"], "full": best.get("_full") or best["_thumb"],
            "caption": f"{best['start'][11:16]} {best.get('title') or best.get('app') or ''}"[:60]}


def _docs_for(block_ids: list[str], refs: dict, model_refs: list[dict]) -> list[dict]:
    """把模型挑过的引用按活动块挂到成果卡上；没挑过的弱相关链接不进正文。"""
    wanted = set(block_ids or [])
    out, seen = [], set()
    for mr in model_refs:
        rid = mr.get("ref_id")
        if not rid or rid in seen or rid not in refs:
            continue
        if not (set(mr.get("block_ids") or []) & wanted):
            continue
        r = refs[rid]
        seen.add(rid)
        out.append({
            "ref_id": rid, "label": mr.get("label") or r.get("label") or rid,
            "uri": r.get("uri", ""), "kind": r.get("kind", ""),
            "clickable": bool(r.get("clickable")),
        })
    return out[:4]


def outcome_cards(payload: dict, limit: int = 5, max_shots: int = EVIDENCE_SHOTS) -> list[dict]:
    """今日成果：状态｜结果｜意义｜下一步。证据（块 id / 文档 / 截图）附在卡后，默认收起。"""
    rep = payload.get("report") or {}
    refs = _ref_map(payload)
    model_refs = rep.get("references") or []
    raw = [it for it in (rep.get("highlights") or []) if isinstance(it, dict) and it.get("what")]
    source = "highlights"
    if not raw:
        raw = [it for it in (rep.get("accomplishments") or []) if isinstance(it, dict) and it.get("what")]
        source = "accomplishments"
    tomorrow = [str(t.get("item") or "") for t in (rep.get("tomorrow") or [])]

    cards: list[dict] = []
    shots_left = max_shots
    for it in raw[:limit]:
        what = str(it.get("what") or "").strip()
        impact = str(it.get("impact") or "").strip()
        next_step = str(it.get("next") or "").strip()
        status = outcome_status(it.get("status"), what, impact, next_step)
        if not next_step and status in ("waiting", "unresolved", "blocked"):
            # 旧日报没有 next：从"明天"里挑一条说得最近的顶上，挑不到就留空
            for t in tomorrow:
                if any(k and k in t for k in _keywords(what)):
                    next_step = t
                    break
        bids = list(it.get("block_ids") or [])
        shot = _pick_shot(payload, bids) if shots_left > 0 else {}
        if shot:
            shots_left -= 1
        label, cls = OUTCOME_STATUS[status]
        cards.append({
            "status": status, "status_label": label, "status_cls": cls,
            "what": what, "impact": impact, "next": next_step,
            "evidence": str(it.get("evidence") or "").strip(),
            "block_ids": bids, "docs": _docs_for(bids, refs, model_refs),
            "shot": shot, "source": source,
        })
    return cards


def _keywords(text: str) -> list[str]:
    """从成果标题里抓两三个可用的关键词，用来在"明天"里找对应的下一步。"""
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-\.]{2,}|\d{3,}", text or "")
    return list(dict.fromkeys(words))[:4]


def followups(payload: dict, limit: int = FOLLOWUP_LIMIT) -> list[dict]:
    """待办与卡点：真正需要行动的事。模型给了 followups 就用；旧日报从卡点/明天里合成。"""
    rep = payload.get("report") or {}
    out: list[dict] = []
    seen: set[str] = set()

    def add(item, kind, why="", block_ids=None):
        item = str(item or "").strip()
        if not item or item in seen:
            return
        seen.add(item)
        out.append({"item": item[:200], "kind": kind, "why": str(why or "")[:200],
                    "block_ids": list(block_ids or [])})

    for it in rep.get("followups") or []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "").strip().lower()
        if kind not in ("waiting", "blocked", "todo"):
            st = outcome_status(it.get("status"), it.get("item"), it.get("why"))
            kind = {"waiting": "waiting", "unresolved": "blocked",
                    "blocked": "blocked"}.get(st, "todo")
        add(it.get("item"), kind, it.get("why") or it.get("basis"), it.get("block_ids"))
    if out:
        return out[:limit]

    blocked, todo = [], []
    for it in rep.get("friction") or []:
        fix = str(it.get("fix") or "")
        if fix and not behavior_mod.UNRESOLVED.search(fix):
            continue
        blocked.append((it.get("what"), it.get("cost") or it.get("cause") or fix, it.get("block_ids")))
    for it in rep.get("in_progress") or []:
        if it.get("next"):
            blocked.append((it.get("what"), str(it.get("next")), it.get("block_ids")))
    for it in rep.get("tomorrow") or []:
        todo.append((it.get("item"), it.get("why") or "", None))

    for what, why, bids in blocked[:2]:
        add(what, "blocked", why, bids)
    for item, why, bids in todo[:max(0, limit - len(out))]:
        add(item, "todo", why, bids)
    return out[:limit]


def task_span_notes(payload: dict, tasks: list[dict], merged: int, away_count: int) -> dict:
    """主时间轴的辅助说明：合并了多少段、略去多少段锁屏。"""
    total_segs = len((payload.get("report") or {}).get("behaviors") or [])
    return {"segments": total_segs, "tasks": len(tasks), "merged": max(0, merged),
            "away": away_count}


def notable_gaps(payload: dict, tasks: list[dict] | None = None,
                 min_minutes: int = GAP_MIN_MINUTES,
                 merge_minutes: int = GAP_MERGE_MINUTES) -> dict:
    """只报"活动时段内、可能影响判断"的缺口；夜间和正常午休不再逐条罗列。

    活动边界优先取任务段（自动排除收工后的锁屏块）：否则 19:00 收工、20:09 一次锁屏，
    中间就会被写成"68 分钟没有采集"——那只是下班了。
    """
    cov = payload.get("coverage") or {}
    anchor = _hm_offset(cov.get("start") or "04:00", 0)
    blocks = payload.get("blocks") or []
    gaps = payload.get("gaps") or []
    if tasks:
        first, last = str(tasks[0].get("start") or ""), str(tasks[-1].get("end") or "")
    elif blocks:
        first = str(blocks[0].get("start") or "")[11:16]
        last = str(blocks[-1].get("end") or "")[11:16]
    else:
        return {"inside": [], "first": "", "last": "", "window": cov.get("window", "")}
    lo, hi = _hm_offset(first, anchor), _hm_offset(last, anchor)

    inside: list[dict] = []
    for g in gaps:
        try:
            s = _hm_offset(g["start"], anchor)
            e = _hm_offset(g["end"], anchor)
            mins = int(g.get("minutes") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if e <= s:
            e += 1440        # 结束不晚于开始：跨零点（20:13–04:00 的 04:00 是次日）
        if s < lo or e > hi or mins < min_minutes:
            continue          # 采集开始前 / 收工后的空白不是"缺口"
        inside.append({"start": g["start"], "end": g["end"], "minutes": mins, "_s": s, "_e": e})

    merged: list[dict] = []
    for g in inside:
        if merged and g["_s"] - merged[-1]["_e"] < merge_minutes:
            merged[-1]["end"] = g["end"]
            merged[-1]["_e"] = g["_e"]
            merged[-1]["minutes"] += g["minutes"]
        else:
            merged.append(dict(g))
    return {"inside": merged, "first": first, "last": last, "window": cov.get("window", "")}


def head_metrics(payload: dict, cards: list[dict], todos: list[dict]) -> list[dict]:
    """首屏四个指标：只留读者真正想知道的，采集量移到附录。"""
    stats = payload.get("stats") or {}
    meet = int(round(float((stats.get("by_category") or {}).get("meet", 0) or 0)))
    return [
        {"n": len(cards), "unit": "项", "k": "今日成果"},
        {"n": len(todos), "unit": "项", "k": "待跟进"},
        {"n": round(float(stats.get("active_minutes", 0) or 0) / 60, 1), "unit": "h", "k": "有效活动"},
        {"n": meet, "unit": "分", "k": "会议"},
    ]


def _task_segments(payload: dict, tasks: list[dict]) -> list[dict]:
    """任务级色带：一组一个色块，图例就是任务本身。"""
    cov = payload.get("coverage") or {}
    try:
        ws = timeutil.parse(cov.get("start_iso", ""))
        we = timeutil.parse(cov.get("end_iso", ""))
    except Exception:
        return []
    span = max(1.0, (we - ws).total_seconds())
    segs = []
    for t in tasks:
        pair = _seg_window(payload, t)
        if pair is None:
            continue
        start, end = pair
        s = min(max(0.0, (start - ws).total_seconds()), span)
        e = min(max(s + 60.0, (end - ws).total_seconds()), span)
        color = behavior_mod.VERBS.get(t.get("verb", ""), behavior_mod.VERBS["admin"])["color"]
        segs.append({
            "left": round(s / span * 100, 3),
            "width": max(0.2, round((e - s) / span * 100, 3)),
            "color": color,
            "title": f"{t['start']}–{t['end']} {t.get('label', '')}",
            "id": t.get("task_id", ""),
        })
    return segs


def reading_view(payload: dict) -> dict:
    """一次算完首屏要用的东西，模板只负责摆位置。"""
    rep = payload.get("report") or {}
    grouped = behavior_mod.group_tasks(
        rep.get("behaviors") or [],
        day=payload.get("day", ""),
        window_start=_coverage_start(payload),
    )
    tasks = grouped["tasks"]
    cards = outcome_cards(payload)
    todos = followups(payload)
    by_id = {b.get("id"): b for b in payload.get("blocks") or []}

    # 任务段挂上落在这个时间段里的成果：主线一栏直接写"这段的结果"
    highlight_pairs = []
    for c in cards:
        for bid in c["block_ids"]:
            b = by_id.get(bid)
            if b:
                highlight_pairs.append((str(b.get("start") or "")[11:16], c))
                break
    for t in tasks:
        hits = [c for hm, c in highlight_pairs if t["start"] <= hm <= t["end"]]
        t["outcome"] = hits[0]["what"] if hits else ""
        t["outcome_status"] = hits[0]["status_label"] if hits else ""
        t["block_ids"] = [bid for c in hits for bid in c["block_ids"]][:8]

    # "明天"里已经变成待办的那几条不再重复列一遍
    todo_items = [t["item"] for t in todos]

    def _dup(text: str) -> bool:
        text = str(text or "").strip()
        return any(text and (text in x or x in text) for x in todo_items)

    tomorrow_extra = [t for t in (rep.get("tomorrow") or [])
                      if isinstance(t, dict) and t.get("item") and not _dup(t["item"])]

    return {
        "tasks": tasks,
        "task_notes": task_span_notes(payload, tasks, grouped["merged"], grouped["away_count"]),
        "task_segs": _task_segments(payload, tasks),
        "dropped_tasks": grouped["dropped"],
        "cards": cards,
        "todos": todos,
        "tomorrow_extra": tomorrow_extra,
        "metrics": head_metrics(payload, cards, todos),
        "gaps": notable_gaps(payload, tasks),
    }


def _coverage_start(payload: dict):
    try:
        return timeutil.parse((payload.get("coverage") or {}).get("start_iso", ""))
    except Exception:
        return None


def coverage_state(payload: dict) -> tuple[bool, str]:
    """报告状态文案。

    次日 03:01 出的终版，覆盖到 03:01 是正常的收尾，不该写"当日未结束、21:00 后还会出终版"——
    那句话只对"当天白天出的增量版"成立。
    """
    cov = payload.get("coverage") or {}
    gen = str(payload.get("generated_at") or "")
    day = str(payload.get("day") or "")
    settled = len(gen) >= 10 and len(day) == 10 and gen[:10] > day
    until = cov.get("until") or ""
    if cov.get("complete"):
        return True, "完整一天"
    if settled:
        return True, f"已收尾 · 覆盖到 {until}"
    return False, f"截至 {until} · 当天还没结束"


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

    cov = payload.get("coverage")
    if isinstance(cov, dict):
        settled, note = coverage_state(payload)
        cov["settled"], cov["note"] = settled, note

    rv = reading_view(payload)
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
        rv=rv,
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
            "acc": len(rep.get("highlights") or rep.get("accomplishments") or []),
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
