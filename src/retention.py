"""滚动清理原始数据与报告 OCR 摘录；不删除叙述、外部文件或同步副本。"""
from __future__ import annotations

from i18n import t

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
            raise OSError(t("The data directory contains a link or junction; "
                            "automatic cleanup was refused"))
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
                    "reason": t("Report generation or another cleanup is running; "
                                "no files were changed this time")}
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
            summary["errors"].append(
                t("{name}: {err}, the directory was not fully cleaned",
                  name=p.name, err=type(exc).__name__))
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
                summary["errors"].append(
                    t("Failed to clean an OCR temp file ({err})", err=type(exc).__name__))
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
        log.info("%s: %d shots, %.1f MB freed, %.2f GB total, OCR excerpts cleared from %d "
                 "reports, %d errors",
                 "cleanup preview" if dry_run else "cleanup done", summary["removed_shots"],
                 summary["freed_mb"], summary["total_gb"], len(summary["excerpt_reports"]),
                 len(summary["errors"]))
    return summary


def _prune_report_excerpts(cfg, summary: dict, touched: set[str], dry_run: bool, log) -> None:
    for src in sorted(paths.REPORTS.glob("????-??-??.json")):
        name = src.stem
        try:
            if keep_report_excerpts(name, cfg):
                continue
            if src.is_symlink() or getattr(src.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError(t("The report is a link; refusing to rewrite it"))
            payload = json.loads(src.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("day") != name:
                raise ValueError(t("Report date does not match the file name"))
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
            summary["errors"].append(
                t("{name}: excerpt cleanup / page refresh failed ({err}); retry needed",
                  name=name, err=type(exc).__name__))
            if log:
                log.warning("%s excerpt cleanup did not finish (%s)", name, type(exc).__name__)


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
                raise ValueError(t("Report date does not match the file name"))
            render.render_day(payload)
            done += 1
        except Exception as exc:
            if errors is not None:
                errors.append(t("{name}: page refresh failed ({err})",
                                name=name, err=type(exc).__name__))
            if log:
                log.warning("Failed to re-render %s (%s)", name, type(exc).__name__)
    if done and log:
        log.info("Re-rendered %d daily reports (shots expired, thumbnails left blank)", done)
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
