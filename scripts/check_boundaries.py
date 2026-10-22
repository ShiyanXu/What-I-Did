"""边界与异常路径自测：不需要模型 Key、不联网、不读真实数据。

前置（必须用隔离目录，不能覆盖真实记录）：

    set WID_HOME=%CD%\\.tmp_wid_test
    python scripts\\dev_fixture.py --day 2026-10-18 --mock
    python scripts\\check_boundaries.py

覆盖：空关键词、日期与天数越界、损坏日报、空周汇总、重复补录、非法时间、
人工改标、分享版不含内部锚点。返回码 0 表示全部通过。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config as cfgmod  # noqa: E402
import export_safe  # noqa: E402
import manual  # noqa: E402
import paths  # noqa: E402
import search  # noqa: E402
import weekly  # noqa: E402

DAY = "2026-10-18"
results: list[tuple[str, str]] = []


def expect_raises(fn, exc, label: str) -> None:
    try:
        fn()
    except exc:
        results.append((label, "ok"))
    except Exception as e:                      # noqa: BLE001
        results.append((label, f"错异常 {type(e).__name__}: {e}"))
    else:
        results.append((label, "没有报错"))


def check(cond, label: str) -> None:
    results.append((label, "ok" if cond else "断言失败"))


def main() -> int:
    cfg = cfgmod.load()
    if not paths.report_json(DAY).exists():
        print(f"缺少 {DAY} 的样本日报。先运行：python scripts/dev_fixture.py --day {DAY} --mock")
        return 2

    # 参数校验
    expect_raises(lambda: search.search("   ", days=3, cfg=cfg), ValueError, "空关键词")
    expect_raises(lambda: search.search("x", days=0, cfg=cfg), ValueError, "days=0")
    expect_raises(lambda: search.search("x", days=400, cfg=cfg), ValueError, "days=400")
    expect_raises(lambda: search.search("x", days=3, cfg=cfg, kinds=("bad",)), ValueError, "未知 kind")
    expect_raises(lambda: export_safe.export_day("2020-01-01", cfg), export_safe.ExportError, "无日报导出")
    expect_raises(lambda: export_safe.export_day("2020-1-1", cfg), ValueError, "导出日期格式")
    expect_raises(lambda: manual.add_offline(DAY, "25:00", "26:00", "x"), ValueError, "非法小时")
    expect_raises(lambda: manual.add_offline(DAY, "11:00", "10:00", "x"), ValueError, "结束早于开始")
    expect_raises(lambda: manual.add_offline(DAY, "10:00", "11:00", "  "), ValueError, "空 label")
    expect_raises(lambda: manual.set_override(DAY, "", label="x"), ValueError, "空 seg_id")
    expect_raises(lambda: weekly.weekly(start="2026-10-01", cfg=cfg), ValueError, "只给 start")
    expect_raises(lambda: weekly.weekly(start="2026-10-20", end="2026-10-01", cfg=cfg),
                  ValueError, "区间倒置")
    expect_raises(lambda: weekly.weekly(start="2026-1-1", end="2026-1-2", cfg=cfg),
                  ValueError, "汇总日期格式")

    # 损坏的日报：检索跳过、周汇总标记、导出明确报错
    paths.ensure_dirs()
    broken = paths.REPORTS / "2026-10-17.json"
    broken.write_text("{ 坏掉的 json", encoding="utf-8")
    try:
        res = search.search("任何词", days=7, cfg=cfg)
        check(isinstance(res, dict) and res["count"] == 0, "损坏日报不影响检索")
        w = weekly.collect("2026-10-16", DAY, cfg)
        check("2026-10-17" in w["broken"], "周汇总标记损坏日报")
        expect_raises(lambda: export_safe.export_day("2026-10-17", cfg),
                      export_safe.ExportError, "损坏日报导出报错")
    finally:
        broken.unlink(missing_ok=True)

    # 空周：没有日报也要能出文件
    w2 = weekly.weekly(start="2020-01-06", end="2020-01-12", cfg=cfg)
    check(Path(w2["path"]).exists(), "空周也能生成汇总")
    check(w2["counted_days"] == 0, "空周计数为 0")

    # 人工补录：幂等 + 改标生效
    mp = paths.day_dir(DAY) / "manual.json"
    mp.unlink(missing_ok=True)
    manual.add_offline(DAY, "10:00", "11:00", "线下客户沟通", "电话会议")
    manual.add_offline(DAY, "10:00", "11:00", "线下客户沟通", "电话会议")
    check(len(manual.load(DAY)["offline"]) == 1, "重复补录不叠加")
    manual.add_offline(DAY, "9:5", "9:50", "晨会")
    check(manual.load(DAY)["offline"][-1]["start"] == "09:05", "时间补零")

    data = {"behaviors": [{"seg_id": "seg-01", "verb": "coding", "label": "旧标签", "detail": ""}]}
    manual.set_override(DAY, "seg-01", "meeting", "复盘会", "和客户过方案")
    manual.apply_overrides(data, manual.load(DAY))
    check(data["behaviors"][0]["label"] == "复盘会", "override 覆盖标签")
    check(data["behaviors"][0]["verb"] == "meeting", "override 覆盖动词")
    check(data["behaviors"][0]["source"] == "manual", "override 标记来源")
    check(len(data.get("offline") or []) >= 1, "offline 挂到报告上")
    check("人工补录" not in (data.get("summary_md") or ""), "不再往 summary 拼文字")

    # 分享版：不含内部锚点
    payload = json.loads(paths.report_json(DAY).read_text(encoding="utf-8"))
    md = export_safe.build_md(DAY, payload, cat_label={})
    check("b-2026" not in md, "分享版不含 block_id")
    check("seg-" not in md, "分享版不含 seg_id")

    failed = [f"{k}: {v}" for k, v in results if v != "ok"]
    print(f"共 {len(results)} 项，失败 {len(failed)} 项")
    for key, val in results:
        print(("  ok  " if val == "ok" else "  FAIL") + f" {key}" + ("" if val == "ok" else f" -> {val}"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
