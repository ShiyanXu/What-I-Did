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
from datetime import datetime, timedelta

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

# 成果状态：一张成果卡只回答一件事——这件事到哪一步了。
# 四类够用：做完了 / 有进展但没完 / 卡在别人手上 / 还没解决。
OUTCOME_STATUS = {
    "done": ("已完成", "done"),
    "advanced": ("已推进", "adv"),
    "waiting": ("待外部", "wait"),
    "blocked": ("受阻", "blk"),
    "unresolved": ("未解决", "open"),
}
_STATUS_ALIAS = {
    "done": "done", "complete": "done", "completed": "done", "finished": "done", "ok": "done",
    "partial": "advanced", "advanced": "advanced", "progress": "advanced", "ongoing": "advanced",
    "waiting": "waiting", "pending": "waiting", "external": "waiting", "blocked": "blocked",
    "unresolved": "unresolved", "open": "unresolved",
}
UNRESOLVED = re.compile(r"未解决|仍未|还没|尚未|没有解决|未修复|未处理|没修好")
WAITING = re.compile(r"待(?!办)|等(客户|对方|外部|答复|确认|回复)|未确定|未定|等待|pending", re.I)
ADVANCED = re.compile(r"基本|初步|部分|推进中|起草|刚开始")


def outcome_status(raw, *texts) -> str:
    """成果状态归一。模型给了就用模型的；没给（旧日报）按文字推断。

    这是唯一一处判定，validate（写入）与 render（展示）都调它，
    免得同一个"待客户答复"在 JSON 里是 waiting、在页面里变成已完成。
    """
    key = _STATUS_ALIAS.get(str(raw or "").strip().lower(), "")
    if key:
        return key
    blob = " ".join(str(t or "") for t in texts)
    if UNRESOLVED.search(blob):
        return "unresolved"
    if WAITING.search(blob):
        return "waiting"
    if ADVANCED.search(blob):
        return "advanced"
    return "done"

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


# ---------------------------------------------------------------- 任务段（主时间轴）
# 行为段是"连续不离开屏幕的一段"，一天十来个；主时间轴要的是"任务推进的几段"。
# 这里把行为段按主线合并，得到 4–6 段：同一件事在短时间里来回切应用不算换任务。
TASK_GAP_MIN = 25          # 同一条主线允许的断档
TASK_SHORT_MIN = 10        # 不到 10 分钟的段视为过渡，允许被邻近主线吸收
TASK_MAX_SPAN = 150        # 一段任务最长跨多久：再往后就是另一件事了
TASK_MAX = 6               # 主时间轴最多几段
TASK_MIN_MIN = 5           # 太短且没有成果的段不占主时间轴

_AWAY_PAT = re.compile(r"锁屏|离开|午休|空闲|未操作|挂机|下班|inactive")


def _span(it: dict, day: str, window_start: datetime | None) -> tuple[datetime, datetime] | None:
    """行为段的真实起止。优先 ISO；旧数据只有 HH:MM 时按工作日窗口推日期。"""
    if it.get("start_iso") and it.get("end_iso"):
        try:
            return timeutil.parse(it["start_iso"]), timeutil.parse(it["end_iso"])
        except Exception:
            pass
    try:
        start = timeutil.parse(f"{day}T{str(it['start'])[:5]}:00+08:00")
        end = timeutil.parse(f"{day}T{str(it['end'])[:5]}:00+08:00")
    except Exception:
        return None
    if window_start is not None and start.date() == window_start.date() and start.time() < window_start.time():
        start += timedelta(days=1)      # 04:00 之前的钟点属于"当晚"
        end += timedelta(days=1)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def is_away(it: dict) -> bool:
    """锁屏/离开这类段不进主时间轴：它们解释不了工作推进了什么。"""
    if str(it.get("verb") or "") == "away":
        return True
    label = str(it.get("label") or "")
    return bool(_AWAY_PAT.search(label)) and int(it.get("minutes") or 0) <= 40


def _minutes(it: dict) -> int:
    try:
        return max(1, int(it.get("minutes") or 0))
    except (TypeError, ValueError):
        return 1


def _can_join(group: list[dict], it: dict, span: tuple[datetime, datetime] | None,
              prev_span: tuple[datetime, datetime] | None,
              first_span: tuple[datetime, datetime] | None) -> bool:
    if span is None or prev_span is None:
        return False
    gap = (span[0] - prev_span[1]).total_seconds() / 60
    if gap < 0:
        gap = 0.0
    if gap > TASK_GAP_MIN:
        return False
    # 一段任务不能无限长：下午从 13:35 一路并到 18:06，读者就分不出这里面是几件事了
    if first_span is not None:
        span_min = (span[1] - first_span[0]).total_seconds() / 60
        if span_min > TASK_MAX_SPAN:
            return False
    if gap <= 5:
        return True
    # 只跟"上一段"比动词：拿整组的动词集合去比，一天下来什么都能并进来
    last_verb = str(group[-1].get("verb") or "")
    if last_verb and str(it.get("verb") or "") == last_verb:
        return True
    return _minutes(it) < TASK_SHORT_MIN or _minutes(group[-1]) < TASK_SHORT_MIN


