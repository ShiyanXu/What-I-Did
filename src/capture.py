"""采集器：前台窗口 + 显式截图范围 + 去重 + 落盘 + 提交 OCR。

一次采样做四件事：
1) 判断是否该暂停（PAUSE 文件 / 空闲 / 锁屏 / 黑名单 / 磁盘不足）
2) 默认只抓活动窗口，按 dHash + 网格指纹判断"是否变化"
3) 只有变化才落盘（降采样 JPEG + 缩略图），静止画面只留一行轻量刻度
4) 按策略挑关键帧送本地 OCR（图片不出本机）
"""
from __future__ import annotations

import json
import os
import random
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

import paths
import privacy
import timeutil
import vision
import winapi
from ocr import OcrQueue


def grab_frame(cfg, fg: dict) -> tuple[Image.Image, tuple[int, int]]:
    """范围不可确定时拒绝抓屏，绝不把窗口失败回退成全屏。"""
    privacy.require_valid(cfg)
    winapi.set_dpi_aware()
    vx, vy, vw, vh = winapi.screen_metrics()["virtual"]
    if cfg.get("privacy.capture_scope", "active_window") == "all_screens":
        return ImageGrab.grab(all_screens=True), (vx, vy)
    rect = fg.get("rect") or (0, 0, 0, 0)
    if len(rect) != 4:
        raise privacy.PrivacyError("活动窗口范围无效，已跳过截图")
    left, top = max(vx, rect[0]), max(vy, rect[1])
    right, bottom = min(vx + vw, rect[2]), min(vy + vh, rect[3])
    if right - left < 80 or bottom - top < 60:
        raise privacy.PrivacyError("活动窗口不可见或范围过小，已跳过截图")
    image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    if image.size != (right - left, bottom - top):
        raise privacy.PrivacyError("截图尺寸与窗口范围不一致，已跳过截图")
    return image, (left, top)


def same_foreground(before: dict, after: dict) -> bool:
    return all(before.get(k) == after.get(k) for k in ("hwnd", "pid", "app", "title", "rect"))


def diagnostic_image(cfg) -> tuple[Image.Image, dict]:
    """屏幕 OCR 诊断与正式采集遵守同一策略，仅允许忽略空闲。"""
    cfg = privacy.current_config(cfg)
    fg = winapi.foreground_info()
    reason = privacy.capture_reason(cfg, fg, winapi.idle_seconds(), winapi.is_locked(), force=True)
    if reason or privacy.should_skip_ocr(fg.get("app", ""), cfg):
        raise privacy.PrivacyError(f"当前隐私策略禁止屏幕识别：{reason or 'metadata_only'}")
    image, origin = grab_frame(cfg, fg)
    if cfg.get("ocr.crop_active_window", True):
        image = vision.crop_region(image, tuple(fg.get("rect") or (0, 0, 0, 0)), origin)
        if image is None:
            raise privacy.PrivacyError("活动窗口裁剪失败，未回退到全屏")
    latest = privacy.current_config(cfg)
    if (not same_foreground(fg, winapi.foreground_info())
            or privacy.capture_reason(latest, fg, 0, winapi.is_locked(), force=True)
            or privacy.should_skip_ocr(fg.get("app", ""), latest)
            or latest.get("privacy", {}) != cfg.get("privacy", {})
            or latest.get("ocr", {}) != cfg.get("ocr", {})):
        raise privacy.PrivacyError("识别前窗口或隐私状态已变化，已取消")
    return image, fg


