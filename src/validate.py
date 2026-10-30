"""模型输出校验：把"看起来像"的 JSON 变成"确实能用"的结构。

三道闸：字段补全 -> 枚举/类型收敛 -> 引用核验（block_ids / ref_id / seg_id 必须真实存在）
"""
from __future__ import annotations

import re

import behavior as behavior_mod

CATEGORIES = {"dev", "doc", "meet", "comm", "web", "other"}
STATUSES = {"done", "partial", "blocked"}
CONFIDENCES = ("high", "medium", "low")
_TIME = re.compile(r"^\d{1,2}:\d{2}$")

_LIST_FIELDS = ["timeline", "accomplishments", "in_progress", "blockers", "decisions",
                "learnings", "tomorrow", "time_breakdown", "apps", "entities", "references",
                "behaviors", "highlights", "followups", "friction", "insight"]


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v]
    return []


def _norm_time(s) -> str:
    if isinstance(s, str) and _TIME.match(s.strip()):
        h, m = s.strip().split(":")
        return f"{int(h):02d}:{int(m):02d}"
    return ""


def _filter_ids(ids, valid: set[str]) -> tuple[list[str], int]:
    out, dropped = [], 0
    for i in _as_list(ids):
        if isinstance(i, str) and i in valid:
            if i not in out:
                out.append(i)
        else:
            dropped += 1
    return out, dropped


