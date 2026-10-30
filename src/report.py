"""日报编排：取数 -> 分块 -> 引用解析 -> 压缩 -> 模型 -> 校验 -> 落盘 -> 出页。"""
from __future__ import annotations

import hashlib
import json
import os
import time

import blocks as blocks_mod
import behavior as behavior_mod
import digest
import llm
import manual as manual_mod
import paths
import privacy
import refs
import render
import retention
import timeutil
import validate
from locking import SingleInstance

REPORT_STATUS = ("ok", "degraded", "empty")


def _load_state(day: str) -> dict:
    p = paths.day_state(day)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(day: str, data: dict) -> None:
    p = paths.day_state(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 出报状态判定
def input_hash(day: str) -> str:
    """输入版本：事件流 + OCR 流的指纹。迟到 OCR / 补采都会让它变化。

    "出过报"不再是一个布尔：覆盖（coverage_until）、模型是否成功（status）、
    输入是否变化（input_hash）三者分开管理。
    """
    parts = []
    for p in (paths.events_path(day), paths.ocr_path(day)):
        try:
            st = p.stat()
            parts.append(f"{p.name}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{p.name}:-")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def upgrade_due(state: dict, now, cfg) -> bool:
    """覆盖完整的降级日报是否还应自动升级为 AI 版（网络恢复后限额重试）。"""
    if state.get("status") != "degraded":
        return False
    if int(state.get("upgrade_attempts", 0)) >= max(1, int(cfg.get("report.max_upgrade_attempts", 3))):
        return False
    hours = float(cfg.get("report.degraded_retry_hours", 12))
    try:
        generated = timeutil.parse(state.get("generated_at", ""))
    except Exception:
        generated = None
    if generated is None:
        return True
    return (now - generated).total_seconds() <= hours * 3600


# ---------------------------------------------------------------- 发送前审核
def review_path(day: str):
    return paths.DAY_STATE / f"{day}-review.json"


def _save_review(day: str, data: dict):
    p = review_path(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    render.write_text_atomic(p, json.dumps(data, ensure_ascii=False, indent=2))


def _load_review(day: str) -> dict:
    p = review_path(day)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discard_review(day: str) -> None:
    try:
        review_path(day).unlink(missing_ok=True)
    except OSError:
        pass


def _coverage(day: str, now, cutoff_hour: int) -> dict:
    ws, we = timeutil.day_window(day, cutoff_hour)
    until = min(now, we)
    complete = now >= we
    # 次日凌晨才出的终版，覆盖到 03:01 属于正常收尾：页面不能再写"当日未结束"
    settled = complete or now.date() > timeutil.parse_day(day).date()
    if complete:
        window_label = f"{ws:%m-%d %H:%M} – 次日 {we:%H:%M}"
        until_label = "完整一天"
        note = "完整一天"
    elif settled:
        window_label = f"{ws:%H:%M} – {until:%H:%M}"
        until_label = until.strftime("%H:%M")
        note = f"已收尾 · 覆盖到 {until:%H:%M}"
    else:
        window_label = f"{ws:%H:%M} – {until:%H:%M}"
        until_label = until.strftime("%H:%M")
        note = f"截至 {until:%H:%M} · 当天还没结束"
    return {
        "start": ws.strftime("%H:%M"),
        "end": we.strftime("%H:%M"),
        "until": until.strftime("%H:%M"),
        "window": window_label,
        "until_label": until_label,
        "start_iso": timeutil.iso(ws),
        "end_iso": timeutil.iso(we),
        "complete": complete,
        "settled": settled,
        "note": note,
        "_until_dt": until,
        "_start_dt": ws,
        "_end_dt": we,
    }


def _degraded_report(blocks, stats, reason_text: str, sessions=None) -> dict:
    """不调模型也能看：纯客观时间线 + 分类统计。"""
    tl = []
    for b in blocks[:40]:
        tl.append({
            "start": b.start.strftime("%H:%M"),
            "end": b.end.strftime("%H:%M"),
            "title": b.title or b.app or "(无标题)",
            "app": b.app,
            "category": b.category,
            "block_ids": [b.id],
        })
    behaviors = behavior_mod.merge([], sessions or [])
    cats = stats.get("by_category", {})
    cat_text = "、".join(f"{blocks_mod.CATEGORY_LABEL.get(k, k)} {v:.0f} 分钟" for k, v in cats.items())
    top_apps = "、".join(f"{k} {v:.0f} 分" for k, v in list(stats.get("by_app", {}).items())[:5])
    r = stats.get("rhythm") or {}

    # 没模型时也要有"读得下去"的正文：按行为段铺一遍，不评价只陈述
    story: list[str] = []
    for it in behaviors[:12]:
        tail = f"　{it['detail']}" if it.get("detail") else ""
        story.append(f"**{it['start']}–{it['end']}**　{it['label']}（{it['minutes']} 分钟）{tail}")
    summary = (
        f"{reason_text}\n\n"
        f"今天共记录到 {stats['block_count']} 个活动块，有效活动 {stats['active_minutes']:.0f} 分钟"
        f"（{stats['first'] or '-'} 至 {stats['last'] or '-'}）。"
        + (f"时间主要花在：{cat_text}。" if cat_text else "")
        + (f"最长连续专注 {r['longest_focus_min']} 分钟（{r.get('longest_focus_at', '')}）；"
           f"碎片时间占 {r.get('fragmented_ratio', 0) * 100:.0f}%。" if r.get("longest_focus_min") else "")
        + "\n\n" + "\n\n".join(story)
    )
    return {
        "schema_version": 1,
        "headline": f"共 {stats['block_count']} 个活动块，有效活动 {stats['active_minutes'] / 60:.1f} 小时",
        "opening_md": "",
        "summary_md": summary,
        "behaviors": behaviors,
        "highlights": [],
        "followups": [],
        "friction": [],
        "insight": [],
        "timeline": tl,
        "accomplishments": [],
        "in_progress": [],
        "blockers": [],
        "decisions": [],
        "learnings": [],
        "tomorrow": [],
        "time_breakdown": [
            {"category": blocks_mod.CATEGORY_LABEL.get(k, k), "minutes": int(v), "note": "由采集数据直接统计"}
            for k, v in cats.items()
        ],
        "apps": [{"app": k, "minutes": int(v), "role": ""} for k, v in list(stats.get("by_app", {}).items())[:8]],
        "entities": [],
        "references": [],
        "confidence": "low",
        "notes_for_human": reason_text,
    }



def generate(day: str, cfg, log, reason: str = "manual", force: bool = False,
             open_browser: bool = True, approved: bool = False) -> dict:
    """approved=True 表示发送前审核已通过（review_mode 下由用户确认后回传）。"""
    cfg = privacy.current_config(cfg)
    privacy.require_valid(cfg)
    paths.ensure_dirs()
    with SingleInstance("WhatIDid.Report") as lock:
        if not lock.acquired:
            if log:
                log.info("已有报表进程在跑，本次跳过")
            return {"status": "busy", "day": day}
        return _generate_locked(day, cfg, log, reason, force, open_browser, approved)


def _generate_locked(day: str, cfg, log, reason: str, force: bool,
                     open_browser: bool, approved: bool) -> dict:
    t_start = time.time()
    now = timeutil.now()
    cov = _coverage(day, now, cfg.cutoff_hour)
    state = _load_state(day)
    target_until = cov["_until_dt"]

    # 幂等判断（tick 层还有一道入口判断，两边共用 input_hash / upgrade_due）：
    # 已覆盖且输入没变 -> ok/empty 跳过；degraded 只在升级窗口内允许重试。
    if not force and state.get("status") in REPORT_STATUS:
        try:
            prev = timeutil.parse(state.get("coverage_until_iso", ""))
        except Exception:
            prev = None
        if (prev is not None and prev >= target_until and state.get("revision")
                and state.get("input_hash") == input_hash(day)):
            if state.get("status") in ("ok", "empty") or not upgrade_due(state, now, cfg):
                if log:
                    log.info("%s 已生成过（%s 第 %s 版，覆盖到 %s，输入无变化），跳过",
                             day, state.get("status"), state.get("revision"),
                             state.get("coverage_until", ""))
                return {"status": "skipped", "day": day, "revision": state.get("revision"),
                        "coverage_until": state.get("coverage_until", "")}

    events = blocks_mod.load_events(paths.events_path(day))
    ocr_map = blocks_mod.load_ocr(paths.ocr_path(day))
    if not events:
        # 连一条观测都没有：说明那天根本没开机或采集没运行，不该产出空日报
        if log:
            log.info("%s 没有任何采集数据，跳过", day)
        return {"status": "nodata", "day": day, "reason": "no_events"}
    all_blocks = blocks_mod.build_blocks(events, ocr_map, cfg)
    stats = blocks_mod.summarize(all_blocks)
    sessions = behavior_mod.build_sessions(all_blocks, cfg)
    # 行为段合并了"块间隙 < 10 分钟"的连续工作，最长专注必须按它算
    stats["rhythm"] = blocks_mod.rhythm(all_blocks, sessions)
    gaps = blocks_mod.find_gaps(all_blocks, cov["_start_dt"], target_until,
                                float(cfg.get("report.gap_threshold_min", 10)))

    # 人工修订独立于重生成：manual.json 每次都重新套用，用户修订永远赢
    mdata = manual_mod.load(day)

    min_active = float(cfg.get("report.min_active_minutes", 20))
    status = "ok"
    issues: list[str] = []
    calls = 0
    prompt_tokens = completion_tokens = 0
    payload_chars = payload_budget = 0
    detailed = 0
    degraded_reason = ""

    if stats["active_minutes"] < min_active:
        status = "empty"
        report_data = validate.empty_report(
            f"这一天有效活动只有 {stats['active_minutes']:.0f} 分钟，不足以生成日报。"
            f"可能是电脑未开机、采集未运行，或当天基本没在电脑上工作。"
        )
        resolved_refs: list[dict] = []
        if log:
            log.info("%s 活跃仅 %.0f 分钟，输出空日报", day, stats["active_minutes"])
    else:
        resolved_refs = refs.resolve_for_report(cfg, all_blocks, log)
        if log:
            log.info("%s：%d 个活动块 / %d 个行为段，解析到 %d 条文档引用",
                     day, len(all_blocks), len(sessions), len(resolved_refs))

        payload, dmeta = digest.build_payload(
            day, all_blocks, stats, cov["_start_dt"], target_until, gaps, cfg, resolved_refs,
            sessions=sessions, manual=mdata,
        )
        payload_chars = dmeta["chars"]
        payload_budget = dmeta["budget"]
        detailed = dmeta["blocks_detailed"]

        client = llm.LLMClient(cfg, log)
        if not client.configured():
            status = "degraded"
            degraded_reason = ("隐私设置已禁止模型外发，输出本地时间线（未发送任何模型请求）"
                               if not cfg.get("privacy.allow_llm", True) else
                               "未配置 API Key，输出的是纯时间线版本（不调用模型）")
            report_data = _degraded_report(all_blocks, stats, degraded_reason, sessions)
            if log:
                log.warning(degraded_reason)
        elif cfg.get("report.review_mode", False) and not approved:
            # 发送前审核：落一份"待发内容"预览，等用户确认后再真正外发。
            # 30 分钟内同输入的重复触发直接复用，避免 tick 每 20 分钟重造一遍。
            ih = input_hash(day)
            prev_review = _load_review(day)
            fresh = (prev_review.get("input_hash") == ih
                     and time.time() - float(prev_review.get("ts", 0) or 0) < 1800)
            if not fresh:
                _save_review(day, {
                    "day": day,
                    "ts": time.time(),
                    "input_hash": ih,
                    "generated_at": timeutil.iso(now),
                    "base_url": cfg.get("llm.base_url", ""),
                    "model": cfg.get("llm.model", ""),
                    "coverage": cov["window"],
                    "payload_chars": payload_chars,
                    "payload": payload,
                })
            if log:
                log.info("%s 进入发送前审核（review_mode），未调用模型", day)
            return {"status": "awaiting_review", "day": day,
                    "preview_path": str(review_path(day)), "cached": fresh}
        else:
            # 审核确认：如果待审预览仍然新鲜，发送的就是用户确认过的那份内容，
            # 不能在这时候悄悄重建一遍——否则"确认的"和"发出去的"可能不是一份东西。
            if approved:
                prev_review = _load_review(day)
                if (isinstance(prev_review.get("payload"), str) and prev_review.get("payload")
                        and prev_review.get("input_hash") == input_hash(day)
                        and time.time() - float(prev_review.get("ts", 0) or 0) < 3600
                        and prev_review.get("base_url") == cfg.get("llm.base_url", "")
                        and prev_review.get("model") == cfg.get("llm.model", "")):
                    payload = prev_review["payload"]
                    payload_chars = len(payload)
                    if log:
                        log.info("%s 使用已确认的待发内容（%d 字）", day, payload_chars)

            block_ids = {b.id for b in all_blocks}
            ref_ids = {r["ref_id"] for r in resolved_refs}
            sys_prompt = digest.build_system_prompt()
            user_prompt = digest.build_user_prompt(day, payload, list(ref_ids))
            try:
                raw = client.chat_json(sys_prompt, user_prompt)
                calls = client.calls
                prompt_tokens, completion_tokens = client.prompt_tokens, client.completion_tokens
                report_data, vissues = validate.validate_report(raw, block_ids, ref_ids, sessions)
                issues.extend(vissues)
            except llm.LLMError as exc:
                # 修复轮也受 max_calls_per_report 约束：超限就降级，不再回灌重试
                if exc.kind == "bad_json" and client.calls < client.max_calls:
                    # 修复轮：把错误回灌，要求只输出纯 JSON
                    try:
                        raw = client.chat_json(
                            sys_prompt,
                            user_prompt + f"\n\n注意：上一次输出无法解析（{exc}）。这次只输出一个 JSON 对象，"
                                          f"不要任何解释文字或代码块标记。",
                        )
                        calls = client.calls
                        prompt_tokens, completion_tokens = client.prompt_tokens, client.completion_tokens
                        report_data, vissues = validate.validate_report(raw, block_ids, ref_ids, sessions)
                        issues.extend(vissues)
                    except llm.LLMError as exc2:
                        status = "degraded"
                        degraded_reason = f"模型两次输出都无法解析，已降级为时间线版：{exc2}"
                        report_data = _degraded_report(all_blocks, stats, degraded_reason, sessions)
                        issues.append(str(exc2))
                else:
                    status = "degraded"
                    degraded_reason = f"模型调用失败（{exc.kind}）：{exc}"
                    report_data = _degraded_report(all_blocks, stats, degraded_reason, sessions)
                    issues.append(str(exc))
                if log:
                    log.warning(degraded_reason)

    latest_cfg = privacy.current_config(cfg)
    privacy.require_valid(latest_cfg)
    if latest_cfg.get("privacy", {}) != cfg.get("privacy", {}):
        raise privacy.PrivacyError("生成期间隐私设置已变化，未覆盖旧报告，请重新生成")
    report_data = privacy.redact_value(report_data, cfg)
    resolved_refs = privacy.redact_value(resolved_refs, cfg)

    # 本地强制覆盖：模型无权改统计与缺口
    report_data["schema_version"] = 1
    report_data["stats"] = stats
    report_data["data_gaps"] = gaps
    if status == "degraded":
        report_data["confidence"] = "low"

    # 人工修订最后套用（在模型校验与本地覆盖之后）：行为段改标、线下补录、备注。
    # manual.json 是独立文件，重跑多少版都保留，用户修订永远赢。
    manual_mod.apply_overrides(report_data, mdata)

    revision = int(state.get("revision", 0)) + 1
    ih = input_hash(day)
    upgrade_attempts = int(state.get("upgrade_attempts", 0))
    if state.get("status") == "degraded":
        upgrade_attempts += 1     # 降级版的每次重生成都算一次升级尝试
    if status == "ok":
        upgrade_attempts = 0      # 升级成功后清零，之后再降级重新计数
    elapsed = int((time.time() - t_start) * 1000)

    render_blocks = []
    keep_excerpts = retention.keep_report_excerpts(day, latest_cfg, now)
    for b in all_blocks:
        render_blocks.append({
            "id": b.id,
            "start": timeutil.iso(b.start),
            "end": timeutil.iso(b.end),
            "app": b.app,
            "title": b.title,
            "category": b.category,
            "minutes": round(b.duration / 60),
            "content_policy": b.content_policy,
            "shots": list(b.shots),
            "text": (b.text or "")[:1500] if keep_excerpts else "",
            "switch_count": b.switch_count,
        })

    payload_doc = {
        "schema_version": 1,
        "day": day,
        "weekday": render.weekday_of(day),
        "generated_at": timeutil.iso(now),
        "revision": revision,
        "status": status,
        "reason": reason,
        "coverage": {k: v for k, v in cov.items() if not k.startswith("_")},
        "report": report_data,
        "stats": stats,
        "gaps": gaps,
        "blocks": render_blocks,
        "refs": resolved_refs,
        "manual": mdata,
        "meta": {
            "model": cfg.get("llm.model", "") if calls else "",
            "calls": calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "payload_chars": payload_chars,
            "payload_budget": payload_budget,
            "blocks_total": len(all_blocks),
            "blocks_detailed": detailed,
            "elapsed_ms": elapsed,
            "issues": issues,
            "degraded_reason": degraded_reason,
        },
    }

    content_hash = hashlib.sha1(
        json.dumps([report_data, render_blocks], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    payload_doc["meta"]["content_hash"] = content_hash
    payload_doc["meta"]["report_excerpt_days"] = retention.report_excerpt_days(latest_cfg)
    render.write_text_atomic(paths.report_json(day), json.dumps(payload_doc, ensure_ascii=False, indent=2))

    md_path = render.write_markdown(payload_doc)
    html_path = render.render_day(payload_doc)
    render.render_index(cfg)

    if cfg.get("obsidian.enabled", False):
        _sync_obsidian(cfg, day, md_path, log)

    _save_state(day, {
        "day": day,
        "status": status,
        "revision": revision,
        "coverage_until": cov["until"],
        "coverage_until_iso": timeutil.iso(target_until),
        "generated_at": timeutil.iso(now),
        "reason": reason,
        "content_hash": content_hash,
        "input_hash": ih,
        "upgrade_attempts": upgrade_attempts,
        "active_minutes": stats["active_minutes"],
        "html": str(html_path),
    })
    # 真报告落盘后，过期的待审预览就没意义了
    discard_review(day)

    if log:
        log.info("%s 日报完成：status=%s rev=%d 块=%d 用时=%dms 输出=%s",
                 day, status, revision, len(all_blocks), elapsed, html_path)

    if open_browser and cfg.get("report.open_browser", True) and os.environ.get("WID_NO_BROWSER") != "1":
        try:
            os.startfile(str(html_path))  # noqa: S606
        except Exception:
            pass

    return {
        "status": status,
        "day": day,
        "revision": revision,
        "html": str(html_path),
        "md": str(md_path),
        "blocks": len(all_blocks),
        "active_minutes": stats["active_minutes"],
        "refs": len(resolved_refs),
        "issues": issues,
        "degraded_reason": degraded_reason,
        "elapsed_ms": elapsed,
    }


def _sync_obsidian(cfg, day: str, md_path, log) -> None:
    from pathlib import Path

    vault = cfg.get("obsidian.vault_dir", "")
    if not vault:
        return
    try:
        target_dir = Path(vault)
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{day} 工作日报.md"
        dest.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
        if log:
            log.info("已同步到 Obsidian: %s", dest)
    except Exception as exc:
        if log:
            log.warning("同步 Obsidian 失败: %s", exc)
