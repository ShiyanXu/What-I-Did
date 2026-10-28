"""计划任务入口：判断哪些天该出报，然后逐天生成。幂等由 report 自己保证。

`pending()` 是"该不该出报"的唯一判断入口：daemon 的 21:00 兜底与 `wid.py tick`
都调它，避免两处逻辑各自漂移（上一版就是因为两边判断不一致，才出现"到点了没反应"）。
"""
from __future__ import annotations

import json
import time
from datetime import timedelta

import paths
import report as report_mod
import timeutil

# 同一天已出过报之后，至少再过这么久才允许下一次增量更新，
# 否则 21:00 之后每次轮询都会重写一版，一天刷出十几个 revision
DEFAULT_REFRESH_GAP_MIN = 90

# 待审内容的新鲜窗口：过了这个时间还没确认，就允许重新准备一份（覆盖旧预览）
REVIEW_TTL_SEC = 12 * 3600


def _load_state(day: str) -> dict:
    p = paths.day_state(day)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _has_data(day: str) -> bool:
    p = paths.events_path(day)
    try:
        return p.exists() and p.stat().st_size > 40
    except OSError:
        return False


def _review_pending(day: str) -> bool:
    """审核模式下内容已备好、只等确认：这不是"漏报"，不该反复触发重生成。"""
    p = paths.DAY_STATE / f"{day}-review.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    try:
        ts = float(data.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    return ts > 0 and (time.time() - ts) < REVIEW_TTL_SEC


def _needs(day: str, now, cfg) -> tuple[bool, str]:
    ws, we = timeutil.day_window(day, cfg.cutoff_hour)
    if now < ws:
        return False, "未到窗口"
    if not _has_data(day):
        return False, "无采集数据"
    target = min(now, we)
    closed = now >= we
    trigger_dt = timeutil.trigger_at(day, cfg.cutoff_hour, int(cfg.get("report.trigger_hour", 21)))
    state = _load_state(day)

    if _review_pending(day):
        return False, "等待审核确认"

    if state.get("status") not in report_mod.REPORT_STATUS:
        if not closed and now < trigger_dt:
            return False, "未到出报时间"
        return True, "首次生成"

    try:
        covered = timeutil.parse(state.get("coverage_until_iso", ""))
    except Exception:
        return True, "状态异常"
    if covered >= target:
        st = state.get("status")
        # 输入有变化（迟到 OCR / 补采）：已成功的版本也要重算一版收尾
        if st in ("ok", "empty"):
            if state.get("input_hash") and state.get("input_hash") == report_mod.input_hash(day):
                return False, "已覆盖"
            return True, "输入有更新"
        # 降级版：网络恢复后在限额窗口内自动升级为 AI 版
        if st == "degraded" and report_mod.upgrade_due(state, now, cfg):
            return True, "降级升级重试"
        return False, "已覆盖"
    if closed:
        return True, "补终版"
    if now < trigger_dt:
        return False, "未到出报时间"
    gap_min = float(cfg.get("report.refresh_gap_min", DEFAULT_REFRESH_GAP_MIN))
    if (target - covered).total_seconds() / 60 < gap_min:
        return False, "已是最新"
    return True, "增量更新"


def pending(cfg, now=None, days: int | None = None) -> list[tuple[str, str]]:
    """返回 [(day, why)]，新的一天排最前。`days` 为回溯天数（含今天）。"""
    now = now or timeutil.now()
    today = timeutil.workday_of(now, cfg.cutoff_hour)
    span = int(cfg.get("report.catchup_days", 7) if days is None else days)
    today_dt = timeutil.parse_day(today)
    out: list[tuple[str, str]] = []
    for i in range(span, -1, -1):
        day = (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        ok, why = _needs(day, now, cfg)
        if ok:
            out.append((day, why))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _final_day(candidates: list[tuple[str, str]], now, cfg) -> str | None:
    """候选项里窗口已关闭（终版）的最新一天；没有则 None。

    `candidates` 已按日期新→旧排好，第一个窗口关闭的就是刚收尾的那一天：
    04:05 补的终版，或漏报后登录补跑的第一版。当天的窗口必然还开着
    （`workday_of` 保证 now 落在当天窗口内），所以它永远不会出现在这里。
    """
    for day, _why in candidates:
        _, we = timeutil.day_window(day, cfg.cutoff_hour)
        if now >= we:
            return day
    return None


def run(cfg, log, limit: int = 3) -> int:
    now = timeutil.now()
    today = timeutil.workday_of(now, cfg.cutoff_hour)
    candidates = pending(cfg, now)

    if not candidates:
        if log:
            log.info("tick: 没有需要生成的日报（today=%s）", today)
        return 0

    if log:
        log.info("tick: 待生成 %d 天：%s", len(candidates),
                 "、".join(f"{d}({w})" for d, w in candidates))

    # 自动弹浏览器只留给终版：窗口已关闭才弹。21:00 首版和晚间断点增量
    # 更新都不弹，免得一晚弹好几遍；一次补多天漏报时也只弹最新那一天，
    # 更早的进索引页自己看。手动 report / GUI 出报不走这里，不受限制。
    final_day = _final_day(candidates, now, cfg)
    rc = 0
    for day, why in candidates[:limit]:  # 单次最多补 3 天，避免开机瞬间连跑
        reason = "catchup" if (day != today or why == "补终版") else "auto"
        try:
            res = report_mod.generate(day, cfg, log, reason=reason, force=False,
                                      open_browser=(day == final_day))
            if log:
                log.info("tick: %s(%s) -> %s", day, why, res.get("status"))
            if res.get("status") not in ("ok", "skipped", "degraded", "empty", "busy", "nodata",
                                         "awaiting_review"):
                rc = 1
        except Exception as exc:
            rc = 1
            if log:
                log.exception("tick: %s 生成失败: %s", day, exc)
    return rc