def validate_report(data: dict, block_ids: set[str], ref_ids: set[str],
                    sessions: list | None = None) -> tuple[dict, list[str]]:
    issues: list[str] = []
    out: dict = {"schema_version": 1}

    headline = str(data.get("headline") or "").strip()
    if not headline:
        issues.append("headline 为空")
    out["headline"] = headline[:80]

    out["opening_md"] = str(data.get("opening_md") or "").strip()[:900]

    summary = str(data.get("summary_md") or "").strip()
    if not summary:
        issues.append("summary_md 为空")
    out["summary_md"] = summary

    dropped_ids = 0

    # ---- 行为时间轴：模型只交标签，时间一律取本地分段
    raw_beh = _as_list(data.get("behaviors"))
    if sessions:
        known = {s.id for s in sessions}
        picked, unknown = [], 0
        for it in raw_beh:
            if not isinstance(it, dict):
                continue
            sid = str(it.get("seg_id") or "").strip()
            if sid not in known:
                unknown += 1
                continue
            picked.append(it)
        if unknown:
            issues.append(f"剔除了 {unknown} 个不存在的 seg_id")
        out["behaviors"] = behavior_mod.merge(picked, sessions)
    else:
        out["behaviors"] = [it for it in raw_beh if isinstance(it, dict)]

    # ---- 成果（兼容旧字段 accomplishments）
    highlights = []
    for it in _as_list(data.get("highlights")):
        if not isinstance(it, dict):
            continue
        what = str(it.get("what") or "").strip()
        if not what:
            continue
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        if not bids:
            issues.append(f"highlights「{what[:24]}…」未关联 block_ids，结论缺少证据回链")
        impact = str(it.get("impact") or "")[:200]
        nxt = str(it.get("next") or "")[:200]
        highlights.append({
            "what": what[:200],
            "evidence": str(it.get("evidence") or "")[:240],
            "impact": impact,
            # 状态和下一步是首屏成果卡的全部信息量：模型没给就按文字判一次，
            # 判定函数只有 behavior.outcome_status 一处，页面展示时不会再改口径。
            "status": behavior_mod.outcome_status(it.get("status"), what, impact, nxt),
            "next": nxt,
            "block_ids": bids,
        })
    if not highlights:
        for it in _as_list(data.get("accomplishments")):
            if not isinstance(it, dict) or not it.get("what"):
                continue
            art = it.get("artifact")
            claim = art.get("label") if isinstance(art, dict) else (art if isinstance(art, str) else "")
            what = str(it["what"])[:200]
            impact = str(it.get("impact") or "")[:200]
            highlights.append({
                "what": what,
                "evidence": str(claim or "")[:240],
                "impact": impact,
                "status": behavior_mod.outcome_status(it.get("status"), what, impact),
                "next": str(it.get("next") or "")[:200],
                "block_ids": _filter_ids(it.get("block_ids"), block_ids)[0],
            })
    out["highlights"] = highlights

    # ---- 待办与卡点：首屏第二块内容，只留"还需要动"的事
    followups = []
    for it in _as_list(data.get("followups")):
        if not isinstance(it, dict):
            continue
        item = str(it.get("item") or "").strip()
        if not item:
            continue
        kind = str(it.get("kind") or "").strip().lower()
        if kind not in ("waiting", "blocked", "todo"):
            st = behavior_mod.outcome_status(it.get("status"), item, it.get("why"))
            kind = {"waiting": "waiting", "blocked": "blocked", "unresolved": "blocked"}.get(st, "todo")
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        followups.append({
            "item": item[:200],
            "kind": kind,
            "why": str(it.get("why") or it.get("basis") or "")[:200],
            "block_ids": bids,
        })
    out["followups"] = followups

    # ---- 卡点（带代价）
    friction = []
    for it in _as_list(data.get("friction")):
        if not isinstance(it, dict):
            continue
        what = str(it.get("what") or "").strip()
        if not what:
            continue
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        if not bids:
            issues.append(f"friction「{what[:24]}…」未关联 block_ids，判断缺少证据回链")
        friction.append({
            "what": what[:200],
            "cause": str(it.get("cause") or "")[:200],
            "cost": str(it.get("cost") or "")[:120],
            "fix": str(it.get("fix") or "")[:200],
            "block_ids": bids,
        })
    if not friction:
        for it in _as_list(data.get("blockers")):
            if not isinstance(it, dict) or not it.get("what"):
                continue
            friction.append({
                "what": str(it["what"])[:200],
                "cause": "",
                "cost": "",
                "fix": str(it.get("detail") or "")[:200],
                "block_ids": _filter_ids(it.get("block_ids"), block_ids)[0],
            })
    out["friction"] = friction

    # ---- 洞察
    insight = []
    for it in _as_list(data.get("insight")):
        if not isinstance(it, dict):
            continue
        obs = str(it.get("observation") or "").strip()
        if not obs:
            continue
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        insight.append({
            "observation": obs[:240],
            "evidence": str(it.get("evidence") or "")[:240],
            "action": str(it.get("action") or "")[:200],
            "block_ids": bids,
        })
    out["insight"] = insight

    timeline = []
    for it in _as_list(data.get("timeline")):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        cat = str(it.get("category") or "other").lower()
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        timeline.append({
            "start": _norm_time(it.get("start")),
            "end": _norm_time(it.get("end")),
            "title": title[:120],
            "app": str(it.get("app") or ""),
            "category": cat if cat in CATEGORIES else "other",
            "block_ids": bids,
        })
    out["timeline"] = timeline

    acc = []
    for it in _as_list(data.get("accomplishments")):
        if not isinstance(it, dict):
            continue
        what = str(it.get("what") or "").strip()
        if not what:
            continue
        status = str(it.get("status") or "done").lower()
        artifact = it.get("artifact")
        art = {"label": "", "ref_id": None}
        if isinstance(artifact, dict):
            art["label"] = str(artifact.get("label") or "")[:120]
            rid = artifact.get("ref_id")
            if isinstance(rid, str) and rid in ref_ids:
                art["ref_id"] = rid
            elif rid:
                issues.append(f"artifact.ref_id 不存在: {rid}")
        elif isinstance(artifact, str):
            art["label"] = artifact[:120]
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        acc.append({
            "what": what[:200],
            "status": status if status in STATUSES else "done",
            "impact": str(it.get("impact") or "")[:300],
            "artifact": art,
            "block_ids": bids,
        })
    out["accomplishments"] = acc

    for key in ("in_progress",):
        items = []
        for it in _as_list(data.get(key)):
            if not isinstance(it, dict):
                continue
            what = str(it.get("what") or "").strip()
            if not what:
                continue
            bids, d = _filter_ids(it.get("block_ids"), block_ids)
            dropped_ids += d
            items.append({
                "what": what[:200],
                "state": str(it.get("state") or "")[:200],
                "next": str(it.get("next") or "")[:200],
                "block_ids": bids,
            })
        out[key] = items

    items = []
    for it in _as_list(data.get("blockers")):
        if not isinstance(it, dict):
            continue
        what = str(it.get("what") or "").strip()
        if not what:
            continue
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        items.append({"what": what[:200], "detail": str(it.get("detail") or "")[:300], "block_ids": bids})
    out["blockers"] = items

    items = []
    for it in _as_list(data.get("decisions")):
        if not isinstance(it, dict):
            continue
        what = str(it.get("what") or "").strip()
        if not what:
            continue
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        items.append({"what": what[:200], "why": str(it.get("why") or "")[:300], "block_ids": bids})
    out["decisions"] = items

    items = []
    for it in _as_list(data.get("learnings")):
        if not isinstance(it, dict):
            continue
        what = str(it.get("what") or "").strip()
        if not what:
            continue
        bids, d = _filter_ids(it.get("block_ids"), block_ids)
        dropped_ids += d
        items.append({"what": what[:250], "block_ids": bids})
    out["learnings"] = items

    out["tomorrow"] = [
        {"item": str(it.get("item") or "")[:200], "why": str(it.get("why") or "")[:200],
         "when": str(it.get("when") or "")[:40]}
        for it in _as_list(data.get("tomorrow")) if isinstance(it, dict) and it.get("item")
    ]

    tb = []
    for it in _as_list(data.get("time_breakdown")):
        if not isinstance(it, dict):
            continue
        try:
            minutes = int(float(it.get("minutes") or 0))
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0:
            continue
        tb.append({"category": str(it.get("category") or "其他")[:20],
                   "minutes": minutes, "note": str(it.get("note") or "")[:120]})
    out["time_breakdown"] = tb

    out["apps"] = [
        {"app": str(it.get("app") or "")[:60], "minutes": int(float(it.get("minutes") or 0)),
         "role": str(it.get("role") or "")[:120]}
        for it in _as_list(data.get("apps")) if isinstance(it, dict) and it.get("app")
    ]

    out["entities"] = [
        {"kind": str(it.get("kind") or "other")[:20], "name": str(it.get("name") or "")[:120],
         "block_ids": _filter_ids(it.get("block_ids"), block_ids)[0]}
        for it in _as_list(data.get("entities")) if isinstance(it, dict) and it.get("name")
    ]

    refs = []
    for it in _as_list(data.get("references")):
        if not isinstance(it, dict):
            continue
        rid = it.get("ref_id")
        if not isinstance(rid, str) or rid not in ref_ids:
            if rid:
                issues.append(f"references.ref_id 不存在: {rid}")
            continue
        refs.append({
            "ref_id": rid,
            "label": str(it.get("label") or "")[:120],
            "why": str(it.get("why") or "")[:200],
            "block_ids": _filter_ids(it.get("block_ids"), block_ids)[0],
        })
    out["references"] = refs

    conf = str(data.get("confidence") or "medium").lower()
    out["confidence"] = conf if conf in ("high", "medium", "low") else "medium"
    out["notes_for_human"] = str(data.get("notes_for_human") or "")[:500]

    # 未使用的字段原样保留，方便以后加字段不丢数据
    for k, v in data.items():
        if k not in out:
            out[k] = v

    if dropped_ids:
        issues.append(f"剔除了 {dropped_ids} 个不存在的 block_id 引用")
    return out, issues


def empty_report(reason: str) -> dict:
    return {
        "schema_version": 1,
        "headline": reason or "这一天没有可用记录",
        "opening_md": "",
        "summary_md": "",
        "behaviors": [],
        "highlights": [],
        "followups": [],
        "friction": [],
        "insight": [],
        "timeline": [],
        "accomplishments": [],
        "in_progress": [],
        "blockers": [],
        "decisions": [],
        "learnings": [],
        "tomorrow": [],
        "time_breakdown": [],
        "apps": [],
        "entities": [],
        "references": [],
        "confidence": "low",
        "notes_for_human": reason,
    }