class Collector:
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.interval = float(cfg.get("capture.interval_sec", 20))
        self.jitter = float(cfg.get("capture.jitter_sec", 3))
        self.max_width = int(cfg.get("capture.max_width", 1920))
        self.quality = int(cfg.get("capture.jpeg_quality", 62))
        self.thumb_width = int(cfg.get("capture.thumb_width", 480))
        self.keyframe_sec = float(cfg.get("capture.keyframe_sec", 300))
        self.min_free_gb = float(cfg.get("capture.min_free_gb", 3))
        self.min_save_gap = float(cfg.get("capture.min_save_gap_sec", 90))
        self.min_ocr_gap = float(cfg.get("ocr.min_interval_same_window_sec", 45))
        self.periodic_ocr = float(cfg.get("ocr.periodic_sample_sec", 180))
        winapi.set_dpi_aware()

        self.queue = OcrQueue(cfg, log)
        self._prev_hash: int | None = None
        self._prev_grid: np.ndarray | None = None
        self._prev_sig = ""
        self._last_saved_ts = 0.0
        self._last_ocr_ts = 0.0
        self._last_ocr_sig = ""
        self._last_window_ocr_ts: dict[str, float] = {}
        self._day = ""
        self._fh = None
        self._disk_checked = 0.0
        self._free_gb = 999.0
        self._paused_log_ts = 0.0
        self._seq = 0
        self.shots_today = 0

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self.queue.warmup()
        self.queue.start()

    def shutdown(self) -> None:
        try:
            self.queue.stop()
        except Exception:
            pass
        self._close_day()

    def _close_day(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def _events_handle(self, day: str):
        if self._day != day or self._fh is None:
            self._close_day()
            paths.day_dir(day).mkdir(parents=True, exist_ok=True)
            paths.shots_dir(day).mkdir(parents=True, exist_ok=True)
            paths.thumbs_dir(day).mkdir(parents=True, exist_ok=True)
            self._fh = paths.events_path(day).open("a", encoding="utf-8")
            self._day = day
        return self._fh

    # ------------------------------------------------------------ 暂停判定
    def pause_reason(self, idle: float, locked: bool, fg: dict, force: bool = False) -> str:
        reason = privacy.capture_reason(self.cfg, fg, idle, locked, force)
        if reason:
            return reason
        if self._free_gb < self.min_free_gb:
            return "low_disk"
        return ""

    def _check_disk(self) -> None:
        now = time.time()
        if now - self._disk_checked < 300:
            return
        self._disk_checked = now
        try:
            self._free_gb = shutil.disk_usage(str(paths.ROOT)).free / 1024 ** 3
        except Exception:
            self._free_gb = 999.0

    # ------------------------------------------------------------ 单次采样
    def sample(self, force: bool = False) -> dict:
        self.cfg = privacy.current_config(self.cfg)
        self.queue.start()  # 幂等：--once 调试时也能把 OCR 队列跑起来
        now = timeutil.now()
        day = timeutil.workday_of(now, self.cfg.cutoff_hour)
        self._check_disk()

        fg = winapi.foreground_info()
        idle = winapi.idle_seconds()
        locked = winapi.is_locked()
        reason = self.pause_reason(idle, locked, fg, force)

        sig = f"{fg.get('app', '')}|{fg.get('title', '')}"
        event: dict = {
            "v": 1,
            "ts": timeutil.iso(now),
            "day": day,
            "app": fg.get("app", ""),
            "title": fg.get("title", ""),
            "pid": fg.get("pid", 0),
            "rect": list(fg.get("rect", (0, 0, 0, 0))),
            "idle": round(idle, 1),
            "state": reason or "active",
        }
        self._seq += 1
        event["seq"] = self._seq

        if reason:
            event = self._write_event(day, privacy.anonymous_event(event, reason), kind="idle")
            self._heartbeat(day, reason, event)
            self._prev_hash, self._prev_grid, self._prev_sig = None, None, ""
            if time.time() - self._paused_log_ts > 300:
                self._paused_log_ts = time.time()
                self.log.info("采集暂停：%s（不记录窗口内容）", reason)
            return event

        event = privacy.sanitize_event(event, self.cfg)
        if event.get("content_policy") == "metadata_only":
            event = self._write_event(day, event, kind="tick", fg=fg, force=force)
            self._heartbeat(day, "metadata_only" if event.get("state") == "active" else event["state"], event)
            self._prev_hash, self._prev_grid, self._prev_sig = None, None, ""
            return event

        # 抓屏
        try:
            img, origin = grab_frame(self.cfg, fg)
        except Exception as exc:
            self.log.warning("抓屏失败（%s），未回退全屏", type(exc).__name__)
            event = self._write_event(day, privacy.anonymous_event(event, "grab_failed"), kind="error")
            self._heartbeat(day, "grab_failed", event)
            return event

        latest = privacy.current_config(self.cfg)
        reason = privacy.capture_reason(latest, fg, winapi.idle_seconds(), winapi.is_locked(), force)
        if latest.get("privacy", {}) != self.cfg.get("privacy", {}):
            reason = reason or "policy_changed"
        if not same_foreground(fg, winapi.foreground_info()):
            reason = reason or "window_changed"
        if reason or privacy.should_skip_ocr(fg.get("app", ""), latest):
            event = self._write_event(day, privacy.anonymous_event(event, reason or "policy_changed"), kind="idle")
            self._heartbeat(day, event["state"], event)
            return event

        cur_hash = vision.dhash(img)
        cur_grid = vision.grid_signature(img, *self.cfg.get("dedupe.cell_grid", [12, 8]))
        same_window = sig == self._prev_sig
        cls, diff = vision.classify(
            self._prev_hash,
            self._prev_grid,
            cur_hash,
            cur_grid,
            same_window=same_window,
            dup_threshold=int(self.cfg.get("dedupe.hamming_threshold", 4)),
            diff_dup=float(self.cfg.get("dedupe.cell_diff_dup", 0.02)),
            diff_major=float(self.cfg.get("dedupe.cell_diff_major", 0.22)),
        )
        if force:
            cls = "new" if not same_window else "minor"
        event.update(cls=cls, diff=round(diff, 4), hash=f"{cur_hash:016x}")

        now_ts = time.time()
        should_save = self._should_save(cls, now_ts)
        saved_img: Path | None = None
        if should_save and self.cfg.get("privacy.store_screenshots", True):
            saved_img = self._save_frame(day, img, now, cls)
            event["img"] = saved_img.relative_to(paths.day_dir(day)).as_posix()

        # OCR：只有落盘的帧才值得识别
        if saved_img is not None:
            ocr_id = self._maybe_ocr(day, sig, img, fg, saved_img, now_ts, origin)
            if ocr_id:
                event["ocr"] = ocr_id

        self._prev_hash, self._prev_grid, self._prev_sig = cur_hash, cur_grid, sig
        if saved_img is not None:
            self._last_saved_ts = now_ts

        event = self._write_event(day, event, kind="shot" if saved_img else "tick", fg=fg, force=force)
        self._heartbeat(day, event.get("state", "active"), event)
        return event

    def _should_save(self, cls: str, now_ts: float) -> bool:
        gap = now_ts - self._last_saved_ts
        if cls in ("new", "major"):
            # 窗口切换要立刻留证；但标题频繁抖动时用 5s 兜一下，避免爆量
            return gap >= 5
        if cls == "minor":
            return gap >= self.min_save_gap
        if cls == "duplicate":
            return gap >= self.keyframe_sec
        return True

    def _save_frame(self, day: str, img: Image.Image, now: datetime, cls: str) -> Path:
        stamp = now.strftime("%H%M%S")
        base = f"{stamp}_{self._seq % 1000:03d}"
        shot = paths.shots_dir(day) / f"{base}.jpg"
        small = vision.downscale(img, self.max_width)
        vision.save_jpeg_atomic(small, shot, self.quality)
        th = paths.thumbs_dir(day) / f"{base}.jpg"
        vision.save_jpeg_atomic(vision.thumb(small, self.thumb_width), th, 55)
        self.shots_today += 1
        return shot

    # ------------------------------------------------------------ OCR
    def _maybe_ocr(self, day: str, sig: str, img: Image.Image, fg: dict, saved: Path,
                   now_ts: float, origin: tuple[int, int]) -> str:
        if self.cfg.get("ocr.engine", "umi_http") == "none":
            return ""
        if privacy.should_skip_ocr(fg.get("app", ""), self.cfg):
            return ""
        ok, why = self.queue.can_accept(day, now_ts)
        if not ok:
            return ""
        last_same = self._last_window_ocr_ts.get(sig, 0.0)
        new_window = sig != self._last_ocr_sig
        due_periodic = now_ts - self._last_ocr_ts >= self.periodic_ocr
        if not new_window and (now_ts - last_same) < self.min_ocr_gap and not due_periodic:
            return ""

        src = saved
        cleanup = False
        if self.cfg.get("ocr.crop_active_window", True):
            rect = fg.get("rect") or (0, 0, 0, 0)
            cropped = vision.crop_region(img, tuple(rect), origin)
            if cropped is None:
                return ""
            ocr_id = _new_ocr_id(now_ts)
            tmp = paths.OCR_TMP / f"{ocr_id}.png"
            try:
                vision.save_png_atomic(cropped, tmp)
                src, cleanup = tmp, True
            except Exception:
                tmp.unlink(missing_ok=True)
                return ""  # 请求窗口 OCR 时，不能失败后偷偷识别全屏。
        if src is saved:
            ocr_id = _new_ocr_id(now_ts)
        submitted = self.queue.submit(ocr_id, day, src, fg.get("app", ""), fg.get("title", ""),
                          capture_cfg=self.cfg)
        if not submitted:
            if cleanup:
                Path(src).unlink(missing_ok=True)
            return ""
        self._last_ocr_ts = now_ts
        self._last_window_ocr_ts[sig] = now_ts
        self._last_ocr_sig = sig
        return ocr_id

    # ------------------------------------------------------------ 写盘
    def _write_event(self, day: str, event: dict, kind: str, fg: dict | None = None,
                     force: bool = False) -> dict:
        original_img = event.get("img")
        event["kind"] = kind
        cfg = privacy.current_config(self.cfg)
        try:
            privacy.require_valid(cfg)
            if event.get("state") == "active":
                # 检查原始前台信息，而非已经脱敏的标题；同时拒绝旧范围的图片。
                reason = privacy.capture_reason(cfg, fg or event, winapi.idle_seconds(),
                                                winapi.is_locked(), force)
                if cfg.get("privacy", {}) != self.cfg.get("privacy", {}):
                    reason = reason or "policy_changed"
                if reason:
                    event = privacy.anonymous_event(event, reason)
            event = privacy.sanitize_event(event, cfg)
        except privacy.PrivacyError:
            event = privacy.anonymous_event(event, "config_error")
        if original_img and not event.get("img"):
            # 抓屏后恰好暂停/改名单：撤掉本轮尚未提交的图像证据。
            for rel in (original_img, original_img.replace("shots/", "thumbs/")):
                (paths.day_dir(day) / rel).unlink(missing_ok=True)
            self.shots_today = max(0, self.shots_today - 1)
        try:
            fh = self._events_handle(day)
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
        except Exception as exc:
            self.log.warning("写事件失败: %s", exc)
        return event

    def _heartbeat(self, day: str, state: str, event: dict) -> None:
        payload = {
            "ts": timeutil.iso(timeutil.now()),
            "day": day,
            "state": state,
            "pid": os.getpid(),
            "session": winapi.session_id(),
            "last_event": event.get("ts", ""),
            "app": event.get("app", ""),
            "shots_today": self.shots_today,
            "pending_ocr": self.queue.pending(),
            "ocr_ok": self.queue.stats.ok,
            "ocr_failed": self.queue.stats.failed,
            "free_gb": round(self._free_gb, 1),
            "interval_sec": self.interval,
        }
        try:
            paths.STATE.mkdir(parents=True, exist_ok=True)
            tmp = paths.HEARTBEAT.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, paths.HEARTBEAT)
        except Exception:
            pass

    # ------------------------------------------------------------ 主循环
    def run(self, duration: float = 0) -> None:
        self.start()
        started = time.time()
        next_at = started
        last_hb = 0.0
        try:
            while True:
                if duration and time.time() - started >= duration:
                    break
                now = time.time()
                if now < next_at:
                    time.sleep(min(1.0, next_at - now))
                    continue
                try:
                    self.sample()
                except Exception as exc:
                    self.log.exception("采样异常: %s", exc)
                jitter = random.uniform(-self.jitter, self.jitter) if self.jitter else 0
                next_at = now + max(5.0, self.interval + jitter)
                if time.time() - last_hb >= 30:
                    last_hb = time.time()
                    self._heartbeat(self._day or timeutil.workday_of(timeutil.now(), self.cfg.cutoff_hour),
                                    "active", {})
        finally:
            self.shutdown()


def _new_ocr_id(ts: float) -> str:
    return f"o{int(ts)}{uuid.uuid4().hex[:4]}"
