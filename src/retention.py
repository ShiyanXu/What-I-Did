"""滚动清理原始数据与报告 OCR 摘录；不删除叙述、外部文件或同步副本。"""
from __future__ import annotations

import json
import shutil
import stat
import time
from pathlib import Path

import paths
import privacy
import timeutil
from locking import SingleInstance


def _dir_size(p: Path) -> int:
    """遇到链接/联接点拒绝清理，不能沿它们统计或删除外部目录。"""
    total = 0
    if not p.exists() and not p.is_symlink():
        return total
    pending = [p]
    while pending:
        f = pending.pop()
        st = f.lstat()
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
            raise OSError("数据目录含链接或联接点，已拒绝自动清理")
        if stat.S_ISDIR(st.st_mode):
            pending.extend(f.iterdir())
        elif stat.S_ISREG(st.st_mode):
            total += st.st_size
    return total


def report_excerpt_days(cfg) -> int:
    value = cfg.get("retention.report_excerpt_days")
    return int(cfg.get("retention.events_days", 30) if value is None else value)


def keep_report_excerpts(day: str, cfg, now=None) -> bool:
    days = report_excerpt_days(cfg)
    today = timeutil.workday_of(now or timeutil.now(), cfg.cutoff_hour)
    age = (timeutil.parse_day(today) - timeutil.parse_day(day)).days
    return days > 0 and age <= days


def clean(cfg, log=None, dry_run: bool = False) -> dict:
    cfg = privacy.current_config(cfg)
    privacy.require_valid(cfg)
    # 清理与生成共用锁，避免 JSON/HTML 被两个进程交叉覆盖。
    with SingleInstance("WhatIDid.Report") as lock:
        if not lock.acquired:
            return {"status": "busy", "dry_run": dry_run,
                    "reason": "日报生成或另一清理正在运行，本次未修改文件"}
        return _clean_locked(cfg, log, dry_run)


def _clean_locked(cfg, log, dry_run: bool) -> dict:
    shots_days = int(cfg.get("retention.shots_days", 7))
    events_days = int(cfg.get("retention.events_days", 30))
    max_gb = float(cfg.get("retention.max_total_gb", 20))
    today = timeutil.workday_of(timeutil.now(), cfg.cutoff_hour)
    today_dt = timeutil.parse_day(today)

    summary = {"status": "ok", "removed_shots": 0, "removed_days": [], "freed_mb": 0.0,
               "tmp_removed": 0, "rerendered": 0, "excerpt_reports": [], "excerpt_blocks": 0,
               "errors": [], "dry_run": dry_run}
    touched: set[str] = set()
    removed: set[Path] = set()
    initial_size = _dir_size(paths.DATA)

    def remove_dir(p: Path) -> bool:
        if p in removed or not p.exists():
            return False
        try:
            size = _dir_size(p)
            if not dry_run:
                shutil.rmtree(p)
        except OSError as exc:
            summary["errors"].append(f"{p.name}: {type(exc).__name__}，目录未完整清理")
            return False
        removed.add(p)
        summary["freed_mb"] += size / 1024 ** 2
        return True

    day_dirs = sorted([p for p in paths.DATA.glob("2*") if p.is_dir()], key=lambda p: p.name)
    for d in day_dirs:
        try:
            age = (today_dt - timeutil.parse_day(d.name)).days
        except Exception:
            continue
        if age > events_days:
            if remove_dir(d):
                summary["removed_days"].append(d.name)
                touched.add(d.name)
            continue
        if age > shots_days:
            for sub in ("shots", "thumbs"):
                p = d / sub
                if p.exists():
                    n = len(list(p.glob("*")))
                    if remove_dir(p):
                        summary["removed_shots"] += n
                        touched.add(d.name)

    # 总量封顶：超了就按最旧优先删截图
    total_gb = max(0, initial_size / 1024 ** 3 - summary["freed_mb"] / 1024)
    if total_gb > max_gb:
        for d in day_dirs:
            if total_gb <= max_gb:
                break
            if d in removed:
                continue
            for sub in ("shots", "thumbs"):
                p = d / sub
                if p not in removed and p.exists():
                    n = len(list(p.glob("*")))
                    before = summary["freed_mb"]
                    if remove_dir(p):
                        total_gb -= (summary["freed_mb"] - before) / 1024
                        summary["removed_shots"] += n
                        touched.add(d.name)

    # 即使原始 data/日期 已不存在，报告里的 OCR 副本也要独立过期。
    _prune_report_excerpts(cfg, summary, touched, dry_run, log)
    if touched and not dry_run:
        summary["rerendered"] += _rerender(touched, log, summary["errors"])

    # 临时文件
    for pattern in ("*.tmp", "*.png", "umi_*.txt"):
        for f in paths.OCR_TMP.glob(pattern):
            try:
                if time.time() - f.stat().st_mtime > 3600:
                    if not dry_run:
                        f.unlink(missing_ok=True)
                    summary["tmp_removed"] += 1
            except OSError as exc:
                summary["errors"].append(f"OCR 临时文件清理失败（{type(exc).__name__}）")
    for f in paths.STATE.glob("*.tmp"):
        try:
            if time.time() - f.stat().st_mtime > 3600:
                if not dry_run:
                    f.unlink(missing_ok=True)
                summary["tmp_removed"] += 1
        except OSError:
            continue

    summary["total_gb"] = round(max(0, total_gb) if dry_run else _dir_size(paths.DATA) / 1024 ** 3, 2)
    summary["freed_mb"] = round(summary["freed_mb"], 1)
    if summary["errors"]:
        summary["status"] = "partial"
    if log:
        log.info("%s：截图 %d 个，释放 %.1f MB，数据 %.2f GB，清除 %d 份报告的 OCR 摘录，错误 %d 项",
                 "清理预览" if dry_run else "清理结束", summary["removed_shots"], summary["freed_mb"],
                 summary["total_gb"], len(summary["excerpt_reports"]), len(summary["errors"]))
    return summary


