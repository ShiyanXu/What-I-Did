# 会话交接

最后更新：2026-11-05 18:35（Harness 建设 + 独立复核后更新）

## 当前已验证

- 现在明确可用的部分：`scripts/harness.py check` 一条命令覆盖 Harness 自测（24 项）、隐私边界（104 项）、
  隔离离线 E2E（`dev_fixture` 造一天 + `check_boundaries` 27 项，临时 `WID_HOME` 自动清理）。
- 这轮实际跑过的验证（均为本轮现场执行，不是沿用旧结论）：
  `harness.py check` 全绿（Harness 自测 24 项 + 隐私 104 项 + 隔离 E2E 27 项）；
  `init.sh` 在 `SKIP_INSTALL=1` 下跑通并打印 `wid.py` 启动命令；
  状态机实跑 `start → verify` 四次，`privacy-001` / `report-001` / `harness-001` / `harness-002`
  由脚本写入 evidence 后变为 `passing`；
  `check_boundaries.py` 在未设 `WID_HOME` 时按设计返回 2，不再往真实数据目录写残留。

## 本轮改动

- 新增了哪些代码或行为：不改变产品行为；新增 Harness 状态机（start/verify/block/resume）、统一初始化、
  隔离 E2E、JSONL 轨迹与凭据静态拦截。
- 基础设施或 harness 发生了哪些变化：新增功能清单、冲刺合同、图蓝图、评分表、质量快照、干净收尾清单及本交接文件；
  `AGENTS.md` 与验证专题增加路由；`PROGRESS.md` 去掉“工作区干净”这类一提交就过期的断言。

## 仍损坏或未验证

- 已知缺陷：既有产品已知问题仍以 `PROGRESS.md` 为准，本轮未夹带修复。
- 未验证路径：DPI、GUI 合成点击、真实一天语义、打包 exe 与生产实例未在本轮验证。
- 下一轮会话需要注意的风险：工作区原本就有 v1.3.0 来源链接未提交改动，必须保留并与本轮 Harness 文件区分；
  `feature_list.json` 里的 evidence 指向本机 `state/harness/`，克隆中看不到，只能重跑命令复核。

## 下一步最佳动作

- 最高优先级未完成功能：先 `source-links-001`（`not_started`，依赖已满足），再 `gui-001`（`blocked`）。
- 为什么它是下一步：来源链接的检查已存在于 `check_privacy`，可直接 `start` + `verify` 把 v1.3.0 的行为登记为证据；
  GUI 点级路径是当前最明显的验证缺口。
- 什么结果才算 passing：`source-links-001` 走 `harness.py start` → `harness.py verify`；
  `gui-001` 先暂停采集并准备全新隔离 WID_HOME，用 `harness.py resume gui-001 --reason "已暂停采集并隔离数据"` 恢复，
  再由 `harness.py verify gui-001` 运行 DPI 与 GUI 回归、写入证据，最后恢复采集。
- 这一步中哪些东西不要动：不修改真实报表，不清理既有未提交改动，不顺带重构 GUI，不在本机 commit/push/tag。

## 命令

- 启动命令：`PY=/path/to/python3.10 ./init.sh`；直接启动界面可设 `RUN_START_COMMAND=1`。
- 验证命令：`PY=/path/to/python3.10 python scripts/harness.py check`。
- 定向调试命令：`python scripts/harness.py status`；查看本机 `state/harness/` 中对应 trace 与 `.log`。
