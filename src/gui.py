"""图形界面（tkinter，标准库，零新增依赖）。

设计取向：
- 只负责"看 + 管"，采集与出报仍由后台进程完成，关掉窗口不影响记录。
- 所有慢操作（出报、自检、OCR 测试、启停进程）都丢到后台线程，主线程只碰控件。
- 状态每 3 秒轮询一次，与 CLI 共用 status / control / doctor 三个模块，结论永远一致。
"""
from __future__ import annotations
from i18n import t, tn
import i18n

import ctypes
import json
import os
import queue
import re
import threading
import traceback
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import ttk
import tkinter as tk
from urllib.parse import unquote
from urllib.request import url2pathname

import autostart
import behavior as behavior_mod
import config as cfgmod
import control
import doctor
import paths
import privacy
import status
import timeutil

VERSION = "1.3.0"
TITLE = t("What-IDid · Work Log")

# 托盘菜单命令号（任意唯一整数即可）
CMD_SHOW = 11
CMD_COLLECT = 12
CMD_REPORT = 13
CMD_DIAG = 14
CMD_QUIT = 99

# ---------------------------------------------------------------- 配色
BG = "#f5f6f8"
CARD = "#ffffff"
LINE = "#e5e8ef"
TEXT = "#111827"
BODY = "#1f2937"
MUTED = "#6b7280"
SUBTLE = "#9aa3b2"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
OK_C = "#059669"
WARN_C = "#d97706"
BAD_C = "#dc2626"
SIDEBAR = "#0f172a"
SIDEBAR_SEL = "#1e293b"

CAT_COLOR = {          # 与 src/render.py 的 CAT_COLOR 保持一致
    "dev": "#2563eb", "doc": "#0d9488", "meet": "#d97706",
    "comm": "#7c3aed", "web": "#64748b", "other": "#94a3b8",
}
CAT_LABEL = {"dev": "Dev", "doc": "Docs", "meet": "Meetings", "comm": "Comms", "web": "Browsing", "other": "Other"}


def cat_label(key: str) -> str:
    """分类的显示名。

    这里存的是**英文源**，翻译在调用时现算。不能像以前那样写成 `t("Dev")`：
    模块级常量在 import 时求值，而切换语言是进程内重建窗口（`gui.main()` 的 while
    循环），模块不会重新导入，那些常量就永远停在首次导入的语言上。
    """
    return t(CAT_LABEL.get(key, key))


# 行为动词（与 src/behavior.py 的 VERBS 同源，这里只做展示用）
VERB_LABEL = {k: v["label"] for k, v in behavior_mod.VERBS.items()}
VERB_COLOR = {k: v["color"] for k, v in behavior_mod.VERBS.items()}


def verb_label(key: str) -> str:
    """动词显示名，理由同 `cat_label()`：英文源 + 调用时翻译。"""
    return t(VERB_LABEL.get(key, key))

S = 1.0                 # DPI 缩放系数，App 创建后设置
FONT = "Microsoft YaHei UI"
MONO = "Consolas"

# 「**09:12–10:06** 其余内容」——日报「过程」里每一段的固定开头
_TIME_HEAD = re.compile(r"^\*\*(\d{1,2}:\d{2}\s*[–\-~]\s*\d{1,2}:\d{2})\*\*\s*(.*)$")


def px(n: float) -> int:
    return max(1, int(round(n * S)))


