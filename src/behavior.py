"""行为时间轴：先把一天切成一串"行为段"，再让模型给每段起个行为名字。

为什么不让模型自己切时间：
- 模型对时间最不可靠，一旦它把 09:12–10:07 写成 09:00–10:30，整条时间轴就废了；
- 本地切段能保证"时间连续、覆盖真实数据、同一份数据每次切法一致"；
- 模型只做它擅长的事：根据标题和识别文字判断"这段到底在干什么"。

所以职责是：本地定 `seg_id` + 起止时间，模型填 `verb/label/detail`，
渲染时一律用本地的起止时间。模型没跑或没给某段时，用本地规则兜底（`local_fill`）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import blocks as blocks_mod
import timeutil

# 行为动词：描述"在做什么"，不是"用了什么软件"
VERBS: dict[str, dict] = {
    "coding":        {"label": "写代码", "color": "#2563eb"},
    "debugging":     {"label": "排问题", "color": "#dc2626"},
    "testing":       {"label": "验证", "color": "#0891b2"},
    "designing":     {"label": "设计", "color": "#7c3aed"},
    "reading":       {"label": "查资料", "color": "#64748b"},
    "writing":       {"label": "写文档", "color": "#0d9488"},
    "meeting":       {"label": "开会", "color": "#d97706"},
    "communicating": {"label": "沟通", "color": "#be185d"},
    "operating":     {"label": "折腾环境", "color": "#b45309"},
    "admin":         {"label": "杂务", "color": "#94a3b8"},
    "away":          {"label": "离开", "color": "#cbd5e1"},
}
VERB_KEYS = tuple(k for k in VERBS if k != "away")

CAT_TO_VERB = {
    "dev": "coding",
    "doc": "writing",
    "meet": "meeting",
    "comm": "communicating",
    "web": "reading",
    "other": "admin",
}

# 标题里的动词线索：本地兜底时用来猜行为，也给模型做提示
_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)error|exception|traceback|报错|失败|冲突|无法|not found|timeout|崩溃|断言"), "debugging"),
    (re.compile(r"(?i)test|pytest|用例|单测|回归|verify|校验|验证"), "testing"),
    (re.compile(r"(?i)\.md\b|笔记|文档|readme|wiki|方案|纪要|总结|周报|月报"), "writing"),
    (re.compile(r"(?i)figma|sketch|原型|架构|流程图|脑图|xmind|design"), "designing"),
    (re.compile(r"(?i)cargo|npm|pip|docker|kubectl|git |部署|安装|配置|环境变量|conda"), "operating"),
    (re.compile(r"(?i)会议|通话|meeting|评审|沟通|周会"), "meeting"),
    (re.compile(r"(?i)微信|飞书|钉钉|邮件|mail|群|消息"), "communicating"),
    (re.compile(r"(?i)github|stackoverflow|文档|手册|api|issue|搜索|百度|google|知乎"), "reading"),
]


@dataclass
class Session:
    id: str
    start: datetime
    end: datetime
    category: str = "other"
    blocks: list = field(default_factory=list)
    app_minutes: dict[str, int] = field(default_factory=dict)
    titles: list[str] = field(default_factory=list)
    text: str = ""
    switches: int = 0
    gap_before_min: int = 0

    @property
    def minutes(self) -> int:
        return max(1, int(round((self.end - self.start).total_seconds() / 60)))


def _session_text(sess: Session, limit: int = 900) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for b in sess.blocks:
        for raw in (b.text or "").splitlines():
            line = " ".join(raw.split())
            if len(line) < 3:
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(line)
    out = " | ".join(lines)
    return out[:limit] + (" …" if len(out) > limit else "")


def build_sessions(blocks: list[blocks_mod.Block], cfg) -> list[Session]:
    """按"连续工作"切成行为段。

    断开条件（任一）：
    - 与上一块的间隔 ≥ report.gap_threshold_min（默认 10 分钟）
    - 段长已到 report.max_session_min（默认 60 分钟）
    - 主题切换：分类变了，且新块本身待了 ≥ 8 分钟（短时间瞄一眼微信不算切段）
    """
    gap_th = float(cfg.get("report.gap_threshold_min", 10)) * 60
    max_len = float(cfg.get("report.max_session_min", 60)) * 60
    topic_min = 8 * 60

    sessions: list[Session] = []
    cur: Session | None = None
    prev_end: datetime | None = None

    def close(end: datetime) -> None:
        nonlocal cur
        if cur is not None and cur.blocks:
            cur.end = max(cur.end, end)
            sessions.append(cur)
        cur = None

    for b in blocks:
        if cur is None:
            cur = Session(id="", start=b.start, end=b.end, category=b.category,
                          gap_before_min=int(round((b.start - prev_end).total_seconds() / 60)) if prev_end else 0)
        else:
            gap = (b.start - prev_end).total_seconds() if prev_end else 0
            dur = (b.end - cur.start).total_seconds()
            topic_break = (b.category != cur.category and b.duration >= topic_min)
            if gap >= gap_th or dur >= max_len or topic_break:
                close(prev_end or b.start)
                cur = Session(id="", start=b.start, end=b.end, category=b.category,
                              gap_before_min=int(round(gap / 60)))
        cur.blocks.append(b)
        cur.end = max(cur.end, b.end)
        cur.switches += b.switch_count
        mins = int(round(b.duration / 60))
        cur.app_minutes[b.app or "?"] = cur.app_minutes.get(b.app or "?", 0) + mins
        if b.title and b.title not in cur.titles:
            cur.titles.append(b.title)
        prev_end = b.end

    if cur is not None:
        close(cur.end)

    for i, s in enumerate(sessions, 1):
        s.id = f"seg-{i:02d}"
        s.text = _session_text(s)
        # 段内占比最高的分类作为主分类
        if s.blocks:
            by_cat: dict[str, float] = {}
            for b in s.blocks:
                by_cat[b.category] = by_cat.get(b.category, 0.0) + b.duration
            s.category = max(by_cat.items(), key=lambda x: x[1])[0]
        s.app_minutes = dict(sorted(s.app_minutes.items(), key=lambda x: -x[1])[:5])
        s.titles = s.titles[:8]
    return sessions


# ---------------------------------------------------------------- 本地兜底
def guess_verb(sess: Session) -> str:
    """没有模型时按标题/应用猜行为。只用于兜底，不追求准确。"""
    if sess.category in ("meet", "comm"):
        return CAT_TO_VERB[sess.category]
    blob = " ".join(sess.titles[:4])
    for pat, verb in _HINTS:
        if pat.search(blob):
            return verb
    return CAT_TO_VERB.get(sess.category, "admin")


def local_fill(sess: Session) -> dict:
    """本地兜底：没有模型时也能看出"这是什么行为"，而不是只显示窗口标题。"""
    verb = guess_verb(sess)
    verb_label = VERBS[verb]["label"]
    title = sess.titles[0] if sess.titles else ""
    if title:
        short = re.sub(r"\s+[-—|]\s+.*$", "", title)[:18] or title[:18]
        label = f"{verb_label}·{short}"[:26]
    else:
        label = f"{verb_label} {sess.minutes} 分钟"
    bits = []
    if title:
        bits.append(f"窗口停在「{title[:40]}」")
    if len(sess.titles) > 1:
        bits.append(f"期间换了 {len(sess.titles)} 个页面/文件")
    if sess.switches:
        bits.append(f"窗口切换 {sess.switches} 次")
    return {
        "verb": verb,
        "label": label,
        "detail": "，".join(bits)[:120],
        "confidence": "low",
        "source": "local",
    }


def merge(model_items: list[dict], sessions: list[Session]) -> list[dict]:
    """把模型给的标签贴回本地时间段。时间永远取本地的。"""
    by_id: dict[str, dict] = {}
    for it in model_items or []:
        sid = str(it.get("seg_id") or "").strip()
        if sid:
            by_id[sid] = it

    out: list[dict] = []
    for sess in sessions:
        base = {
            "seg_id": sess.id,
            "start": sess.start.strftime("%H:%M"),
            "end": sess.end.strftime("%H:%M"),
            # 跨午夜（如 23:40–00:20）只有 ISO 能说清真实日期，渲染一律优先用它
            "start_iso": timeutil.iso(sess.start),
            "end_iso": timeutil.iso(sess.end),
            "minutes": sess.minutes,
            "category": sess.category,
            "apps": [{"app": k, "minutes": v} for k, v in sess.app_minutes.items()],
            "titles": list(sess.titles),
            "switches": sess.switches,
            "gap_before_min": sess.gap_before_min,
        }
        got = by_id.get(sess.id)
        if got and str(got.get("label") or "").strip():
            verb = str(got.get("verb") or "").strip().lower()
            if verb not in VERBS:
                verb = guess_verb(sess)
            base.update({
                "verb": verb,
                "label": str(got.get("label") or "").strip()[:28],
                "detail": str(got.get("detail") or "").strip()[:160],
                "confidence": (str(got.get("confidence") or "medium").lower()
                               if str(got.get("confidence") or "").lower() in ("high", "medium", "low")
                               else "medium"),
                "source": "model",
            })
        else:
            base.update(local_fill(sess))
        out.append(base)
    return out


# ---------------------------------------------------------------- 给模型看
def prompt_section(sessions: list[Session], text_limit: int = 700) -> str:
    """把行为段摊成模型友好的清单。时间/编号由本地给定，模型不许改。"""
    lines = [
        "## 行为分段（我已按连续工作切好，你给每段起一个「行为」，不要改时间和编号）",
        "格式：编号 | 起止 | 时长 | 切换次数 | 应用分布 | 窗口标题 | 屏幕文字",
    ]
    for s in sessions:
        apps = "、".join(f"{k.replace('.exe', '')} {v}分" for k, v in s.app_minutes.items())
        titles = "；".join(t[:60] for t in s.titles[:6]) or "（无标题）"
        lines.append("")
        lines.append(f"### {s.id} | {s.start:%H:%M}–{s.end:%H:%M} | {s.minutes} 分 | "
                     f"切换 {s.switches} 次 | {apps}")
        lines.append(f"标题：{titles}")
        if s.text:
            body = s.text[:text_limit] + (" …" if len(s.text) > text_limit else "")
            lines.append(f"文字：{body}")
        else:
            lines.append("文字：（这一段没有 OCR 文本，只能靠标题推断，可以标低置信度）")
    return "\n".join(lines)


def timeline_segments(behaviors: list[dict], day_minutes: int = 1440) -> list[dict]:
    """给界面色带用：分钟偏移 + 颜色 + 悬浮提示。

    优先用 start_iso/end_iso（跨午夜无歧义）；旧数据只有 HH:MM 时，
    结束不大于起点按跨午夜处理（结束顺延一天）。
    """
    segs: list[dict] = []
    for it in behaviors or []:
        s = e = None
        if it.get("start_iso") and it.get("end_iso"):
            try:
                sdt, edt = timeutil.parse(it["start_iso"]), timeutil.parse(it["end_iso"])
                s = sdt.hour * 60 + sdt.minute
                e = edt.hour * 60 + edt.minute
                if edt.date() != sdt.date():
                    e += 1440  # 跨午夜：以起点所在日铺条
            except Exception:
                s = e = None
        if s is None:
            try:
                sh, sm = str(it["start"])[:5].split(":")
                eh, em = str(it["end"])[:5].split(":")
                s = int(sh) * 60 + int(sm)
                e = int(eh) * 60 + int(em)
            except Exception:
                continue
            if e <= s:
                e = s + 1  # 跨午夜的旧行为段：至少显示 1 分钟
        s = min(s, day_minutes)
        e = min(max(e, s + 1), day_minutes)
        color = VERBS.get(it.get("verb", ""), VERBS["admin"])["color"]
        label = it.get("label") or ""
        tip = f"{it['start']}–{it['end']}  {label}"
        if it.get("detail"):
            tip += f"　·　{it['detail']}"
        segs.append({"s": s, "e": e, "color": color, "tip": tip,
                     "verb": it.get("verb", ""), "label": label})
    segs.sort(key=lambda x: x["s"])
    return segs
