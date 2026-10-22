"""安全分享版导出：把一份日报变成可以发给别人的 Markdown。

与私人归档的区别（默认全部生效）：
- 不带截图、不带缩略图、不带 OCR 原文摘录；
- 不带 block_id / seg_id（内部锚点对外无意义，还会暴露结构）；
- 不带本机路径（`C:\\Users\\...`、`/Users/...`、`data/<day>/...` 一律换成「本机路径」）；
- 不带 `notes_for_human` 与数据缺口细节（那是给自己看的）；
- 最后整篇再过一遍 `privacy.redact`，密钥/手机号/身份证/邮箱照旧抹掉。

导出的是"叙述与结论"，不是原始证据库。想连证据一起给，应该先问自己是否真的需要。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import paths
import privacy
import timeutil

# 本机路径：盘符、用户目录、相对数据目录、UNC、Git Bash 的 /e/... 写法
# 尾部遇到空格或中文标点就停：否则会把「（44 分钟）」一起吞掉，句子就读不通了
_TAIL = r"[^\s|，。；、（）()\[\]【】「」『』]*"
_PATH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"(?i)\bmingw64:/{_TAIL}"), "本机路径"),
    (re.compile(rf"[A-Za-z]:[\\/]{_TAIL}"), "本机路径"),
    (re.compile(rf"\\\\{_TAIL}"), "网络路径"),
    # Git Bash：/e/Self/Project/xxx（单字母盘符 + 至少两段）
    (re.compile(rf"(?:^|(?<=[\s（(「]))/[a-z]/[\w.\-]+/{_TAIL}"), "本机路径"),
    (re.compile(rf"(?:\.\./)+(?:data|reports|web)/{_TAIL}"), "本机路径"),
    (re.compile(rf"(?<![\w/])(?:data|reports|web|state)/\d{{4}}-\d{{2}}-\d{{2}}{_TAIL}"), "本机路径"),
    (re.compile(rf"/Users/{_TAIL}"), "本机路径"),
    (re.compile(rf"/home/{_TAIL}"), "本机路径"),
]

_SAFE_URL = re.compile(r"^https?://", re.I)


class ExportError(RuntimeError):
    """导出条件不满足（比如那天没有日报）。"""


def clean_paths(text: str) -> str:
    """把本机路径换成占位符，保留句子可读性。"""
    out = str(text or "")
    for pat, repl in _PATH_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _clean(text) -> str:
    return clean_paths(str(text or "")).strip()


def _items(value) -> list:
    return value if isinstance(value, list) else []


def _stats_table(stats: dict, cat_label: dict) -> list[str]:
    rows = ["| 分类 | 有效活动 |", "|---|---|"]
    for key, minutes in (stats.get("by_category") or {}).items():
        rows.append(f"| {cat_label.get(key, key)} | {int(round(float(minutes or 0)))} 分 |")
    if len(rows) == 2:
        return []
    return rows


def build_md(day: str, data: dict, include_evidence: bool = True,
             cat_label: dict | None = None) -> str:
    """把日报 JSON 摊成分享版 Markdown。缺字段一律按空处理，不抛异常。"""
    labels = cat_label or {}
    rep = data.get("report") or {}
    stats = data.get("stats") or {}
    weekday = data.get("weekday") or ""
    status = str(data.get("status") or "")

    lines = [f"# {day} {weekday} 工作日报".rstrip(), ""]
    if status == "degraded":
        lines.append("> 本份为本地时间线版：模型未参与，只有客观统计与时间轴。")
        lines.append("")
    elif status == "empty":
        lines.append("> 当天有效活动很少，正文仅保留统计与说明。")
        lines.append("")

    if rep.get("headline"):
        lines.append(f"**{_clean(rep['headline'])}**")
        lines.append("")

    if rep.get("opening_md"):
        lines.append(_clean(rep["opening_md"]))
        lines.append("")

    if rep.get("summary_md"):
        lines.append("## 过程")
        lines.append("")
        lines.append(_clean(rep["summary_md"]))
        lines.append("")

    table = _stats_table(stats, labels)
    if table:
        lines.append("## 时间分布")
        lines.append("")
        lines.extend(table)
        lines.append("")

    behaviors = [b for b in _items(rep.get("behaviors")) if isinstance(b, dict)]
    # 过程叙述里通常已经把时间轴讲了一遍；没有叙述时才单独铺行为段，避免同一段内容出现两次
    if behaviors and not rep.get("summary_md"):
        lines.append("## 行为时间轴")
        lines.append("")
        for it in behaviors:
            label = _clean(it.get("label"))
            if not label:
                continue
            tail = f"　{_clean(it.get('detail'))}" if _clean(it.get("detail")) else ""
            lines.append(f"- {it.get('start', '')}–{it.get('end', '')}　{label}{tail}")
        lines.append("")

    highlights = [h for h in _items(rep.get("highlights")) if isinstance(h, dict)]
    if highlights:
        lines.append("## 关键进展")
        lines.append("")
        for it in highlights:
            what = _clean(it.get("what"))
            if not what:
                continue
            lines.append(f"- {what}")
            if it.get("impact"):
                lines.append(f"  - 影响：{_clean(it['impact'])}")
            if include_evidence and it.get("evidence"):
                lines.append(f"  - 依据：{_clean(it['evidence'])}")
        lines.append("")

    friction = [f for f in _items(rep.get("friction")) if isinstance(f, dict)]
    if friction:
        lines.append("## 卡点")
        lines.append("")
        for it in friction:
            what = _clean(it.get("what"))
            if not what:
                continue
            lines.append(f"- {what}")
            for label, key in (("原因", "cause"), ("代价", "cost"), ("处理", "fix")):
                if it.get(key):
                    lines.append(f"  - {label}：{_clean(it[key])}")
        lines.append("")

    insight = [i for i in _items(rep.get("insight")) if isinstance(i, dict)]
    if insight:
        lines.append("## 观察")
        lines.append("")
        for it in insight:
            obs = _clean(it.get("observation"))
            if not obs:
                continue
            lines.append(f"- {obs}")
            if include_evidence and it.get("evidence"):
                lines.append(f"  - 依据：{_clean(it['evidence'])}")
            if it.get("action"):
                lines.append(f"  - 下次：{_clean(it['action'])}")
        lines.append("")

    tomorrow = [t for t in _items(rep.get("tomorrow")) if isinstance(t, dict)]
    if tomorrow:
        lines.append("## 明日")
        lines.append("")
        for it in tomorrow:
            item = _clean(it.get("item"))
            if not item:
                continue
            why = f"（{_clean(it.get('why'))}）" if it.get("why") else ""
            when = f"　{_clean(it.get('when'))}" if it.get("when") else ""
            lines.append(f"- {item}{why}{when}")
        lines.append("")

    refs = [r for r in _items(data.get("refs")) if isinstance(r, dict)]
    public_refs = [r for r in refs if _SAFE_URL.match(str(r.get("uri") or ""))]
    if public_refs:
        lines.append("## 相关链接")
        lines.append("")
        for r in public_refs:
            lines.append(f"- [{_clean(r.get('label') or r.get('uri'))}]({r['uri']})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> 分享版：已去除截图、屏幕原文、内部锚点与本机路径；"
                 "「有效活动」为观测口径，不等于实际工作投入。")
    lines.append("")
    return "\n".join(lines)


def export_day(day: str, cfg=None, out: str | Path | None = None,
               include_evidence: bool = True) -> dict:
    """导出某天的分享版。返回路径、字数与被移除的字段清单。"""
    if cfg is None:
        import config as cfgmod

        cfg = cfgmod.load()
    if not isinstance(day, str) or len(day) != 10:
        raise ValueError("day 应为 YYYY-MM-DD")
    try:
        timeutil.parse_day(day)
    except ValueError as exc:
        raise ValueError(f"day 应为 YYYY-MM-DD，收到「{day}」") from exc

    src = paths.report_json(day)
    if not src.exists():
        raise ExportError(f"{day} 还没有日报，先生成一份再导出")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"{day} 的日报文件损坏，无法导出") from exc
    if not isinstance(data, dict) or not data.get("report"):
        raise ExportError(f"{day} 的日报缺少正文，无法导出")

    try:
        import blocks as blocks_mod

        cat_label = blocks_mod.CATEGORY_LABEL
    except Exception:
        cat_label = {}

    md = build_md(day, data, include_evidence=include_evidence, cat_label=cat_label)
    # 最后一道：整篇过隐私规则（路径已先清洗，避免把「本机路径」也当作可疑串）
    md = privacy.redact(md, privacy.current_config(cfg))

    target = Path(out) if out else (paths.REPORTS / "share" / f"{day}-分享版.md")
    if target.suffix.lower() != ".md":
        target = target.with_suffix(".md")
    try:
        target.resolve().relative_to(paths.REPORTS.resolve())
    except ValueError as exc:
        raise ValueError("分享版只能写到 reports 目录内") from exc

    import render as render_mod

    render_mod.write_text_atomic(target, md)
    removed = ["截图", "缩略图", "OCR 原文", "block_ids", "seg_id",
               "notes_for_human", "数据缺口细节", "本机路径"]
    if not include_evidence:
        removed.append("证据字段")
    return {
        "day": day,
        "path": str(target),
        "chars": len(md),
        "include_evidence": bool(include_evidence),
        "removed": removed,
    }
