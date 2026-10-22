"""人工修订与线下活动补录。

数据落在 `data/<day>/manual.json`，与自动产物完全分离：
- 重跑、重生成、retention 清理都不会动它；
- 每次生成日报时重新套用一遍——"用户修订永远赢"。

结构（只增字段，缺项按空处理）：
{
  "offline": [            # 线下活动：屏幕看不到的工作（会议、外出、思考）
    {"start": "10:00", "end": "11:00", "label": "线下会议", "note": "…"}
  ],
  "behavior_overrides": [ # 行为段改标：改模型给的 verb/label
    {"seg_id": "seg-03", "verb": "meeting", "label": "评审日报方案", "note": "…"}
  ],
  "notes": ""             # 给当天日报的一句话备注
}
"""
from __future__ import annotations

import json

import paths
import render


def path(day: str):
    return paths.day_dir(day) / "manual.json"


def empty() -> dict:
    return {"offline": [], "behavior_overrides": [], "notes": ""}


def load(day: str) -> dict:
    p = path(day)
    if not p.exists():
        return empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty()
    if not isinstance(data, dict):
        return empty()
    data.setdefault("offline", [])
    data.setdefault("behavior_overrides", [])
    data.setdefault("notes", "")
    data["offline"] = [o for o in data["offline"] if isinstance(o, dict) and o.get("label")]
    data["behavior_overrides"] = [o for o in data["behavior_overrides"]
                                  if isinstance(o, dict) and o.get("seg_id")]
    data["notes"] = str(data["notes"] or "")[:1000]
    return data


def save(day: str, data: dict) -> None:
    p = path(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    render.write_text_atomic(p, json.dumps(data, ensure_ascii=False, indent=2))


def _norm_hm(v) -> str:
    """'9:5' -> '09:05'；小时/分钟越界返回空串（不能让 25:00 混进时间轴）。"""
    s = str(v or "").strip()
    hh, _, mm = s.partition(":")
    try:
        h, m = int(hh), int(mm)
    except ValueError:
        return ""
    if not 0 <= h <= 23 or not 0 <= m <= 59 or not mm.strip():
        return ""
    return f"{h:02d}:{m:02d}"


def add_offline(day: str, start: str, end: str, label: str, note: str = "") -> dict:
    """补录一条线下活动（时间只支持当天 HH:MM，跨午夜请拆成两条）。

    同一时段同一名称视为重复补录：只更新备注，不叠加两条一样的记录。
    """
    data = load(day)
    s, e = _norm_hm(start), _norm_hm(end)
    if not s or not e:
        raise ValueError(f"时间格式应为 HH:MM（00:00–23:59），收到 {start} – {end}")
    if e <= s:
        raise ValueError("结束时间必须晚于开始时间（跨午夜请拆成两条）")
    name = str(label or "").strip()
    if not name:
        raise ValueError("label 不能为空")
    clean = {
        "start": s, "end": e,
        "label": name[:60],
        "note": str(note or "").strip()[:200],
    }
    for old in data["offline"]:
        if (old.get("start"), old.get("end"), old.get("label")) == (s, e, clean["label"]):
            if clean["note"]:
                old["note"] = clean["note"]
            save(day, data)
            return data
    data["offline"].append(clean)
    save(day, data)
    return data


def set_override(day: str, seg_id: str, verb: str = "", label: str = "", note: str = "") -> dict:
    """改某一段行为的标签；verb 留空表示沿用模型结果。"""
    data = load(day)
    seg_id = str(seg_id or "").strip()
    if not seg_id:
        raise ValueError("seg_id 不能为空")
    if not (verb or label or note):
        raise ValueError("verb / label / note 至少填一项")
    data["behavior_overrides"] = [o for o in data["behavior_overrides"] if o.get("seg_id") != seg_id]
    data["behavior_overrides"].append({
        "seg_id": seg_id,
        "verb": str(verb or "").strip(),
        "label": str(label or "").strip()[:28],
        "note": str(note or "").strip()[:160],
    })
    save(day, data)
    return data


def set_notes(day: str, notes: str) -> dict:
    data = load(day)
    data["notes"] = str(notes or "").strip()[:1000]
    save(day, data)
    return data


def apply_overrides(report_data: dict, mdata: dict) -> None:
    """把 manual.json 套用到日报数据上（report 在模型校验之后调用）。"""
    if not isinstance(report_data, dict):
        return
    behaviors = report_data.get("behaviors") or []
    for ov in mdata.get("behavior_overrides") or []:
        for it in behaviors:
            if it.get("seg_id") != ov.get("seg_id"):
                continue
            if ov.get("verb"):
                it["verb"] = str(ov["verb"]).strip()
            if ov.get("label"):
                it["label"] = str(ov["label"]).strip()[:28]
            if ov.get("note"):
                it["detail"] = str(ov["note"]).strip()[:160]
            it["source"] = "manual"
            it["confidence"] = "high"

    offline = [o for o in (mdata.get("offline") or []) if o.get("label")]
    report_data["offline"] = offline
    # 不往 summary_md 里拼文字：模板有独立的「人工补录」小节，
    # 拼进正文会在页面、Markdown、界面里各重复一遍。
    notes = str(mdata.get("notes") or "").strip()
    if notes:
        prev = str(report_data.get("notes_for_human") or "").strip()
        report_data["notes_for_human"] = (prev + ("\n" if prev else "") + f"人工备注：{notes}")[:500]
