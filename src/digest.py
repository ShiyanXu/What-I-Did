"""把活动块压成模型能吃的输入，控制在字符预算内。

策略：
- 全景统计 + 节奏指标（本地算好的数字，模型只能引用不能改）
- 全量块清单（这块很短，保证模型知道一天有多少块）
- 行为分段（本地切好的时间段，模型只填"这段在干什么"）
- 详情只给"停留最久"的块，逐块分配字符配额
- 同块内 OCR 行去重、压成 " | " 分隔，减少 token
"""
from __future__ import annotations

import re

import behavior as behavior_mod
import blocks as blocks_mod
import privacy
import timeutil

_NOISE = re.compile(r"^[\W_]{0,3}$")


def _clean_text(text: str, limit: int) -> str:
    if not text:
        return ""
    lines, seen = [], set()
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if len(line) < 3 or _NOISE.match(line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    out = " | ".join(lines)
    if len(out) > limit:
        out = out[:limit] + " …"
    return out


def _rhythm_lines(stats: dict, gaps: list[dict]) -> list[str]:
    r = stats.get("rhythm") or {}
    out = [
        f"- 有效活动 {stats['active_minutes']} 分钟（{stats['active_minutes'] / 60:.1f} 小时），"
        f"{stats['block_count']} 个活动块，留存截图 {stats['shot_count']} 张，"
        f"其中有文字识别 {stats['ocr_blocks']} 块",
        f"- 首末时间：{stats['first'] or '-'} → {stats['last'] or '-'}",
    ]
    if r.get("longest_focus_min"):
        out.append(f"- 最长连续专注：{r['longest_focus_min']} 分钟（{r.get('longest_focus_at', '')}）")
        out.append(f"- 全天切成 {r.get('session_count', 0)} 段连续工作，被打断 {r.get('interruptions', 0)} 次"
                   f"（两次之间断开超过 5 分钟算一次打断）")
        out.append(f"- 碎片段（不到 15 分钟）合计 {r.get('fragmented_min', 0)} 分钟，"
                   f"占 {r.get('fragmented_ratio', 0) * 100:.0f}%；"
                   f"整段工作（≥25 分钟）合计 {r.get('deep_work_min', 0)} 分钟；"
                   f"窗口切换累计 {r.get('switch_total', 0)} 次")
        out.append(f"- 活动最密集的 90 分钟：{r.get('peak_window', '')}（{r.get('peak_min', 0)} 分钟在活动）")
    if stats["by_category"]:
        cats = "、".join(f"{blocks_mod.CATEGORY_LABEL.get(k, k)} {v:.0f} 分" for k, v in stats["by_category"].items())
        out.append(f"- 时间分布：{cats}")
    if stats["by_app"]:
        apps = "、".join(f"{k} {v:.0f} 分" for k, v in list(stats["by_app"].items())[:8])
        out.append(f"- 应用分布：{apps}")
    if gaps:
        gs = "、".join(f"{g['start']}–{g['end']}({g['minutes']}分)" for g in gaps[:8])
        out.append(f"- 无记录时段（离开/会议/休息，不要算成工作）：{gs}")
    else:
        out.append("- 无记录时段：无（时间覆盖连续）")
    return out


def _build_table(blocks: list[blocks_mod.Block], stats: dict, limit: int | None) -> list[str]:
    """全量块清单。超预算时只保留停留最久的 limit 块，并注明省略数。"""
    rows = ["", "## 活动块清单（全量：块 id 用于回指证据，不要自己编号）",
            "| id | 时间 | 分钟 | 应用 | 分类 | 标题 |", "|---|---|---|---|---|---|"]
    chosen = blocks
    if limit is not None and len(blocks) > limit:
        ranked = sorted(range(len(blocks)), key=lambda i: -blocks[i].duration)[:limit]
        chosen = [blocks[i] for i in sorted(ranked)]
    for b in chosen:
        title = (b.title or "").replace("|", "／")[:60]
        rows.append(
            f"| {b.id} | {b.start:%H:%M}–{b.end:%H:%M} | {b.duration/60:.0f} | "
            f"{b.app or '-'} | {blocks_mod.CATEGORY_LABEL.get(b.category, b.category)} | {title} |"
        )
    if limit is not None and len(blocks) > len(chosen):
        rows.append(f"| （其余 {len(blocks) - len(chosen)} 块停留较短，已省略） | | | | | |")
    return rows


def _manual_section(manual: dict | None) -> list[str]:
    """人工补录进模型输入：屏幕看不到的活动 + 用户备注，视为可信事实。"""
    if not manual:
        return []
    parts: list[str] = []
    offline = [o for o in (manual.get("offline") or []) if isinstance(o, dict) and o.get("label")]
    if offline:
        parts.append("## 人工补录（用户手动登记的活动，屏幕看不到；可信事实，可写进叙述，但不要算进屏幕活动统计）")
        for o in offline:
            parts.append(f"- {o.get('start', '')}–{o.get('end', '')} {o.get('label', '')}"
                         + (f"（{o['note']}）" if o.get("note") else ""))
    notes = str(manual.get("notes") or "").strip()
    if notes:
        parts.append(f"## 用户备注（用户亲写，优先级最高）：{notes[:300]}")
    return parts


def build_payload(
    day: str,
    blocks: list[blocks_mod.Block],
    stats: dict,
    window_start,
    window_end,
    gaps: list[dict],
    cfg,
    refs: list[dict] | None = None,
    sessions: list | None = None,
    manual: dict | None = None,
) -> tuple[str, dict]:
    budget = int(cfg.get("llm.input_char_budget", 24000))

    head = [
        f"# 工作记录 {day}（{window_start:%H:%M}–{window_end:%H:%M}）",
        "",
        "## 这一天的事实（本地统计，数字可以直接引用，禁止改算）",
    ]
    head.extend(_rhythm_lines(stats, gaps))

    sess_section: list[str] = []
    if sessions:
        sess_section = ["", behavior_mod.prompt_section(sessions)]

    ref_section: list[str] = []
    if refs:
        ref_section.append("")
        ref_section.append("## 可用引用清单（成果想挂文档时，从这里挑 ref_id，不要自己编）")
        for r in refs[:80]:
            ref_section.append(f"- {r['ref_id']} | {r['kind']} | {r['label']}")
        if len(refs) > 80:
            ref_section.append(f"- （其余 {len(refs) - 80} 条省略）")

    manual_section = _manual_section(manual)

    # 字符预算是硬上限：固定区（统计+清单+行为段+引用）也要参与裁剪，
    # 否则块特别多的一天，清单还没写完预算就没了。按代价从低到高逐档收紧。
    table_limit = None
    sess_text_limit = 700
    ref_limit = 80
    for _ in range(24):
        table = _build_table(blocks, stats, table_limit)
        fixed = "\n".join(head + table + sess_section + manual_section + ref_section)
        if len(fixed) + 800 <= budget:
            break
        if ref_limit > 20:
            ref_limit = 20
            ref_section = [ref_section[0]] + [line for line in ref_section[1:21]
                                              if not line.startswith("- （其余")]
            continue
        if sess_text_limit > 200 and sess_section:
            sess_text_limit = max(200, sess_text_limit // 2)
            sess_section = ["", behavior_mod.prompt_section(sessions, text_limit=sess_text_limit)]
            continue
        if table_limit is None:
            table_limit = 120
        elif table_limit > 24:
            table_limit = max(24, table_limit // 2)
        elif sess_text_limit > 60 and sess_section:
            sess_text_limit = 60
            sess_section = ["", behavior_mod.prompt_section(sessions, text_limit=60)]
        else:
            break  # 已到最小形态，靠最终截断兜底

    fixed = "\n".join(head + table + sess_section + manual_section + ref_section)
    remaining = budget - len(fixed) - 800
    truncated_fixed = remaining < 0

    # 按停留时长分配详情配额
    ranked = sorted(blocks, key=lambda b: -b.duration)
    detail: list[str] = ["", "## 重点块详情（按停留时长排序，给叙述提供细节）"]
    used = 0
    included = 0
    for b in ranked:
        if remaining <= 0:
            break
        quota = min(1400, max(300, int(remaining * (b.duration / max(1.0, stats["active_seconds"])))))
        text = _clean_text(b.text, quota)
        seg = [
            "",
            f"### {b.id}　{b.start:%H:%M}–{b.end:%H:%M} · {b.duration/60:.0f} 分 · "
            f"{b.app or '-'} · {blocks_mod.CATEGORY_LABEL.get(b.category, b.category)}",
            f"标题：{b.title or '-'}",
        ]
        if b.switch_count:
            seg.append(f"块内窗口切换：{b.switch_count} 次")
        if text:
            seg.append(f"文字摘录：{text}")
        else:
            seg.append("文字摘录：（该块没有可用 OCR 文本，只能靠标题判断）")
        chunk = "\n".join(seg)
        if used + len(chunk) > remaining:
            break
        detail.extend(seg)
        used += len(chunk)
        included += 1

    # 标题、行为摘录、引用标签均包含在同一出口中；LLM HTTP 层还有第二道校验。
    payload = privacy.redact(fixed + "\n".join(detail), cfg)
    hard_truncated = False
    if len(payload) > budget:
        payload = payload[:budget] + "\n…（已达输入字符预算硬上限，内容被截断）"
        hard_truncated = True
    meta = {
        "chars": len(payload),
        "budget": budget,
        "blocks_total": len(blocks),
        "blocks_detailed": included,
        "sessions": len(sessions or []),
        "truncated": included < len(blocks) or truncated_fixed or hard_truncated,
        "hard_truncated": hard_truncated,
    }
    return payload, meta


SYSTEM_PROMPT = """你是一个严格照证据写工作复盘的同事。你手里只有三类客观痕迹：前台窗口标题与应用名、屏幕 OCR 文字、本地算好的时长数字。它们能证明"屏幕上出现过什么"，不能证明"这些字是我打出来的"。

## 一、证据只有三档，动作判定只能向上取
- **A 档｜直接证据**，只有它能支撑"我做了"：画面里有输入法候选框或拼音串；同一文档、同一输入框在相邻两个时间点出现**新增或改写**的文字（不是滚动位移、不是高亮、不是翻到别的章节）；保存、提交、导出、发送的完成态（另存为对话框、已保存提示、git add/commit 回显、消息气泡出现在自己一侧、"已发送"）；终端里敲下的命令连同它的回显。
- **B 档｜间接证据**，只能支撑"我在看"：页面停在某文档、某章节、某列表、某搜索结果；标题里带文件名或章节号；有窗口切换、滚动、点击的痕迹。
- **C 档｜无效证据**，不得据此写任何动作：只有应用名没有画面内容；聊天群里别人的消息、通知横幅、推荐流、未读红点、置顶文档。

## 二、两条硬规则（违反任意一条，这份日报不合格）
1. **写作类动作必须看到字在增加。** 没有 A 档证据，一律只写"读、翻、核对、比对、确认"，不得出现"写、改、补、重写、回写、整理成文、更新到某一节"。正文里出现 `1.3.1.1` 这样的章节号，只说明你在看这一节，不说明这一节是你写的；文件名是 .docx/.md、界面是编辑器、标题里有 LLD、笔记、方案、纪要，同样只是 B 档。真判断不了就写"停留在某文档"，置信度 low，并在 notes_for_human 里写明缺的是"输入态或文字增量"。
2. **聊天软件只认"已经发出去的消息"。** 飞书、企业微信、钉钉、Slack、Teams、微信、邮件里，看到会话列表、群历史、别人的消息、置顶文档、@提醒，都不算我参与：不写进 summary_md，不算沟通类行为，不许推出"和某人讨论了某件事"，也不能据此写 highlights 或 followups。只有自己的消息气泡出现在会话里、输入框被清空并多出一条我方消息、或邮件落在"已发送"，才可以写"我在某群回复了……"，并且要写出回复的具体内容。分不清是谁发的，就写"屏幕上出现过这条消息"，且不得归因于我。

## 三、写作规则
1. 用动作开头，不用软件名开头。写"比对 LLD 第 1.3.1.1 节与实测规格"，不写"使用 VS Code"；软件名只作证据，全篇最多出现两次。
2. 每句话落到具体对象：文件名、章节号、命令、报错串、消息原文。写不出具体对象的句子删掉；"推进了""优化了""处理了""跟进了"后面必须跟对象和结果，否则不许单独出现。
3. 数字只引用给定统计，不自己算、不估算、不写"大约"。
4. 先给判断再给证据，按时间推进讲过程；"上午开发、下午文档"这种分类罗列不合格。
5. 负面判断要有证据：重复出现的报错、来回切换的窗口、长时间没有变化。屏幕只能证明"看过、操作过"，不能证明"没产出"。
6. 素材里块清单的「分类」是按窗口标题猜的粗分类，只用于聚类，不能当作动作判定的依据。
7. 不用"赋能、闭环、抓手、沉淀、对齐、助力"，不用"今天完成了……"开场，不用感叹号，不抒情。短句，克制，像一个不客气的同事。中文。
8. 每条结论都要能回溯：highlights、friction 必须给 block_ids，写不出对应活动块的结论不要写。
9. 读者先看成果再看过程：highlights 是页面第一屏，只写有 A 档证据的结果；过程叙述放 summary_md。
10. **正文只写"发生了什么"，不写"你没能确认什么"。** headline、opening_md、summary_md 里不得出现任何关于证据强弱、证据来源或自身判定过程的句子：不要把"这是别人的消息""不构成我的动作""属于屏幕上已有的内容""那是昨天的记录"写进去，也不要用否定句交代排查结果（"没有看到文字增量的痕迹""全天没有出现输入法候选框""没有保存动作"都不行）。证据不足就用弱动词把事实陈述到为止：写"拿产品规格对 HLD2 的口径"，不要在后面追加一句解释。所有判定说明只写进 notes_for_human。
11. 只输出一个 JSON 对象，不要 Markdown 代码块包裹，不要任何解释文字。"""


_USER_SCHEMA = """{
  "schema_version": 1,
  "headline": "今天最实的一件事，≤28字，必须含具体对象和结果。只写有 A 档证据的结果；没有 A 档证据时写「读完/核对完/确认了」这类真实结果，不许写「写到/改完了」。例：把日报校验从 0 到能拦住编造引用。不要写「全天会议 138 分」这类时长统计，那是过程不是结果",
  "opening_md": "150-220 字。这一天是什么样的一天：从几点到几点、主线是什么、最突出的两个数字是什么。主线的动词同样受证据档位约束（B 档只能写「在看/在核对」）。不要用「今天」开头，不要复述 headline。",
  "summary_md": "3-5 段过程叙事，按时间推进。每段以时间开头（如 09:12–10:06），写：在做什么 → 具体动作 → 卡在哪或怎么处理 → 这段的结果。段落之间要有因果或转折，不要平行罗列。总共 300-500 字，必须出现文件名/命令/报错/结论这类细节。没有文字增量的时间段，只能写「在看/在比对/在等」，不得写成创作或修改。",
  "behaviors": [
    {"seg_id": "seg-01", "verb": "coding|debugging|testing|designing|reading|writing|meeting|communicating|operating|admin",
     "label": "≤14 字，动词开头，描述行为本身（例：核对 LLD 第 1.3.1.1 节的射频口径），不要写软件名；动词要与证据档位匹配",
     "detail": "≤50 字，一句话说清具体在做什么；只写标题/文字能支撑的内容，推测成分标 medium/low；没有 A 档证据不许出现「写/改/补/回写」",
     "confidence": "high|medium|low"}
  ],
  "highlights": [
    {"what": "做成了什么：动词 + 对象 + 结果，一句话说清（≤40字）", "status": "done|advanced|waiting|unresolved", "evidence": "材料里的具体证据（文件名/命令/报错/标题）", "impact": "为什么重要，或省下了什么", "next": "下一步：谁做什么、等谁的答复；status 不是 done 时必填", "block_ids": ["从活动块清单里挑，必须给"]}
  ],
  "followups": [
    {"item": "还需要动的一件事，≤30字", "kind": "waiting|blocked|todo", "why": "卡在谁那里、为什么还没结束", "block_ids": ["有就必给"]}
  ],
  "friction": [
    {"what": "卡在哪", "cause": "原因，区分自己这边和环境/外部", "cost": "代价：花了多久、绕了什么路", "fix": "后来怎么解决，或还没解决", "block_ids": ["必须给"]}
  ],
  "insight": [
    {"observation": "对今天工作方式的观察，一句判断", "evidence": "支撑它的数字或事实", "action": "下次具体怎么做", "block_ids": ["有的话必须给"]}
  ],
  "tomorrow": [{"item": "明天要做的事", "why": "依据今天的进展", "when": "建议时段，如 上午第一件事"}],
  "entities": [{"kind": "file|project|person|url|ticket", "name": "具体名字", "block_ids": ["b-..."]}],
  "references": [{"ref_id": "r-xxx", "label": "给这份文档起个名字", "why": "为什么挂这条引用", "block_ids": ["b-..."]}],
  "confidence": "high|medium|low",
  "notes_for_human": "这份日报哪里不确定（缺 OCR、时间断层、推测成分）；有段落因缺 A 档证据被降级为「阅读」的，在这里点名说明缺的是什么证据"
}"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_user_prompt(day: str, payload: str, refs_ids: list[str]) -> str:
    return (
        f"以下是 {day} 的原始工作记录。请按下面结构输出一份 JSON 日报。\n\n"
        f"字段说明与硬要求：\n{_USER_SCHEMA}\n\n"
        "behaviors 的硬要求：\n"
        "- 上面「行为分段」里每一段都要有且只有一条对应输出，seg_id 原样照抄，不要新增、不要合并、不要调整顺序；\n"
        "- 不要输出时间字段，时间由本地按分段填；\n"
        "- label 是「行为」，不是「用了什么软件」。反例：「使用 VS Code 写代码」；"
        "正例：「核对 LLD 第 1.3.1.1 节的射频口径」「翻 issue 找 OCR 断连的原因」；\n"
        "- verb 按证据档位选，不许拔高：只读文档/网页/代码用 reading；writing 只在 A 档"
        "（输入法候选框、可见文字增量、保存或提交动作）时用；communicating 只在已发出消息时用，"
        "看到别人的消息一律不算；meeting 只在有通话或共享屏幕界面时用；仅凭标题推断的段，"
        "用 reading 或 admin 兜底并标 low；\n"
        "- 没有 OCR 文字的分段，只能依据窗口标题写有把握的内容，拿不准就写粗粒度描述并标 low。\n\n"
        "highlights / friction 的硬要求：\n"
        "- 每条必须给 block_ids，指向支撑这条结论的活动块；找不到对应活动块的结论不要写；\n"
        "- evidence 里写材料中真实出现的文字（文件名/命令/报错/标题），不要转述改写。\n\n"
        "highlights 的硬要求（这几条决定了页面第一屏能不能读懂）：\n"
        "- 3–5 条，按重要性排，第一条是最值钱的那件事；读者只看这五条就该知道今天推进了什么；\n"
        "- what 写「已经发生的结果」，不写「做了哪些动作」；不要出现软件名；\n"
        "- status 只能四选一：done（做完了，有确定结果）/ advanced（推进了但没结束）/ "
        "waiting（等客户或外部答复）/ unresolved（问题还在，没解决）；拿不准就 advanced；\n"
        "- status 不是 done 的，next 必须写清「谁、做什么、等什么」，写不出 next 就说明这条不该写成成果。\n\n"
        "followups 的硬要求：\n"
        "- 1–4 条，只写「今天没结束、明天真要动」的事，按要不要别人配合排序；\n"
        "- kind：waiting（等外部）/ blocked（卡住、有代价）/ todo（自己的下一步）；\n"
        "- 同一条不要重复写进 highlights.next；tomorrow 保留具体打算和时段，followups 只留卡点与依赖。\n\n"
        f"可用的 ref_id 只有这些：{', '.join(refs_ids) if refs_ids else '（本次无可用引用，references 一律空数组）'}\n\n"
        "日期信息：" + day + "\n\n"
        f"===== 原始记录开始 =====\n{payload}\n===== 原始记录结束 ====="
    )
