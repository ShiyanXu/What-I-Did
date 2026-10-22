"""开发用：点击级 GUI 回归（tkinter，用 event_generate 真发按键/点击事件）。

    WID_HOME=E:/Self/Project/What-I-Did/.tmp_wid_gui \
        "C:/.../Python310/python.exe" .build/gui_regression.py

覆盖：导航点击、悬停态、日报行选择、补录（成功/格式错）、遗忘（取消/确认）、
审核视图（丢弃/审核并发送）、刷新按钮、F5、关窗口。

退出码 0 = 全部通过；1 = 有失败。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

HOME = Path(os.environ.get("WID_HOME") or "")
if not HOME.name.startswith(".tmp_wid"):
    raise SystemExit("请先设置 WID_HOME 指向一个 .tmp_wid* 临时目录，避免动到真实数据")

import tkinter.simpledialog as simpledialog  # noqa: E402

import gui  # noqa: E402
import paths  # noqa: E402
import status  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def pump(app, seconds: float = 1.2, until=None) -> bool:
    """跑事件循环到条件成立或超时（代替 mainloop）。"""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        if until and until():
            return True
        time.sleep(0.02)
    app.update()
    return bool(until and until())


def click(widget, app, label: str = "") -> bool:
    """在控件中心真的发一次按下/抬起，顺带过一遍 hover。

    注意：补录/遗忘/审核这类按钮的回调会重建整块界面，按下的那一刻控件就没了，
    所以抬键失败不算点击失败——按下事件已经真的送到控件上并执行了回调。
    """
    ok = True
    try:
        widget.update_idletasks()
        w = max(2, widget.winfo_width())
        h = max(2, widget.winfo_height())
        x, y = w // 2, h // 2
        widget.event_generate("<Enter>", x=x, y=y)
        app.update()
        widget.event_generate("<Button-1>", x=x, y=y)
    except Exception:
        print(f"  !! 按下 {label or widget} 失败")
        traceback.print_exc()
        return False
    try:
        widget.event_generate("<ButtonRelease-1>", x=x, y=y)
    except Exception:
        pass                                   # 控件已经被回调重建掉了，属正常
    try:
        app.update()
        pump(app, 0.25)
    except Exception:
        pass
    return ok


def find_button(parent, text: str):
    """按文字找 FlatButton（它就是个 tk.Label 子类）。"""
    for child in parent.winfo_children():
        try:
            if child.winfo_class() == "Label" and str(child.cget("text")) == text:
                return child
        except Exception:
            pass
        got = find_button(child, text)
        if got is not None:
            return got
    return None


def main() -> int:
    print(f"WID_HOME = {paths.ROOT}")
    day_ok = "2026-10-18"
    day_del = "2026-10-19"

    # 托盘在测试里会留下真实图标，这次只验界面逻辑
    gui.App._setup_tray = lambda self: None

    print("\n[1] 启动与窗口尺寸")
    app = gui.App()
    app.deiconify()
    pump(app, 2.5, until=lambda: bool(app.snap))
    app.deiconify()
    pump(app, 0.8)
    req_w, req_h = app.winfo_width(), app.winfo_height()
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    check("窗口已映射", app.winfo_ismapped(), f"{req_w}x{req_h}")
    check("窗口不超出屏幕", req_w <= sw and req_h <= sh, f"screen {sw}x{sh}")
    check("缩放系数 S 合理", 0.85 <= gui.S <= 3.0, f"S={gui.S:.2f}")
    check("状态快照已加载", bool(app.snap), f"当前页={app.current}")

    print("\n[2] 导航点击")
    for key in ("reports", "settings", "diag", "dash"):
        item = app.nav_items[key]
        ok = click(item["label"], app, f"nav:{key}")
        check(f"点击导航「{key}」切页", ok and app.current == key, f"current={app.current}")
        check(f"导航「{key}」高亮", item["bar"].cget("bg") == gui.ACCENT, item["bar"].cget("bg"))

    print("\n[3] 悬停态")
    item = app.nav_items["reports"]
    item["label"].event_generate("<Enter>")
    app.update()
    hovered = item["label"].cget("bg")
    item["label"].event_generate("<Leave>")
    app.update()
    restored = item["label"].cget("bg")
    check("离开当前页的导航会响应 hover", hovered == gui.SIDEBAR_SEL, f"{hovered} → {restored}")
    check("hover 离开后恢复", restored == gui.SIDEBAR, restored)

    print("\n[4] 日报列表点击")
    app.show("reports")
    pump(app, 1.2)
    page = app.pages["reports"]
    check("列表里有日报行", len(page._rows) >= 2, f"{len(page._rows)} 行")
    if day_del in page._rows:
        ok = click(page._rows[day_del]["widgets"][0], app, "row:2026-10-19")
        check("点击另一天切换选中", ok and page.selected == day_del, f"selected={page.selected}")
        check("详情标题跟着变", gui.pretty_date(day_del) == str(page.date_lbl.cget("text")),
              f"{page.date_lbl.cget('text')}")
    ok = click(page._rows[day_ok]["widgets"][2], app, "row:label")
    check("点击行内子控件也能选中", ok and page.selected == day_ok, f"selected={page.selected}")

    print("\n[5] 补录（点击「补录」→ 输入 → 落盘）")
    manual_path = paths.day_dir(day_ok) / "manual.json"
    before = manual_path.read_text(encoding="utf-8") if manual_path.exists() else ""
    btn = find_button(page.badge_box, "补录")
    check("日报页有「补录」按钮", btn is not None)
    if btn is not None:
        simpledialog.askstring = lambda *a, **k: "10:00-11:00 线下客户沟通"
        click(btn, app, "补录")
        pump(app, 0.8)
        after = manual_path.read_text(encoding="utf-8") if manual_path.exists() else ""
        got = json.loads(after)["offline"] if after else []
        check("补录写入 manual.json", any("线下客户沟通" in (o.get("label") or "") for o in got),
              f"{len(got)} 条")

        simpledialog.askstring = lambda *a, **k: "随便写点没时间的"
        click(find_button(page.badge_box, "补录"), app, "补录(坏格式)")
        pump(app, 0.8)
        after2 = manual_path.read_text(encoding="utf-8") if manual_path.exists() else ""
        got2 = json.loads(after2)["offline"] if after2 else []
        check("格式不对不写入", len(got2) == len(got), f"{len(got2)} 条（期望 {len(got)}）")

        simpledialog.askstring = lambda *a, **k: "25:00-26:00 错时间"
        click(find_button(page.badge_box, "补录"), app, "补录(错时间)")
        pump(app, 0.8)
        after3 = manual_path.read_text(encoding="utf-8") if manual_path.exists() else ""
        got3 = json.loads(after3)["offline"] if after3 else []
        check("越界时间被拦下", len(got3) == len(got), f"{len(got3)} 条")
    simpledialog.askstring = lambda *a, **k: None

    print("\n[6] 遗忘（先取消，再确认）")
    if day_del in page._rows:
        click(page._rows[day_del]["widgets"][0], app, "row:2026-10-19")
    btn = find_button(page.badge_box, "遗忘这天")
    check("日报页有「遗忘这天」按钮", btn is not None)
    if btn is not None:
        gui.messagebox.askyesno = lambda *a, **k: False
        click(btn, app, "遗忘(取消)")
        pump(app, 1.0)
        check("取消时不删数据", (paths.REPORTS / f"{day_del}.json").exists(), "日报还在")

        gui.messagebox.askyesno = lambda *a, **k: True
        click(find_button(page.badge_box, "遗忘这天") or btn, app, "遗忘(确认)")
        # 等后台线程跑完并且结果已经回主线程（_busy 归零），否则 done() 里的
        # show_placeholder 会晚一步落到界面上，把后续断言全带偏
        ok = pump(app, 20.0, until=lambda: app._busy == 0
                  and not (paths.REPORTS / f"{day_del}.json").exists())
        pump(app, 0.8)
        check("确认后整日删除", ok and not (paths.REPORTS / f"{day_del}.json").exists(),
              f"busy={app._busy}")
        check("删除后列表里不再有这天", day_del not in page._rows, f"{list(page._rows)}")

    print("\n[7] 审核视图：看 → 审核并发送 → 再丢弃")
    app.show("reports")
    pump(app, 1.2)
    page = app.pages["reports"]
    info = status.pending_review(day_ok)
    check("存在待审预览", bool(info) and not info.get("error"), str(info.get("path", ""))[:60])
    if info and not info.get("error"):
        if page.selected != day_ok:
            click(page._rows[day_ok]["widgets"][0], app, f"row:{day_ok}")
        page.render(day_ok)
        pump(app, 0.6)
        badges = []

        def _collect(w):
            for c in w.winfo_children():
                try:
                    badges.append(str(c.cget("text")))
                except Exception:
                    pass
                _collect(c)

        _collect(page.badge_box)
        print(f"       徽章区: {badges}")
        btn_rv = find_button(page.badge_box, "审核待发内容")
        check("已出报+有待审时出现入口", btn_rv is not None)
        if btn_rv is not None:
            click(btn_rv, app, "审核待发内容")
            pump(app, 0.8)
            text = page.text.get("1.0", "end")
            check("点击后切到审核视图", "将要发给模型" in text, f"{len(text)} 字")

            send = find_button(page.badge_box, "审核并发送")
            check("审核视图有「审核并发送」", send is not None)
            if send is not None:
                prev_rev = int((status.load_report(day_ok) or {}).get("revision") or 0)
                click(send, app, "审核并发送")
                done = pump(app, 40.0, until=lambda: app._busy == 0
                            and not status.pending_review(day_ok))
                pump(app, 0.8)
                rep = status.load_report(day_ok) or {}
                check("点击「审核并发送」真的发出并出报",
                      done and rep.get("status") in ("ok", "degraded"),
                      f"status={rep.get('status')} rev={prev_rev}→{rep.get('revision')}")
                check("发送后待审内容清空", not status.pending_review(day_ok))

        # 再制造一份待审内容，验证「丢弃」按钮：点了什么都不发
        import report as report_mod

        cfg = gui.cfgmod.load(reload=True)
        report_mod.generate(day_ok, cfg, app.log, reason="regression", force=True,
                            open_browser=False)
        app.refresh(force=True)
        page.render(day_ok)
        pump(app, 0.8)
        check("重新制造出待审预览", bool(status.pending_review(day_ok)))
        btn_rv = find_button(page.badge_box, "审核待发内容")
        if btn_rv is not None:
            click(btn_rv, app, "审核待发内容(第二次)")
            pump(app, 0.6)
        rev_before = int((status.load_report(day_ok) or {}).get("revision") or 0)
        discard = find_button(page.badge_box, "丢弃")
        check("审核视图有「丢弃」", discard is not None)
        if discard is not None:
            click(discard, app, "丢弃")
            pump(app, 3.0, until=lambda: not status.pending_review(day_ok))
            rev_after = int((status.load_report(day_ok) or {}).get("revision") or 0)
            check("丢弃后预览消失", not status.pending_review(day_ok))
            check("丢弃不外发、版本不变", rev_after == rev_before, f"rev {rev_before}→{rev_after}")
            check("丢弃后旧日报还在", (paths.REPORTS / f"{day_ok}.json").exists())

    print("\n[8] 刷新按钮与 F5")
    app.show("dash")
    pump(app, 0.6)
    refresh = find_button(app, "刷新")
    check("顶部有「刷新」按钮", refresh is not None)
    if refresh is not None:
        click(refresh, app, "刷新")
        pump(app, 2.5, until=lambda: bool(app.snap))
        check("点击刷新后仍有快照", bool(app.snap))
    try:
        app.event_generate("<F5>")
        pump(app, 1.5)
        check("F5 刷新不抛异常", True)
    except Exception as exc:
        check("F5 刷新不抛异常", False, str(exc))

    print("\n[9] 收尾：关窗口逻辑")
    running = bool((app.snap or {}).get("running"))
    gui.messagebox.askyesno = lambda *a, **k: True
    app.tray = None
    try:
        app._quit()
        pump(app, 0.5)
        gone = not app.winfo_exists()
    except Exception as exc:
        # destroy() 之后再碰任何控件都会 TclError，这本身就是"确实退出了"
        gone = "destroyed" in str(exc)
    check("无托盘时关窗口真的退出", gone, f"采集运行中={running}")

    ok_n = sum(1 for ok, _, _ in RESULTS if ok)
    bad = [r for r in RESULTS if not r[0]]
    print("\n" + "=" * 62)
    print(f"共 {len(RESULTS)} 项：通过 {ok_n}，失败 {len(bad)}")
    for _, name, detail in bad:
        print(f"  FAIL  {name}  {detail}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
