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

from i18n import t

import json
import re
from pathlib import Path

import paths
import privacy
import timeutil

# 本机路径：盘符、用户目录、相对数据目录、UNC、Git Bash 的 /e/... 写法
# 尾部遇到空格或中文标点就停：否则会把「（44 分钟）」一起吞掉，句子就读不通了
_TAIL = r"[^\s|，。；、（）()\[\]【】「」『』]*"
#: 占位符存英文原文，替换时再走 t()：模块导入时还不能取词。
_PATH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"(?i)\bmingw64:/{_TAIL}"), "local path"),
    (re.compile(rf"[A-Za-z]:[\\/]{_TAIL}"), "local path"),
    (re.compile(rf"\\\\{_TAIL}"), "network path"),
    # Git Bash：/e/Self/Project/xxx（单字母盘符 + 至少两段）
    (re.compile(rf"(?:^|(?<=[\s（(「]))/[a-z]/[\w.\-]+/{_TAIL}"), "local path"),
    (re.compile(rf"(?:\.\./)+(?:data|reports|web)/{_TAIL}"), "local path"),
    (re.compile(rf"(?<![\w/])(?:data|reports|web|state)/\d{{4}}-\d{{2}}-\d{{2}}{_TAIL}"), "local path"),
    (re.compile(rf"/Users/{_TAIL}"), "local path"),
    (re.compile(rf"/home/{_TAIL}"), "local path"),
]

_SAFE_URL = re.compile(r"^https?://", re.I)


class ExportError(RuntimeError):
    """导出条件不满足（比如那天没有日报）。"""


def clean_paths(text: str) -> str:
    """把本机路径换成占位符，保留句子可读性。"""
    out = str(text or "")
    for pat, repl in _PATH_PATTERNS:
        out = pat.sub(t(repl), out)
    return out


def _clean(text) -> str:
    return clean_paths(str(text or "")).strip()


def _items(value) -> list:
    return value if isinstance(value, list) else []


def _stats_table(stats: dict, cat_label: dict) -> list[str]:
    rows = [t("| Category | Active time |"), "|---|---|"]
    for key, minutes in (stats.get("by_category") or {}).items():
        rows.append(t("| {category} | {n} min |", category=cat_label.get(key, key),
                      n=int(round(float(minutes or 0)))))
    if len(rows) == 2:
        return []
    return rows


def build_md(day: str, data: dict, include_evidence: bool = True,
             cat_label: dict | None = None, cfg=None) -> str:
    """把日报 JSON 摊成分享版 Markdown。缺字段一律按空处理，不抛异常。"""
    labels = cat_label or {}
    rep = data.get("report") or {}
    stats = data.get("stats") or {}
    weekday = str(data.get("weekday") or "")
    status = str(data.get("status") or "")

    if weekday and not weekday.isascii():
        # 旧日报里存的是中文星期：按日期重算，别让英文分享版里冒出中文
        try:
            import render as render_mod

            weekday = render_mod.weekday_of(day)
        except Exception:
            pass

    lines = [t("# {day} {weekday} daily report", day=day, weekday=weekday).rstrip(), ""]
    if status == "degraded":
        lines.append(t("> This is the local timeline version: the model was not involved, "
                       "so it holds only objective stats and the timeline."))
        lines.append("")
    elif status == "empty":
        lines.append(t("> There was very little active time that day; only the stats and "
                       "notes are kept."))
        lines.append("")

    if rep.get("headline"):
        lines.append(f"**{_clean(rep['headline'])}**")
        lines.append("")

    try:
        import render as render_mod

        rv = render_mod.reading_view(data)
    except Exception:                      # 渲染层出问题也不能让导出失败
        rv = {"cards": [], "todos": [], "tasks": [], "tomorrow_extra": [], "gaps": {"inside": []}}

    cards = rv.get("cards") or []
    if cards:
        lines.append(t("## What got done"))
        lines.append("")
        for c in cards:
            lines.append(t("- **[{status}] {what}**", status=c.get("status_label", t("Moved forward")),
                           what=_clean(c.get("what"))))
            if c.get("impact"):
                lines.append(t("  - Why it matters: {text}", text=_clean(c["impact"])))
            if c.get("next"):
                lines.append(t("  - Next: {text}", text=_clean(c["next"])))
            if include_evidence and c.get("evidence"):
                lines.append(t("  - Evidence: {text}", text=_clean(c["evidence"])))
        lines.append("")

    todos = rv.get("todos") or []
    if todos:
        lines.append(t("## To-dos and blockers"))
        lines.append("")
        kind_label = {"waiting": t("Waiting on others"), "blocked": t("Blocked"), "todo": t("To-do")}
        for todo in todos:
            why = t(" — {text}", text=_clean(todo.get("why"))) if todo.get("why") else ""
            lines.append(t("- [{kind}] {item}{why}",
                           kind=kind_label.get(todo.get("kind"), t("To-do")),
                           item=_clean(todo.get("item")), why=why))
        lines.append("")

    tasks = rv.get("tasks") or []
    if tasks:
        lines.append(t("## Main threads"))
        lines.append("")
        lines.append(t("| Time | Duration | What | Outcome |"))
        lines.append("|---|---|---|---|")
        for task in tasks:
            lines.append(t("| {start}–{end} | {minutes} min | {label} | {outcome} |",
                           start=task.get("start", ""), end=task.get("end", ""),
                           minutes=task.get("minutes", 0), label=_clean(task.get("label")),
                           outcome=_clean(task.get("outcome")) or "—"))
        lines.append("")

    table = _stats_table(stats, labels)
    if table:
        lines.append(t("## Time breakdown"))
        lines.append("")
        lines.extend(table)
        lines.append("")

    insight = [i for i in _items(rep.get("insight")) if isinstance(i, dict)]
    if insight:
        lines.append(t("## Observations"))
        lines.append("")
        for it in insight:
            obs = _clean(it.get("observation"))
            if not obs:
                continue
            lines.append(f"- {obs}")
            if include_evidence and it.get("evidence"):
                lines.append(t("  - Evidence: {text}", text=_clean(it["evidence"])))
            if it.get("action"):
                lines.append(t("  - Next time: {text}", text=_clean(it["action"])))
        lines.append("")

    friction = [f for f in _items(rep.get("friction")) if isinstance(f, dict)]
    if friction:
        lines.append(t("## Friction and cost"))
        lines.append("")
        for it in friction:
            what = _clean(it.get("what"))
            if not what:
                continue
            lines.append(f"- {what}")
            for label, key in ((t("Cause"), "cause"), (t("Cost"), "cost"), (t("Fix"), "fix")):
                if it.get(key):
                    lines.append(t("  - {label}: {text}", label=label, text=_clean(it[key])))
        lines.append("")

    tomorrow = [x for x in (rv.get("tomorrow_extra") or []) if isinstance(x, dict)]
    if tomorrow:
        lines.append(t("## Tomorrow"))
        lines.append("")
        for it in tomorrow:
            item = _clean(it.get("item"))
            if not item:
                continue
            why = t(" ({text})", text=_clean(it.get("why"))) if it.get("why") else ""
            when = t(" {text}", text=_clean(it.get("when"))) if it.get("when") else ""
            lines.append(f"- {item}{why}{when}")
        lines.append("")

    if rep.get("opening_md") or rep.get("summary_md"):
        lines.append(t("## Process"))
        lines.append("")
        if rep.get("opening_md"):
            lines.append(_clean(rep["opening_md"]))
            lines.append("")
        if rep.get("summary_md"):
            lines.append(_clean(rep["summary_md"]))
            lines.append("")

    refs = [r for r in _items(data.get("refs")) if isinstance(r, dict)]
    public_refs = [r for r in refs if _SAFE_URL.match(str(r.get("uri") or ""))]
    if public_refs:
        lines.append(t("## Related links"))
        lines.append("")
        keep_query = bool(cfg.get("share.keep_query", False)) if cfg is not None else False
        seen_urls: set[str] = set()
        for r in public_refs:
            # 分享版默认去掉查询串：查询串里常有会话 id、邮件地址、搜索词
            uri = privacy.normalize_url(str(r["uri"]), cfg, keep_query=keep_query) or str(r["uri"])
            if uri in seen_urls:
                continue
            seen_urls.add(uri)
            label = _clean(r.get("label") or uri)
            lines.append(f"- [{label}]({uri})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(t("> Shared version: screenshots, on-screen text, internal anchors and local "
                   "paths have been removed; \"active time\" is a measured figure, not the "
                   "real work effort."))
    lines.append("")
    return "\n".join(lines)


