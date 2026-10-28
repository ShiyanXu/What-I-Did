"""图形界面（tkinter，标准库，零新增依赖）。

设计取向：
- 只负责"看 + 管"，采集与出报仍由后台进程完成，关掉窗口不影响记录。
- 所有慢操作（出报、自检、OCR 测试、启停进程）都丢到后台线程，主线程只碰控件。
- 状态每 3 秒轮询一次，与 CLI 共用 status / control / doctor 三个模块，结论永远一致。
"""
from __future__ import annotations

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

VERSION = "1.0"
TITLE = "What-IDid · 工作记录"

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
CAT_LABEL = {"dev": "开发", "doc": "文档", "meet": "会议", "comm": "沟通", "web": "浏览", "other": "其他"}

# 行为动词（与 src/behavior.py 的 VERBS 同源，这里只做展示用）
VERB_LABEL = {k: v["label"] for k, v in behavior_mod.VERBS.items()}
VERB_COLOR = {k: v["color"] for k, v in behavior_mod.VERBS.items()}

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
        return "0 分"
    if m < 60:
        return f"{m} 分"
    return f"{m // 60} 小时 {m % 60} 分" if m % 60 else f"{m // 60} 小时"


def short_date(day: str) -> str:
    return day[5:].replace("-", "/") if len(day) == 10 else day


def pretty_date(day: str) -> str:
    try:
        d = timeutil.parse_day(day)
    except Exception:
        return day
    return f"{d.month} 月 {d.day} 日"


def weekday_cn(day: str) -> str:
    try:
        return "周" + "一二三四五六日"[timeutil.parse_day(day).weekday()]
    except Exception:
        return ""