def fmt_dur(minutes: float) -> str:
    m = int(round(minutes or 0))
    if m <= 0:
        return t("0 min")
    if m < 60:
        return t("{n} min", n=m)
    return t("{h} h {m} min", h=m // 60, m=m % 60) if m % 60 else t("{h} h", h=m // 60)


def short_date(day: str) -> str:
    return day[5:].replace("-", "/") if len(day) == 10 else day


def pretty_date(day: str) -> str:
    try:
        d = timeutil.parse_day(day)
    except Exception:
        return day
    return t("{m}/{d}", m=d.month, d=d.day)


#: 英文是源，其它语言在 catalog 里各有一条（周一 / 周一…）
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAYS_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def weekday_short(day: str) -> str:
    try:
        return t(_WEEKDAYS[timeutil.parse_day(day).weekday()])
    except Exception:
        return ""


def weekday_long(day: str) -> str:
    """详情页标题用全名：旧日报里存的是生成时的语言，这里按日期重算。"""
    try:
        return t(_WEEKDAYS_LONG[timeutil.parse_day(day).weekday()])
    except Exception:
        return ""


def next_run_label(iso: str) -> str:
    """把 ISO 时间说成「Today 21:00 / Tomorrow 21:00」之类的相对时间。"""
    if not iso:
        return ""
    try:
        dt = timeutil.parse(iso)
    except Exception:
        return ""
    today = timeutil.now().date()
    delta = (dt.date() - today).days
    if delta == 0:
        prefix = t("Today")
    elif delta == 1:
        prefix = t("Tomorrow")
    else:
        prefix = t("{m}/{d}", m=dt.month, d=dt.day)
    return f"{prefix} {dt:%H:%M}"


def open_uri(uri: str) -> bool:
    """file:// 交给系统默认程序（.md 会用 Obsidian/编辑器打开），其余交给 shell 协议处理。"""
    if not uri:
        return False
    try:
        if uri.startswith("file://"):
            local = url2pathname(unquote(uri[7:]))
            if os.path.exists(local):
                os.startfile(local)          # noqa: S606
                return True
            return False
        os.startfile(uri)                    # noqa: S606  obsidian:// / https:// / ms-...
        return True
    except Exception:
        return False


def segments_from_blocks(blocks: list[dict]) -> list[dict]:
    """把报告里的活动块折算成 0–1440 分钟区间的色带段。"""
    segs = []
    for b in blocks or []:
        try:
            s = str(b["start"])[11:16]
            e = str(b["end"])[11:16]
            sm = int(s[:2]) * 60 + int(s[3:5])
            em = int(e[:2]) * 60 + int(e[3:5])
        except Exception:
            continue
        if em <= sm:
            em = min(1440, sm + 1)
        segs.append({
            "s": sm, "e": em,
            "color": CAT_COLOR.get(b.get("category", "other"), CAT_COLOR["other"]),
            "tip": f"{s}–{e}  {cat_label(b.get('category', ''))}  {b.get('title', '')}",
        })
    return segs


def shade(ratio: float) -> str:
    """0 → 浅灰蓝，1 → 主色。用于两周活跃度柱状图。"""
    ratio = max(0.0, min(1.0, ratio))
    if ratio <= 0:
        return "#e6eaf2"
    a = (37, 99, 235)
    b = (191, 219, 254)
    rgb = tuple(int(b[i] + (a[i] - b[i]) * ratio) for i in range(3))
    return "#%02x%02x%02x" % rgb


# ---------------------------------------------------------------- 基础控件
class FlatButton(tk.Label):
    """用 Label 自绘按钮：tk.Button 在 Windows 上的内边距和高度不可控。"""

    STYLES = {
        "primary": ("#2563eb", "#ffffff", "#1d4ed8", ""),
        "ghost": ("#ffffff", "#374151", "#f3f5f9", "#dfe3ea"),
        "soft": ("#eef2f7", "#334155", "#e2e8f0", ""),
        "danger": ("#ffffff", "#b91c1c", "#fef2f2", "#fecaca"),
        "sidebar": (SIDEBAR_SEL, "#e5e7eb", "#243044", ""),
    }

    def __init__(self, parent, text, command=None, kind="ghost", padx=14, pady=7,
                 font_size=10, bold=False, bg=None):
        self._bg, fg, self._hover, border = self.STYLES[kind]
        if bg:
            self._bg = self._hover = bg
        super().__init__(parent, text=text, bg=self._bg, fg=fg,
                         font=(FONT, font_size, "bold" if bold else "normal"),
                         padx=px(padx), pady=px(pady), cursor="hand2", bd=0)
        if border:
            self.configure(highlightbackground=border, highlightthickness=1, highlightcolor=border)
        self._cmd = command
        self._enabled = True
        self._fg = fg
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _click(self, _=None):
        if self._enabled and self._cmd:
            self._cmd()

    def _enter(self, _=None):
        if self._enabled:
            self.configure(bg=self._hover)

    def _leave(self, _=None):
        if self._enabled:
            self.configure(bg=self._bg)

    def set_enabled(self, flag: bool):
        self._enabled = bool(flag)
        self.configure(cursor="hand2" if flag else "arrow",
                       fg=self._fg if flag else SUBTLE,
                       bg=self._bg if flag else "#f1f3f7")

    def set_text(self, text: str):
        self.configure(text=text)


class Card(tk.Frame):
    def __init__(self, parent, title=None, sub=None, pad=16, bg=CARD):
        super().__init__(parent, bg=bg, highlightbackground=LINE, highlightthickness=1)
        self.bg = bg
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=px(pad), pady=px(pad))
        if title:
            head = tk.Frame(self.body, bg=bg)
            head.pack(fill="x", pady=(0, px(10)))
            tk.Label(head, text=title, bg=bg, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
            if sub:
                self.sub_lbl = tk.Label(head, text=sub, bg=bg, fg=SUBTLE, font=(FONT, 8))
                self.sub_lbl.pack(side="left", padx=(px(8), 0))


class ScrollFrame(tk.Frame):
    """Canvas + 内嵌 Frame：内容超过高度才滚动。"""

    def __init__(self, parent, bg=BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=1)
        self.vs = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vs.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.body.bind("<Enter>", self._bind_wheel)
        self.body.bind("<Leave>", self._unbind_wheel)

    def _on_body(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_bar()

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        self._sync_bar()

    def _sync_bar(self):
        """内容放得下就把滚动条收起来，免得多一条空条。"""
        try:
            need = self.body.winfo_reqheight() > self.canvas.winfo_height() + 2
            if need and not self.vs.winfo_ismapped():
                self.vs.pack(side="right", fill="y")
            elif not need and self.vs.winfo_ismapped():
                self.vs.pack_forget()
        except Exception:
            pass

    def _bind_wheel(self, _=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _unbind_wheel(self, _=None):
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass

    def _wheel(self, event):
        if self.canvas.winfo_height() >= self.body.winfo_reqheight():
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class StatCard(Card):
    def __init__(self, parent, title, value="—", sub="", color=TEXT):
        super().__init__(parent, pad=14)
        self.value_lbl = tk.Label(self.body, text=value, bg=CARD, fg=color, font=(FONT, 19, "bold"))
        self.value_lbl.pack(anchor="w")
        tk.Label(self.body, text=title, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(px(1), 0))
        self.sub_lbl = tk.Label(self.body, text=sub, bg=CARD, fg=SUBTLE, font=(FONT, 8),
                                wraplength=px(200), justify="left")
        self.sub_lbl.pack(anchor="w", pady=(px(7), 0))

    def set_value(self, value: str, sub: str = "", color: str = TEXT):
        self.value_lbl.configure(text=value, fg=color)
        self.sub_lbl.configure(text=sub)


# ---------------------------------------------------------------- 概览
class DashPage(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg=BG)
        self.app = app
        sf = ScrollFrame(self, BG)
        sf.pack(fill="both", expand=True)
        wrap = tk.Frame(sf.body, bg=BG)
        wrap.pack(fill="x", padx=px(22), pady=(px(2), px(24)))

        # 提醒区放最上面：没配 Key、采集没跑、没自启，一进来就该看见
        self.notices = tk.Frame(wrap, bg=BG)
        self.notices.pack(fill="x")

        # 四个数字卡
        stats = tk.Frame(wrap, bg=BG)
        stats.pack(fill="x")
        for i in range(4):
            stats.columnconfigure(i, weight=1, uniform="stat")
        stats.rowconfigure(0, weight=1)
        self.c_state = StatCard(stats, t("Capture status"), t("Checking"), t("Reading heartbeat"))
        self.c_active = StatCard(stats, t("Active today"), "—", t("Visible after the 21:00 report"))
        self.c_data = StatCard(stats, t("Captured today"), "—", "—")
        self.c_disk = StatCard(stats, t("Free disk"), "—")
        for i, c in enumerate((self.c_state, self.c_active, self.c_data, self.c_disk)):
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(10), 0))

        # 控制条
        self.ctrl = Card(wrap, t("Capture control"), t("Closing this window does not stop recording"))
        self.ctrl.pack(fill="x", pady=(px(12), 0))
        bar = tk.Frame(self.ctrl.body, bg=CARD)
        bar.pack(fill="x")
        self.btn_start = FlatButton(bar, t("Start capture"), lambda: self.app.start_daemon(), "primary")
        self.btn_stop = FlatButton(bar, t("Stop capture"), lambda: self.app.stop_daemon(), "ghost")
        self.btn_pause = FlatButton(bar, t("Pause 30 min"), lambda: self.app.pause(30), "soft")
        self.btn_resume = FlatButton(bar, t("Resume capture"), lambda: self.app.resume(), "soft")
        for b in (self.btn_start, self.btn_stop, self.btn_pause, self.btn_resume):
            b.pack(side="left", padx=(0, px(8)))
        FlatButton(bar, t("Open full dashboard"), lambda: self.app.open_board(), "ghost").pack(side="right")
        self.btn_gen = FlatButton(bar, t("Generate report now"), lambda: self.app.generate(), "primary")
        self.btn_gen.pack(side="right", padx=(0, px(8)))

        self.ctrl_hint = tk.Label(self.ctrl.body, text="", bg=CARD, fg=SUBTLE, font=(FONT, 8), justify="left")
        self.ctrl_hint.pack(anchor="w", pady=(px(10), 0))

        # 今日行为时间轴
        self.tl = Card(wrap, t("Today's activity timeline"), t("The strip is colored by activity; hover a segment to see what it was."))
        self.tl.pack(fill="x", pady=(px(12), 0))
        self.tl_now = tk.Label(self.tl.body, text="", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.tl_now.pack(fill="x", pady=(0, px(6)))
        self.strip = tk.Canvas(self.tl.body, bg=CARD, height=px(56), highlightthickness=0, bd=0)
        self.strip.pack(fill="x")
        self.strip.bind("<Configure>", lambda e: self._draw_strip())
        self.strip.bind("<Motion>", self._hover_strip)
        self._segs: list[dict] = []
        self._beh_sig = None
        self._empty_hint = t("No activity blocks yet today. The report runs at 21:00, after which the full timeline appears here.")
        self.legend = tk.Frame(self.tl.body, bg=CARD)
        self.legend.pack(fill="x", pady=(px(8), 0))
        self.blist = tk.Frame(self.tl.body, bg=CARD)
        self.blist.pack(fill="x", pady=(px(4), 0))

        # 最近两周
        self.wk = Card(wrap, t("Last 14 days"), t("Click a bar to open that day's report"))
        self.wk.pack(fill="x", pady=(px(12), 0))
        self.week = tk.Canvas(self.wk.body, bg=CARD, height=px(96), highlightthickness=0, bd=0)
        self.week.pack(fill="x")
        self.week.bind("<Configure>", lambda e: self._draw_week())
        self.week.bind("<Button-1>", self._click_week)
        self._week_days: list[dict] = []

        self._week_sig = None

    # -------------------------------------------------- 时间线
    def _draw_strip(self):
        c = self.strip
        c.delete("all")
        w = c.winfo_width()
        if w <= 1:
            return
        top = px(6)
        h = px(26)
        c.create_rectangle(0, top, w, top + h, fill="#eef2f7", outline="")
        for seg in self._segs:
            x0 = seg["s"] / 1440 * w
            x1 = max(x0 + px(2), seg["e"] / 1440 * w)
            c.create_rectangle(x0, top, min(w, x1), top + h,
                               fill=seg["color"], outline="", tags=("seg",))
        for hh in range(0, 25, 3):
            x = hh / 24 * w
            c.create_line(x, top + h + px(2), x, top + h + px(5), fill="#cbd3e0")
            anchor = "nw" if hh == 0 else ("ne" if hh == 24 else "n")
            c.create_text(x, top + h + px(8), text=f"{hh:02d}", fill=SUBTLE, font=(FONT, 7), anchor=anchor)

    def _hover_strip(self, event):
        if not self._segs:
            self.tl_now.configure(text=self._empty_hint, fg=SUBTLE)
            return
        w = self.strip.winfo_width() or 1
        minute = event.x / w * 1440
        for seg in self._segs:
            if seg["s"] <= minute <= seg["e"]:
                self.tl_now.configure(text=seg["tip"], fg=TEXT)
                return
        self.tl_now.configure(text="", fg=MUTED)

    def set_behaviors(self, segs: list[dict], items: list[dict], empty_hint: str = "",
                      source: str = ""):
        """色带 + 行为清单。签名没变就不重建，避免 3 秒轮询把界面刷得闪。"""
        self._segs = segs
        if empty_hint:
            self._empty_hint = empty_hint
        sig = tuple((it.get("start"), it.get("end"), it.get("label")) for it in items)
        if sig == self._beh_sig:
            self._draw_strip()
            return
        self._beh_sig = sig
        self._draw_strip()

        if not items:
            self.tl_now.configure(text=self._empty_hint, fg=SUBTLE)
        else:
            total = sum(int(it.get("minutes") or 0) for it in items)
            tail = t("· Activity labels come from the model") if source == "model" else t("· Activity labels come from local rules; the model's reading replaces them after the report")
            self.tl_now.configure(
                text=t("{n} segments · covering {dur}　{tail}",
  n=len(items), dur=fmt_dur(total), tail=tail), fg=SUBTLE)

        for w in self.legend.winfo_children():
            w.destroy()
        seen: list[str] = []
        for it in items:
            v = it.get("verb") or "admin"
            if v not in seen:
                seen.append(v)
        for v in seen[:8]:
            box = tk.Frame(self.legend, bg=CARD)
            box.pack(side="left", padx=(0, px(12)))
            dot = tk.Canvas(box, width=px(8), height=px(8), bg=CARD, highlightthickness=0)
            dot.create_rectangle(0, 0, px(8), px(8), fill=VERB_COLOR.get(v, "#94a3b8"), outline="")
            dot.pack(side="left", padx=(0, px(5)))
            tk.Label(box, text=verb_label(v), bg=CARD, fg=MUTED,
                     font=(FONT, 8)).pack(side="left")

        for w in self.blist.winfo_children():
            w.destroy()
        shown = items[:12]
        for it in shown:
            row = tk.Frame(self.blist, bg=CARD)
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text=f"{it.get('start', '')}–{it.get('end', '')}", bg=CARD, fg=SUBTLE,
                     font=(MONO, 8), width=12, anchor="w").pack(side="left")
            tk.Label(row, text=t("{n} min", n=it.get("minutes", 0)), bg=CARD, fg=SUBTLE,
                     font=(FONT, 8), width=5, anchor="w").pack(side="left")
            dot = tk.Canvas(row, width=px(7), height=px(7), bg=CARD, highlightthickness=0)
            dot.create_oval(0, 0, px(7), px(7),
                            fill=VERB_COLOR.get(it.get("verb") or "admin", "#94a3b8"), outline="")
            dot.pack(side="left", padx=(0, px(7)))
            tk.Label(row, text=it.get("label") or "", bg=CARD, fg=TEXT,
                     font=(FONT, 9, "bold"), anchor="w").pack(side="left")
            detail = it.get("detail") or ""
            if detail:
                lbl = tk.Label(row, text="　" + detail, bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w")
                lbl.pack(side="left", fill="x", expand=True)
        if len(items) > len(shown):
            tk.Label(self.blist, text=t("…{n} more segments; open the Reports page after the report to see them all",
  n=len(items) - len(shown)),
                     bg=CARD, fg=SUBTLE, font=(FONT, 8)).pack(anchor="w", pady=(px(4), 0))

    def set_timeline(self, segs: list[dict], empty_hint: str = ""):
        self.set_behaviors(segs, [], empty_hint)

    # -------------------------------------------------- 两周
    def set_weeks(self, days: list[dict]):
        self._week_days = days
        self._draw_week()

    def _draw_week(self):
        c = self.week
        c.delete("all")
        w = c.winfo_width()
        if w <= 1 or not self._week_days:
            return
        n = len(self._week_days)
        gap = px(6)
        col = (w - gap * (n - 1)) / n
        base = px(56)
        top_pad = px(12)
        peak = max([d.get("minutes", 0) for d in self._week_days] + [60])
        for i, d in enumerate(self._week_days):
            x0 = i * (col + gap)
            ratio = (d.get("minutes", 0) / peak) if peak else 0
            h = max(px(3), int(base * ratio))
            c.create_rectangle(x0, top_pad + base - h, x0 + col, top_pad + base,
                               fill=shade(ratio), outline="", tags=(f"d{i}",))
            if d.get("minutes", 0) > 0:
                c.create_text(x0 + col / 2, top_pad + base - h - px(6), text=fmt_dur(d["minutes"]),
                              fill=SUBTLE, font=(FONT, 7), anchor="s")
            c.create_text(x0 + col / 2, top_pad + base + px(8), text=short_date(d["day"]),
                          fill=MUTED if d.get("minutes") else SUBTLE, font=(FONT, 7), anchor="n")
            if d.get("is_today"):
                c.create_line(x0, top_pad + base + px(2), x0 + col, top_pad + base + px(2),
                              fill=ACCENT, width=px(2))

    def _click_week(self, event):
        if not self._week_days:
            return
        w = self.week.winfo_width() or 1
        n = len(self._week_days)
        gap = px(6)
        col = (w - gap * (n - 1)) / n
        idx = int(event.x / (col + gap))
        if 0 <= idx < n:
            self.app.open_report(self._week_days[idx]["day"])

    # -------------------------------------------------- 刷新
    def refresh(self, snap: dict):
        hb = snap["heartbeat"]
        pause = snap["paused"]
        if snap.get("privacy_error"):
            self.c_state.set_value(t("Content processing stopped"), snap["privacy_error"], BAD_C)
        elif pause:
            until = pause.get("until") or ""
            tail = t(", resumes at {t}", t=timeutil.parse(until).strftime("%H:%M")) if until else t("(resume manually)")
            self.c_state.set_value(t("Paused"), t("paused at {at}{tail}", at=(pause.get("paused_at") or "")[11:16], tail=tail), WARN_C)
        elif hb["alive"]:
            app_name = (hb["raw"].get("app") or "").replace(".exe", "")
            self.c_state.set_value(t("Running"), t("{age}s since last heartbeat · {app}", age=hb["age"], app=app_name or "idle"), OK_C)
        else:
            self.c_state.set_value(t("Not running"), t("Not recording now. Hit \"Start capture\"."), BAD_C)

        tr = snap["today_report"]
        if tr["exists"]:
            self.c_active.set_value(fmt_dur(tr["active_minutes"]),
                                 t("{n} blocks · today's report is ready", n=tr["block_count"]))
        else:
            self.c_active.set_value("—", t("Runs at 21:00 automatically; the button on the right works too"))

        self.c_data.set_value(t("{n} shots", n=snap["shots"]),
                           t("{a:,} events · {b:.0f} MB", a=snap["events"], b=snap["day_size_mb"]))

        disk = snap["disk_free_gb"]
        self.c_disk.set_value(f"{disk:.0f} GB" if disk is not None else "—",
                           t("drive {drive} · data in {name}", drive=paths.ROOT.drive, name=paths.ROOT.name),
                           BAD_C if (disk is not None and disk < 5) else TEXT)

        # 按钮可用性
        self.btn_start.set_enabled(not hb["alive"])
        self.btn_stop.set_enabled(hb["alive"])
        self.btn_pause.set_enabled(hb["alive"] and not pause)
        self.btn_resume.set_enabled(bool(pause))

        pending = hb["raw"].get("pending_ocr") or 0
        self.ctrl_hint.configure(
            text=(t("Data folder {root}    ·    sample interval {sec}s"
  "    ·    OCR queue {q}    ·    OCR today: {ok} ok / {fail} failed",
  root=paths.ROOT, sec=snap["interval_sec"], q=pending,
  ok=hb["raw"].get("ocr_ok", 0), fail=hb["raw"].get("ocr_failed", 0))))

        self._render_notices(snap)
        self._refresh_timeline(snap)
        self._refresh_weeks(snap)

    def _refresh_timeline(self, snap: dict):
        day = snap["workday"]
        nxt = next_run_label((snap.get("report") or {}).get("next_at", ""))
        if nxt:
            self.tl.sub_lbl.configure(text=t("Strip colored by activity · next auto report {nxt}", nxt=nxt))
        d = status.load_report(day) or {}
        rep = d.get("report") or {}
        beh = rep.get("behaviors") or []
        if beh:
            segs = behavior_mod.timeline_segments(beh)
            src = "model" if d.get("status") == "ok" else "local"
            self.set_behaviors(segs, beh, source=src)
            return
        live = status.live_behaviors(day, cfgmod.load())
        if live:
            self.set_behaviors(behavior_mod.timeline_segments(live), live, source="local")
        else:
            self.set_behaviors([], [], t("No activity blocks yet. Once capture starts this shows a live timeline; after the 21:00 report it becomes the model's reading of each segment."))

    def _refresh_weeks(self, snap: dict):
        today = snap["workday"]
        days = []
        base = timeutil.parse_day(today)
        from datetime import timedelta

        report_map = {r["day"]: r for r in snap["reports"]}
        for i in range(13, -1, -1):
            d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
            item = report_map.get(d)
            days.append({"day": d, "minutes": (item or {}).get("active_minutes", 0), "is_today": d == today})
        sig = tuple((d["day"], d["minutes"]) for d in days)
        if sig != self._week_sig:
            self._week_sig = sig
            self.set_weeks(days)

    def _render_notices(self, snap: dict):
        items = []
        if snap.get("privacy_error"):
            items.append((BAD_C, t("Invalid configuration: capture and sending are stopped"), snap["privacy_error"],
                          t("Open config"), lambda: open_uri(str(paths.CONFIG_PATH))))
        elif not snap.get("allow_llm", True):
            items.append((MUTED, t("Local-only mode"), t("Model requests are disabled; reports use the local timeline"),
                          t("Settings"), lambda: self.app.show("settings")))
        sched = snap.get("report") or {}
        review = snap.get("review") or {}
        if review and not review.get("error"):
            items.append((WARN_C, t("Content is waiting for your approval"),
                          t("Target {model}, about {chars} chars; the model is not called until you confirm",
  model=review.get("model") or "model", chars=review.get("chars", 0)),
                          t("Review"),
                          lambda d=review.get("day"): (self.app.open_report(d) if d
                                                       else self.app.show("reports"))))
        if sched.get("overdue"):
            day = sched.get("overdue_day") or ""
            why = sched.get("overdue_why") or t("Scheduled run did not finish")
            items.append((BAD_C, t("No report for {day} yet", day=day[5:]),
                          t("Current status is \"{why}\"; use the button on the right to catch up", why=why), t("Generate now"),
                          lambda d=day: self.app.generate(d or None, force=True)))
        if not snap["has_key"] and snap.get("allow_llm", True) and not snap.get("privacy_error"):
            items.append((WARN_C, t("No model API key configured"), t("Reports fall back to a timeline and lose details like which file you were editing"),
                          t("Settings"), lambda: self.app.show("settings")))
        if not snap["heartbeat"]["alive"] and not snap["paused"]:
            items.append((BAD_C, t("Capture is not running"), t("Current work is not being recorded"),
                          t("Start capture"), lambda: self.app.start_daemon()))
        if not self.app.autostart_installed():
            items.append((MUTED, t("Autostart is not registered"), t("Recording will not resume automatically after a reboot"),
                          t("Register now"), lambda: self.app.enable_autostart()))
        elif not self.app.report_task_installed():
            items.append((MUTED, t("Report schedule is not registered"),
                          t("If capture stops or the machine is off at 21:00, the report is not filled in automatically"),
                          t("Register now"), lambda: self.app.enable_autostart()))
        if snap["today_report"]["exists"] and snap["today_report"]["status"] == "degraded":
            items.append((MUTED, t("Today's report is degraded"), t("Cause: the model is missing or the call failed, so the page is timeline-only"),
                          t("View"), lambda: self.app.show("reports")))
        sig = tuple((t, a) for _, t, _, a, _ in items)
        if sig == getattr(self, "_notice_sig", None):
            return
        self._notice_sig = sig
        for w in self.notices.winfo_children():
            w.destroy()
        for color, title, desc, action, cmd in items:
            row = tk.Frame(self.notices, bg=CARD, highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=(0, px(10)))
            inner = tk.Frame(row, bg=CARD)
            inner.pack(fill="x", padx=px(14), pady=px(10))
            dot = tk.Canvas(inner, width=px(8), height=px(8), bg=CARD, highlightthickness=0)
            dot.create_oval(0, 0, px(8), px(8), fill=color, outline="")
            dot.pack(side="left", padx=(0, px(10)))
            tk.Label(inner, text=title, bg=CARD, fg=TEXT, font=(FONT, 10, "bold")).pack(side="left")
            tk.Label(inner, text=desc, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(px(10), 0))
            FlatButton(inner, action, cmd, "soft", padx=10, pady=4, font_size=9).pack(side="right")


# ---------------------------------------------------------------- 日报
class ReportsPage(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg=BG)
        self.app = app
        self._rows: dict[str, dict] = {}
        self._sig = None
        self.selected = None

        self.columnconfigure(0, minsize=px(292), weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # 左：日期列表
        left = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(px(22), 0), pady=px(2))
        head = tk.Frame(left, bg=CARD)
        head.pack(fill="x", padx=px(14), pady=(px(12), px(6)))
        tk.Label(head, text=t("Report archive"), bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
        self.count_lbl = tk.Label(head, text=t("0 reports"), bg=CARD, fg=SUBTLE, font=(FONT, 8))
        self.count_lbl.pack(side="left", padx=(px(8), 0))
        self.list_sf = ScrollFrame(left, CARD)
        self.list_sf.pack(fill="both", expand=True, padx=px(6), pady=(0, px(8)))

        # 右：详情
        right = tk.Frame(self, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(px(14), px(22)), pady=px(2))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        self.right = right

        top = Card(right, pad=16)
        top.grid(row=0, column=0, sticky="ew")
        head2 = tk.Frame(top.body, bg=CARD)
        head2.pack(fill="x")
        self.date_lbl = tk.Label(head2, text="", bg=CARD, fg=TEXT, font=(FONT, 15, "bold"))
        self.date_lbl.pack(side="left")
        self.wd_lbl = tk.Label(head2, text="", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.wd_lbl.pack(side="left", padx=(px(8), 0), pady=(px(4), 0))

        btns = tk.Frame(head2, bg=CARD)
        btns.pack(side="right")
        self.btn_md = FlatButton(btns, t("Copy Markdown"), self._copy_md, "soft", padx=10, pady=5, font_size=9)
        self.btn_md.pack(side="right", padx=(px(6), 0))
        FlatButton(btns, t("Full dashboard"), self._open_board, "ghost", padx=10, pady=5, font_size=9).pack(side="right", padx=(px(6), 0))
        self.btn_regen = FlatButton(btns, t("Regenerate"), self._regen, "ghost", padx=10, pady=5, font_size=9)
        self.btn_regen.pack(side="right")

        self.badge_box = tk.Frame(top.body, bg=CARD)
        self.badge_box.pack(fill="x", pady=(px(6), 0))

        self.headline = tk.Label(top.body, text="", bg=CARD, fg=BODY, font=(FONT, 11),
                                 wraplength=px(600), justify="left", anchor="w")
        self.headline.pack(fill="x", pady=(px(8), px(10)))
        top.body.bind("<Configure>", lambda e: self.headline.configure(
            wraplength=max(px(240), e.width - px(8))))

        self.chips = tk.Frame(top.body, bg=CARD)
        self.chips.pack(fill="x")

        self.strip = tk.Canvas(top.body, bg=CARD, height=px(44), highlightthickness=0, bd=0)
        self.strip.pack(fill="x", pady=(px(10), 0))
        self.strip.bind("<Configure>", lambda e: self._draw_strip())
        self._segs: list[dict] = []

        body_card = Card(right, pad=0)
        body_card.grid(row=1, column=0, sticky="nsew", pady=(px(12), 0))
        holder = tk.Frame(body_card.body, bg=CARD)
        holder.pack(fill="both", expand=True)
        self.text = tk.Text(holder, bg=CARD, fg=BODY, bd=0, highlightthickness=0,
                            wrap="word", font=(FONT, 10), padx=px(20), pady=px(16),
                            spacing1=px(1), spacing3=px(3), cursor="arrow",
                            width=1, height=6)
        vs = ttk.Scrollbar(holder, orient="vertical", command=self.text.yview)

        def _scroll_yn(first, last):
            """内容放得下就收起滚动条。"""
            try:
                fits = float(first) <= 0.0 and float(last) >= 1.0
                if fits and vs.winfo_ismapped():
                    vs.pack_forget()
                elif not fits and not vs.winfo_ismapped():
                    vs.pack(side="right", fill="y")
            except Exception:
                pass
            vs.set(first, last)

        self.text.configure(yscrollcommand=_scroll_yn)
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(state="disabled")
        self._init_tags()

        self.show_placeholder()

    def _init_tags(self):
        t = self.text
        t.tag_configure("h2", font=(FONT, 10, "bold"), foreground=TEXT, spacing1=px(14), spacing3=px(6))
        t.tag_configure("p", font=(FONT, 10), foreground=BODY, spacing3=px(5), lmargin1=0, lmargin2=0)
        t.tag_configure("bullet", font=(FONT, 10), foreground=BODY, lmargin1=px(4), lmargin2=px(18), spacing3=px(4))
        t.tag_configure("bold", font=(FONT, 10, "bold"), foreground=TEXT)
        t.tag_configure("code", font=(MONO, 9), background="#f1f3f7", foreground="#334155")
        t.tag_configure("muted", font=(FONT, 9), foreground=SUBTLE)
        t.tag_configure("quote", font=(FONT, 9), foreground=MUTED, lmargin1=px(10), lmargin2=px(10))
        t.tag_configure("chip", font=(FONT, 9, "underline"), foreground=ACCENT)
        t.tag_configure("warn", font=(FONT, 9), foreground=WARN_C)
        t.tag_configure("ok", font=(FONT, 9), foreground=OK_C)
        # 行为时间轴：时间列用等宽，色点按行为类型上色
        t.tag_configure("time", font=(MONO, 9), foreground=SUBTLE)
        t.tag_configure("dur", font=(FONT, 8), foreground=SUBTLE)
        t.tag_configure("behlabel", font=(FONT, 10, "bold"), foreground=TEXT)
        t.tag_configure("behdetail", font=(FONT, 9), foreground=MUTED,
                        lmargin1=px(116), lmargin2=px(116), spacing3=px(8))
        # 条目下的从属说明（证据/影响/原因/代价/处理/依据/下次）：缩进比正文深一档，
        # 但不能借用 behdetail 的 116px（那是给行为时间轴对齐用的），否则贴到中间去了。
        t.tag_configure("sub", font=(FONT, 9), foreground=MUTED,
                        lmargin1=px(22), lmargin2=px(22), spacing3=px(2))
        t.tag_configure("sublabel", font=(FONT, 9), foreground=SUBTLE)
        # 以时间开头的段落（"**09:12–10:06** 补上幂等判断…"）：时间占固定宽度，
        # 折行后对齐到冒号后面而不是退回左边界。日报的「过程」全是这个形状。
        t.tag_configure("tl", font=(FONT, 10), foreground=BODY,
                        lmargin1=0, lmargin2=px(92), spacing3=px(6))
        t.tag_configure("tlhead", font=(MONO, 9, "bold"), foreground=TEXT,
                        lmargin1=0, lmargin2=px(92))
        for key, meta in behavior_mod.VERBS.items():
            t.tag_configure(f"vb_{key}", foreground=meta["color"])

    # -------------------------------------------------- 列表
    def set_index(self, reports: list[dict]):
        sig = tuple((r["day"], r["status"], r["headline"]) for r in reports)
        if sig == self._sig:
            return
        self._sig = sig
        for w in self.list_sf.body.winfo_children():
            w.destroy()
        self._rows.clear()
        self.count_lbl.configure(text=t("{n} reports", n=len(reports)))
        if not reports:
            tk.Label(self.list_sf.body, text=t("No reports yet\nThe first one is generated tonight at 21:00"),
                     bg=CARD, fg=SUBTLE, font=(FONT, 9), justify="left").pack(anchor="w", padx=px(12), pady=px(14))
            return
        for r in reports:
            self._add_row(r)
        if self.selected and self.selected in self._rows:
            self._paint(self.selected)
        elif reports:
            self.select(reports[0]["day"], notify=False)

    def _add_row(self, r: dict):
        day = r["day"]
        holder = tk.Frame(self.list_sf.body, bg=CARD)
        holder.pack(fill="x", padx=px(4), pady=px(1))
        bar = tk.Frame(holder, bg=CARD, width=px(3))
        bar.pack(side="left", fill="y")
        box = tk.Frame(holder, bg=CARD)
        box.pack(side="left", fill="x", expand=True, padx=(px(10), px(8)), pady=px(8))
        line1 = tk.Frame(box, bg=CARD)
        line1.pack(fill="x")
        d1 = tk.Label(line1, text=short_date(day), bg=CARD, fg=TEXT, font=(FONT, 9, "bold"))
        d1.pack(side="left")
        d2 = tk.Label(line1, text=weekday_short(r.get("day", "") or day), bg=CARD, fg=SUBTLE, font=(FONT, 8))
        d2.pack(side="left", padx=(px(5), 0))
        d3 = tk.Label(line1, text=fmt_dur(r.get("active_minutes", 0)) if r.get("active_minutes") else "",
                      bg=CARD, fg=SUBTLE, font=(FONT, 8))
        d3.pack(side="right")
        head_text = (r.get("headline") or t("(untitled)")).strip()
        d4 = tk.Label(box, text=head_text, bg=CARD, fg=MUTED,
                      font=(FONT, 8), anchor="w", justify="left", wraplength=px(200))
        d4.pack(fill="x", pady=(px(3), 0))
        # 列表宽度随窗口变，wraplength 不能写死，否则文字会被卡片右边缘切掉
        memo = {"w": 0}

        def _rewrap(e, lbl=d4, st=memo):
            if e.width and e.width != st["w"]:      # 只按宽度变化触发，避免 configure 回环
                st["w"] = e.width
                lbl.configure(wraplength=max(px(80), e.width))

        d4.bind("<Configure>", _rewrap)

        widgets = [holder, box, line1, d1, d2, d3, d4]
        for w in widgets:
            w.bind("<Button-1>", lambda e, d=day: self.select(d))
        self._rows[day] = {"bar": bar, "widgets": widgets}

    def _paint(self, day: str):
        for d, row in self._rows.items():
            sel = d == day
            bg = "#eff6ff" if sel else CARD
            for w in row["widgets"]:
                w.configure(bg=bg)
            row["bar"].configure(bg=ACCENT if sel else CARD)

    def select(self, day: str, notify: bool = True):
        self.selected = day
        self._paint(day)
        self.render(day)

    # -------------------------------------------------- 详情
    def show_placeholder(self, msg: str | None = None, day: str = ""):
        # 默认值只能在这里取：写成 `msg=t("…")` 就在 import 时固化了语言
        self.date_lbl.configure(text=pretty_date(day) if day else t("No reports yet"))
        self.wd_lbl.configure(text=weekday_short(day) if day else "")
        self.headline.configure(text=msg or t("Pick a report on the left,"
                                              "\nor hit \"Generate report now\" to turn today's data into text."))
        for w in self.badge_box.winfo_children():
            w.destroy()
        for w in self.chips.winfo_children():
            w.destroy()
        self._segs = []
        self._draw_strip()
        self._write_plain("")

    def _badge(self, text: str, color: str, bg: str):
        lbl = tk.Label(self.badge_box, text=text, bg=bg, fg=color, font=(FONT, 8),
                       padx=px(8), pady=px(2))
        lbl.pack(side="left", padx=(0, px(5)))
        return lbl

    def _chip(self, text: str):
        lbl = tk.Label(self.chips, text=text, bg="#f4f6fa", fg="#475569", font=(FONT, 8),
                       padx=px(9), pady=px(3))
        lbl.pack(side="left", padx=(0, px(6)))
        return lbl

    def render(self, day: str):
        d = status.load_report(day)
        review = status.pending_review(day)
        if not d:
            # 审核模式：内容已经准备好，只是还没得到"可以外发"的确认。
            # 这种情况要单独展示，不能和"这天没出报"混为一谈。
            if review and not review.get("error"):
                self._render_review(day, review)
                return
            # 分清两种"没有"：这天压根没出过报，vs 出过但 JSON 没了
            if (paths.REPORTS / f"{day}.md").exists():
                msg = t("The JSON for this report is gone; only the Markdown is left.\nHit \"Regenerate\" to rebuild it from that day's data.")
            else:
                msg = (t("No report for this day yet.\nIt is generated at 21:00, or hit \"Regenerate\" now."))
            self.show_placeholder(msg, day=day)
            return
        rep = d.get("report") or {}
        self.date_lbl.configure(text=pretty_date(day))
        self.wd_lbl.configure(text=weekday_long(day))
        self.headline.configure(text=rep.get("headline") or t("(the model gave no title)"))

        for w in self.badge_box.winfo_children():
            w.destroy()
        st = d.get("status", "")
        if st == "ok":
            self._badge(t("Model"), OK_C, "#ecfdf5")
        elif st == "degraded":
            self._badge(t("Degraded timeline"), WARN_C, "#fffbeb")
        elif st == "empty":
            self._badge(t("No data that day"), MUTED, "#f1f3f7")
        if review:
            self._badge(t("Newer content is waiting for review"), WARN_C, "#fffbeb")
        conf = rep.get("confidence")
        if conf:
            self._badge(t("confidence {c}", c=conf), MUTED, "#f1f3f7")
        rev = d.get("revision", 1)
        if rev and rev > 1:
            self._badge(t("revision {n}", n=rev), MUTED, "#f1f3f7")
        self._day_ops(day, bool(review))

        for w in self.chips.winfo_children():
            w.destroy()
        stats = d.get("stats") or {}
        cov = d.get("coverage") or {}
        self._chip(t("covers {start}-{end}", start=cov.get("start", "?"), end=cov.get("until", "?")))
        self._chip(t("active {dur}", dur=fmt_dur(stats.get("active_minutes", 0))))
        self._chip(t("{n} blocks", n=stats.get("block_count", 0)))
        self._chip(t("{n} screenshots", n=stats.get("shot_count", 0)))
        if d.get("generated_at"):
            self._chip(t("generated {t}", t=d["generated_at"][11:16]))

        beh = rep.get("behaviors") or []
        self._segs = (behavior_mod.timeline_segments(beh) if beh
                      else segments_from_blocks(d.get("blocks") or []))
        self._draw_strip()
        self._write_report(d)
        self.text.yview_moveto(0)

    def _day_ops(self, day: str, has_review: bool = False):
        """与"这一天"相关的补充/危险操作，靠右放，避免顶部按钮继续变长。"""
        ops = tk.Frame(self.badge_box, bg=CARD)
        ops.pack(side="right")
        FlatButton(ops, t("Add entry"), lambda: self._add_manual(day), "ghost",
                   padx=8, pady=2, font_size=8).pack(side="right", padx=(px(6), 0))
        FlatButton(ops, t("Forget day"), lambda: self._forget(day), "ghost",
                   padx=8, pady=2, font_size=8).pack(side="right")
        if has_review:
            FlatButton(ops, t("Review queued content"),
                       lambda: self._render_review(day, status.pending_review(day)),
                       "primary", padx=8, pady=2, font_size=8).pack(side="right", padx=(px(6), 0))

    def _render_review(self, day: str, info: dict):
        """展示"将要发送给模型的原文"，确认才外发。"""
        self.date_lbl.configure(text=pretty_date(day))
        self.wd_lbl.configure(text=weekday_short(day))
        self.headline.configure(text=t("No report yet: model input is waiting for your review, and nothing is sent before you confirm."))

        for w in self.badge_box.winfo_children():
            w.destroy()
        self._badge(t("Pending review"), WARN_C, "#fffbeb")
        if info.get("model"):
            self._badge(str(info["model"]), MUTED, "#f1f3f7")
        ops = tk.Frame(self.badge_box, bg=CARD)
        ops.pack(side="right")
        FlatButton(ops, t("Discard"), lambda: self._discard_review(day), "ghost",
                   padx=8, pady=2, font_size=8).pack(side="right", padx=(px(6), 0))
        FlatButton(ops, t("Approve & send"), lambda: self.app.approve_review(day), "primary",
                   padx=10, pady=2, font_size=8).pack(side="right")

        for w in self.chips.winfo_children():
            w.destroy()
        if info.get("coverage"):
            self._chip(t("covers {c}", c=info["coverage"]))
        if info.get("chars"):
            self._chip(t("{n} chars to send", n=info["chars"]))
        if info.get("generated_at"):
            self._chip(t("generated {t}", t=info["generated_at"][11:16]))

        self._segs = []
        self._draw_strip()

        payload = ""
        try:
            data = json.loads(Path(info["path"]).read_text(encoding="utf-8"))
            payload = str((data or {}).get("payload") or "")
        except (OSError, ValueError, TypeError):
            payload = ""

        self._enable()
        self.text.insert("end", t("Below is the exact text that will be sent to the model (already redacted by the privacy rules). Send only if it looks right; nothing leaves this machine until you confirm.\n\n"), ("p", "warn"))
        if payload:
            self.text.insert("end", payload[:6000], ("p",))
            if len(payload) > 6000:
                self.text.insert("end", t("\n…(preview truncated; see the preview file for the full text)"), ("muted",))
        else:
            self.text.insert("end", t("The preview file cannot be read. Discard it and regenerate."), ("muted",))
        self._disable()
        self.text.yview_moveto(0)

    def _write_behaviors(self, items: list[dict]):
        for it in items:
            verb = it.get("verb") or "admin"
            self.text.insert("end", f"{it.get('start', '')}–{it.get('end', '')}", ("time",))
            self.text.insert("end", t("  {n} min  ", n=it.get("minutes", 0)), ("dur",))
            self.text.insert("end", "● ", (f"vb_{verb}",))
            self.text.insert("end", it.get("label") or "", ("behlabel",))
            if it.get("confidence") == "low":
                self.text.insert("end", "  ?", ("behdetail",))
            self.text.insert("end", "\n", ("behlabel",))
            if it.get("detail"):
                self.text.insert("end", it["detail"], ("behdetail",))
                self.text.insert("end", "\n", ("behdetail",))

    def _draw_strip(self):
        c = self.strip
        c.delete("all")
        w = c.winfo_width()
        if w <= 1:
            return
        top, h = px(6), px(20)
        c.create_rectangle(0, top, w, top + h, fill="#eef2f7", outline="")
        for seg in self._segs:
            x0 = seg["s"] / 1440 * w
            x1 = max(x0 + px(2), seg["e"] / 1440 * w)
            c.create_rectangle(x0, top, min(w, x1), top + h, fill=seg["color"], outline="")
        for hh in range(0, 25, 6):
            x = hh / 24 * w
            anchor = "nw" if hh == 0 else ("ne" if hh == 24 else "n")
            c.create_text(x, top + h + px(6), text=f"{hh:02d}", fill=SUBTLE, font=(FONT, 7), anchor=anchor)

    # -------------------------------------------------- 文本渲染
    def _enable(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")

    def _disable(self):
        self.text.configure(state="disabled")

    def _write_plain(self, msg: str):
        self._enable()
        if msg:
            self.text.insert("end", msg, ("p", "muted"))
        self._disable()

    def _write_md(self, md: str, base: str = "p"):
        """极简 markdown：段落 / 列表 / 粗体 / 行内代码。"""
        if not md:
            return
        for raw in (md or "").splitlines():
            line = raw.rstrip()
            if not line.strip():
                self.text.insert("end", "\n", (base,))
                continue
            if line.startswith(("- ", "* ")):
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(line[2:], "bullet")
                self.text.insert("end", "\n", ("bullet",))
            elif line.startswith("#"):
                self.text.insert("end", line.lstrip("#").strip() + "\n", ("h2",))
            else:
                head = _TIME_HEAD.match(line) if base == "p" else None
                if head:
                    self.text.insert("end", head.group(1) + "  ", ("tlhead",))
                    self._write_inline(head.group(2).strip(), "tl")
                    self.text.insert("end", "\n", ("tl",))
                else:
                    self._write_inline(line, base)
                    self.text.insert("end", "\n", (base,))

    def _write_inline(self, text: str, base: str):
        pattern = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")
        for part in pattern.split(text):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                self.text.insert("end", part[2:-2], (base, "bold"))
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                self.text.insert("end", part[1:-1], (base, "code"))
            elif part.startswith("[") and "](" in part:
                label = part[1:part.index("]")]
                self.text.insert("end", label, (base, "chip"))
            else:
                self.text.insert("end", part, (base,))

    def _section(self, title: str, first: bool = False):
        self.text.insert("end", ("\n" if not first else "") + title + "\n", ("h2",))

    def _write_sub(self, label: str, text: str):
        """条目下的一行从属说明：灰标签 + 缩进正文，折行仍对齐。"""
        if not text:
            return
        self.text.insert("end", f"{label}　", ("sub", "sublabel"))
        self._write_inline(text, "sub")
        self.text.insert("end", "\n", ("sub",))

    def _write_report(self, d: dict):
        rep = d.get("report") or {}
        self._enable()
        first = True

        if d.get("status") == "degraded":
            self.text.insert("end", t("This is a degraded report: the model was not configured or the call failed, so only local stats and the timeline are included.\n"),
                             ("p", "warn"))
            first = False
        elif d.get("status") == "empty":
            self.text.insert("end", t("Too little activity that day to write a narrative. The stats and gaps below are all there is.\n"),
                             ("p", "muted"))
            first = False

        try:
            import render as render_mod

            rv = render_mod.reading_view(d)
        except Exception:
            rv = {"cards": [], "todos": [], "tasks": [], "metrics": []}

        # 页面顺序：成果 → 待办 → 主线 → … → 过程。读者先要知道"做成了什么"，
        # 而不是"我打开过哪些窗口"。与 web/day 页面、Markdown 归档保持同一套顺序。
        cards = rv.get("cards") or []
        if cards:
            self._section(t("Outcomes today"), first)
            first = False
            for c in cards:
                self.text.insert("end", f"【{c.get('status_label', '')}】", ("bullet", "muted"))
                self._write_inline(c.get("what", ""), "bullet")
                self.text.insert("end", "\n", ("bullet",))
                for label, key in ((t("Significance"), "impact"), (t("Next"), "next"), (t("Evidence"), "evidence")):
                    self._write_sub(label, c.get(key, ""))

        todos = rv.get("todos") or []
        if todos:
            self._section(t("Open items & blockers"), first)
            first = False
            kind_label = {"waiting": t("Waiting on others"), "blocked": t("Blockers"), "todo": t("To do")}
            for item in todos:
                self.text.insert("end", t("[{kind}] ", kind=kind_label.get(item.get("kind"), "to do")), ("bullet", "muted"))
                self._write_inline(item.get("item", ""), "bullet")
                self.text.insert("end", "\n", ("bullet",))
                self._write_sub(t("Why"), item.get("why", ""))

        tasks = rv.get("tasks") or []
        if tasks:
            self._section(t("Main threads"), first)
            first = False
            tnotes = rv.get("task_notes") or {}
            for task in tasks:
                self.text.insert("end", f"{task.get('start', '')}–{task.get('end', '')}  ", ("time",))
                self._write_inline(task.get("label", ""), "behlabel")
                self.text.insert("end", t("　{n} min\n", n=task.get("minutes", 0)), ("behlabel", "muted"))
                self._write_sub(t("Result"), task.get("outcome", ""))
            if tnotes.get("merged"):
                self.text.insert("end", t("({n} short switches merged into their main threads; "
  "per-segment detail is in the timeline below)\n", n=tnotes["merged"]), ("p", "muted"))

        cats = (d.get("stats") or {}).get("by_category") or {}
        if cats:
            self._section(t("Time split"), first)
            first = False
            parts = [t("{cat} {n:.0f} min", cat=cat_label(k), n=float(v)) for k, v in cats.items()]
            rhythm = (d.get("stats") or {}).get("rhythm") or {}
            if rhythm.get("longest_focus_min"):
                parts.append(t("Longest focus {m} min ({at})",
  m=rhythm["longest_focus_min"], at=rhythm.get("longest_focus_at", "")))
            self.text.insert("end", "　·　".join(parts) + "\n", ("p", "muted"))

        offline = rep.get("offline") or []
        if offline:
            self._section(t("Manual entries"), first)
            first = False
            for o in offline:
                if not isinstance(o, dict):
                    continue
                self.text.insert("end", f"{o.get('start', '')}–{o.get('end', '')}  ", ("time",))
                self.text.insert("end", o.get("label") or "", ("behlabel",))
                self.text.insert("end", "\n", ("behlabel",))
                if o.get("note"):
                    self.text.insert("end", str(o["note"]), ("sub",))
                    self.text.insert("end", "\n", ("sub",))

        fr = rep.get("friction") or []
        if fr:
            self._section(t("Blockers & cost"), first)
            first = False
            for it in fr:
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(it.get("what", ""), "bullet")
                self.text.insert("end", "\n", ("bullet",))
                for label, key in ((t("Cause"), "cause"), (t("Cost"), "cost"), (t("Handling"), "fix")):
                    self._write_sub(label, it.get(key, ""))

        ins = rep.get("insight") or []
        if ins:
            self._section(t("Insights"), first)
            first = False
            for it in ins:
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(it.get("observation", ""), "bullet")
                self.text.insert("end", "\n", ("bullet",))
                for label, key in ((t("Basis"), "evidence"), (t("Next time"), "action")):
                    self._write_sub(label, it.get(key, ""))

        for key, title in (("in_progress", t("In progress")),
                           ("blockers", t("Where it got stuck")),
                           ("decisions", t("Decisions made"))):
            items = rep.get(key) or []
            if not items:
                continue
            if key == "blockers" and fr:
                continue                    # 新的 friction 已经覆盖了
            self._section(title, first)
            first = False
            for it in items:
                text = it if isinstance(it, str) else (it.get("what") or it.get("text") or it.get("title") or json.dumps(it, ensure_ascii=False))
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(str(text), "bullet")
                self.text.insert("end", "\n", ("bullet",))

        if rep.get("opening_md") or rep.get("summary_md"):
            self._section(t("What happened"), first)
            first = False
            if rep.get("opening_md"):
                self._write_md(rep["opening_md"])
                self.text.insert("end", "\n", ("p",))
            if rep.get("summary_md"):
                self._write_md(rep["summary_md"])

        beh = rep.get("behaviors") or []
        if beh:
            self._section(t("Activity timeline (segment by segment)"), first)
            first = False
            self._write_behaviors(beh)

        tl = rep.get("timeline") or []
        if tl:
            self._section(t("Timeline"), first)
            first = False
            for it in tl:
                if not isinstance(it, dict):
                    continue
                start = str(it.get("start", ""))[11:16] or str(it.get("start", ""))
                end = str(it.get("end", ""))[11:16] or str(it.get("end", ""))
                self.text.insert("end", f"{start}–{end}  ", ("bullet", "muted"))
                self._write_inline(f"{it.get('title', '')}", "bullet")
                self.text.insert("end", f"　{cat_label(it.get('category', ''))}\n", ("bullet", "muted"))

        refs = d.get("refs") or []
        if refs:
            self._section(t("Related documents"), first)
            first = False
            for r in refs:
                label = r.get("label") or r.get("uri") or ""
                clickable = r.get("clickable", True) and r.get("uri")
                tag = f"ref:{r.get('ref_id', label)}"
                self.text.insert("end", "↗  ", ("p", "muted"))
                if clickable:
                    self.text.insert("end", label, ("p", "chip", tag))
                    self.text.tag_bind(tag, "<Button-1>", lambda e, u=r["uri"]: self._open(u))
                    self.text.tag_bind(tag, "<Enter>",
                                       lambda e: self.text.configure(cursor="hand2"))
                    self.text.tag_bind(tag, "<Leave>",
                                       lambda e: self.text.configure(cursor="arrow"))
                else:
                    self.text.insert("end", label, ("p", "muted"))
                if r.get("kind"):
                    self.text.insert("end", f"　{r['kind']}", ("p", "muted"))
                if r.get("why"):
                    self.text.insert("end", f"　·　{r['why']}", ("p", "muted"))
                self.text.insert("end", "\n", ("p",))

        gaps = (rv.get("gaps") or {}).get("inside") or []
        if gaps:
            self._section(t("Recording gaps"), first)
            first = False
            for g in gaps:
                self.text.insert("end", t("•  {start} - {end}　{min} minutes with no record "
  "(likely away, in a meeting, or on a break)\n",
  start=g.get("start", ""), end=g.get("end", ""), min=g.get("minutes", "")),
                                 ("bullet", "muted"))

        meta = d.get("meta") or {}
        if meta.get("model") or meta.get("calls"):
            self._section(t("Generation details"), first)
            parts = [t("model {m}", m=meta.get("model", "?")), t("{n} calls", n=meta.get("calls", 0))]
            if meta.get("prompt_tokens"):
                parts.append(t("{n} input tokens", n=meta["prompt_tokens"]))
            if meta.get("completion_tokens"):
                parts.append(t("{n} output tokens", n=meta["completion_tokens"]))
            if meta.get("elapsed_ms"):
                parts.append(t("{s:.1f} s", s=meta["elapsed_ms"] / 1000))
            if meta.get("issues"):
                parts.append(t("{n} validation notes", n=len(meta["issues"])))
            self.text.insert("end", "　·　".join(parts) + "\n", ("p", "muted"))

        self._disable()

    def _open(self, uri: str):
        if not open_uri(uri):
            self.app.toast(t("Cannot open: {uri}", uri=uri), "warn")

    # -------------------------------------------------- 动作
    def _open_board(self):
        if self.selected:
            self.app.open_board(self.selected)

    def _regen(self):
        if self.selected:
            self.app.generate(self.selected, force=True)

    def _copy_md(self):
        if not self.selected:
            return
        p = paths.report_md(self.selected)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            self.app.toast(t("This report has no Markdown file"), "warn")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.app.toast(t("Copied the full Markdown for {day} ({n} chars)", day=self.selected, n=len(text)))

    # -------------------------------------------------- 补录 / 遗忘 / 审核
    def _add_manual(self, day: str):
        """补一条屏幕看不到的活动：会议、外出、思考。"""
        from tkinter import simpledialog

        text = simpledialog.askstring(
            t("Add offline activity"),
            t("Add an activity for {day} that the screen could not see.\n"
  "Format: start-end name\nExample: 10:00-11:00 client meeting", day=day),
            parent=self)
        if not text:
            return
        m = re.match(r"\s*(\d{1,2}:\d{2})\s*[-–—~至]\s*(\d{1,2}:\d{2})\s+(.+)$", text)
        if not m:
            self.app.toast(t("Wrong format. Try something like 10:00-11:00 client meeting"), "warn")
            return
        import manual as manual_mod

        try:
            manual_mod.add_offline(day, m.group(1), m.group(2), m.group(3))
        except ValueError as exc:
            self.app.toast(str(exc), "warn")
            return
        self.app.toast(t("Added. Hit \"Regenerate\" to fold it into the report."))

    def _forget(self, day: str):
        ok = messagebox.askyesno(
            t("Forget this day"),
            t("This permanently deletes screenshots, events, OCR, reports, share copies and "
  "weekly summaries for {day}. It cannot be undone.\n\n"
  "Anything already sent to the model service cannot be recalled. Continue?", day=day),
            icon="warning", default="no", parent=self)
        if not ok:
            return
        self.app.forget_day(day)

    def _discard_review(self, day: str):
        import report as report_mod

        report_mod.discard_review(day)
        self.app.toast(t("Pending content discarded. Nothing was sent."))
        self.app.refresh(force=True)
        self.render(day)


# ---------------------------------------------------------------- 设置
class SettingsPage(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg=BG)
        self.app = app
        sf = ScrollFrame(self, BG)
        sf.pack(fill="both", expand=True)
        self.wrap = tk.Frame(sf.body, bg=BG)
        self.wrap.pack(fill="x", padx=px(22), pady=(px(2), px(20)))

        self.vars: dict[str, tk.Variable] = {}
        self._build_language()
        self._build_llm()
        self._build_capture()
        self._build_ocr()
        self._build_report()
        self._build_sources()
        self._build_privacy()
        self._build_startup()

        # 底部保存条
        bar = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        bar.pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=px(22), pady=px(10))
        FlatButton(inner, t("Save settings"), self._save, "primary", padx=18).pack(side="left")
        FlatButton(inner, t("Open config file"), lambda: open_uri(str(paths.CONFIG_PATH)), "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(inner, t("Reload"), self.reload_form, "ghost").pack(side="left", padx=(px(8), 0))
        self.save_hint = tk.Label(inner, text=t("Saving restarts the capture process once so the change takes effect"),
                                  bg=CARD, fg=SUBTLE, font=(FONT, 8))
        self.save_hint.pack(side="left", padx=(px(14), 0))

        self.reload_form()

    # -------------------------------------------------- 表单构件
    def _card(self, title, sub=""):
        c = Card(self.wrap, title, sub)
        c.pack(fill="x", pady=(0, px(12)))
        grid = tk.Frame(c.body, bg=CARD)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        return c, grid

    @staticmethod
    def _row(grid, r: int, label: str, ctrl, hint: str = ""):
        box = tk.Frame(grid, bg=CARD)
        box.grid(row=r, column=0, sticky="nw", pady=px(7), padx=(0, px(20)))
        tk.Label(box, text=label, bg=CARD, fg=TEXT, font=(FONT, 10)).pack(anchor="w")
        if hint:
            tk.Label(box, text=hint, bg=CARD, fg=SUBTLE, font=(FONT, 8),
                     wraplength=px(210), justify="left").pack(anchor="w", pady=(px(2), 0))
        ctrl.grid(row=r, column=1, sticky="w", pady=px(7))

    def _entry(self, parent, key: str, width=34, show=""):
        var = tk.StringVar()
        self.vars[key] = var
        e = tk.Entry(parent, textvariable=var, bg="#fbfcfe", fg=TEXT, bd=0,
                     highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                     insertbackground=TEXT, font=(FONT, 9), width=width, show=show)
        e.configure(relief="flat")
        return e

    def _spin(self, parent, key: str, lo: int, hi: int, width=8, step=1):
        var = tk.StringVar()
        self.vars[key] = var
        s = tk.Spinbox(parent, from_=lo, to=hi, increment=step, textvariable=var, width=width,
                       bg="#fbfcfe", fg=TEXT, bd=0, highlightthickness=1,
                       highlightbackground=LINE, highlightcolor=ACCENT, font=(FONT, 9),
                       buttonbackground="#eef2f7", relief="flat")
        return s

    def _check(self, parent, key: str, text: str):
        var = tk.BooleanVar()
        self.vars[key] = var
        return tk.Checkbutton(parent, text=text, variable=var, bg=CARD, fg=BODY,
                              activebackground=CARD, activeforeground=TEXT, selectcolor=CARD,
                              bd=0, highlightthickness=0, font=(FONT, 9), anchor="w",
                              cursor="hand2")

    def _combo(self, parent, key: str, values: list[str], width=22):
        var = tk.StringVar()
        self.vars[key] = var
        c = ttk.Combobox(parent, textvariable=var, values=values, state="readonly",
                         width=width, font=(FONT, 9))
        return c

    def _text(self, parent, key: str, height=3):
        box = tk.Frame(parent, bg="#fbfcfe", highlightbackground=LINE, highlightthickness=1)
        t = tk.Text(box, height=height, bg="#fbfcfe", fg=TEXT, bd=0, highlightthickness=0,
                    font=(FONT, 9), wrap="word", padx=px(6), pady=px(4), insertbackground=TEXT)
        t.pack(fill="both", expand=True)
        self.vars[key] = t
        return box

    # -------------------------------------------------- 各分组
    def _build_language(self):
        card, g = self._card(t("Language"),
                             t("Applies to the window and to the report headings"))
        self.lang_var = tk.StringVar()
        combo = ttk.Combobox(g, textvariable=self.lang_var, values=i18n.display_values(),
                             state="readonly", width=24, font=(FONT, 9))
        self._row(g, 0, t("Display language"), combo,
                  t("The window reloads when you save. A language without a translation "
                    "falls back to English"))
        self._lang_shown = self.lang_var.get()

    def _build_llm(self):
        card, g = self._card(t("Language model"), t("Any service with an OpenAI-compatible API works"))
        r = 0
        self._row(g, r, t("Endpoint"), self._entry(g, "llm.base_url"), t("e.g. https://api.deepseek.com/v1")); r += 1
        self._row(g, r, t("Model"), self._entry(g, "llm.model", width=24), t("e.g. deepseek-chat / qwen-plus")); r += 1
        key_box = tk.Frame(g, bg=CARD)
        self.key_entry = self._entry(key_box, "llm.key", width=38, show="•")
        self.key_entry.pack(side="left", ipady=px(4))
        self.key_toggle = FlatButton(key_box, t("Show"), self._toggle_key, "soft", padx=10, pady=4, font_size=8)
        self.key_toggle.pack(side="left", padx=(px(6), 0))
        FlatButton(key_box, t("Test connection"), self._test_llm, "ghost", padx=10, pady=4, font_size=8).pack(side="left", padx=(px(6), 0))
        self._row(g, r, "API Key", key_box, t("Leave blank to use the WID_LLM_API_KEY env var")); r += 1
        box = tk.Frame(card.body, bg=CARD)
        box.pack(fill="x", pady=(px(6), 0))
        self.llm_hint = tk.Label(box, text="", bg=CARD, fg=SUBTLE, font=(FONT, 8), justify="left")
        self.llm_hint.pack(anchor="w")

    def _toggle_key(self):
        hidden = str(self.key_entry.cget("show")) == "•"
        self.key_entry.configure(show="" if hidden else "•")
        self.key_toggle.set_text(t("Hide") if hidden else t("Show"))

    def _build_capture(self):
        card, g = self._card(t("Capture"), t("Saving restarts the capture process automatically"))
        r = 0
        self._row(g, r, t("Sample interval (s)"), self._spin(g, "capture.interval_sec", 5, 300),
                  t("Smaller means finer detail, at the cost of power and disk")); r += 1
        self._row(g, r, t("Pause after N minutes idle"), self._spin(g, "capture.idle_pause_sec_min", 1, 120),
                  t("Stops capturing when there is no input, so idle time is not recorded")); r += 1
        self._row(g, r, t("Min gap for the same window (s)"), self._spin(g, "capture.min_save_gap_sec", 10, 600)); r += 1
        self._row(g, r, t("JPEG quality"), self._spin(g, "capture.jpeg_quality", 30, 95),
                  t("Around 62 balances legibility and file size")); r += 1
        self._row(g, r, t("Keep screenshots"), self._check(g, "privacy.store_screenshots", t("Keep (turning this off records titles only; reports get coarser)"))); r += 1

    def _build_ocr(self):
        card, g = self._card(t("OCR"), t("OCR runs entirely on this machine; screenshots never leave it"))
        r = 0
        self._row(g, r, t("Engine"), self._combo(g, "ocr.engine",
                                            ["auto", "rapidocr", "umi_http", "umi_cli", "none"]),
                  t("auto prefers Umi, otherwise the bundled RapidOCR; none disables OCR")); r += 1
        self._row(g, r, t("Max OCR calls per day"), self._spin(g, "ocr.max_per_day", 0, 5000, width=8),
                  t("Caps model input size and processing time")); r += 1
        self._row(g, r, t("OCR the active window only"), self._check(g, "ocr.crop_active_window",
                                                      t("Recommended: full-screen captures blur small text"))); r += 1

    def _build_report(self):
        card, g = self._card(t("Reports & retention"))
        r = 0
        self._row(g, r, t("Report hour"), self._spin(g, "report.trigger_hour", 0, 23),
                  t("21:00 by default; a final version follows at 04:05")); r += 1
        self._row(g, r, t("Empty-day threshold (minutes)"), self._spin(g, "report.min_active_minutes", 0, 600)); r += 1
        self._row(g, r, t("Open the dashboard after generating"), self._check(g, "report.open_browser", t("Open the dashboard when the final report is ready"))); r += 1
        self._row(g, r, t("Review before sending"), self._check(g, "report.review_mode", t("Prepare content for review; send only after you confirm")),
                  t("When on, the scheduled run only prepares content; nothing is sent until you hit \"Approve & send\" on the Reports page")); r += 1
        self._row(g, r, t("Keep screenshots for (days)"), self._spin(g, "retention.shots_days", 1, 365),
                  t("Expired screenshots are deleted; reports are kept forever")); r += 1
        self._row(g, r, t("Keep events & OCR for (days)"), self._spin(g, "retention.events_days", 1, 365)); r += 1
        self._row(g, r, t("Keep report OCR excerpts for (days)"), self._entry(g, "retention.report_excerpt_days", width=8),
              t("Blank follows the event retention; 0 keeps none. Only appendix excerpts are removed, never the narrative")); r += 1
        self._row(g, r, t("Total data cap (GB)"), self._spin(g, "retention.max_total_gb", 1, 500),
                  t("Above the cap, the oldest screenshots go first")); r += 1

    def _build_sources(self):
        card, g = self._card(t("Source links"),
                             t("Attaches the original URL to each page you looked at"))
        r = 0
        self._row(g, r, t("Read browser history"),
                  self._check(g, "browse.history.enabled",
                              t("Match page blocks against local Chrome/Edge history")),
                  t("Read-only and offline. Only the URLs of this day are matched; nothing is uploaded "
                    "and no browsing history is copied into the data folder.")); r += 1
        self._row(g, r, t("Fall back to the address bar"),
                  self._check(g, "browse.address_bar.enabled",
                              t("When history has no hit, try the URL shown at the top of the window")),
                  t("Only works on captures that stored line positions; older days are skipped")); r += 1
        self._row(g, r, t("Keep the query string in shared reports"),
                  self._check(g, "share.keep_query",
                              t("Off by default: query strings often carry session ids and search terms")),
                  t("Local reports always keep it, minus sensitive values")); r += 1

    def _build_privacy(self):
        card, g = self._card(t("Privacy"), t("Pause/block leaves only an anonymous state; regex redaction cannot catch everything"))
        r = 0
        self._row(g, r, t("Capture scope"), self._combo(g, "privacy.capture_scope", ["active_window", "all_screens"]),
                  t("Active window by default; all screens also captures background windows and other monitors")); r += 1
        self._row(g, r, t("Allow sending to the model"), self._check(g, "privacy.allow_llm", t("Allow sending redacted titles and OCR excerpts")),
                  t("When off, only a local timeline is built; even the connection test sends nothing")); r += 1
        self._row(g, r, t("Apps never recorded"), self._text(g, "privacy.blocked_apps", 3),
                  t("One process name per line, e.g. 1Password.exe; case-insensitive")); r += 1
        self._row(g, r, t("Apps recorded as time only"), self._text(g, "privacy.skip_ocr_apps", 3),
                  t("No title, no screenshot, no OCR; also filtered when older days are regenerated")); r += 1
        self._row(g, r, t("Blocked title patterns"), self._text(g, "privacy.blocked_title_patterns", 2),
                  t("One regex per line; an invalid one rejects the save instead of being ignored")); r += 1

    def _build_startup(self):
        card, g = self._card(t("Startup"), "")
        r = 0
        box = tk.Frame(g, bg=CARD)
        self.start_var = tk.BooleanVar()
        cb = tk.Checkbutton(box, text=t("Start recording automatically at logon"), variable=self.start_var,
                            bg=CARD, fg=BODY, activebackground=CARD, selectcolor=CARD,
                            bd=0, highlightthickness=0, font=(FONT, 9), cursor="hand2",
                            command=self._toggle_autostart)
        cb.pack(side="left")
        self._row(g, r, t("Start with Windows"), box, t("No admin needed; registers a per-user scheduled task or a Run entry")); r += 1
        dir_lbl = tk.Label(g, text=str(paths.ROOT), bg=CARD, fg=MUTED, font=(FONT, 9), cursor="hand2")
        dir_lbl.bind("<Button-1>", lambda e: open_uri(str(paths.ROOT)))
        self._row(g, r, t("Data folder"), dir_lbl, t("Click to open the folder"))

    # -------------------------------------------------- 读写
    def reload_form(self):
        cfg = cfgmod.load(reload=True)
        lang_code = i18n.normalize(cfg.get("ui.language", i18n.DEFAULT_LOCALE))
        try:
            self.lang_var.set(f"{i18n.native_name(lang_code)} ({lang_code})")
        except Exception:
            pass
        getters = {
            "llm.base_url": lambda: str(cfg.get("llm.base_url", "")),
            "llm.model": lambda: str(cfg.get("llm.model", "")),
            "llm.key": lambda: cfg.api_key(),
            "capture.interval_sec": lambda: str(int(cfg.get("capture.interval_sec", 20))),
            "capture.idle_pause_sec_min": lambda: str(int(float(cfg.get("capture.idle_pause_sec", 300)) // 60)),
            "capture.min_save_gap_sec": lambda: str(int(cfg.get("capture.min_save_gap_sec", 90))),
            "capture.jpeg_quality": lambda: str(int(cfg.get("capture.jpeg_quality", 62))),
            "privacy.store_screenshots": lambda: bool(cfg.get("privacy.store_screenshots", True)),
            "privacy.capture_scope": lambda: str(cfg.get("privacy.capture_scope", "active_window")),
            "privacy.allow_llm": lambda: bool(cfg.get("privacy.allow_llm", True)),
            "ocr.engine": lambda: str(cfg.get("ocr.engine", "auto")),
            "ocr.max_per_day": lambda: str(int(cfg.get("ocr.max_per_day", 1200))),
            "ocr.crop_active_window": lambda: bool(cfg.get("ocr.crop_active_window", True)),
            "report.trigger_hour": lambda: str(int(cfg.get("report.trigger_hour", 21))),
            "report.min_active_minutes": lambda: str(int(cfg.get("report.min_active_minutes", 20))),
            "report.open_browser": lambda: bool(cfg.get("report.open_browser", True)),
            "report.review_mode": lambda: bool(cfg.get("report.review_mode", False)),
            "browse.history.enabled": lambda: bool(cfg.get("browse.history.enabled", False)),
            "browse.address_bar.enabled": lambda: bool(cfg.get("browse.address_bar.enabled", True)),
            "share.keep_query": lambda: bool(cfg.get("share.keep_query", False)),
            "retention.shots_days": lambda: str(int(cfg.get("retention.shots_days", 7))),
            "retention.events_days": lambda: str(int(cfg.get("retention.events_days", 30))),
            "retention.report_excerpt_days": lambda: ("" if cfg.get("retention.report_excerpt_days") is None
                                                       else str(cfg.get("retention.report_excerpt_days"))),
            "retention.max_total_gb": lambda: str(int(cfg.get("retention.max_total_gb", 20))),
            "privacy.blocked_apps": lambda: "\n".join(cfg.get("privacy.blocked_apps") or []),
            "privacy.skip_ocr_apps": lambda: "\n".join(cfg.get("privacy.skip_ocr_apps") or []),
            "privacy.blocked_title_patterns": lambda: "\n".join(cfg.get("privacy.blocked_title_patterns") or []),
        }
        for key, getter in getters.items():
            var = self.vars.get(key)
            value = getter()
            try:
                if isinstance(var, tk.Text):
                    var.delete("1.0", "end")
                    var.insert("1.0", value)
                else:
                    var.set(value)
            except Exception:
                pass
        try:
            self.start_var.set(bool(autostart.status().get("installed")))
        except Exception:
            self.start_var.set(False)
        self.llm_hint.configure(
            text=(cfg.error or (t("Sending is disabled, including the connection test") if not cfg.get("privacy.allow_llm", True) else
                  t("Current: {model} @ {url}　", model=cfg.get("llm.model", ""),
  url=cfg.get("llm.base_url", ""))
                  + (t("Key configured") if cfg.has_api_key() else t("No key yet; reports will be degraded")))),
            fg=BAD_C if cfg.error else SUBTLE)
        if cfg.error:
            self.save_hint.configure(text=t("Invalid config: open the file and fix it. The placeholder defaults will not overwrite it."), fg=BAD_C)

    def _num(self, key: str, lo: int, hi: int, label: str) -> int:
        raw = str(self.vars[key].get()).strip()
        try:
            value = int(float(raw))
        except Exception:
            raise ValueError(t("{label} must be a number; it is currently \"{raw}\"", label=label, raw=raw))
        if value < lo or value > hi:
            raise ValueError(t("{label} should be between {lo} and {hi}", label=label, lo=lo, hi=hi))
        return value

    def _lines(self, key: str) -> list[str]:
        var = self.vars[key]
        text = var.get("1.0", "end") if isinstance(var, tk.Text) else str(var.get())
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _save(self):
        lang_before = i18n.current()
        lang_after = i18n.code_of_display(str(self.lang_var.get()))
        try:
            updates = {
                "llm.base_url": str(self.vars["llm.base_url"].get()).strip(),
                "llm.model": str(self.vars["llm.model"].get()).strip(),
                "capture.interval_sec": self._num("capture.interval_sec", 5, 300, t("Interval")),
                "capture.idle_pause_sec": self._num("capture.idle_pause_sec_min", 1, 120, t("Idle pause")) * 60,
                "capture.min_save_gap_sec": self._num("capture.min_save_gap_sec", 10, 600, t("Min gap")),
                "capture.jpeg_quality": self._num("capture.jpeg_quality", 30, 95, t("JPEG quality")),
                "privacy.store_screenshots": bool(self.vars["privacy.store_screenshots"].get()),
                "privacy.capture_scope": str(self.vars["privacy.capture_scope"].get()),
                "privacy.allow_llm": bool(self.vars["privacy.allow_llm"].get()),
                "ocr.engine": str(self.vars["ocr.engine"].get()),
                "ocr.max_per_day": self._num("ocr.max_per_day", 0, 5000, t("Daily OCR cap")),
                "ocr.crop_active_window": bool(self.vars["ocr.crop_active_window"].get()),
                "report.trigger_hour": self._num("report.trigger_hour", 0, 23, t("Report hour")),
                "report.min_active_minutes": self._num("report.min_active_minutes", 0, 600, t("Empty-day threshold")),
                "report.open_browser": bool(self.vars["report.open_browser"].get()),
                "report.review_mode": bool(self.vars["report.review_mode"].get()),
                "browse.history.enabled": bool(self.vars["browse.history.enabled"].get()),
                "browse.address_bar.enabled": bool(self.vars["browse.address_bar.enabled"].get()),
                "share.keep_query": bool(self.vars["share.keep_query"].get()),
                "retention.shots_days": self._num("retention.shots_days", 1, 365, t("Keep screenshots for (days)")),
                "retention.events_days": self._num("retention.events_days", 1, 365, t("Event retention")),
                "retention.max_total_gb": self._num("retention.max_total_gb", 1, 500, t("Total cap")),
                "privacy.blocked_apps": self._lines("privacy.blocked_apps"),
                "privacy.skip_ocr_apps": self._lines("privacy.skip_ocr_apps"),
                "privacy.blocked_title_patterns": self._lines("privacy.blocked_title_patterns"),
                "ui.language": lang_after,
            }
            if not updates["llm.base_url"] or not updates["llm.model"]:
                raise ValueError(t("Endpoint and model are required"))
            excerpt_days = str(self.vars["retention.report_excerpt_days"].get()).strip()
            if excerpt_days and (not excerpt_days.isdecimal() or int(excerpt_days) > 36500):
                raise ValueError(t("Report excerpt days must be an integer 0-36500; leave blank to follow event retention"))
            updates["retention.report_excerpt_days"] = int(excerpt_days) if excerpt_days else None
        except ValueError as exc:
            self.app.toast(str(exc), "warn")
            return

        try:
            cfgmod.save_values(updates)
            cfgmod.save_api_key(str(self.vars["llm.key"].get()))
        except (OSError, ValueError, privacy.PrivacyError) as exc:
            self.save_hint.configure(text=t("Save did not complete: {e}", e=exc), fg=BAD_C)
            self.app.toast(t("Save did not complete. Check the config and file permissions; capture was not restarted."), "warn")
            return
        self.save_hint.configure(text=t("Saved · restarting capture to apply the new settings…"), fg=OK_C)
        self.app.toast(t("Settings saved"))

        hb = status.heartbeat()
        if hb["alive"]:
            self.app.run_bg(control.restart, self._after_restart)
        else:
            self.save_hint.configure(text=t("Saved · capture is not running; the change applies on its next start"), fg=SUBTLE)
        self.reload_form()
        if lang_after != lang_before:
            # 语言变了：整窗重建一次，否则只有新控件是英文、旧的还是中文
            self.app.relaunch()

    def _after_restart(self, kind: str, payload):
        if kind == "ok":
            ok, msg = payload
            self.save_hint.configure(text=t("Saved · {msg}", msg=msg), fg=OK_C if ok else WARN_C)
        else:
            self.save_hint.configure(text=t("Saved, but the capture process failed to restart: {p}", p=payload), fg=WARN_C)

    def _toggle_autostart(self):
        want = bool(self.start_var.get())
        try:
            if want:
                msg = autostart.install(cfgmod.load(reload=True))
            else:
                msg = autostart.uninstall()
            self.app.toast(msg)
            self.start_var.set(bool(autostart.status().get("installed")))
            self.app.invalidate_autostart()
        except Exception as exc:
            self.app.toast(t("Autostart setting failed: {e}", e=exc), "warn")
            self.start_var.set(not want)

    def _test_llm(self):
        import llm

        saved_cfg = cfgmod.load(reload=True)
        if saved_cfg.error:
            self.app.toast(saved_cfg.error, "warn")
            return
        if not saved_cfg.get("privacy.allow_llm", True) or not self.vars["privacy.allow_llm"].get():
            self.app.toast(t("Sending to the model is disabled. Allow it and save the settings first if you want to test."), "warn")
            return
        base = str(self.vars["llm.base_url"].get()).strip()
        model = str(self.vars["llm.model"].get()).strip()
        key = str(self.vars["llm.key"].get()).strip()
        if not base or not model:
            self.app.toast(t("Fill in the endpoint and model first"), "warn")
            return
        if (base.rstrip("/") != str(saved_cfg.get("llm.base_url", "")).rstrip("/")
                or model != saved_cfg.get("llm.model")):
            self.app.toast(t("Endpoint or model changed; save the settings before testing"), "warn")
            return
        if not key:
            self.app.toast(t("Enter an API key first"), "warn")
            return
        self.llm_hint.configure(text=t("Connecting…"), fg=MUTED)

        def work():
            client = llm.LLMClient(saved_cfg, self.app.log)
            client.api_key = key
            client.timeout = 25
            client.max_retries = 1
            client.chat_json(t("You are a connectivity test assistant."), t("Output only JSON: {\"ok\": true}"), max_tokens=20)
            return model

        def done(kind, payload):
            if kind == "ok":
                self.llm_hint.configure(text=t("Connection OK · {p}", p=payload), fg=OK_C)
                self.app.toast(t("Model connection OK"))
            else:
                self.llm_hint.configure(text=t("Connection failed · {p}", p=payload), fg=BAD_C)
                self.app.toast(t("Model connection failed - see the red text below"), "warn")

        self.app.run_bg(work, done)


# ---------------------------------------------------------------- 诊断
class DiagPage(tk.Frame):
    def __init__(self, parent, app: "App"):
        super().__init__(parent, bg=BG)
        self.app = app
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=px(22), pady=(px(2), px(22)))

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(fill="x")
        FlatButton(bar, t("Run checks"), self.run_doctor, "primary").pack(side="left")
        FlatButton(bar, t("OCR test"), self.run_ocr, "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(bar, t("Open log folder"), lambda: open_uri(str(paths.LOGS)), "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(bar, t("Open data folder"), lambda: open_uri(str(paths.ROOT)), "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(bar, t("Cleanup preview"), self.run_clean, "ghost").pack(side="left", padx=(px(8), 0))
        self.summary = tk.Label(bar, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.summary.pack(side="right")

        card = Card(wrap, pad=0)
        card.pack(fill="both", expand=True, pady=(px(12), 0))
        self.sf = ScrollFrame(card.body, CARD)
        self.sf.pack(fill="both", expand=True, padx=px(14), pady=px(12))
        self.hint = None
        self._set_hint(t("Hit \"Run checks\" to inspect the environment"))

    def _set_hint(self, text: str):
        self.hint = tk.Label(self.sf.body, text=text, bg=CARD, fg=SUBTLE, font=(FONT, 9),
                             justify="left", anchor="w", wraplength=px(700))
        self.hint.pack(anchor="w")

    def run_doctor(self):
        self._clear()
        self._set_hint(t("Running checks…"))
        self.app.run_bg(lambda: doctor.run(cfgmod.load(reload=True)), self._show)

    def _clear(self):
        for w in self.sf.body.winfo_children():
            w.destroy()
        self.hint = None

    def _show(self, kind: str, payload):
        self._clear()
        if kind != "ok":
            tk.Label(self.sf.body, text=t("Checks failed: {p}", p=payload), bg=CARD, fg=BAD_C, font=(FONT, 9)).pack(anchor="w")
            return
        rows = payload
        s = doctor.summary(rows)
        self.summary.configure(text=t("{ok} ok / {warn} warnings / {fail} failed",
  ok=s["ok"], warn=s["warn"], fail=s["fail"]))
        for level, name, detail in rows:
            color = OK_C if level == doctor.OK else (WARN_C if level == doctor.WARN else BAD_C)
            row = tk.Frame(self.sf.body, bg=CARD)
            row.pack(fill="x", pady=px(3))
            dot = tk.Canvas(row, width=px(8), height=px(8), bg=CARD, highlightthickness=0)
            dot.create_oval(0, 0, px(8), px(8), fill=color, outline="")
            dot.pack(side="left", padx=(0, px(10)))
            tk.Label(row, text=name, bg=CARD, fg=TEXT, font=(FONT, 9, "bold"), width=10,
                     anchor="w").pack(side="left")
            tk.Label(row, text=detail, bg=CARD, fg=MUTED, font=(FONT, 9),
                     anchor="w", justify="left", wraplength=px(560)).pack(side="left", fill="x", expand=True)

    def run_ocr(self):
        self._clear()
        self._set_hint(t("Capturing and running OCR (the first run loads the model, 5-15 s)…"))

        def work():
            import ocr

            return ocr.recognize_screen(cfgmod.load(reload=True))

        self.app.run_bg(work, self._show_ocr)

    def _show_ocr(self, kind: str, payload):
        self._clear()
        if kind != "ok":
            tk.Label(self.sf.body, text=t("OCR failed: {p}", p=payload), bg=CARD, fg=BAD_C, font=(FONT, 9)).pack(anchor="w")
            return
        res = payload
        head = t("engine {engine} · status {status} · {ms} ms · {chars} chars", engine=res.get("engine") or "—", status=res.get("status"), ms=res.get("ms"), chars=len(res.get("text") or ""))
        tk.Label(self.sf.body, text=head, bg=CARD, fg=TEXT, font=(FONT, 9, "bold")).pack(anchor="w")
        if res.get("error"):
            tk.Label(self.sf.body, text=res["error"], bg=CARD, fg=BAD_C, font=(FONT, 8)).pack(anchor="w", pady=(px(4), 0))
        body = tk.Text(self.sf.body, height=14, bg="#fbfcfe", fg=BODY, bd=1, relief="flat",
                       font=(MONO, 9), wrap="word", highlightbackground=LINE, highlightthickness=1)
        body.pack(fill="both", expand=True, pady=(px(8), 0))
        body.insert("1.0", (res.get("text") or t("(no text recognized)"))[:3000])
        body.configure(state="disabled")

    def run_clean(self):
        self._clear()
        self._set_hint(t("Measuring reclaimable data (nothing is deleted)…"))

        def work():
            import retention

            return retention.clean(cfgmod.load(reload=True), self.app.log, dry_run=True)

        def done(kind, payload):
            self._clear()
            if kind != "ok":
                tk.Label(self.sf.body, text=t("Cleanup preview failed: {p}", p=payload), bg=CARD, fg=BAD_C, font=(FONT, 9)).pack(anchor="w")
                return
            s = payload or {}
            if s.get("status") == "busy":
                self._set_hint(s.get("reason", t("A report job is already running; try the preview again shortly")), WARN_C)
                return
            lines = [
                t("{n} screenshots can be deleted", n=s.get("removed_shots", 0)),
                t("{mb:.0f} MB can be freed", mb=s.get("freed_mb", 0)),
                t("{n} expired day folders", n=len(s.get("removed_days") or []))
                + (f"（{', '.join(s.get('removed_days') or [])[:120]}）" if s.get("removed_days") else ""),
                t("{b} OCR excerpts in {r} reports can be cleared",
  b=s.get("excerpt_blocks", 0), r=len(s.get("excerpt_reports") or [])),
                "",
                t("Preview only; no files are deleted. Automatic cleanup runs in the capture process after clean_hour (04:00 by default)."),
                t("Only OCR excerpts in the local report appendix are cleared; narrative, titles, external references and Obsidian copies are untouched."),
                *(s.get("errors") or []),
            ]
            for line in lines:
                tk.Label(self.sf.body, text=line, bg=CARD, fg=BODY, font=(FONT, 9),
                         justify="left", anchor="w").pack(anchor="w", pady=px(1))

        self.app.run_bg(work, done)


# ---------------------------------------------------------------- 主窗口
def nav_items() -> list[tuple[str, str, str]]:
    """左侧导航项（key, 显示名, 图标）。

    同样是「英文源 + 调用时翻译」：写成模块级列表的话，切语言后菜单会停在旧语言，
    这正是 v1.2 里报出来的那个 bug（内容页已经是中文，左侧仍是 Overview/Reports）。
    """
    return [("dash", t("Overview"), "dash"), ("reports", t("Reports"), "report"),
            ("settings", t("Settings"), "gear"), ("diag", t("Diagnostics"), "pulse")]


class App(tk.Tk):
    def __init__(self, shot_path: str | None = None, shot_page: str | None = None,
                 shot_day: str | None = None):
        super().__init__()
        self.withdraw()
        self.shot_path = shot_path
        self.shot_page = shot_page
        self.shot_day = shot_day
        self.cfg = cfgmod.load()
        self.log = None
        try:
            import logging_setup

            self.log = logging_setup.setup("gui", console=False)
        except Exception:
            pass
        self._q: queue.Queue = queue.Queue()
        self._busy = 0
        self._autostart_cache: tuple[float, bool] | None = None
        self._lock_handle = None
        self.snap: dict = {}
        self.current = ""
        self._diag_ran = False
        self.tray = None                 # 系统托盘（装不上就退化成「关窗口=退出」）
        self._tray_hint = False
        self._autostarted = False
        self.relaunch_requested = False   # 改语言后要求整窗重建

        self._setup_window()
        self._setup_style()
        self._build_shell()
        self._build_pages()
        self.show("dash")

        self.bind("<F5>", lambda e: self.refresh(force=True))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._reveal)
        self.after(120, self._drain)
        self.after(400, lambda: self.refresh(force=True))
        self.after(3000, self._auto_tick)

    # -------------------------------------------------- 初始化
    def _setup_window(self):
        global S, FONT, MONO
        try:
            self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        except Exception:
            pass
        S = max(1.0, self.winfo_fpixels("1i") / 96.0)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        # 高 DPI 下窗口会按比例放大，先夹到屏幕尺寸内，避免超出屏幕看不见底部
        S = max(0.85, min(S, sw * 0.94 / 1080.0, sh * 0.90 / 740.0))

        families = set(tkfont.families(self))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
            if name in families:
                FONT = name
                break
        MONO = "Cascadia Mono" if "Cascadia Mono" in families else ("Consolas" if "Consolas" in families else "Courier New")

        w, h = px(1080), px(740)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - px(20))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(min(px(940), int(sw * 0.8)), min(px(620), int(sh * 0.8)))
        self.title(TITLE)
        self.configure(bg=BG)
        icon = paths.BUNDLE / "assets" / "app.ico"
        try:
            if icon.exists():
                self.iconbitmap(str(icon))
        except Exception:
            pass

    def _setup_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground="#fbfcfe", background=CARD,
                     bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                     arrowcolor=MUTED, padding=px(5), relief="flat")
        st.map("TCombobox", fieldbackground=[("readonly", "#fbfcfe")],
               bordercolor=[("focus", ACCENT)], arrowcolor=[("active", TEXT)])
        st.configure("Vertical.TScrollbar", background="#d7dbe4", troughcolor=BG,
                     bordercolor=BG, arrowcolor=MUTED, arrowsize=px(10), width=px(9))
        st.map("Vertical.TScrollbar", background=[("active", "#c3c9d6")])

    def _build_shell(self):
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)

        # 侧栏
        side = tk.Frame(shell, bg=SIDEBAR, width=px(198))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=px(18), pady=(px(22), px(26)))
        logo = tk.Canvas(brand, width=px(30), height=px(30), bg=SIDEBAR, highlightthickness=0)
        logo.create_rectangle(px(1), px(1), px(29), px(29), fill="#2563eb", outline="")
        for i, hh in enumerate((8, 15, 21)):
            logo.create_line(px(7), px(hh), px(23), px(hh), fill="#ffffff", width=px(2))
        logo.create_line(px(7), px(8), px(7), px(21), fill="#93c5fd", width=px(2))
        logo.pack(side="left")
        box = tk.Frame(brand, bg=SIDEBAR)
        box.pack(side="left", padx=(px(10), 0))
        tk.Label(box, text="What-IDid", bg=SIDEBAR, fg="#f8fafc",
                 font=(FONT, 12, "bold")).pack(anchor="w")
        tk.Label(box, text=t("Automatic work log"), bg=SIDEBAR, fg="#7c8698", font=(FONT, 8)).pack(anchor="w")

        self.nav_items = {}
        for key, label, icon in nav_items():
            item = tk.Frame(side, bg=SIDEBAR, cursor="hand2")
            item.pack(fill="x", padx=px(10), pady=px(2))
            bar = tk.Frame(item, bg=SIDEBAR, width=px(3))
            bar.pack(side="left", fill="y")
            ico = self._nav_icon(item, icon, "#94a3b8")
            ico.pack(side="left", padx=(px(12), px(10)), pady=px(9))
            lbl = tk.Label(item, text=label, bg=SIDEBAR, fg="#cbd5e1", font=(FONT, 10), anchor="w")
            lbl.pack(side="left", fill="x", expand=True, pady=px(9))
            for w in (item, ico, lbl):
                w.bind("<Button-1>", lambda e, k=key: self.show(k))
                w.bind("<Enter>", lambda e, i=item, l=lbl: self._hover_nav(i, l, True))
                w.bind("<Leave>", lambda e, i=item, l=lbl: self._hover_nav(i, l, False))
            self.nav_items[key] = {"frame": item, "bar": bar, "icon": ico, "label": lbl}

        foot = tk.Frame(side, bg=SIDEBAR)
        foot.pack(side="bottom", fill="x", padx=px(18), pady=px(16))
        tk.Label(foot, text=f"v{VERSION}", bg=SIDEBAR, fg="#5b6779", font=(FONT, 8)).pack(anchor="w")
        dirlbl = tk.Label(foot, text=str(paths.ROOT), bg=SIDEBAR, fg="#78849a", font=(FONT, 7),
                          wraplength=px(160), justify="left", cursor="hand2", anchor="w")
        dirlbl.pack(anchor="w", pady=(px(2), 0))
        dirlbl.bind("<Button-1>", lambda e: open_uri(str(paths.ROOT)))

        # 主区
        main = tk.Frame(shell, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        header = tk.Frame(main, bg=BG)
        header.pack(fill="x", padx=px(22), pady=(px(18), px(8)))
        tbox = tk.Frame(header, bg=BG)
        tbox.pack(side="left")
        self.title_lbl = tk.Label(tbox, text="", bg=BG, fg=TEXT, font=(FONT, 16, "bold"))
        self.title_lbl.pack(anchor="w")
        self.sub_lbl = tk.Label(tbox, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.sub_lbl.pack(anchor="w", pady=(px(2), 0))

        pill = tk.Frame(header, bg=BG)
        pill.pack(side="right")
        self.pill = tk.Frame(pill, bg="#ffffff", highlightbackground=LINE, highlightthickness=1)
        self.pill.pack(side="right")
        self.pill_dot = tk.Canvas(self.pill, width=px(9), height=px(9), bg="#ffffff", highlightthickness=0)
        self.pill_dot.pack(side="left", padx=(px(10), px(6)), pady=px(7))
        self.pill_lbl = tk.Label(self.pill, text=t("Checking"), bg="#ffffff", fg=MUTED, font=(FONT, 8))
        self.pill_lbl.pack(side="left", padx=(0, px(10)))
        FlatButton(pill, t("Refresh"), lambda: self.refresh(force=True), "ghost", padx=10, pady=5, font_size=9).pack(side="right", padx=(0, px(8)))

        self.container = tk.Frame(main, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.container.rowconfigure(0, weight=1)
        self.container.columnconfigure(0, weight=1)

        # 轻提示
        self._toast_lbl = tk.Label(self, text="", bg="#111827", fg="#f8fafc", font=(FONT, 9),
                                   padx=px(16), pady=px(9))
        self._toast_job = None

    def _nav_icon(self, parent, kind: str, color: str):
        c = tk.Canvas(parent, width=px(16), height=px(16), bg=SIDEBAR, highlightthickness=0)
        k = px
        if kind == "dash":
            for dx in (0, 8):
                for dy in (0, 8):
                    c.create_rectangle(k(1) + k(dx), k(1) + k(dy), k(6) + k(dx), k(6) + k(dy),
                                       outline=color, width=1)
        elif kind == "report":
            c.create_rectangle(k(2), k(1), k(14), k(15), outline=color, width=1)
            for i, yy in enumerate((5, 8, 11)):
                c.create_line(k(5), k(yy), k(11 - i), k(yy), fill=color, width=1)
        elif kind == "gear":
            c.create_oval(k(4.5), k(4.5), k(11.5), k(11.5), outline=color, width=1)
            c.create_oval(k(7), k(7), k(9), k(9), outline=color, width=1)
            c.create_line(k(8), k(1), k(8), k(3), fill=color)
            c.create_line(k(8), k(13), k(8), k(15), fill=color)
            c.create_line(k(1), k(8), k(3), k(8), fill=color)
            c.create_line(k(13), k(8), k(15), k(8), fill=color)
        else:
            c.create_line(k(1), k(8), k(4), k(8), k(6), k(3), k(9), k(13), k(11), k(8), k(15), k(8),
                          fill=color, width=1)
        return c

    def _hover_nav(self, item, label, entering: bool):
        key = next((k for k, v in self.nav_items.items() if v["frame"] is item), None)
        if key and key == self.current:
            return
        bg = SIDEBAR_SEL if entering else SIDEBAR
        item.configure(bg=bg)
        label.configure(bg=bg)

    def _build_pages(self):
        self.pages = {
            "dash": DashPage(self.container, self),
            "reports": ReportsPage(self.container, self),
            "settings": SettingsPage(self.container, self),
            "diag": DiagPage(self.container, self),
        }
        for p in self.pages.values():
            p.grid(row=0, column=0, sticky="nsew")
            p.grid_remove()
        self.current = ""

    # -------------------------------------------------- 导航
    def show(self, key: str):
        if key not in self.pages:
            return
        for k, frame in self.pages.items():
            frame.grid() if k == key else frame.grid_remove()
        self.current = key
        for k, item in self.nav_items.items():
            sel = k == key
            bg = SIDEBAR_SEL if sel else SIDEBAR
            item["frame"].configure(bg=bg)
            item["label"].configure(bg=bg, fg="#f1f5f9" if sel else "#cbd5e1")
            item["bar"].configure(bg=ACCENT if sel else bg)
            item["icon"].configure(bg=bg)
        titles = {
            "dash": (t("Overview"), t("Today at a glance")),
            "reports": (t("Reports"), t("Daily records, outcomes and traceable evidence")),
            "settings": (t("Settings"), t("Capture, OCR, model and privacy")),
            "diag": (t("Diagnostics"), t("Start here when something looks wrong")),
        }
        head, sub = titles.get(key, ("", ""))
        self.title_lbl.configure(text=head)
        self.sub_lbl.configure(text=sub)
        if key == "reports":
            self.refresh_reports()
        if key == "settings":
            self.pages["settings"].reload_form()
        if key == "diag" and not self._diag_ran:
            self._diag_ran = True
            self.after(120, self.pages["diag"].run_doctor)

    # -------------------------------------------------- 刷新
    def _auto_tick(self):
        try:
            self.refresh()
        except Exception:
            traceback.print_exc()
        # 收起到托盘后不用 3 秒一刷，省电也省 IO
        hidden = self.state() == "withdrawn"
        self.after(15000 if hidden else 3000, self._auto_tick)

    def refresh(self, force: bool = False):
        prev = self.snap.get("today_report", {}).get("exists") if self.snap else None
        self.snap = status.snapshot(cfgmod.load())
        self._update_pill()
        self.pages["dash"].refresh(self.snap)
        self._update_tray_tip()
        if prev is False and self.snap["today_report"]["exists"]:
            self.toast(t("Today's report is ready - switch to \"Reports\""), "ok")
        if self.current == "reports":
            self.refresh_reports()

    def refresh_reports(self):
        snap = self.snap or status.snapshot(cfgmod.load())
        self.pages["reports"].set_index(snap["reports"])

    def _update_pill(self):
        if not self.snap:
            return
        hb = self.snap["heartbeat"]
        pause = self.snap["paused"]
        if self._busy:
            text, color = t("Working…"), ACCENT
        elif self.snap.get("privacy_error"):
            text, color = t("Config error · content processing stopped"), BAD_C
        elif pause:
            text, color = t("Paused"), WARN_C
        elif hb["alive"]:
            text, color = t("Recording · {age}s", age=hb["age"]), OK_C
        else:
            text, color = t("Not running"), BAD_C
        self.pill_lbl.configure(text=text, fg=color if self._busy or hb["alive"] or pause else MUTED)
        self.pill_dot.delete("all")
        self.pill_dot.create_oval(0, 0, px(9), px(9), fill=color, outline="")

    def autostart_state(self) -> dict:
        import time

        now = time.time()
        if self._autostart_cache and now - self._autostart_cache[0] < 60:
            return self._autostart_cache[1]
        try:
            st = autostart.status()
        except Exception:
            st = {}
        self._autostart_cache = (now, st or {})
        return self._autostart_cache[1]

    def autostart_installed(self) -> bool:
        return bool(self.autostart_state().get("installed"))

    def report_task_installed(self) -> bool:
        return bool(self.autostart_state().get("report_task"))

    def invalidate_autostart(self):
        self._autostart_cache = None
        self.refresh(force=True)

    # -------------------------------------------------- 后台任务
    def run_bg(self, fn, done=None):
        self._busy += 1
        self._update_pill()

        def worker():
            try:
                res = ("ok", fn())
            except Exception as exc:
                traceback.print_exc()
                res = ("err", f"{type(exc).__name__}: {exc}")
            self._q.put((done, res))

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        try:
            while True:
                done, (kind, payload) = self._q.get_nowait()
                self._busy = max(0, self._busy - 1)
                if callable(done):
                    try:
                        done(kind, payload)
                    except Exception:
                        traceback.print_exc()
                self._update_pill()
        except queue.Empty:
            pass
        if self.tray is not None:
            try:
                self.tray.poll()          # 托盘线程收到的事件在这里回到主线程
            except Exception:
                traceback.print_exc()
        self.after(120, self._drain)

    def toast(self, text: str, level: str = "info"):
        color = {"info": "#111827", "warn": "#92400e", "ok": "#065f46"}.get(level, "#111827")
        self._toast_lbl.configure(text=text, bg=color)
        self._toast_lbl.place(relx=0.5, rely=1.0, anchor="s", y=-px(22))
        self._toast_lbl.lift()
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(4200, self._toast_lbl.place_forget)

    # -------------------------------------------------- 动作
    def start_daemon(self):
        def work():
            import time

            ok, msg = control.start()
            if ok:
                time.sleep(2.2)          # 让守护进程先落一次心跳
            return ok, msg

        def done(kind, payload):
            if kind != "ok":
                self.toast(t("Start failed: {p}", p=payload), "warn")
                return
            ok, msg = payload
            self.toast(msg, "ok" if ok else "info")
            self.refresh(force=True)

        self.run_bg(work, done)

    def stop_daemon(self):
        def done(kind, payload):
            if kind != "ok":
                self.toast(t("Stop failed: {p}", p=payload), "warn")
                return
            ok, msg = payload
            self.toast(msg, "ok" if ok else "warn")
            self.refresh(force=True)

        self.run_bg(control.stop, done)

    def pause(self, minutes: int):
        ok, msg = control.pause(minutes)
        self.toast(msg, "ok" if ok else "warn")
        self.refresh(force=True)

    def resume(self):
        ok, msg = control.resume()
        self.toast(msg)
        self.refresh(force=True)

    def enable_autostart(self):
        try:
            msg = autostart.install(cfgmod.load(reload=True))
        except Exception as exc:
            self.toast(t("Registration failed: {e}", e=exc), "warn")
            return
        self.toast(msg, "ok")
        self.invalidate_autostart()

    def generate(self, day: str | None = None, force: bool = True):
        day = day or (self.snap.get("workday") if self.snap else None) or \
            timeutil.workday_of(timeutil.now(), cfgmod.load().cutoff_hour)
        self.toast(t("Generating the report for {day}…", day=day))

        def work():
            import report as report_mod

            cfg = cfgmod.load(reload=True)
            log = self.log
            if log is None:
                import logging_setup

                log = logging_setup.setup("gui", console=False)
            return report_mod.generate(day, cfg, log, reason="gui", force=force, open_browser=False)

        def done(kind, payload):
            if kind != "ok":
                self.toast(t("Generation failed: {p}", p=payload), "warn")
                return
            res = payload or {}
            st = res.get("status")
            if st == "ok":
                self.toast(t("Report for {day} is ready", day=day))
            elif st == "degraded":
                self.toast(res.get("degraded_reason") or t("Local timeline version generated"), "warn")
            elif st == "nodata":
                self.toast(t("{day} has no usable records yet; let it run for a while", day=day))
            elif st == "busy":
                self.toast(t("Another report is being generated; give it a few seconds"))
            elif st == "empty":
                self.toast(t("{day} had too little activity; treated as an empty day", day=day))
            elif st == "skipped":
                self.toast(t("A newer version exists; no rerun needed"))
            elif st == "awaiting_review":
                self.toast(t("Content prepared for review; nothing is sent until you confirm on the Reports page"), "info")
            else:
                self.toast(t("Finished: {st}", st=res.get("status") or res.get("reason") or t("Unknown state")), "warn")
            self.refresh(force=True)
            self.refresh_reports()
            if st in ("ok", "degraded", "empty", "awaiting_review"):
                self.show("reports")
                self.pages["reports"].select(day)

        self.run_bg(work, done)

    def approve_review(self, day: str):
        """按用户确认过的内容真正外发：发送的必须就是刚才预览的那一份。"""
        self.toast(t("Generating the report for {day} from the confirmed content…", day=day))

        def work():
            import report as report_mod

            cfg = cfgmod.load(reload=True)
            log = self.log
            if log is None:
                import logging_setup

                log = logging_setup.setup("gui", console=False)
            return report_mod.generate(day, cfg, log, reason="review", force=True,
                                       open_browser=False, approved=True)

        def done(kind, payload):
            if kind != "ok":
                self.toast(t("Send failed: {p}", p=payload), "warn")
                return
            res = payload or {}
            st = res.get("status")
            if st == "ok":
                self.toast(t("Report for {day} is ready", day=day))
            elif st == "degraded":
                self.toast(res.get("degraded_reason") or t("Local timeline version generated"), "warn")
            elif st == "awaiting_review":
                self.toast(t("Still in review mode; please confirm again"), "warn")
            else:
                self.toast(t("Finished: {st}", st=st or t("Unknown state")), "warn")
            self.refresh(force=True)
            self.refresh_reports()
            self.pages["reports"].select(day)

        self.run_bg(work, done)

    def forget_day(self, day: str):
        """整日遗忘：删原始与派生数据。调用方必须已经做过二次确认。"""
        def work():
            import forget as forget_mod
            import logging_setup

            cfg = cfgmod.load(reload=True)
            log = logging_setup.setup("gui", console=False)
            return forget_mod.forget_day(day, cfg, log)

        def done(kind, payload):
            if kind != "ok":
                self.toast(t("Delete failed: {p}", p=payload), "warn")
                return
            res = payload or {}
            removed = len(res.get("derived") or [])
            self.toast(t("Forgot {day} ({n} items deleted); content already sent cannot be recalled",
  day=day, n=removed), "ok")
            self.refresh(force=True)
            self.refresh_reports()
            self.pages["reports"].show_placeholder(t("Records for {day} deleted", day=day), day=day)

        self.run_bg(work, done)

    def open_board(self, day: str | None = None):
        day = day or (self.snap.get("workday") if self.snap else None)
        target = paths.web_day(day) if day else None
        if not (target and target.exists()):
            target = paths.WEB / "index.html"
        if not target.exists():
            self.toast(t("No dashboard yet. Hit \"Generate report now\" first."), "warn")
            return
        open_uri(str(target))

    def open_report(self, day: str):
        self.show("reports")
        page = self.pages["reports"]
        page.select(day)
        if day not in page._rows:
            self.toast(t("No report for {day} yet", day=day))

    # -------------------------------------------------- 生命周期
    def _reveal(self):
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:
            pass
        if self.shot_path:
            if self.shot_page:
                self.show(self.shot_page)
            if self.shot_day:
                self.pages["reports"].select(self.shot_day)
            self.after(900, lambda: self._shot_when_idle(self.shot_path))
            return
        self._setup_tray()
        self.after(900, self._maybe_start_capture)

    # -------------------------------------------------- 托盘
    def _setup_tray(self):
        if self.tray is not None:
            return
        try:
            import tray as traymod

            icon = traymod.Tray(
                self,
                paths.BUNDLE / "assets" / "app.ico",
                tip=t("What-IDid · Work Log"),
                on_command=self._tray_command,
                on_activate=self._restore_window,
            )
            if icon.install():
                self.tray = icon
                icon.set_menu(self._tray_menu())
                self._update_tray_tip()
            else:
                self.log and self.log.warning(t("Tray icon failed to install: %s"), icon.last_error)
        except Exception:
            traceback.print_exc()

    def _tray_menu(self):
        snap = self.snap or {}
        running = bool(snap.get("running"))
        paused = bool(snap.get("paused"))
        if running:
            collect = t("Pause capture for 30 minutes")
        elif paused:
            collect = t("Resume capture")
        else:
            collect = t("Start capture")
        return [
            (t("Open main window"), CMD_SHOW, True),
            ("-", 0, False),
            (collect, CMD_COLLECT, True),
            (t("Generate today's report now"), CMD_REPORT, bool(snap.get("has_key"))),
            (t("Diagnostics & cleanup"), CMD_DIAG, True),
            ("-", 0, False),
            (t("Quit (stops capture)"), CMD_QUIT, True),
        ]

    def _tray_command(self, cmd: int):
        if cmd == CMD_SHOW:
            self._restore_window()
        elif cmd == CMD_COLLECT:
            self._toggle_collect()
        elif cmd == CMD_REPORT:
            self.generate(None, force=True)
        elif cmd == CMD_DIAG:
            self._restore_window()
            self.show("diag")
        elif cmd == CMD_QUIT:
            self._quit()

    def _toggle_collect(self):
        snap = self.snap or {}
        if snap.get("running"):
            ok, msg = control.pause(30)
        elif snap.get("paused"):
            ok, msg = control.resume()
        else:
            self._restore_window()
            self.start_daemon()
            return
        self.toast(msg, "ok" if ok else "warn")
        self.refresh(force=True)

    def _update_tray_tip(self):
        if self.tray is None:
            return
        snap = self.snap or {}
        if not snap:
            return
        self.tray.set_menu(self._tray_menu())
        if snap.get("running"):
            tip = t("What-IDid · Recording")
        elif snap.get("paused"):
            tip = t("What-IDid · Paused")
        else:
            tip = t("What-IDid · Capture stopped")
        self.tray.set_tip(tip)

    def _restore_window(self):
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except Exception:
            pass
        try:
            self.refresh(force=True)
        except Exception:
            pass

    def _maybe_start_capture(self):
        """带着 Key 打开界面时顺手把采集拉起来——否则「关掉窗口继续记录」是句空话。"""
        if self._autostarted:
            return
        self._autostarted = True
        snap = self.snap or {}
        if not snap or snap.get("running") or snap.get("paused"):
            return
        if not snap.get("has_key"):
            return
        ok, _msg = control.start()
        if ok:
            self.toast(t("Capture started in the background; closing the window keeps it running"))
            self.refresh(force=True)

    def _shot_when_idle(self, path: str, tries: int = 60):
        """等后台任务都收尾再截图，否则抓到的是「处理中…」。"""
        if self._busy and tries > 0:
            self.after(250, lambda: self._shot_when_idle(path, tries - 1))
            return
        self.after(400, lambda: self._self_shot(path))

    def _self_shot(self, path: str):
        """开发自检用：显式导出本窗口的屏幕像素；只应对隔离的合成数据使用。

        必须先把窗口置顶再抓：否则用户正在用的窗口会盖在上面，抓到的是别人的界面。
        """
        try:
            self.attributes("-topmost", True)
            self.lift()
            self.update_idletasks()
            self.update()
            self.after(180, lambda: self._grab_self(path))
        except Exception:
            traceback.print_exc()
            self.destroy()

    def _grab_self(self, path: str):
        try:
            self.update_idletasks()
            self.update()
            from PIL import ImageGrab
            import capture
            import winapi

            cfg = cfgmod.load()
            fg = winapi.foreground_info()
            reason = privacy.capture_reason(cfg, fg, 0, winapi.is_locked(), force=True)
            if (reason or privacy.should_skip_ocr(fg.get("app", ""), cfg)
                    or fg.get("pid") != os.getpid()):
                raise privacy.PrivacyError(t("The privacy policy or the foreground window does not allow exporting a screenshot"))
            self.pages["settings"].key_entry.configure(show="•")
            self.update_idletasks()

            x, y = self.winfo_rootx(), self.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            im = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            latest = cfgmod.load()
            if (privacy.capture_reason(latest, fg, 0, winapi.is_locked(), force=True)
                    or not capture.same_foreground(fg, winapi.foreground_info())
                    or cfg.get("privacy", {}) != latest.get("privacy", {})):
                raise privacy.PrivacyError(t("The window or privacy policy changed during capture; no image was exported"))
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            im.save(path)
            print(f"[shot] {path} {im.size}")
        except Exception:
            traceback.print_exc()
        finally:
            self.destroy()

    def _on_close(self):
        """点 × 只是收起界面，采集与托盘图标都留着。"""
        if self.tray is not None:
            self.withdraw()
            if not self._tray_hint:
                self._tray_hint = True
                self.tray.notify(t("What-IDid is still recording in the background"),
                                 t("Right-click the tray icon to reopen or quit (look under ^ in the taskbar)"))
            return
        self._quit()

    def _quit(self):
        snap = self.snap or {}
        running = bool(snap.get("running"))
        if running:
            hidden = self.state() == "withdrawn"
            if hidden:
                self.deiconify()
            ok = messagebox.askyesno(
                t("Quit What-IDid"),
                t("Quitting stops capture. Everything recorded so far is kept, and it carries on next time.\n\nIf you only want to hide the window and keep recording, choose \"No\"."),
                parent=self)
            if not ok:
                if hidden:
                    self.withdraw()
                return
        try:
            control.stop()
        except Exception:
            pass
        if self.tray is not None:
            try:
                self.tray.remove()
            except Exception:
                pass
            self.tray = None
        self._release_lock()
        self.destroy()

    def relaunch(self):
        """换语言后整窗重建。

        不另起进程：新进程会和本进程抢 gui.lock，结果是新窗口一闪就退、
        旧窗口也跟着关掉。改由 main() 的循环重建一次，数据与托盘都留在进程内。
        """
        self.relaunch_requested = True
        if self.tray is not None:
            try:
                self.tray.remove()
            except Exception:
                pass
            self.tray = None
        self._release_lock()
        self.destroy()

    def _release_lock(self):
        if not self._lock_handle:
            return
        try:
            import msvcrt

            self._lock_handle.seek(0)
            msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        self._lock_handle = None


def _single_instance():
    """已有窗口就把它提到前台并退出，避免双击多次开一堆。"""
    try:
        paths.ensure_dirs()
        handle = open(paths.STATE / "gui.lock", "a+")
    except OSError:
        return open(os.devnull, "w")
    try:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        pass
    except Exception:
        return handle

    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return None


def _apply_locale() -> str:
    """按配置切界面语言。

    窗口标题也要跟着变，而且它参与 `FindWindowW` 的单实例查找，所以必须在这里
    一起改，不能只改控件文案。
    """
    global TITLE
    try:
        code = cfgmod.load(reload=True).get("ui.language", i18n.DEFAULT_LOCALE)
    except Exception:
        code = i18n.DEFAULT_LOCALE
    i18n.set_locale(code)
    TITLE = t("What-IDid · Work Log")
    return i18n.current()


def main(shot_path: str | None = None, shot_page: str | None = None,
         shot_day: str | None = None) -> int:
    import sys

    if sys.stdout is None:                      # 窗口化 exe 里 stdout 是 None
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    try:
        import winapi

        winapi.set_dpi_aware()
    except Exception:
        pass

    # 界面文案的语言：默认英文，配置里 ui.language 可以是 zh-CN / auto
    _apply_locale()
    while True:
        handle = _single_instance()
        if handle is None:
            print(t("Already running; switched to the foreground"))
            return 0

        app = App(shot_path=shot_path, shot_page=shot_page, shot_day=shot_day)
        app._lock_handle = handle
        try:
            app.mainloop()
        finally:
            try:
                if app.tray is not None:
                    app.tray.remove()
            except Exception:
                pass
            try:
                if app._lock_handle:
                    app._lock_handle.close()
            except Exception:
                pass
        if not app.relaunch_requested:
            return 0
        _apply_locale()          # 语言变了：重新读配置，再开一轮窗口


if __name__ == "__main__":
    raise SystemExit(main())