def share_path(day: str) -> Path:
    """分享版的落盘路径。

    文件名刻意**不跟界面语言走**：它是归档产物，切一次语言就换个名字会留下两份，
    忘记清理时还会被 `forget` 漏掉。语言只影响文件里的标题。
    """
    return paths.REPORTS / "share" / f"{day}-share.md"


#: v1.2 之前的旧命名（中文），`forget` 要一并删掉，否则切换过语言的人会留下残留
LEGACY_SHARE_NAMES = ("{day}-分享版.md",)


def legacy_share_paths(day: str) -> list[Path]:
    return [paths.REPORTS / "share" / name.format(day=day) for name in LEGACY_SHARE_NAMES]


def export_day(day: str, cfg=None, out: str | Path | None = None,
               include_evidence: bool = True) -> dict:
    """导出某天的分享版。返回路径、字数与被移除的字段清单。"""
    if cfg is None:
        import config as cfgmod

        cfg = cfgmod.load()
    if not isinstance(day, str) or len(day) != 10:
        raise ValueError(t("day must be YYYY-MM-DD"))
    try:
        timeutil.parse_day(day)
    except ValueError as exc:
        raise ValueError(t("day must be YYYY-MM-DD, got \"{day}\"", day=day)) from exc

    src = paths.report_json(day)
    if not src.exists():
        raise ExportError(t("{day} has no report yet; generate one before exporting", day=day))
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(t("The report file for {day} is corrupted; cannot export", day=day)) from exc
    if not isinstance(data, dict) or not data.get("report"):
        raise ExportError(t("The report for {day} has no body; cannot export", day=day))

    try:
        import render as render_mod

        cat_label = {key: render_mod.cat_name(key) for key in render_mod.CAT_NAME}
    except Exception:
        cat_label = {}

    md = build_md(day, data, include_evidence=include_evidence, cat_label=cat_label, cfg=cfg)
    # 最后一道：整篇过隐私规则（路径已先清洗，避免把「本机路径」也当作可疑串）
    md = privacy.redact(md, privacy.current_config(cfg))

    target = Path(out) if out else share_path(day)
    if target.suffix.lower() != ".md":
        target = target.with_suffix(".md")
    try:
        target.resolve().relative_to(paths.REPORTS.resolve())
    except ValueError as exc:
        raise ValueError(t("The shared version can only be written inside the reports folder")) from exc

    import render as render_mod

    render_mod.write_text_atomic(target, md)
    removed = [t("Screenshots"), t("Thumbnails"), t("OCR text"), "block_ids", "seg_id",
               "notes_for_human", t("Data gap details"), t("Local paths")]
    if not include_evidence:
        removed.append(t("Evidence fields"))
    return {
        "day": day,
        "path": str(target),
        "chars": len(md),
        "include_evidence": bool(include_evidence),
        "removed": removed,
    }