def next_run_label(iso: str) -> str:
    """把 ISO 时间说成「今天 21:00 / 明天 21:00」。"""
    if not iso:
        return ""
    try:
        dt = timeutil.parse(iso)
    except Exception:
        return ""
    today = timeutil.now().date()
    delta = (dt.date() - today).days
    prefix = "今天" if delta == 0 else ("明天" if delta == 1 else f"{dt.month}/{dt.day}")
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
            "tip": f"{s}–{e}  {CAT_LABEL.get(b.get('category', ''), '')}  {b.get('title', '')}",
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
        self.c_state = StatCard(stats, "采集状态", "检查中", "正在读取心跳")
        self.c_active = StatCard(stats, "今日有效活动", "—", "21:00 出报后可见")
        self.c_data = StatCard(stats, "今日记录", "—", "—")
        self.c_disk = StatCard(stats, "磁盘余量", "—")
        for i, c in enumerate((self.c_state, self.c_active, self.c_data, self.c_disk)):
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(10), 0))

        # 控制条
        self.ctrl = Card(wrap, "采集控制", "关掉这个窗口不会停止记录")
        self.ctrl.pack(fill="x", pady=(px(12), 0))
        bar = tk.Frame(self.ctrl.body, bg=CARD)
        bar.pack(fill="x")
        self.btn_start = FlatButton(bar, "开始采集", lambda: self.app.start_daemon(), "primary")
        self.btn_stop = FlatButton(bar, "停止采集", lambda: self.app.stop_daemon(), "ghost")
        self.btn_pause = FlatButton(bar, "暂停 30 分钟", lambda: self.app.pause(30), "soft")
        self.btn_resume = FlatButton(bar, "恢复采集", lambda: self.app.resume(), "soft")
        for b in (self.btn_start, self.btn_stop, self.btn_pause, self.btn_resume):
            b.pack(side="left", padx=(0, px(8)))
        FlatButton(bar, "打开完整看板", lambda: self.app.open_board(), "ghost").pack(side="right")
        self.btn_gen = FlatButton(bar, "立即生成日报", lambda: self.app.generate(), "primary")
        self.btn_gen.pack(side="right", padx=(0, px(8)))

        self.ctrl_hint = tk.Label(self.ctrl.body, text="", bg=CARD, fg=SUBTLE, font=(FONT, 8), justify="left")
        self.ctrl_hint.pack(anchor="w", pady=(px(10), 0))

        # 今日行为时间轴
        self.tl = Card(wrap, "今日行为时间轴", "色带按行为上色，鼠标移上去看这段在做什么")
        self.tl.pack(fill="x", pady=(px(12), 0))
        self.tl_now = tk.Label(self.tl.body, text="", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.tl_now.pack(fill="x", pady=(0, px(6)))
        self.strip = tk.Canvas(self.tl.body, bg=CARD, height=px(56), highlightthickness=0, bd=0)
        self.strip.pack(fill="x")
        self.strip.bind("<Configure>", lambda e: self._draw_strip())
        self.strip.bind("<Motion>", self._hover_strip)
        self._segs: list[dict] = []
        self._beh_sig = None
        self._empty_hint = "今天还没有活动块：日报在 21:00 生成，之后这里会显示完整时间线"
        self.legend = tk.Frame(self.tl.body, bg=CARD)
        self.legend.pack(fill="x", pady=(px(8), 0))
        self.blist = tk.Frame(self.tl.body, bg=CARD)
        self.blist.pack(fill="x", pady=(px(4), 0))

        # 最近两周
        self.wk = Card(wrap, "最近 14 天", "点击柱子查看当天日报")
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
            tail = "· 行为标签来自模型推测" if source == "model" else "· 行为标签是本地规则推的，出报后会换成模型判断"
            self.tl_now.configure(
                text=f"{len(items)} 段行为 · 覆盖 {fmt_dur(total)}　{tail}", fg=SUBTLE)

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
            tk.Label(box, text=VERB_LABEL.get(v, v), bg=CARD, fg=MUTED,
                     font=(FONT, 8)).pack(side="left")

        for w in self.blist.winfo_children():
            w.destroy()
        shown = items[:12]
        for it in shown:
            row = tk.Frame(self.blist, bg=CARD)
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text=f"{it.get('start', '')}–{it.get('end', '')}", bg=CARD, fg=SUBTLE,
                     font=(MONO, 8), width=12, anchor="w").pack(side="left")
            tk.Label(row, text=f"{it.get('minutes', 0)} 分", bg=CARD, fg=SUBTLE,
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
            tk.Label(self.blist, text=f"…另外 {len(items) - len(shown)} 段，出报后可在「日报」页看全",
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
            self.c_state.set_value("内容处理已停止", snap["privacy_error"], BAD_C)
        elif pause:
            until = pause.get("until") or ""
            tail = f"，{timeutil.parse(until):%H:%M} 自动恢复" if until else "（手动恢复）"
            self.c_state.set_value("已暂停", f"暂停于 {(pause.get('paused_at') or '')[11:16]}{tail}", WARN_C)
        elif hb["alive"]:
            app_name = (hb["raw"].get("app") or "").replace(".exe", "")
            self.c_state.set_value("运行中", f"{hb['age']} 秒前心跳 · {app_name or '空闲'}", OK_C)
        else:
            self.c_state.set_value("未运行", "现在没有在记录，点「开始采集」", BAD_C)

        tr = snap["today_report"]
        if tr["exists"]:
            self.c_active.set_value(fmt_dur(tr["active_minutes"]),
                                 f"{tr['block_count']} 个活动块 · 今日日报已生成")
        else:
            self.c_active.set_value("—", "21:00 自动出报，也可点右侧按钮")

        self.c_data.set_value(f"{snap['shots']} 张",
                           f"{snap['events']:,} 条事件 · {snap['day_size_mb']:.0f} MB")

        disk = snap["disk_free_gb"]
        self.c_disk.set_value(f"{disk:.0f} GB" if disk is not None else "—",
                           f"{paths.ROOT.drive} 盘 · 数据在 {paths.ROOT.name}",
                           BAD_C if (disk is not None and disk < 5) else TEXT)

        # 按钮可用性
        self.btn_start.set_enabled(not hb["alive"])
        self.btn_stop.set_enabled(hb["alive"])
        self.btn_pause.set_enabled(hb["alive"] and not pause)
        self.btn_resume.set_enabled(bool(pause))

        pending = hb["raw"].get("pending_ocr") or 0
        self.ctrl_hint.configure(
            text=(f"数据目录 {paths.ROOT}    ·    采样间隔 {snap['interval_sec']} 秒"
                  f"    ·    OCR 队列 {pending}    ·    今日 OCR 成功 {hb['raw'].get('ocr_ok', 0)} / 失败 {hb['raw'].get('ocr_failed', 0)}"))

        self._render_notices(snap)
        self._refresh_timeline(snap)
        self._refresh_weeks(snap)

    def _refresh_timeline(self, snap: dict):
        day = snap["workday"]
        nxt = next_run_label((snap.get("report") or {}).get("next_at", ""))
        if nxt:
            self.tl.sub_lbl.configure(text=f"色带按行为上色　·　下次自动出报 {nxt}")
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
            self.set_behaviors([], [], "今天还没有活动块：开始采集后这里会实时显示行为轴，"
                                      "21:00 出报后会换成模型对每段时间的行为判断")

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
            items.append((BAD_C, "配置无效，内容采集与外发已停止", snap["privacy_error"],
                          "打开配置", lambda: open_uri(str(paths.CONFIG_PATH))))
        elif not snap.get("allow_llm", True):
            items.append((MUTED, "仅本地模式", "已禁止模型请求，日报使用本地时间线",
                          "设置", lambda: self.app.show("settings")))
        sched = snap.get("report") or {}
        review = snap.get("review") or {}
        if review and not review.get("error"):
            items.append((WARN_C, "有内容等你确认才外发",
                          f"目标 {review.get('model') or '模型'}，约 {review.get('chars', 0)} 字；"
                          "确认前不会调用模型",
                          "去确认",
                          lambda d=review.get("day"): (self.app.open_report(d) if d
                                                       else self.app.show("reports"))))
        if sched.get("overdue"):
            day = sched.get("overdue_day") or ""
            why = sched.get("overdue_why") or "到点没跑成"
            items.append((BAD_C, f"{day[5:]} 的日报还没生成",
                          f"现在是「{why}」，点右边立刻补一版", "立即生成",
                          lambda d=day: self.app.generate(d or None, force=True)))
        if not snap["has_key"] and snap.get("allow_llm", True) and not snap.get("privacy_error"):
            items.append((WARN_C, "还没配置大模型 Key", "日报会降级成时间线版，丢掉“在改哪个文件”这类细节",
                          "去设置", lambda: self.app.show("settings")))
        if not snap["heartbeat"]["alive"] and not snap["paused"]:
            items.append((BAD_C, "采集没有在运行", "现在的工作不会被记录",
                          "启动采集", lambda: self.app.start_daemon()))
        if not self.app.autostart_installed():
            items.append((MUTED, "没有注册开机自启", "重启电脑后不会自动开始记录",
                          "立即注册", lambda: self.app.enable_autostart()))
        elif not self.app.report_task_installed():
            items.append((MUTED, "没有注册出报定时任务",
                          "采集停了或电脑 21:00 关着的时候，日报不会自动补",
                          "立即注册", lambda: self.app.enable_autostart()))
        if snap["today_report"]["exists"] and snap["today_report"]["status"] == "degraded":
            items.append((MUTED, "今日日报是降级版本", "原因：模型未配置或调用失败，页面只有时间线",
                          "查看", lambda: self.app.show("reports")))
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
        tk.Label(head, text="日报归档", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
        self.count_lbl = tk.Label(head, text="0 份", bg=CARD, fg=SUBTLE, font=(FONT, 8))
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
        self.btn_md = FlatButton(btns, "复制 Markdown", self._copy_md, "soft", padx=10, pady=5, font_size=9)
        self.btn_md.pack(side="right", padx=(px(6), 0))
        FlatButton(btns, "完整看板", self._open_board, "ghost", padx=10, pady=5, font_size=9).pack(side="right", padx=(px(6), 0))
        self.btn_regen = FlatButton(btns, "重新生成", self._regen, "ghost", padx=10, pady=5, font_size=9)
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
        self.count_lbl.configure(text=f"{len(reports)} 份")
        if not reports:
            tk.Label(self.list_sf.body, text="还没有日报\n今晚 21:00 会自动生成第一份",
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
        d2 = tk.Label(line1, text=r.get("weekday", ""), bg=CARD, fg=SUBTLE, font=(FONT, 8))
        d2.pack(side="left", padx=(px(5), 0))
        d3 = tk.Label(line1, text=fmt_dur(r.get("active_minutes", 0)) if r.get("active_minutes") else "",
                      bg=CARD, fg=SUBTLE, font=(FONT, 8))
        d3.pack(side="right")
        head_text = (r.get("headline") or "（无标题）").strip()
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
    def show_placeholder(self, msg: str = "左侧选一份日报\n或点「立即生成日报」把今天的数据变成文字",
                         day: str = ""):
        self.date_lbl.configure(text=pretty_date(day) if day else "还没有日报")
        self.wd_lbl.configure(text=weekday_cn(day) if day else "")
        self.headline.configure(text=msg)
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
                msg = "这份日报的 JSON 文件丢了，只剩 Markdown\n可以点「重新生成」按当天数据重建"
            else:
                msg = ("这天还没有日报\n"
                       "晚上 21:00 会自动生成，也可以点「重新生成」立刻整理")
            self.show_placeholder(msg, day=day)
            return
        rep = d.get("report") or {}
        self.date_lbl.configure(text=pretty_date(day))
        self.wd_lbl.configure(text=d.get("weekday", ""))
        self.headline.configure(text=rep.get("headline") or "（模型没有给出标题）")

        for w in self.badge_box.winfo_children():
            w.destroy()
        st = d.get("status", "")
        if st == "ok":
            self._badge("模型生成", OK_C, "#ecfdf5")
        elif st == "degraded":
            self._badge("降级时间线版", WARN_C, "#fffbeb")
        elif st == "empty":
            self._badge("当日无数据", MUTED, "#f1f3f7")
        if review:
            self._badge("有更新的待审内容", WARN_C, "#fffbeb")
        conf = rep.get("confidence")
        if conf:
            self._badge(f"置信度 {conf}", MUTED, "#f1f3f7")
        rev = d.get("revision", 1)
        if rev and rev > 1:
            self._badge(f"第 {rev} 版", MUTED, "#f1f3f7")
        self._day_ops(day, bool(review))

        for w in self.chips.winfo_children():
            w.destroy()
        stats = d.get("stats") or {}
        cov = d.get("coverage") or {}
        self._chip(f"覆盖 {cov.get('start', '?')}–{cov.get('until', '?')}")
        self._chip(f"有效 {fmt_dur(stats.get('active_minutes', 0))}")
        self._chip(f"{stats.get('block_count', 0)} 个活动块")
        self._chip(f"{stats.get('shot_count', 0)} 张截图")
        if d.get("generated_at"):
            self._chip(f"生成于 {d['generated_at'][11:16]}")

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
        FlatButton(ops, "补录", lambda: self._add_manual(day), "ghost",
                   padx=8, pady=2, font_size=8).pack(side="right", padx=(px(6), 0))
        FlatButton(ops, "遗忘这天", lambda: self._forget(day), "ghost",
                   padx=8, pady=2, font_size=8).pack(side="right")
        if has_review:
            FlatButton(ops, "审核待发内容",
                       lambda: self._render_review(day, status.pending_review(day)),
                       "primary", padx=8, pady=2, font_size=8).pack(side="right", padx=(px(6), 0))

    def _render_review(self, day: str, info: dict):
        """展示"将要发送给模型的原文"，确认才外发。"""
        self.date_lbl.configure(text=pretty_date(day))
        self.wd_lbl.configure(text=weekday_cn(day))
        self.headline.configure(text="这一天还没出报：有一份待审核的模型输入，确认后才会发送")

        for w in self.badge_box.winfo_children():
            w.destroy()
        self._badge("待审核", WARN_C, "#fffbeb")
        if info.get("model"):
            self._badge(str(info["model"]), MUTED, "#f1f3f7")
        ops = tk.Frame(self.badge_box, bg=CARD)
        ops.pack(side="right")
        FlatButton(ops, "丢弃", lambda: self._discard_review(day), "ghost",
                   padx=8, pady=2, font_size=8).pack(side="right", padx=(px(6), 0))
        FlatButton(ops, "审核并发送", lambda: self.app.approve_review(day), "primary",
                   padx=10, pady=2, font_size=8).pack(side="right")

        for w in self.chips.winfo_children():
            w.destroy()
        if info.get("coverage"):
            self._chip(f"覆盖 {info['coverage']}")
        if info.get("chars"):
            self._chip(f"将要发送 {info['chars']} 字")
        if info.get("generated_at"):
            self._chip(f"生成于 {info['generated_at'][11:16]}")

        self._segs = []
        self._draw_strip()

        payload = ""
        try:
            data = json.loads(Path(info["path"]).read_text(encoding="utf-8"))
            payload = str((data or {}).get("payload") or "")
        except (OSError, ValueError, TypeError):
            payload = ""

        self._enable()
        self.text.insert("end", "下面是将要发给模型的原文（发送前已按隐私规则脱敏）。"
                               "确认无误再发送；不确认就不会外发。\n\n", ("p", "warn"))
        if payload:
            self.text.insert("end", payload[:6000], ("p",))
            if len(payload) > 6000:
                self.text.insert("end", "\n…（预览已截断，完整内容见预览文件）", ("muted",))
        else:
            self.text.insert("end", "预览文件读不出来，可以丢弃后重新生成。", ("muted",))
        self._disable()
        self.text.yview_moveto(0)

    def _write_behaviors(self, items: list[dict]):
        for it in items:
            verb = it.get("verb") or "admin"
            self.text.insert("end", f"{it.get('start', '')}–{it.get('end', '')}", ("time",))
            self.text.insert("end", f"  {it.get('minutes', 0)} 分  ", ("dur",))
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
            self.text.insert("end", "这份日报是降级版本：模型未配置或调用失败，只保留了本地统计与时间线。\n",
                             ("p", "warn"))
            first = False
        elif d.get("status") == "empty":
            self.text.insert("end", "当天有效活动太少，没有生成正文。下面这些统计与缺口说明就是全部内容。\n",
                             ("p", "muted"))
            first = False

        if rep.get("opening_md"):
            self._write_md(rep["opening_md"])
            self.text.insert("end", "\n", ("p",))

        if rep.get("summary_md"):
            self._section("过程", first)
            first = False
            self._write_md(rep["summary_md"])

        beh = rep.get("behaviors") or []
        if beh:
            self._section("行为时间轴", first)
            first = False
            self._write_behaviors(beh)

        offline = rep.get("offline") or []
        if offline:
            self._section("人工补录", first)
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

        hi = rep.get("highlights") or []
        if hi:
            self._section("关键进展", first)
            first = False
            for it in hi:
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(it.get("what", ""), "bullet")
                self.text.insert("end", "\n", ("bullet",))
                self._write_sub("证据", it.get("evidence", ""))
                self._write_sub("影响", it.get("impact", ""))

        acc = rep.get("accomplishments") or []
        if acc and not hi:
            self._section("成果", first)
            first = False
            for a in acc:
                if isinstance(a, str):
                    a = {"what": a}
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(a.get("what", ""), "bullet")
                extra = []
                if a.get("status"):
                    extra.append(str(a["status"]))
                art = a.get("artifact")
                if isinstance(art, dict) and art.get("label"):
                    extra.append(str(art["label"]))
                elif isinstance(art, str) and art:
                    extra.append(art)
                if extra:
                    self.text.insert("end", "  （" + " · ".join(extra) + "）", ("bullet", "muted"))
                self.text.insert("end", "\n", ("bullet",))

        fr = rep.get("friction") or []
        if fr:
            self._section("卡点与代价", first)
            first = False
            for it in fr:
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(it.get("what", ""), "bullet")
                self.text.insert("end", "\n", ("bullet",))
                for label, key in (("原因", "cause"), ("代价", "cost"), ("处理", "fix")):
                    self._write_sub(label, it.get(key, ""))

        ins = rep.get("insight") or []
        if ins:
            self._section("洞察", first)
            first = False
            for it in ins:
                self.text.insert("end", "•  ", ("bullet",))
                self._write_inline(it.get("observation", ""), "bullet")
                self.text.insert("end", "\n", ("bullet",))
                for label, key in (("依据", "evidence"), ("下次", "action")):
                    self._write_sub(label, it.get(key, ""))

        for key, title in (("in_progress", "进行中"),
                           ("blockers", "卡住的地方"),
                           ("decisions", "做的决定")):
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

        tl = rep.get("timeline") or []
        if tl:
            self._section("时间线", first)
            first = False
            for it in tl:
                if not isinstance(it, dict):
                    continue
                start = str(it.get("start", ""))[11:16] or str(it.get("start", ""))
                end = str(it.get("end", ""))[11:16] or str(it.get("end", ""))
                self.text.insert("end", f"{start}–{end}  ", ("bullet", "muted"))
                self._write_inline(f"{it.get('title', '')}", "bullet")
                self.text.insert("end", f"　{CAT_LABEL.get(it.get('category', ''), '')}\n", ("bullet", "muted"))

        refs = d.get("refs") or []
        if refs:
            self._section("相关文档", first)
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

        gaps = d.get("gaps") or []
        if gaps:
            self._section("数据缺口", first)
            first = False
            for g in gaps:
                self.text.insert("end", f"•  {g.get('start', '')} – {g.get('end', '')}　{g.get('minutes', '')} 分钟没有记录\n",
                                 ("bullet", "muted"))

        meta = d.get("meta") or {}
        if meta.get("model") or meta.get("calls"):
            self._section("生成信息", first)
            parts = [f"模型 {meta.get('model', '?')}", f"调用 {meta.get('calls', 0)} 次"]
            if meta.get("prompt_tokens"):
                parts.append(f"输入 {meta['prompt_tokens']} tokens")
            if meta.get("completion_tokens"):
                parts.append(f"输出 {meta['completion_tokens']} tokens")
            if meta.get("elapsed_ms"):
                parts.append(f"耗时 {meta['elapsed_ms'] / 1000:.1f} 秒")
            if meta.get("issues"):
                parts.append(f"校验提示 {len(meta['issues'])} 条")
            self.text.insert("end", "　·　".join(parts) + "\n", ("p", "muted"))

        self._disable()

    def _open(self, uri: str):
        if not open_uri(uri):
            self.app.toast(f"打不开：{uri}", "warn")

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
            self.app.toast("这份日报没有 Markdown 文件", "warn")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.app.toast(f"已复制 {self.selected} 的 Markdown 全文（{len(text)} 字）")

    # -------------------------------------------------- 补录 / 遗忘 / 审核
    def _add_manual(self, day: str):
        """补一条屏幕看不到的活动：会议、外出、思考。"""
        from tkinter import simpledialog

        text = simpledialog.askstring(
            "补录线下活动",
            f"给 {day} 补一条屏幕看不到的活动。\n"
            "格式：开始-结束 名称\n例如：10:00-11:00 线下客户沟通",
            parent=self)
        if not text:
            return
        m = re.match(r"\s*(\d{1,2}:\d{2})\s*[-–—~至]\s*(\d{1,2}:\d{2})\s+(.+)$", text)
        if not m:
            self.app.toast("格式不对，应该像 10:00-11:00 线下客户沟通", "warn")
            return
        import manual as manual_mod

        try:
            manual_mod.add_offline(day, m.group(1), m.group(2), m.group(3))
        except ValueError as exc:
            self.app.toast(str(exc), "warn")
            return
        self.app.toast("已补录；点「重新生成」会套用到日报")

    def _forget(self, day: str):
        ok = messagebox.askyesno(
            "遗忘这一天",
            f"将永久删除 {day} 的截图、事件、OCR、日报、分享版与周汇总，无法恢复。\n\n"
            "已经发给模型服务的内容无法撤回。继续？",
            icon="warning", default="no", parent=self)
        if not ok:
            return
        self.app.forget_day(day)

    def _discard_review(self, day: str):
        import report as report_mod

        report_mod.discard_review(day)
        self.app.toast("已丢弃待审内容，什么都没外发")
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
        self._build_llm()
        self._build_capture()
        self._build_ocr()
        self._build_report()
        self._build_privacy()
        self._build_startup()

        # 底部保存条
        bar = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        bar.pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=px(22), pady=px(10))
        FlatButton(inner, "保存设置", self._save, "primary", padx=18).pack(side="left")
        FlatButton(inner, "打开配置文件", lambda: open_uri(str(paths.CONFIG_PATH)), "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(inner, "重新读取", self.reload_form, "ghost").pack(side="left", padx=(px(8), 0))
        self.save_hint = tk.Label(inner, text="改动保存后，采集进程会自动重启一次以生效",
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
    def _build_llm(self):
        card, g = self._card("大模型", "兼容 OpenAI 接口的服务都能用")
        r = 0
        self._row(g, r, "接口地址", self._entry(g, "llm.base_url"), "如 https://api.deepseek.com/v1"); r += 1
        self._row(g, r, "模型名", self._entry(g, "llm.model", width=24), "如 deepseek-chat / qwen-plus"); r += 1
        key_box = tk.Frame(g, bg=CARD)
        self.key_entry = self._entry(key_box, "llm.key", width=38, show="•")
        self.key_entry.pack(side="left", ipady=px(4))
        self.key_toggle = FlatButton(key_box, "显示", self._toggle_key, "soft", padx=10, pady=4, font_size=8)
        self.key_toggle.pack(side="left", padx=(px(6), 0))
        FlatButton(key_box, "测试连接", self._test_llm, "ghost", padx=10, pady=4, font_size=8).pack(side="left", padx=(px(6), 0))
        self._row(g, r, "API Key", key_box, "留空则用环境变量 WID_LLM_API_KEY"); r += 1
        box = tk.Frame(card.body, bg=CARD)
        box.pack(fill="x", pady=(px(6), 0))
        self.llm_hint = tk.Label(box, text="", bg=CARD, fg=SUBTLE, font=(FONT, 8), justify="left")
        self.llm_hint.pack(anchor="w")

    def _toggle_key(self):
        hidden = str(self.key_entry.cget("show")) == "•"
        self.key_entry.configure(show="" if hidden else "•")
        self.key_toggle.set_text("隐藏" if hidden else "显示")

    def _build_capture(self):
        card, g = self._card("采集", "改完保存，采集进程会自动重启")
        r = 0
        self._row(g, r, "采样间隔（秒）", self._spin(g, "capture.interval_sec", 5, 300),
                  "越小越细，但耗电与占空间更多"); r += 1
        self._row(g, r, "空闲多少分钟后暂停", self._spin(g, "capture.idle_pause_sec_min", 1, 120),
                  "没有键鼠动作就停止截图，避免记录空转"); r += 1
        self._row(g, r, "同窗口最小截图间隔（秒）", self._spin(g, "capture.min_save_gap_sec", 10, 600)); r += 1
        self._row(g, r, "JPEG 质量", self._spin(g, "capture.jpeg_quality", 30, 95),
                  "62 左右在清晰度和体积间比较平衡"); r += 1
        self._row(g, r, "保存截图证据", self._check(g, "privacy.store_screenshots", "保存（关闭后只记录窗口标题，日报会变粗）")); r += 1

    def _build_ocr(self):
        card, g = self._card("文字识别", "OCR 全在本机完成，截图不出本机")
        r = 0
        self._row(g, r, "引擎", self._combo(g, "ocr.engine",
                                            ["auto", "rapidocr", "umi_http", "umi_cli", "none"]),
                  "auto 优先 Umi，没有就用内置 RapidOCR；none 关闭识别"); r += 1
        self._row(g, r, "每天最多识别多少张", self._spin(g, "ocr.max_per_day", 0, 5000, width=8),
                  "控制模型输入量与耗时"); r += 1
        self._row(g, r, "只识别活动窗口", self._check(g, "ocr.crop_active_window",
                                                      "推荐开启：整屏缩小后字迹会糊")); r += 1

    def _build_report(self):
        card, g = self._card("日报与保留")
        r = 0
        self._row(g, r, "每天几点生成", self._spin(g, "report.trigger_hour", 0, 23),
                  "默认 21:00，次日 04:05 再出终版"); r += 1
        self._row(g, r, "有效活动低于多少分钟算空日", self._spin(g, "report.min_active_minutes", 0, 600)); r += 1
        self._row(g, r, "生成后自动打开网页", self._check(g, "report.open_browser", "终版出报后打开浏览器看板")); r += 1
        self._row(g, r, "发送前先给我看", self._check(g, "report.review_mode", "生成待审内容，确认后才外发"),
                  "开启后到点只准备内容、不调用模型；需要在日报页点「审核并发送」"); r += 1
        self._row(g, r, "截图保留天数", self._spin(g, "retention.shots_days", 1, 365),
                  "过期只删截图，日报永久保留"); r += 1
        self._row(g, r, "事件与 OCR 保留天数", self._spin(g, "retention.events_days", 1, 365)); r += 1
        self._row(g, r, "报告 OCR 摘录保留天数", self._entry(g, "retention.report_excerpt_days", width=8),
              "留空跟随事件期限；0 不保留。仅清除附录摘录，不删除日报叙述"); r += 1
        self._row(g, r, "数据总量上限（GB）", self._spin(g, "retention.max_total_gb", 1, 500),
                  "超了按最旧优先删截图"); r += 1

    def _build_privacy(self):
        card, g = self._card("隐私", "暂停/完全屏蔽只留匿名状态；正则脱敏不能识别所有敏感信息")
        r = 0
        self._row(g, r, "截图范围", self._combo(g, "privacy.capture_scope", ["active_window", "all_screens"]),
                  "默认活动窗口；全屏会包含后台窗口和副屏，请谨慎选择"); r += 1
        self._row(g, r, "允许模型外发", self._check(g, "privacy.allow_llm", "允许发送脱敏后的标题与 OCR 摘要"),
                  "关闭后仅生成本地时间线，连接测试也不发送请求"); r += 1
        self._row(g, r, "不记录的应用", self._text(g, "privacy.blocked_apps", 3),
                  "一行一个完整进程名，如 1Password.exe；不区分大小写"); r += 1
        self._row(g, r, "仅记录应用及时长", self._text(g, "privacy.skip_ocr_apps", 3),
                  "不存标题、不截图、不 OCR；旧记录重新生成时也会过滤"); r += 1
        self._row(g, r, "标题关键词屏蔽", self._text(g, "privacy.blocked_title_patterns", 2),
                  "每行一个正则；无效正则会拒绝保存，不会静默忽略"); r += 1

    def _build_startup(self):
        card, g = self._card("启动", "")
        r = 0
        box = tk.Frame(g, bg=CARD)
        self.start_var = tk.BooleanVar()
        cb = tk.Checkbutton(box, text="开机登录后自动开始记录", variable=self.start_var,
                            bg=CARD, fg=BODY, activebackground=CARD, selectcolor=CARD,
                            bd=0, highlightthickness=0, font=(FONT, 9), cursor="hand2",
                            command=self._toggle_autostart)
        cb.pack(side="left")
        self._row(g, r, "开机自启", box, "免管理员权限，写入用户级计划任务或注册表 Run"); r += 1
        dir_lbl = tk.Label(g, text=str(paths.ROOT), bg=CARD, fg=MUTED, font=(FONT, 9), cursor="hand2")
        dir_lbl.bind("<Button-1>", lambda e: open_uri(str(paths.ROOT)))
        self._row(g, r, "数据目录", dir_lbl, "点击打开文件夹")

    # -------------------------------------------------- 读写
    def reload_form(self):
        cfg = cfgmod.load(reload=True)
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
            text=(cfg.error or ("已禁止模型外发；连接测试也已停用" if not cfg.get("privacy.allow_llm", True) else
                  f"当前：{cfg.get('llm.model', '')} @ {cfg.get('llm.base_url', '')}　"
                  + ("Key 已配置" if cfg.has_api_key() else "还没有 Key，日报会降级"))),
            fg=BAD_C if cfg.error else SUBTLE)
        if cfg.error:
            self.save_hint.configure(text="配置无效：请打开配置文件修复；不会用占位默认值覆盖原文件", fg=BAD_C)

    def _num(self, key: str, lo: int, hi: int, label: str) -> int:
        raw = str(self.vars[key].get()).strip()
        try:
            value = int(float(raw))
        except Exception:
            raise ValueError(f"{label} 需要填数字，现在是「{raw}」")
        if value < lo or value > hi:
            raise ValueError(f"{label} 应在 {lo}–{hi} 之间")
        return value

    def _lines(self, key: str) -> list[str]:
        var = self.vars[key]
        text = var.get("1.0", "end") if isinstance(var, tk.Text) else str(var.get())
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _save(self):
        try:
            updates = {
                "llm.base_url": str(self.vars["llm.base_url"].get()).strip(),
                "llm.model": str(self.vars["llm.model"].get()).strip(),
                "capture.interval_sec": self._num("capture.interval_sec", 5, 300, "采样间隔"),
                "capture.idle_pause_sec": self._num("capture.idle_pause_sec_min", 1, 120, "空闲暂停") * 60,
                "capture.min_save_gap_sec": self._num("capture.min_save_gap_sec", 10, 600, "最小截图间隔"),
                "capture.jpeg_quality": self._num("capture.jpeg_quality", 30, 95, "JPEG 质量"),
                "privacy.store_screenshots": bool(self.vars["privacy.store_screenshots"].get()),
                "privacy.capture_scope": str(self.vars["privacy.capture_scope"].get()),
                "privacy.allow_llm": bool(self.vars["privacy.allow_llm"].get()),
                "ocr.engine": str(self.vars["ocr.engine"].get()),
                "ocr.max_per_day": self._num("ocr.max_per_day", 0, 5000, "每天识别上限"),
                "ocr.crop_active_window": bool(self.vars["ocr.crop_active_window"].get()),
                "report.trigger_hour": self._num("report.trigger_hour", 0, 23, "生成时间"),
                "report.min_active_minutes": self._num("report.min_active_minutes", 0, 600, "空日阈值"),
                "report.open_browser": bool(self.vars["report.open_browser"].get()),
                "report.review_mode": bool(self.vars["report.review_mode"].get()),
                "retention.shots_days": self._num("retention.shots_days", 1, 365, "截图保留天数"),
                "retention.events_days": self._num("retention.events_days", 1, 365, "事件保留天数"),
                "retention.max_total_gb": self._num("retention.max_total_gb", 1, 500, "总量上限"),
                "privacy.blocked_apps": self._lines("privacy.blocked_apps"),
                "privacy.skip_ocr_apps": self._lines("privacy.skip_ocr_apps"),
                "privacy.blocked_title_patterns": self._lines("privacy.blocked_title_patterns"),
            }
            if not updates["llm.base_url"] or not updates["llm.model"]:
                raise ValueError("接口地址和模型名不能为空")
            excerpt_days = str(self.vars["retention.report_excerpt_days"].get()).strip()
            if excerpt_days and (not excerpt_days.isdecimal() or int(excerpt_days) > 36500):
                raise ValueError("报告摘录天数应为 0–36500 的整数；留空跟随事件期限")
            updates["retention.report_excerpt_days"] = int(excerpt_days) if excerpt_days else None
        except ValueError as exc:
            self.app.toast(str(exc), "warn")
            return

        try:
            cfgmod.save_values(updates)
            cfgmod.save_api_key(str(self.vars["llm.key"].get()))
        except (OSError, ValueError, privacy.PrivacyError) as exc:
            self.save_hint.configure(text=f"保存未完成：{exc}", fg=BAD_C)
            self.app.toast("保存未完成，请检查配置及文件权限；未重启采集", "warn")
            return
        self.save_hint.configure(text="已保存 · 正在重启采集以应用新设置…", fg=OK_C)
        self.app.toast("设置已保存")

        hb = status.heartbeat()
        if hb["alive"]:
            self.app.run_bg(control.restart, self._after_restart)
        else:
            self.save_hint.configure(text="已保存 · 采集当前没在运行，下次启动即生效", fg=SUBTLE)
        self.reload_form()

    def _after_restart(self, kind: str, payload):
        if kind == "ok":
            ok, msg = payload
            self.save_hint.configure(text=f"已保存 · {msg}", fg=OK_C if ok else WARN_C)
        else:
            self.save_hint.configure(text=f"已保存，但重启采集失败：{payload}", fg=WARN_C)

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
            self.app.toast(f"自启设置失败：{exc}", "warn")
            self.start_var.set(not want)

    def _test_llm(self):
        import llm

        saved_cfg = cfgmod.load(reload=True)
        if saved_cfg.error:
            self.app.toast(saved_cfg.error, "warn")
            return
        if not saved_cfg.get("privacy.allow_llm", True) or not self.vars["privacy.allow_llm"].get():
            self.app.toast("已禁止模型外发；如需测试，请先明确允许外发并保存设置", "warn")
            return
        base = str(self.vars["llm.base_url"].get()).strip()
        model = str(self.vars["llm.model"].get()).strip()
        key = str(self.vars["llm.key"].get()).strip()
        if not base or not model:
            self.app.toast("先填接口地址和模型名", "warn")
            return
        if (base.rstrip("/") != str(saved_cfg.get("llm.base_url", "")).rstrip("/")
                or model != saved_cfg.get("llm.model")):
            self.app.toast("接口或模型已修改，请先保存设置，再测试连接", "warn")
            return
        if not key:
            self.app.toast("先填 API Key", "warn")
            return
        self.llm_hint.configure(text="正在连接…", fg=MUTED)

        def work():
            client = llm.LLMClient(saved_cfg, self.app.log)
            client.api_key = key
            client.timeout = 25
            client.max_retries = 1
            client.chat_json("你是连通性测试助手。", '请只输出 JSON：{"ok": true}', max_tokens=20)
            return model

        def done(kind, payload):
            if kind == "ok":
                self.llm_hint.configure(text=f"连接正常 · {payload}", fg=OK_C)
                self.app.toast("模型连接正常")
            else:
                self.llm_hint.configure(text=f"连接失败 · {payload}", fg=BAD_C)
                self.app.toast("模型连接失败，看下方红字", "warn")

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
        FlatButton(bar, "重新自检", self.run_doctor, "primary").pack(side="left")
        FlatButton(bar, "OCR 实测", self.run_ocr, "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(bar, "打开日志目录", lambda: open_uri(str(paths.LOGS)), "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(bar, "打开数据目录", lambda: open_uri(str(paths.ROOT)), "ghost").pack(side="left", padx=(px(8), 0))
        FlatButton(bar, "清理预览", self.run_clean, "ghost").pack(side="left", padx=(px(8), 0))
        self.summary = tk.Label(bar, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.summary.pack(side="right")

        card = Card(wrap, pad=0)
        card.pack(fill="both", expand=True, pady=(px(12), 0))
        self.sf = ScrollFrame(card.body, CARD)
        self.sf.pack(fill="both", expand=True, padx=px(14), pady=px(12))
        self.hint = None
        self._set_hint("点「重新自检」检查环境")

    def _set_hint(self, text: str):
        self.hint = tk.Label(self.sf.body, text=text, bg=CARD, fg=SUBTLE, font=(FONT, 9),
                             justify="left", anchor="w", wraplength=px(700))
        self.hint.pack(anchor="w")

    def run_doctor(self):
        self._clear()
        self._set_hint("正在自检…")
        self.app.run_bg(lambda: doctor.run(cfgmod.load(reload=True)), self._show)

    def _clear(self):
        for w in self.sf.body.winfo_children():
            w.destroy()
        self.hint = None

    def _show(self, kind: str, payload):
        self._clear()
        if kind != "ok":
            tk.Label(self.sf.body, text=f"自检失败：{payload}", bg=CARD, fg=BAD_C, font=(FONT, 9)).pack(anchor="w")
            return
        rows = payload
        s = doctor.summary(rows)
        self.summary.configure(text=f"{s['ok']} 正常 / {s['warn']} 提醒 / {s['fail']} 失败")
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
        self._set_hint("正在截屏并识别（首次调用要加载模型，约 5-15 秒）…")

        def work():
            import ocr

            return ocr.recognize_screen(cfgmod.load(reload=True))

        self.app.run_bg(work, self._show_ocr)

    def _show_ocr(self, kind: str, payload):
        self._clear()
        if kind != "ok":
            tk.Label(self.sf.body, text=f"识别失败：{payload}", bg=CARD, fg=BAD_C, font=(FONT, 9)).pack(anchor="w")
            return
        res = payload
        head = f"引擎 {res.get('engine') or '—'} · 状态 {res.get('status')} · 耗时 {res.get('ms')} ms · {len(res.get('text') or '')} 字"
        tk.Label(self.sf.body, text=head, bg=CARD, fg=TEXT, font=(FONT, 9, "bold")).pack(anchor="w")
        if res.get("error"):
            tk.Label(self.sf.body, text=res["error"], bg=CARD, fg=BAD_C, font=(FONT, 8)).pack(anchor="w", pady=(px(4), 0))
        body = tk.Text(self.sf.body, height=14, bg="#fbfcfe", fg=BODY, bd=1, relief="flat",
                       font=(MONO, 9), wrap="word", highlightbackground=LINE, highlightthickness=1)
        body.pack(fill="both", expand=True, pady=(px(8), 0))
        body.insert("1.0", (res.get("text") or "（没有识别到文字）")[:3000])
        body.configure(state="disabled")

    def run_clean(self):
        self._clear()
        self._set_hint("正在统计可清理的数据（不会真的删除）…")

        def work():
            import retention

            return retention.clean(cfgmod.load(reload=True), self.app.log, dry_run=True)

        def done(kind, payload):
            self._clear()
            if kind != "ok":
                tk.Label(self.sf.body, text=f"清理预览失败：{payload}", bg=CARD, fg=BAD_C, font=(FONT, 9)).pack(anchor="w")
                return
            s = payload or {}
            if s.get("status") == "busy":
                self._set_hint(s.get("reason", "已有报告任务运行，请稍后预览"), WARN_C)
                return
            lines = [
                f"可删除截图 {s.get('removed_shots', 0)} 张",
                f"可释放空间 {s.get('freed_mb', 0):.0f} MB",
                f"整日目录过期 {len(s.get('removed_days') or [])} 天"
                + (f"（{', '.join(s.get('removed_days') or [])[:120]}）" if s.get("removed_days") else ""),
                f"可清除 {len(s.get('excerpt_reports') or [])} 份报告中的 {s.get('excerpt_blocks', 0)} 条 OCR 摘录",
                "",
                "这只是预览；不会删除文件。自动清理由采集进程在 clean_hour 后执行（默认 04:00 后）。",
                "只清除本地报告附录的 OCR 摘录；叙述、标题、外部文件与 Obsidian 副本不在此范围。",
                *(s.get("errors") or []),
            ]
            for line in lines:
                tk.Label(self.sf.body, text=line, bg=CARD, fg=BODY, font=(FONT, 9),
                         justify="left", anchor="w").pack(anchor="w", pady=px(1))

        self.app.run_bg(work, done)


# ---------------------------------------------------------------- 主窗口
NAV = [("dash", "概览", "dash"), ("reports", "日报", "report"),
       ("settings", "设置", "gear"), ("diag", "诊断", "pulse")]


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
        tk.Label(box, text="自动工作记录", bg=SIDEBAR, fg="#7c8698", font=(FONT, 8)).pack(anchor="w")

        self.nav_items = {}
        for key, label, icon in NAV:
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
        self.pill_lbl = tk.Label(self.pill, text="检查中", bg="#ffffff", fg=MUTED, font=(FONT, 8))
        self.pill_lbl.pack(side="left", padx=(0, px(10)))
        FlatButton(pill, "刷新", lambda: self.refresh(force=True), "ghost", padx=10, pady=5, font_size=9).pack(side="right", padx=(0, px(8)))

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
            "dash": ("概览", "今天在做什么，一眼看完"),
            "reports": ("日报", "每天的记录、成果与可回溯的证据"),
            "settings": ("设置", "采集、识别、模型与隐私"),
            "diag": ("诊断", "出问题时先看这里"),
        }
        t, s = titles.get(key, ("", ""))
        self.title_lbl.configure(text=t)
        self.sub_lbl.configure(text=s)
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
            self.toast("今日日报已生成，切到「日报」就能看", "ok")
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
            text, color = "处理中…", ACCENT
        elif self.snap.get("privacy_error"):
            text, color = "配置错误 · 已停止内容处理", BAD_C
        elif pause:
            text, color = "已暂停", WARN_C
        elif hb["alive"]:
            text, color = f"记录中 · {hb['age']}s", OK_C
        else:
            text, color = "未运行", BAD_C
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
                self.toast(f"启动失败：{payload}", "warn")
                return
            ok, msg = payload
            self.toast(msg, "ok" if ok else "info")
            self.refresh(force=True)

        self.run_bg(work, done)

    def stop_daemon(self):
        def done(kind, payload):
            if kind != "ok":
                self.toast(f"停止失败：{payload}", "warn")
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
            self.toast(f"注册失败：{exc}", "warn")
            return
        self.toast(msg, "ok")
        self.invalidate_autostart()

    def generate(self, day: str | None = None, force: bool = True):
        day = day or (self.snap.get("workday") if self.snap else None) or \
            timeutil.workday_of(timeutil.now(), cfgmod.load().cutoff_hour)
        self.toast(f"正在生成 {day} 的日报…")

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
                self.toast(f"生成失败：{payload}", "warn")
                return
            res = payload or {}
            st = res.get("status")
            if st == "ok":
                self.toast(f"{day} 日报已生成")
            elif st == "degraded":
                self.toast(res.get("degraded_reason") or "已生成本地时间线版", "warn")
            elif st == "nodata":
                self.toast(f"{day} 没有可用记录，先让它跑一会儿")
            elif st == "busy":
                self.toast("另一份日报正在生成，稍等几秒")
            elif st == "empty":
                self.toast(f"{day} 有效活动太少，判为空日")
            elif st == "skipped":
                self.toast("已有新版本，无需重跑")
            elif st == "awaiting_review":
                self.toast("已生成待审内容，到「日报」页确认后才会外发", "info")
            else:
                self.toast(f"生成结束：{res.get('status') or res.get('reason') or '未知状态'}", "warn")
            self.refresh(force=True)
            self.refresh_reports()
            if st in ("ok", "degraded", "empty", "awaiting_review"):
                self.show("reports")
                self.pages["reports"].select(day)

        self.run_bg(work, done)

    def approve_review(self, day: str):
        """按用户确认过的内容真正外发：发送的必须就是刚才预览的那一份。"""
        self.toast(f"正在按确认的内容生成 {day} 的日报…")

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
                self.toast(f"发送失败：{payload}", "warn")
                return
            res = payload or {}
            st = res.get("status")
            if st == "ok":
                self.toast(f"{day} 日报已生成")
            elif st == "degraded":
                self.toast(res.get("degraded_reason") or "已生成本地时间线版", "warn")
            elif st == "awaiting_review":
                self.toast("仍处于审核模式，请重新确认", "warn")
            else:
                self.toast(f"生成结束：{st or '未知状态'}", "warn")
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
                self.toast(f"删除失败：{payload}", "warn")
                return
            res = payload or {}
            removed = len(res.get("derived") or [])
            self.toast(f"已遗忘 {day}（删除 {removed} 项），已经外发的内容无法撤回", "ok")
            self.refresh(force=True)
            self.refresh_reports()
            self.pages["reports"].show_placeholder(f"{day} 的记录已删除", day=day)

        self.run_bg(work, done)

    def open_board(self, day: str | None = None):
        day = day or (self.snap.get("workday") if self.snap else None)
        target = paths.web_day(day) if day else None
        if not (target and target.exists()):
            target = paths.WEB / "index.html"
        if not target.exists():
            self.toast("还没有生成看板，先点「立即生成日报」", "warn")
            return
        open_uri(str(target))

    def open_report(self, day: str):
        self.show("reports")
        page = self.pages["reports"]
        page.select(day)
        if day not in page._rows:
            self.toast(f"{day} 还没有日报")

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

            t = traymod.Tray(
                self,
                paths.BUNDLE / "assets" / "app.ico",
                tip="What-IDid · 工作记录",
                on_command=self._tray_command,
                on_activate=self._restore_window,
            )
            if t.install():
                self.tray = t
                t.set_menu(self._tray_menu())
                self._update_tray_tip()
            else:
                self.log and self.log.warning("托盘图标没装上：%s", t.last_error)
        except Exception:
            traceback.print_exc()

    def _tray_menu(self):
        snap = self.snap or {}
        running = bool(snap.get("running"))
        paused = bool(snap.get("paused"))
        if running:
            collect = "暂停采集 30 分钟"
        elif paused:
            collect = "恢复采集"
        else:
            collect = "开始采集"
        return [
            ("打开主窗口", CMD_SHOW, True),
            ("-", 0, False),
            (collect, CMD_COLLECT, True),
            ("立即生成今日日报", CMD_REPORT, bool(snap.get("has_key"))),
            ("诊断与清理", CMD_DIAG, True),
            ("-", 0, False),
            ("退出（停止采集）", CMD_QUIT, True),
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
            tip = "What-IDid · 正在记录"
        elif snap.get("paused"):
            tip = "What-IDid · 已暂停"
        else:
            tip = "What-IDid · 采集未运行"
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
            self.toast("采集已在后台启动，关掉窗口也会继续记录")
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
                raise privacy.PrivacyError("隐私策略或前台窗口状态不允许导出界面截图")
            self.pages["settings"].key_entry.configure(show="•")
            self.update_idletasks()

            x, y = self.winfo_rootx(), self.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            im = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            latest = cfgmod.load()
            if (privacy.capture_reason(latest, fg, 0, winapi.is_locked(), force=True)
                    or not capture.same_foreground(fg, winapi.foreground_info())
                    or cfg.get("privacy", {}) != latest.get("privacy", {})):
                raise privacy.PrivacyError("截图期间窗口或隐私策略变化，未导出图像")
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
                self.tray.notify("What-IDid 还在后台记录",
                                 "右键托盘图标可以重新打开或退出（没看到就点任务栏的 ^）")
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
                "退出 What-IDid",
                "退出会停止采集。已经记录的内容都会保留，\n"
                "下次打开可以继续。\n\n"
                "如果只想收起界面、让它继续记录，点「否」。",
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

    handle = _single_instance()
    if handle is None:
        print("已经在运行，已切到前台")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
