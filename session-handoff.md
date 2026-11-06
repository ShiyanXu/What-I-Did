# 会话交接

最后更新：2026-11-06 14:14（v1.3.1 发布后）

## 当前已验证

- 现在明确可用的部分：`scripts/harness.py check` 一条命令覆盖 Harness 自测、隐私边界、
  隔离离线 E2E（`dev_fixture` 造一天 + `check_boundaries`，临时 `WID_HOME` 自动清理）。
  **各项项数随分支浮动，以命令输出为准，本文件不写死数字。**
- 这轮实际跑过的验证（2026-11-06 现场执行，不是沿用旧结论）：
  `harness.py check` 全绿（`exit_code=0`）；`harness.py status` 6 项全 `passing`，
  `not_started` / `in_progress` / `blocked` 均为 0；
  `verify_dpi.py` 14/14、`gui_regression.py` 41/41（随 `publish.py --full` 跑过一遍，前台执行）；
  `doctor --full` 在生产实例 `D:\What-IDid` 上 18 项全绿；打包产物落地 `release/v1.3.1/` 并已发布。

## 本轮改动

- 产品行为：只改一处布局——当天的操作按钮（含「审核待发内容」）从 `badge_box` 移到独立的
  `page.ops_row`，窗口变窄时裁徽章而不是把确认步骤挤出屏幕；`VERSION` 升到 `1.3.1`。
- 测试侧：`gui_regression.py` 的按钮查找统一走 `find_op()`，断言 `winfo_ismapped()`——
  只「找得到」不够，要「看得到」。
- 基础设施：`harness.py` 在写证据前用 `_compact_command()` 把 `sys.executable` / 仓库根折回
  `{python}` / `{root}`，`check_harness.py` 加三条断言防止本机绝对路径再次入库；
  `AGENTS.md` 的仓库地图移入 `agents/layout.md`（入口文件 127 → 97 行）。
- 文档：`PROGRESS.md` 已改为 v1.3.1 已发布；`agents/environment.md`、`workflow.md`、`decisions.md`
  的共享盘权限表述统一改为「可原地覆盖、禁改名删除」。

## 仍损坏或未验证

- 已知缺陷：以 `PROGRESS.md` 的「已知问题」为准（当前 9 条），本轮只修了审核按钮那一条。
- 未验证路径：日报的证据档位（11-06 起）是否守住「看 ≠ 写」「别人的排期 ≠ 我的待办」，
  还没有可判定的一天；`src/paths.py:56-60` 的等价分支仍未动。
- 下一轮会话需要注意的风险：`feature_list.json` 里的 evidence 指向本机 `state/harness/`，
  克隆中看不到，只能重跑命令复核；`W:` 的 `.new` 中转文件要在 iMac 侧 `mv -f`，不处理就等于没同步。

## 下一步最佳动作

- 最高优先级：**发 v1.4.0**。产物已在 `release/v1.4.0/`，打 tag 与建 Release 由 iMac 侧做；
  发布后把 `PROGRESS.md` 的「v1.4.0 状态」改成已发布，并替换生产实例 `D:\What-IDid`。
- 定位已收窄为「只记录我做了什么」（D-19）：R1 来源链接与 R2 知识卡都已移出产品，
  **不要重新开工这两块**。新需求先过准入判据——它是在记录「我做了什么」，还是在管理「这些内容是什么」。
- 发布后回到常态：观察日报证据档位，按 D-08 调 prompt，不加特例。
- 功能项层面已无待办（5/5 `passing`）；下一个功能项出现前，先跑 `harness.py status` 确认，
  不要凭本文件推断。
- 什么结果才算通过：`harness.py check` 退出码 0；功能项若要新增，走 `harness.py start` →
  `harness.py verify` 登记证据，`passing` 不可回退。
- 这一步中哪些东西不要动：不修改真实报表，不清理既有未提交改动，不顺带重构 GUI，
  不在本机 commit / push / tag（Windows 侧无凭据，见 `PROGRESS.md` 已知问题 3）。

## 命令

- 启动命令：`PY=/path/to/python3.10 ./init.sh`；直接启动界面可设 `RUN_START_COMMAND=1`。
- 验证命令：`PY=/path/to/python3.10 python scripts/harness.py check`。
- 定向调试命令：`python scripts/harness.py status`；查看本机 `state/harness/` 中对应 trace 与 `.log`。
