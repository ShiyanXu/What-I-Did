# 会话交接

最后更新：2026-11-06 15:35（v1.4.0 发布、生产实例已换装后）

## 当前已验证

- 现在明确可用的部分：`scripts/harness.py check` 一条命令覆盖 Harness 自测、隐私边界、
  隔离离线 E2E（`dev_fixture` 造一天 + `check_boundaries`，临时 `WID_HOME` 自动清理）。
  **各项项数随分支浮动，以命令输出为准，本文件不写死数字。**
- 这轮实际跑过的验证（2026-11-06 现场执行，不是沿用旧结论）：
  `publish.py --full` 前台跑通（`EXIT=0`，15:00–15:04），其中 `harness.py check` 全绿（`exit_code=0`）、
  `verify_dpi.py` 14/14、`gui_regression.py` 41/41（`seed_gui_home.py` 建的隔离目录，跑完即删）；
  `harness.py status` **5 项全 `passing`**（`source-links-001` 已随 R1 一起删除）；
  生产实例 `D:\What-IDid` 换装后 `doctor --full` **16 正常 / 1 提醒 / 0 失败**
  （项数比 1.3.1 少 1，少的正是删掉的 `Source links`）。
- 发布链：`release/v1.4.0/` 三件套 → tag `v1.4.0`（`4283584` → 提交 `18de063`）→
  GitHub Release 15:32 发布，资产 116,136,372 字节 / sha256 `7b6f17d1…50a32`，与本地逐位一致。

## 本轮改动

- 产品行为：**范围收窄，两个功能整体移出产品**（依据 `agents/decisions.md` D-19）——
  来源链接（R1：读浏览器历史、地址栏兜底、域名兜底、日报里可点链接、设置页两个开关、`doctor` 项、
  整个 `browse.*` 配置段）与知识卡（R2，从未实现）。`src/browse.py` 整文件删除，
  `ocr.jsonl` 不再存 `lines` / `height`，`build.spec` 不再隐藏导入 `sqlite3` / `_sqlite3`。
  `VERSION` 升到 `1.4.0`。
- 顺带修的真 bug：`config._set_path` 插三层键时缩进写死 `"  "`，父节已有子键时会把 `a.b.c`
  写到 `b` 同级；现在按层级算缩进，`check_privacy.py` 的三层键用例改用 `ocr.rapidocr.min_side`。
- 测试与打包：`feature_list.json` 删 `source-links-001`（现 5 项）；
  `check_privacy.py` 的 `test_source_links` 收窄成 `test_url_and_classify`（URL 打码与微信/飞书改判仍必须验）；
  `dev_fixture.py` 去掉地址栏样本。
- 文档：8 份对外文档（`docs/architecture*`、`configuration*`、`privacy*`、`usage*`）删掉来源链接章节；
  `PROGRESS.md` 新增「R1 移除实况」；`agents/requirements/wid-req-2026-11-001.md` 为需求原文归档
  （逐字保留、不回改，口径冲突时以 `PROGRESS.md` 为准）。

## 仍损坏或未验证

- 已知缺陷：以 `PROGRESS.md` 的「已知问题」为准（当前 9 条）。本轮修了深层配置键缩进；
  `src/paths.py:56-60` 的等价分支仍未动。
- **采集进程曾无日志消失**（已知问题 9）：2026-11-06 14:55:01 心跳停、日志无退出记录、事件日志无崩溃，
  原因未定位。判断办法是把 `state/heartbeat.json` 的 `ts` 与现实时间比，超 2 分钟即视为已停；
  停了用 WMI 拉起（`start` 在会话边界会被回收）。当天因此有三段断档：11:06–11:22、11:37–11:46、14:55–15:22。
- 未验证路径：日报的证据档位（11-06 起）是否守住「看 ≠ 写」「别人的排期 ≠ 我的待办」，
  还没有可判定的一天。
- 下一轮会话需要注意的风险：`feature_list.json` 里的 evidence 指向本机 `state/harness/`，
  克隆中看不到，只能重跑命令复核；`W:` 的 `.new` 中转文件要在 iMac 侧 `mv -f`，不处理就等于没同步。

## 下一步最佳动作

- 最高优先级：**观察今晚 21:00 的日报**，按 `agents/decisions.md` D-08 检查证据档位；
  有偏差调 prompt，不加特例。这是软件唯一的核心，比加功能重要。
- 别重开 R1 / R2：定位已收窄为「只记录我做了什么」（D-19）。新需求先过准入判据——
  它是在记录「我做了什么」，还是在管理「这些内容是什么」？后者不进这个软件。
- 功能项层面已无待办（5/5 `passing`）；下一个功能项出现前先跑 `harness.py status` 确认，
  不要凭本文件推断。
- 什么结果才算通过：`harness.py check` 退出码 0；新增功能项走 `harness.py start` →
  `harness.py verify` 登记证据，`passing` 不可回退。
- 这一步中哪些东西不要动：不修改真实报表，不清理既有未提交改动，不顺带重构 GUI，
  不在本机 commit / push / tag（Windows 侧无凭据，见 `PROGRESS.md` 已知问题 3）。

## 命令

- 启动命令：`PY=/path/to/python3.10 ./init.sh`；直接启动界面可设 `RUN_START_COMMAND=1`。
- 验证命令：`PY=/path/to/python3.10 python scripts/harness.py check`。
- 定向调试命令：`python scripts/harness.py status`；查看本机 `state/harness/` 中对应 trace 与 `.log`。
- 生产心跳：`cat D:/What-IDid/state/heartbeat.json`；采集日志 `D:/What-IDid/logs/daemon-*.log`。
