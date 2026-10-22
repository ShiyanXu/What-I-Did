"""把事件流切成"活动块"，并给出稳定 ID。

稳定 ID 很重要：它同时是 HTML 锚点、外部文档的反向引用目标，
必须做到"同一份数据重跑两次结果完全一样"。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import timeutil
import privacy

DEV_APPS = {
    "code.exe", "cursor.exe", "pycharm64.exe", "idea64.exe", "devenv.exe", "sublime_text.exe",
    "windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe",
    "git-bash.exe", "mintty.exe", "notepad++.exe", "rstudio.exe", "jupyter.exe", "postman.exe",
    "dbeaver.exe", "navicat.exe", "xshell.exe", "xftp.exe", "mobaxterm.exe", "python.exe",
    "node.exe", "vim.exe", "nvim-qt.exe", "android studio.exe", "goland64.exe", "webstorm64.exe",
}
DOC_APPS = {
    "winword.exe", "excel.exe", "powerpnt.exe", "wps.exe", "et.exe", "wpp.exe", "acrobat.exe",
    "acrord32.exe", "sumatrapdf.exe", "obsidian.exe", "typora.exe", "notepad.exe", "onenote.exe",
    "foxitreader.exe", "xmind.exe", "mindmaster.exe", "evernote.exe", "yourapp.exe",
}
MEET_APPS = {"wemeetapp.exe", "zoom.exe", "teams.exe", "dingtalk.exe", "feishu.exe", "wxwork.exe",
             "webexmta.exe", "voov.exe"}
COMM_APPS = {"wechat.exe", "weixin.exe", "qq.exe", "telegram.exe", "slack.exe", "discord.exe",
             "dingtalk.exe", "feishu.exe", "wxwork.exe", "mailmaster.exe", "outlook.exe"}
BROWSER_APPS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "360se.exe",
                "qqbrowser.exe", "safari.exe"}

CATEGORY_LABEL = {
    "dev": "开发",
    "doc": "文档",
    "meet": "会议",
    "comm": "沟通",
    "web": "浏览/查询",
    "other": "其他",
}

# 标题里带这些词，即使开着浏览器也算开发/文档
_TITLE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)github|gitlab|pull request|\bissue\b|\bmerge\b|ci/cd|pipeline|jenkins|stack overflow|jira|confluence\s*-\s*develop"), "dev"),
    (re.compile(r"(?i)docs\.|文档|笔记|知识库|wiki|readme|手册|规范"), "doc"),
    (re.compile(r"(?i)会议|语音通话|视频通话|meeting|zoom|腾讯会议"), "meet"),
    (re.compile(r"(?i)邮箱|mail|邮件"), "comm"),
]

_SUFFIX = re.compile(r"\s+[-—|]\s+(Google Chrome|Microsoft.*Edge|Mozilla Firefox|Visual Studio Code|"
                     r"Visual Studio|Cursor|PyCharm|IntelliJ IDEA|WebStorm|Notepad\+\+|Obsidian.*|"
                     r"Typora|Windows PowerShell|命令提示符|Windows Terminal)$", re.I)


def normalize_title(title: str) -> str:
    t = (title or "").strip()
    t = _SUFFIX.sub("", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t[:120]


def classify(app: str, title: str) -> str:
    a = (app or "").lower()
    t = title or ""
    if a in MEET_APPS and re.search(r"会议|通话|meeting|共享", t):
        return "meet"
    if a in DEV_APPS:
        return "dev"
    if a in DOC_APPS:
        return "doc"
    if a in MEET_APPS:
        return "meet"
    if a in COMM_APPS:
        return "comm"
    if a in BROWSER_APPS:
        for pat, cat in _TITLE_RULES:
            if pat.search(t):
                return cat
        return "web"
    for pat, cat in _TITLE_RULES:
        if pat.search(t):
            return cat
    return "other"


@dataclass
class Block:
    id: str
    start: datetime
    end: datetime
    app: str
    title: str
    category: str
    events: list = field(default_factory=list)
    shots: list = field(default_factory=list)
    ocr_ids: list = field(default_factory=list)
    _texts: list = field(default_factory=list)
    switch_count: int = 0
    continuation: bool = False
    content_policy: str = "allowed"
    idle_seconds: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def active_seconds(self) -> float:
        """墙钟跨度扣除块内的暂停/锁屏区间：观测到的“有效活动”口径。"""
        return max(0.0, self.duration - self.idle_seconds)

    @property
    def text(self) -> str:
        return "\n".join(self._texts)

    def add_text(self, t: str) -> None:
        if t:
            self._texts.append(t)


def _block_id(day: str, start: datetime, app: str, title: str) -> str:
    raw = f"{app}|{title}".lower()
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:4]
    return f"b-{start.strftime('%Y%m%d-%H%M')}-{h}"


def load_events(path) -> list[dict]:
    import json

    out: list[dict] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_ocr(path) -> dict[str, dict]:
    import json

    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            oid = rec.get("id")
            if oid:
                out[oid] = rec
    return out


def _idle_spans(events: list[dict], cfg) -> list[tuple[datetime, datetime]]:
    """把非活动事件（暂停/锁屏/名单/抓屏失败）的停留区间挑出来。

    每个非活动事件只吸收"到下一个事件"的时长，并按采样间隔的 3 倍封顶：
    一天结束时的最后一个 idle 事件不会把整晚都算成暂停。
    """
    cap = max(60.0, float(cfg.get("capture.interval_sec", 20)) * 3)
    spans: list[tuple[datetime, datetime]] = []
    prev_ts: datetime | None = None
    prev_active = True
    for e in sorted(events, key=lambda x: str(x.get("ts", ""))):
        try:
            ts = timeutil.parse(e["ts"])
        except Exception:
            continue
        if prev_ts is not None and not prev_active:
            dur = min((ts - prev_ts).total_seconds(), cap)
            if dur > 0:
                spans.append((prev_ts, prev_ts + timedelta(seconds=dur)))
        prev_ts, prev_active = ts, e.get("state", "active") == "active"
    return spans


def _overlap_seconds(spans: list[tuple[datetime, datetime]],
                     start: datetime, end: datetime) -> float:
    total = 0.0
    for s, e in spans:
        lo, hi = max(s, start), min(e, end)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def build_blocks(events: list[dict], ocr_map: dict[str, dict], cfg) -> list[Block]:
    privacy.require_valid(cfg)
    # 不只保护新数据：旧版本留下的标题/OCR 也必须经过当前名单与脱敏规则。
    events = [privacy.sanitize_event(e, cfg) for e in events]
    gap_th = float(cfg.get("report.gap_threshold_min", 10)) * 60
    max_len = float(cfg.get("report.max_block_min", 30)) * 60
    min_split = 90.0  # 90 秒内换标题不切块，避免浏览器切标签把一天切成碎片

    active = [
        e for e in events
        if e.get("state", "active") == "active" and e.get("kind", "tick") in ("shot", "tick")
    ]
    active.sort(key=lambda e: e.get("ts", ""))

    blocks: list[Block] = []
    cur: Block | None = None
    last_ts: datetime | None = None
    used_ids: dict[str, int] = {}

    def close(b: Block, end: datetime) -> None:
        b.end = end
        if end > b.start:
            blocks.append(b)

    for e in active:
        try:
            ts = timeutil.parse(e["ts"])
        except Exception:
            continue
        app = e.get("app", "")
        title = normalize_title(e.get("title", ""))
        policy = e.get("content_policy", "allowed")
        day = e.get("day") or timeutil.workday_of(ts, cfg.cutoff_hour)

        if cur is None:
            cur = Block(id="", start=ts, end=ts, app=app, title=title, category=classify(app, title),
                        content_policy=policy)
        else:
            elapsed = (ts - cur.start).total_seconds()
            gap = (ts - (last_ts or cur.end)).total_seconds()
            changed = (app != cur.app) or (title != cur.title)
            privacy_boundary = (policy != cur.content_policy
                                or (policy == "metadata_only" and app != cur.app))
            if privacy_boundary or gap >= gap_th or elapsed >= max_len or (changed and elapsed >= min_split):
                close(cur, last_ts or cur.end)
                cont = (app == cur.app) and (title == cur.title) and elapsed >= max_len
                cur = Block(id="", start=ts, end=ts, app=app, title=title,
                            category=classify(app, title), continuation=cont, content_policy=policy)
            elif changed:
                cur.switch_count += 1
                # 同块内记下短暂切走的标题，作为上下文线索
                if title and title != cur.title:
                    cur.add_text(f"[{ts:%H:%M} 短暂切到] {title}")

        if e.get("img"):
            cur.shots.append(e["img"])
        oid = e.get("ocr")
        if oid and oid in ocr_map:
            rec = ocr_map[oid]
            if rec.get("status") == "ok" and rec.get("text"):
                cur.add_text(privacy.redact(rec["text"], cfg))
                cur.ocr_ids.append(oid)
        cur.events.append(e.get("seq"))
        last_ts = ts

    if cur is not None:
        close(cur, last_ts or cur.end)

    # 收尾：分配稳定 ID（同日同标题同分钟会撞，加后缀区分）
    for b in blocks:
        base = _block_id(b.start.strftime("%Y-%m-%d"), b.start, b.app, b.title)
        n = used_ids.get(base, 0)
        used_ids[base] = n + 1
        b.id = base if n == 0 else f"{base}-{n}"

    # 丢弃过短的孤立块（<15 秒且无截图）
    blocks = [b for b in blocks if b.duration >= 15 or b.shots]

    # 同一窗口内短于断块阈值的暂停不再被并入活动时长：按非活动区间扣除
    idle = _idle_spans(events, cfg)
    if idle:
        for b in blocks:
            b.idle_seconds = min(_overlap_seconds(idle, b.start, b.end), b.duration)
    return blocks


def rhythm(blocks: list[Block], sessions: list | None = None) -> dict:
    """节奏指标：全在本地算，模型只负责解读，数字不允许它编。

    这些数字是"看完有感觉"的关键——"最长连续专注 55 分钟""碎片时间占 21%"
    比"今天做了不少事情"有力得多，而且模型算不出来。

    注意：块本身被 `report.max_block_min` 截断（默认 30 分钟），所以"最长专注"
    必须按行为段（sessions，连续工作）算，不能按单个块算，否则永远显示 30 分钟。
    """
    if not blocks:
        return {"longest_focus_min": 0, "longest_focus_at": "", "median_block_min": 0,
                "fragmented_min": 0, "fragmented_ratio": 0.0, "switch_total": 0,
                "deep_work_min": 0, "block_count": 0, "peak_window": "", "peak_min": 0,
                "session_count": 0, "interruptions": 0}

    durations = sorted(b.duration / 60 for b in blocks)
    mid = durations[len(durations) // 2]

    sessions = sessions or []
    if sessions:
        total = sum(s.minutes for s in sessions) or 1
        longest_s = max(sessions, key=lambda s: s.minutes)
        longest_min = longest_s.minutes
        longest_at = f"{longest_s.start:%H:%M}–{longest_s.end:%H:%M}"
        deep = sum(s.minutes for s in sessions if s.minutes >= 25)
        frag = [s.minutes for s in sessions if s.minutes < 15]
        interruptions = sum(1 for s in sessions[1:] if s.gap_before_min >= 5)
    else:
        total = sum(durations) or 1.0
        longest = max(blocks, key=lambda b: b.duration)
        longest_min = int(round(longest.duration / 60))
        longest_at = f"{longest.start:%H:%M}–{longest.end:%H:%M}"
        deep = int(round(sum(d for d in durations if d >= 25)))
        frag = [d for d in durations if d < 8]
        interruptions = 0

    # 以 10 分钟为桶铺满全天，滑 9 个桶（90 分钟）找活动最密的窗口
    base = blocks[0].start
    buckets: dict[int, float] = {}
    for b in blocks:
        t = b.start
        while t < b.end:
            idx = int((t - base).total_seconds() // 600)
            nxt = min(b.end, t + timedelta(minutes=10))
            buckets[idx] = buckets.get(idx, 0.0) + (nxt - t).total_seconds()
            t = nxt
    span = int((blocks[-1].end - base).total_seconds() // 600) + 1
    best_i, best_v = 0, -1.0
    for i in range(max(1, span - 8)):
        v = sum(buckets.get(i + k, 0.0) for k in range(9))
        if v > best_v:
            best_i, best_v = i, v

    peak_start = base + timedelta(minutes=10 * best_i)
    peak_end = peak_start + timedelta(minutes=90)
    return {
        "longest_focus_min": int(longest_min),
        "longest_focus_at": longest_at,
        "median_block_min": int(round(mid)),
        "fragmented_min": int(round(sum(frag))),
        "fragmented_ratio": round(sum(frag) / total, 3),
        "switch_total": sum(b.switch_count for b in blocks),
        "deep_work_min": int(deep),
        "block_count": len(blocks),
        "peak_window": f"{peak_start:%H:%M}–{peak_end:%H:%M}",
        "peak_min": int(round(best_v / 60)),
        "session_count": len(sessions),
        "interruptions": int(interruptions),
    }


def summarize(blocks: list[Block]) -> dict:
    # 时长口径：块墙钟跨度扣除块内暂停（idle_seconds），见 Block.active_seconds。
    # 应用分布与分类分布同为“有效活动”口径；行为段（sessions）仍是墙钟口径。
    total = sum(b.active_seconds for b in blocks)
    by_cat: dict[str, float] = {}
    by_app: dict[str, float] = {}
    for b in blocks:
        by_cat[b.category] = by_cat.get(b.category, 0.0) + b.active_seconds
        key = b.app or "?"
        by_app[key] = by_app.get(key, 0.0) + b.active_seconds
    return {
        "active_seconds": round(total, 1),
        "active_minutes": round(total / 60, 1),
        "idle_minutes": round(sum(b.idle_seconds for b in blocks) / 60, 1),
        "block_count": len(blocks),
        "shot_count": sum(len(b.shots) for b in blocks),
        "ocr_blocks": sum(1 for b in blocks if b.ocr_ids),
        "by_category": {k: round(v / 60, 1) for k, v in sorted(by_cat.items(), key=lambda x: -x[1])},
        "by_app": {k: round(v / 60, 1) for k, v in sorted(by_app.items(), key=lambda x: -x[1])[:12]},
        "first": blocks[0].start.strftime("%H:%M") if blocks else "",
        "last": blocks[-1].end.strftime("%H:%M") if blocks else "",
        "rhythm": rhythm(blocks),
    }


def find_gaps(blocks: list[Block], window_start: datetime, window_end: datetime,
              threshold_min: float = 10) -> list[dict]:
    gaps: list[dict] = []
    cursor = window_start
    for b in blocks:
        gap_min = (b.start - cursor).total_seconds() / 60
        if gap_min >= threshold_min:
            gaps.append({"start": cursor.strftime("%H:%M"), "end": b.start.strftime("%H:%M"),
                         "minutes": round(gap_min)})
        cursor = max(cursor, b.end)
    tail = (window_end - cursor).total_seconds() / 60
    if tail >= threshold_min:
        gaps.append({"start": cursor.strftime("%H:%M"), "end": window_end.strftime("%H:%M"),
                     "minutes": round(tail)})
    return gaps