def _prune_report_excerpts(cfg, summary: dict, touched: set[str], dry_run: bool, log) -> None:
    for src in sorted(paths.REPORTS.glob("????-??-??.json")):
        name = src.stem
        try:
            if keep_report_excerpts(name, cfg):
                continue
            if src.is_symlink() or getattr(src.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("报告是链接，拒绝改写")
            payload = json.loads(src.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("day") != name:
                raise ValueError("报告日期与文件名不符")
            changed = 0
            for block in payload.get("blocks", []):
                if block.get("text"):
                    block["text"] = ""
                    changed += 1
            if changed:
                summary["excerpt_reports"].append(name)
                summary["excerpt_blocks"] += changed
            if dry_run:
                continue
            import render

            if changed:
                payload.setdefault("meta", {})["excerpts_cleared_at"] = timeutil.iso(timeutil.now())
                render.write_text_atomic(src, json.dumps(payload, ensure_ascii=False, indent=2))
            # 上次可能在 JSON 更新后、HTML 更新前中断；即便正文已空也要重渲。
            render.render_day(payload)
            summary["rerendered"] += 1
            touched.discard(name)
        except Exception as exc:
            summary["errors"].append(f"{name}: 摘录清理/页面刷新失败（{type(exc).__name__}），需重试")
            if log:
                log.warning("%s 摘录清理未完整完成（%s）", name, type(exc).__name__)


def _rerender(day_names, log=None, errors=None) -> int:
    """截图被清理后重渲受影响日报，避免页面指向已删除的文件。"""
    done = 0
    for name in sorted(set(day_names)):
        src = paths.report_json(name)
        if not src.exists():
            continue
        try:
            import render

            payload = json.loads(src.read_text(encoding="utf-8"))
            if payload.get("day") != name:
                raise ValueError("报告日期与文件名不符")
            render.render_day(payload)
            done += 1
        except Exception as exc:
            if errors is not None:
                errors.append(f"{name}: 页面刷新失败（{type(exc).__name__}）")
            if log:
                log.warning("重渲 %s 失败（%s）", name, type(exc).__name__)
    if done and log:
        log.info("已重渲 %d 天日报（截图已过期，缩略图位置留空）", done)
    return done


def maybe_clean(cfg, log, state_file: Path) -> None:
    """每天在 clean_hour 之后跑一次，用状态文件记录，避免重复。"""
    cfg = privacy.current_config(cfg)
    if getattr(cfg, "error", ""):
        return
    hour = int(cfg.get("retention.clean_hour", 4))
    now = timeutil.now()
    today = now.strftime("%Y-%m-%d")
    if now.hour < hour:
        return
    try:
        if state_file.exists() and state_file.read_text(encoding="utf-8").strip() == today:
            return
    except OSError:
        pass
    result = clean(cfg, log)
    if result.get("status") != "ok":
        return  # 忙或部分失败不标记完成，留待下一轮重试。
    try:
        state_file.write_text(today, encoding="utf-8")
    except OSError:
        pass
