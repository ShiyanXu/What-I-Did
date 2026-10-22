"""跨日检索：在本地记录里找"这件事什么时候做过"。

只在本地扫描，不联网、不调模型。命中的文本在写盘时已经过一遍脱敏，
但日报正文与标题仍可能带敏感信息——所以默认只输出命中片段，不整篇倾倒。

数据来源：
- `data/<day>/events.jsonl` 的窗口标题与应用名
- `data/<day>/ocr.jsonl` 的脱敏文字
- `reports/<day>.json` 的日报叙述、关键进展、卡点、洞察

关键词按空格/逗号切分，多个关键词取"全部命中"（AND），大小写不敏感。
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

import paths
import timeutil

KINDS = ("title", "ocr", "report")
KIND_LABEL = {"title": "窗口标题", "ocr": "屏幕文字", "report": "日报"}
MAX_HITS_PER_DAY = 30      # 单日命中上限：避免一个高频词把整天记录刷满
SNIPPET_WIDTH = 72

_SPLIT = re.compile(r"[\s,，、;；]+")


def parse_keywords(raw) -> list[str]:
    """把用户输入拆成关键词列表；去重、去空、统一小写。"""
    items = [str(x) for x in raw] if isinstance(raw, (list, tuple)) else _SPLIT.split(str(raw or ""))
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in out:
            out.append(key)
    return out


def _match(text: str, keywords: list[str]) -> bool:
    """全部关键词都出现才算命中（AND）。"""
    low = (text or "").lower()
    if not low:
        return False
    return all(k in low for k in keywords)


def _snippet(text: str, keywords: list[str], width: int = SNIPPET_WIDTH) -> str:
    """截命中位置附近的一段，避免把整屏文字倒出来。"""
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    low = flat.lower()
    pos = -1
    for key in keywords:
        pos = low.find(key)
        if pos >= 0:
            break
    if pos < 0:
        return flat[:width]
    start = max(0, pos - width // 3)
    piece = flat[start:start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(flat) else "")


def _day_list(days: int, cfg) -> list[str]:
    """最近 days 天（含今天），新的一天排前面。"""
    today = timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)
    base = timeutil.parse_day(today)
    return [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def _scan_events(day: str, keywords: list[str], hits: list[dict]) -> int:
    """扫事件流：命中标题或应用名。返回扫描行数（用于报告扫描量）。"""
    p = paths.events_path(day)
    if not p.exists():
        return 0
    scanned = 0
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                scanned += 1
                try:
                    rec = json.loads(s)
                except json.JSONDecodeError:
                    continue
                title = str(rec.get("title") or "")
                app = str(rec.get("app") or "")
                if not _match(f"{title} {app}", keywords):
                    continue
                try:
                    at = timeutil.fmt_hm(timeutil.parse(rec["ts"]))
                except Exception:
                    at = ""
                hits.append({
                    "day": day,
                    "kind": "title",
                    "at": at,
                    "app": app,
                    "snippet": _snippet(f"{title} {app}".strip(), keywords),
                })
                if len(hits) >= MAX_HITS_PER_DAY:
                    break
    except OSError:
        return scanned
    return scanned


def _scan_ocr(day: str, keywords: list[str], hits: list[dict]) -> int:
    """扫 OCR 流：命中屏幕文字。ts 是 epoch 秒。"""
    p = paths.ocr_path(day)
    if not p.exists():
        return 0
    scanned = 0
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                scanned += 1
                try:
                    rec = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if rec.get("status") not in (None, "ok"):
                    continue
                text = str(rec.get("text") or "")
                if not _match(text, keywords):
                    continue
                try:
                    at = timeutil.fmt_hm(timeutil.parse(
                        timeutil.iso(__import__("datetime").datetime.fromtimestamp(
                            float(rec.get("ts") or 0), timeutil.TZ))))
                except Exception:
                    at = ""
                hits.append({
                    "day": day,
                    "kind": "ocr",
                    "at": at,
                    "app": "",
                    "snippet": _snippet(text, keywords),
                })
                if len(hits) >= MAX_HITS_PER_DAY:
                    break
    except OSError:
        return scanned
    return scanned


def _report_blobs(data: dict) -> list[tuple[str, str]]:
    """把日报里可检索的正文摊成 (字段名, 文本)。缺字段按空处理。"""
    rep = data.get("report") or {}
    out: list[tuple[str, str]] = []
    for key, label in (("headline", "标题"), ("opening_md", "开场"),
                       ("summary_md", "过程"), ("notes_for_human", "备注")):
        val = rep.get(key)
        if isinstance(val, str) and val:
            out.append((label, val))
    for key, label in (("highlights", "关键进展"), ("friction", "卡点"),
                       ("insight", "洞察"), ("tomorrow", "明日")):
        for it in rep.get(key) or []:
            if isinstance(it, dict):
                text = " ".join(str(v) for v in it.values() if isinstance(v, (str, int, float)))
            else:
                text = str(it)
            if text:
                out.append((label, text))
    for it in rep.get("behaviors") or []:
        if isinstance(it, dict) and (it.get("label") or it.get("detail")):
            out.append(("行为", f"{it.get('label', '')} {it.get('detail', '')}".strip()))
    return out


def _scan_report(day: str, keywords: list[str], hits: list[dict]) -> bool:
    """扫日报 JSON：命中叙述与条目。返回该天是否有日报文件。"""
    p = paths.report_json(day)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True          # 文件在但坏了：算作"有"，但没命中
    if not isinstance(data, dict):
        return True
    for label, text in _report_blobs(data):
        if not _match(text, keywords):
            continue
        hits.append({
            "day": day,
            "kind": "report",
            "at": "",
            "app": label,
            "snippet": _snippet(text, keywords, SNIPPET_WIDTH * 2),
        })
        if len(hits) >= MAX_HITS_PER_DAY:
            break
    return True


def search(raw_keywords, days: int = 14, cfg=None, limit: int = 200,
           kinds: tuple[str, ...] = KINDS) -> dict:
    """跨日检索。返回按天归并的命中，以及扫描量，便于判断"是不是范围太小"。

    - 关键词为空 -> ValueError（不允许空查询把整库倒出来）
    - days 越界 -> ValueError
    - 单日命中上限 MAX_HITS_PER_DAY，总量上限 limit
    """
    keywords = parse_keywords(raw_keywords)
    if not keywords:
        raise ValueError("至少给一个关键词")
    if cfg is None:
        import config as cfgmod

        cfg = cfgmod.load()
    days = int(days)
    if days < 1 or days > 365:
        raise ValueError("days 应在 1–365 之间")
    limit = max(1, int(limit))
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise ValueError(f"未知检索范围：{'、'.join(unknown)}")

    hits: list[dict] = []
    scanned = {"days": 0, "events": 0, "ocr": 0, "reports": 0}
    truncated = False

    for day in _day_list(days, cfg):
        if len(hits) >= limit:
            truncated = True
            break
        day_hits: list[dict] = []
        scanned["days"] += 1
        if "title" in kinds:
            scanned["events"] += _scan_events(day, keywords, day_hits)
        if "ocr" in kinds:
            scanned["ocr"] += _scan_ocr(day, keywords, day_hits)
        if "report" in kinds and _scan_report(day, keywords, day_hits):
            scanned["reports"] += 1
        if len(day_hits) > MAX_HITS_PER_DAY:
            day_hits = day_hits[:MAX_HITS_PER_DAY]
            truncated = True
        hits.extend(day_hits)

    if len(hits) > limit:
        hits = hits[:limit]
        truncated = True

    by_day: dict[str, list[dict]] = {}
    for hit in hits:
        by_day.setdefault(hit["day"], []).append(hit)

    return {
        "keywords": keywords,
        "days": days,
        "count": len(hits),
        "truncated": truncated,
        "scanned": scanned,
        "hits": hits,
        "by_day": [{"day": d, "hits": v} for d, v in by_day.items()],
    }


def render_text(result: dict) -> str:
    """给命令行看的纯文本结果。"""
    keys = "、".join(result.get("keywords") or [])
    lines = [f"检索：{keys}　范围：最近 {result.get('days', 0)} 天　命中 {result.get('count', 0)} 条"]
    if not result.get("hits"):
        lines.append("")
        lines.append("没有命中。可能的原因：关键词拼写不同、保留期已过、或那段时间没有采集。")
        lines.append(f"扫描量：{result['scanned']['days']} 天 / 事件 {result['scanned']['events']} 行 / "
                     f"OCR {result['scanned']['ocr']} 行 / 日报 {result['scanned']['reports']} 份")
        return "\n".join(lines)
    for block in result.get("by_day") or []:
        lines.append("")
        lines.append(f"## {block['day']}（{len(block['hits'])} 条）")
        for hit in block["hits"]:
            head = f"- {hit['at'] or '--:--'}　[{KIND_LABEL.get(hit['kind'], hit['kind'])}]"
            if hit.get("app") and hit["kind"] == "title":
                head += f" {hit['app']}"
            lines.append(head)
            lines.append(f"  {hit['snippet']}")
    if result.get("truncated"):
        lines.append("")
        lines.append("（结果已截断，缩小关键词或减少天数可以看得更细）")
    return "\n".join(lines)