def _pack(group: list[dict], spans: dict[str, tuple[datetime, datetime]]) -> dict:
    """把一组行为段合成一个任务段。时间取组的首尾，标签取最长的那段。"""
    total = sum(_minutes(x) for x in group) or 1
    by_verb: dict[str, int] = {}
    for x in group:
        v = str(x.get("verb") or "admin")
        by_verb[v] = by_verb.get(v, 0) + _minutes(x)
    primary = max(by_verb.items(), key=lambda kv: kv[1])[0]
    main = max(group, key=_minutes)

    details: list[str] = []
    labels: list[str] = []
    for x in group:
        if x.get("label") and x["label"] not in labels:
            labels.append(str(x["label"]))
        d = str(x.get("detail") or "").strip()
        if d and d not in details:
            details.append(d)

    first, last = group[0], group[-1]
    s = spans.get(str(first.get("seg_id")))
    e = spans.get(str(last.get("seg_id")))
    label = str(main.get("label") or "").strip() or (labels[0] if labels else "工作")
    return {
        "task_id": "",                     # 由调用方按顺序编号
        "start": str(first.get("start") or ""),
        "end": str(last.get("end") or ""),
        "start_iso": timeutil.iso(s[0]) if s else "",
        "end_iso": timeutil.iso(e[1]) if e else "",
        "minutes": int(round(total)),
        "verb": primary,
        "label": label,
        "detail": "；".join(details[:2])[:220],
        "items": [{"seg_id": x.get("seg_id", ""), "start": x.get("start", ""),
                   "end": x.get("end", ""), "minutes": _minutes(x),
                   "label": str(x.get("label") or ""),
                   "verb": str(x.get("verb") or "")} for x in group],
        "seg_ids": [x.get("seg_id", "") for x in group],
        "switches": sum(int(x.get("switches") or 0) for x in group),
        "block_ids": [],                   # 由 render 按成果卡补齐
        "confidence": "low" if all(str(x.get("confidence")) == "low" for x in group) else "medium",
    }


def group_tasks(behaviors: list[dict], day: str = "", window_start: datetime | None = None,
                max_tasks: int = TASK_MAX, min_task_min: int = TASK_MIN_MIN) -> dict:
    """把十几段行为合成 4–6 段"任务推进"，供主时间轴使用。

    规则（全部本地计算，结果可复现）：
    - 锁屏/离开段直接排除，不算任务；计入 `away_minutes`；
    - 与上一段的间隔 ≤25 分钟、且（同行为动词 / 有一段是短过渡）才合并；
    - 段数超过 max_tasks 时，反复合并"间隔最小的相邻一对"；
    - 剩下的段不足 min_task_min 且没有成果挂靠时，降级为"其他片段"，不占主时间轴。

    返回 {"tasks": [...], "away_minutes": int, "away_count": int, "merged": int, "dropped": [...]}。
    """
    ordered = sorted(behaviors or [], key=lambda x: str(x.get("start_iso") or x.get("start") or ""))
    spans = {}
    for it in ordered:
        sp = _span(it, day, window_start)
        if sp:
            spans[str(it.get("seg_id"))] = sp

    tasks: list[list[dict]] = []
    away = [it for it in ordered if is_away(it)]
    for it in ordered:
        if is_away(it):
            continue
        if tasks and _can_join(tasks[-1], it, spans.get(str(it.get("seg_id"))),
                               spans.get(str(tasks[-1][-1].get("seg_id"))),
                               spans.get(str(tasks[-1][0].get("seg_id")))):
            tasks[-1].append(it)
        else:
            tasks.append([it])

    def gap_between(a: list[dict], b: list[dict]) -> float:
        sa = spans.get(str(a[-1].get("seg_id")))
        sb = spans.get(str(b[0].get("seg_id")))
        if not sa or not sb:
            return 1e9
        return max(0.0, (sb[0] - sa[1]).total_seconds() / 60)

    # 段太多：从间隔最小的相邻对开始并
    while len(tasks) > max_tasks:
        best, best_gap = None, None
        for i in range(len(tasks) - 1):
            g = gap_between(tasks[i], tasks[i + 1])
            if best_gap is None or g < best_gap:
                best, best_gap = i, g
        if best is None:
            break
        tasks[best] = tasks[best] + tasks[best + 1]
        del tasks[best + 1]

    packed = [_pack(g, spans) for g in tasks]
    packed.sort(key=lambda t: str(t.get("start_iso") or t.get("start")))

    kept, dropped = [], []
    for t in packed:
        (kept if t["minutes"] >= min_task_min else dropped).append(t)
    if not kept and packed:
        kept, dropped = packed, []

    for i, t in enumerate(kept, 1):
        t["task_id"] = f"task-{i:02d}"
    return {
        "tasks": kept,
        "dropped": dropped,
        "merged": max(0, len([b for b in ordered if not is_away(b)]) - len(kept)),
        "away_minutes": sum(_minutes(x) for x in away),
        "away_count": len(away),
    }


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
