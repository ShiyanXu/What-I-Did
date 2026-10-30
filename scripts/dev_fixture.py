"""开发夹具：造一天样本数据 + 可选的假模型输出，用来端到端验证渲染链路。

    python scripts/dev_fixture.py --day 2026-10-18 --mock     # 造数据 + 用假模型跑一遍
    python scripts/dev_fixture.py --day 2026-10-18 --clean    # 清掉这个样本日

样本数据只写进 data/<day>/，不会碰真实日期。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import timedelta
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config as cfgmod  # noqa: E402
import paths  # noqa: E402
import timeutil  # noqa: E402

SAMPLE = [
    ("code.exe", "report.py - What-I-Did - Visual Studio Code", "dev", 55,
     "report.py\n130 def generate(day, cfg, log):\n131     cov = _coverage(day, now)\n"
     "132     state = _load_state(day)  # 幂等判断\n133     if state.get('status') in REPORT_STATUS:\n"
     "134         return {'status': 'skipped'}\n数据源 E:/Self/Project/What-I-Did/data/2026-10-18/events.jsonl"),
    ("WindowsTerminal.exe", "MINGW64:/e/Self/Project/What-I-Did", "dev", 22,
     "$ python wid.py doctor\n[ OK ] Python 3.10.1\n[ OK ] 磁盘 E: 剩余 382.4 GB\n"
     "[WARN] 模型 Key 未配置\n$ python wid.py report --date 2026-10-18\nstatus=degraded"),
    ("chrome.exe", "refs/providers.py · Issue #128 · hiroi-sora/Umi-OCR - Google Chrome", "dev", 18,
     "GitHub  hiroi-sora/Umi-OCR  Issues\n#128 HTTP 服务在 OCR 期间拒绝连接\n"
     "https://github.com/hiroi-sora/Umi-OCR/issues/128\n状态 Open · 3 comments"),
    ("chrome.exe", "PaddleOCR 方向分类器 - Google Chrome", "web", 12,
     "PaddleOCR 使用文档\nuse_angle_cls=True 时识别速度下降约 15%\n"
     "https://github.com/PaddlePaddle/PaddleOCR/blob/main/README_ch.md"),
    ("Obsidian.exe", "08-P8 Flash Attention - Obsidian v1.5.3", "doc", 48,
     "Flash Attention 把 attention 的计算分块放进 SRAM\n"
     "核心是减少 HBM 读写次数\n输入 Q K V，块大小 Bc Br 需要按 SRAM 容量调"),
    ("Feishu.exe", "项目资源协调 - 飞书", "meet", 35,
     "会议 项目资源协调\n参与人 徐诗言 涂海芝 Rachel\n"
     "议题：12X 编译农场本周提测，需要 2 台测试机\n结论：周四前给出机器清单"),
    ("WINWORD.EXE", "周报模板 - Word", "doc", 26,
     "本周进展\n1. 完成采集器与 OCR 链路\n2. 日报模板定稿\n遗留问题：Session 0 截图权限"),
    ("chrome.exe", "12X编译农场 - Google Chrome", "web", 20,
     "编译查询(新)  编译工程：请选择  编译分支：20261018170800  共 1 条\n"
     "编译看板  系统设置  我的任务\n包: RGOS-12.X 编译输出 重新启动即可更新"),
    ("code.exe", "digest.py - What-I-Did - Visual Studio Code", "dev", 33,
     "def build_payload(day, blocks, stats, ...) -> tuple[str, dict]:\n"
     "    budget = int(cfg.get('llm.input_char_budget', 24000))\n"
     "    # 全量块清单 + 重点块详情，按停留时长分配配额"),
    ("WINWORD.EXE", "需求评审纪要 - Word", "doc", 24,
     "需求评审 2026-10-18\n评审结论：日报需要支持跳转到原始文档\n"
     "待办：引用解析先做本地文件与 Obsidian 两类"),
]


def fake_image(path: Path, title: str, color: tuple[int, int, int]) -> None:
    img = Image.new("RGB", (960, 540), color)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 959, 32], fill=(30, 30, 40))
    d.text((10, 10), title[:90], fill=(235, 235, 240))
    for i in range(12):
        d.text((18, 60 + i * 28), f"line {i:02d}  " + "样本渲染用占位文字 " * 3, fill=(40, 40, 46))
    img.save(path, "JPEG", quality=70)


def make_day(day: str, seed: int = 7) -> None:
    rnd = random.Random(seed)
    ddir = paths.day_dir(day)
    (ddir / "shots").mkdir(parents=True, exist_ok=True)
    (ddir / "thumbs").mkdir(parents=True, exist_ok=True)
    base = timeutil.parse_day(day) + timedelta(hours=9, minutes=12)

    events: list[dict] = []
    ocrs: list[dict] = []
    seq = 0
    cursor = base

    for app, title, cat, minutes, text in SAMPLE:
        block_start = cursor
        # 每块按 20 秒采样，但只落 3 张图，模拟真实去重后的样子
        shots_at = {int(minutes * 60 * f) for f in (0.05, 0.45, 0.8)}
        shot_idx = 0
        for sec in range(0, minutes * 60, 20):
            seq += 1
            ts = block_start + timedelta(seconds=sec)
            ev = {
                "v": 1, "ts": timeutil.iso(ts), "day": day, "app": app, "title": title,
                "pid": 1000 + seq, "rect": [-8, -8, 1928, 1048], "idle": 12.0,
                "state": "active", "seq": seq, "cls": "tick", "diff": 0.01,
                "hash": f"{rnd.getrandbits(64):016x}", "kind": "tick",
            }
            if sec in shots_at and shot_idx < 3:
                stamp = ts.strftime("%H%M%S")
                name = f"{stamp}_{seq % 1000:03d}"
                color = {"dev": (32, 34, 40), "doc": (250, 250, 248), "meet": (238, 242, 255),
                         "web": (248, 250, 252), "comm": (245, 243, 255)}.get(cat, (240, 240, 240))
                fake_image(ddir / "shots" / f"{name}.jpg", title, color)
                fake_image(ddir / "thumbs" / f"{name}.jpg", title, color)
                ev.update(cls="minor", img=f"shots/{name}.jpg", kind="shot")
                oid = f"o{int(ts.timestamp())}{rnd.randrange(16**4):04x}"
                ev["ocr"] = oid
                ocrs.append({
                    "id": oid, "status": "ok", "text": text, "ms": 1900 + rnd.randrange(900),
                    "engine": "umi_http", "error": "", "ts": ts.timestamp(), "day": day, "app": app,
                })
                shot_idx += 1
            events.append(ev)
        cursor = block_start + timedelta(minutes=minutes) + timedelta(minutes=rnd.choice([4, 6, 8]))

    # 中午留一个 62 分钟的空档（模拟午休）
    with paths.events_path(day).open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    with paths.ocr_path(day).open("w", encoding="utf-8") as f:
        for rec in ocrs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"样本数据已写入 {ddir}（{len(events)} 条事件，{len(ocrs)} 条 OCR）")


def install_mock_llm() -> None:
    """用假模型替换真实调用：从 prompt 里挑真实 block_id / ref_id / seg_id，保证校验链被真正走一遍。

    返回的结构跟新提示词一致（behaviors / highlights / friction / insight），
    这样渲染链路（md + html + 界面）都能被验证到。
    """
    import llm as llm_mod

    def fake_chat_json(self, system, user, max_tokens=6000):
        self.calls += 1
        self.prompt_tokens += 12000
        self.completion_tokens += 2000
        bids = re.findall(r"\|\s*(b-\d{8}-\d{4}-[0-9a-f]{4}(?:-\d+)?)\s*\|", user)
        rids = re.findall(r"^\s*-\s*(r-[0-9a-f]{8})\s*\|", user, re.M)
        segs = re.findall(r"^### (seg-\d+)", user, re.M)
        pick = lambda i: [bids[i]] if i < len(bids) else []  # noqa: E731

        canned = [
            ("coding", "补上日报的幂等与覆盖范围判断", "在 report.py 里改 generate()，重复触发只更新覆盖水位"),
            ("debugging", "查 Umi-OCR 连不上的原因", "命令行自检报 10061，翻 issue #128 确认是并发问题"),
            ("reading", "核对 PaddleOCR 的方向分类参数", "确认 use_angle_cls 会拖慢约 15%，决定关掉"),
            ("writing", "把 Flash Attention 整理成课程笔记", "写 SRAM 分块与 HBM 读写次数两节"),
            ("meeting", "参加项目资源协调会", "定下 12X 编译农场周四提测，要两台测试机"),
            ("writing", "改写周报模板", "把本周进展压成三条，遗留 Session 0 截图权限"),
            ("reading", "查编译农场的当前包", "确认 20261018170800 那条编译结果可用"),
            ("coding", "写日报输入的压缩逻辑", "按停留时长分配字符配额，全量块清单保持完整"),
            ("writing", "整理需求评审纪要", "结论是日报要支持跳回原始文档"),
            ("admin", "收尾与整理", "核对留档，补上引用绑定"),
        ]
        behaviors = []
        for i, sid in enumerate(segs):
            verb, label, detail = canned[i % len(canned)]
            behaviors.append({"seg_id": sid, "verb": verb, "label": label,
                              "detail": detail, "confidence": "high" if i % 3 else "medium"})

        return {
            "headline": "把日报从「能出」修到「能信」：校验、引用、21 点触发全通",
            "opening_md": (
                "09:12 到 15:06，四小时五十四分钟里真正连续工作的只有三段，最长一段 55 分钟。"
                "主线是日报链路的收口：先把重复触发的幂等判断补上，再解决 OCR 服务在识别期间断连的问题，"
                "最后把排版全部压回本地模板。中间被钉钉和浏览器切开 4 次，碎片时间占了五分之一。"
            ),
            "summary_md": (
                "**09:12–10:06**（55 分钟）在 report.py 里补幂等判断。原来的 generate() 只要被调一次就写一版，"
                "21 点后每次重试都会多出一个 revision。改成先读 state 里的 coverage_until，"
                "只有覆盖水位往前推了才真正出报，重跑三次确认只写了一版。\n\n"
                "**10:15–10:58**（44 分钟）命令行自检时发现 Key 没配上，顺手跑了 doctor；"
                "真正的坑是 Umi-OCR 的 HTTP 端口在识别期间会被自己占住，"
                "报 [WinError 10061] 目标计算机积极拒绝。翻到 issue #128 才确认是并发问题，"
                "于是把 OCR 改成单线程队列 + 失败冷却，识别耗时从 14 秒降到 2 秒左右。\n\n"
                "**11:25–12:12**（48 分钟）切到 Obsidian 整理 Flash Attention 的笔记，"
                "重点是 SRAM 分块和 HBM 读写次数这两节，写完之后回头对照了一遍 SAMPLE 数据。\n\n"
                "**12:19–12:53**（35 分钟）项目资源协调会。结论是 12X 编译农场周四前提测，需要两台测试机；"
                "这一步把明天的计划直接定死了。\n\n"
                "**14:02–14:34**（33 分钟）回到代码，把 digest.py 的字符预算写完。"
                "按停留时长分配配额、全量块清单保持完整，用样本数据跑通了降级路径——但没接真模型验证过。"
            ),
            "behaviors": behaviors,
            "highlights": [
                {"what": "report.py 的幂等判断补完，重复触发不再多出版本",
                 "status": "done",
                 "evidence": "state 里的 coverage_until_iso 比对，重跑 3 次只写 1 版",
                 "impact": "21 点后的重试从「每次多一版」变成「只在有新增数据时更新」",
                 "next": "",
                 "block_ids": pick(0)},
                {"what": "OCR 改成单线程队列 + 失败冷却，识别耗时 14s → 2s",
                 "status": "done",
                 "evidence": "[WinError 10061]；issue #128",
                 "impact": "实例挂掉能自动拉起，采集不再被 OCR 拖住",
                 "next": "观察一周，确认并发问题没再复现",
                 "block_ids": pick(2)},
                {"what": "日报输入压缩写完，落在 24k 字预算内",
                 "status": "advanced",
                 "evidence": "digest.build_payload 的配额分配",
                 "impact": "全量块清单 + 重点块详情，长日也不会截断关键证据",
                 "next": "等真实 Key 到位后跑一次长日样本，确认截断行为",
                 "block_ids": pick(8)},
            ],
            "followups": [
                {"item": "接真实 Key 跑一次日报，逐句核对有没有编造",
                 "kind": "waiting", "why": "现在只验证了降级路径，摘要质量没被检验",
                 "block_ids": pick(1)},
                {"item": "给 12X 编译农场准备两台测试机清单",
                 "kind": "todo", "why": "协调会结论，周四前提测", "block_ids": pick(7)},
            ],
            "friction": [
                {"what": "Umi-OCR 在识别期间 HTTP 端口拒连",
                 "cause": "同一个实例既跑识别又接请求，识别时把接口堵住（外部依赖问题，不是自己的代码）",
                 "cost": "来回试了三四次，加上翻 issue 大约 40 分钟",
                 "fix": "改成单线程队列 + 失败冷却，实例不在时自动拉起",
                 "block_ids": pick(2)},
                {"what": "模型 Key 一直没配，真实日报没跑过",
                 "cause": "一直在验证降级路径，把最关键的一步留到了最后",
                 "cost": "模板和摘要质量都没被真实数据检验过",
                 "fix": "还没解决，明天第一件事",
                 "block_ids": pick(1)},
            ],
            "insight": [
                {"observation": "这天有效活动接近 5 小时，但最长连续专注只有 55 分钟，剩下全是 20–40 分钟的碎片",
                 "evidence": "14 个活动块，块长中位数 26 分钟，碎片时间占 21%",
                 "action": "把「写代码」这类需要上下文的活固定到上午第一段，会议和查资料压到下午"},
                {"observation": "两次卡点都不是技术难，而是「先信了工具的说法」——Umi 的 CLI 只是转发、OCR 报错其实是并发",
                 "evidence": "issue #128、[WinError 10061]",
                 "action": "以后遇到「工具报错」，先看它是不是只是个壳，再决定往哪查"},
            ],
            "tomorrow": [
                {"item": "接上 API Key 跑一次真实日报，逐句核对摘要有没有编造",
                 "why": "目前只验证了降级路径", "when": "上午第一件事"},
                {"item": "给 12X 编译农场准备两台测试机清单",
                 "why": "协调会结论，周四前提测", "when": "下午 14:00 前"},
            ],
            "entities": [
                {"kind": "file", "name": "report.py", "block_ids": pick(0)},
                {"kind": "project", "name": "12X 编译农场", "block_ids": pick(7)},
                {"kind": "ticket", "name": "Umi-OCR issue #128", "block_ids": pick(2)},
            ],
            "references": [
                {"ref_id": rids[0], "label": "当日实现记录", "why": "成果对应的代码位置",
                 "block_ids": pick(0)} if rids else None,
            ],
            "confidence": "high",
            "notes_for_human": "样本数据由 dev_fixture.py 生成，用于验证渲染链路；文字都是编的。",
        }

    llm_mod.LLMClient.chat_json = fake_chat_json


def clean(day: str) -> None:
    import shutil

    targets = [paths.day_dir(day), paths.report_json(day), paths.report_md(day),
               paths.web_day(day), paths.day_state(day)]
    removed = []
    for t in targets:
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)
            removed.append(str(t))
        elif t.exists():
            t.unlink()
            removed.append(str(t))
    cfg = cfgmod.load()
    import render

    render.render_index(cfg)
    print("已清理：" + ("、".join(removed) if removed else "（未找到）"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-10-18")
    ap.add_argument("--mock", action="store_true", help="用假模型跑一遍 report")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    paths.ensure_dirs()
    # 先落地配置再写样本：写完 data/ 之后 ensure_default 会把它当成"有运行数据但配置丢失"而拒绝
    cfgmod.ensure_default()
    if not paths.BINDINGS_PATH.exists():
        paths.BINDINGS_PATH.write_text(yaml.safe_dump([], allow_unicode=True), encoding="utf-8")
    if args.clean:
        clean(args.day)
        return 0

    make_day(args.day)
    if args.mock:
        install_mock_llm()
        import logging_setup
        import report

        cfg = cfgmod.load()
        # 让假模型"看起来"已配置
        cfg.set("llm.base_url", "http://mock.local/v1")
        cfg.api_key = lambda: "mock-key"  # type: ignore[method-assign]
        log = logging_setup.setup("fixture", console=True)
        res = report.generate(args.day, cfg, log, reason="manual", force=True, open_browser=False)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
