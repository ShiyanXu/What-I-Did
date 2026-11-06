# 冲刺合同：补齐 Harness 工作系统

日期：2026-11-05

## 目标

让全新 coding agent 只读仓库就能初始化、选择单一功能、执行分层验证、留下可观测证据并干净交接，同时不改变 What-IDid 产品行为。

## 范围

- 建立机器可读 `feature_list.json`，记录行为、依赖、验证命令、状态和证据。
- 建立 `init.sh` 与 `scripts/harness.py`，统一初始化、离线 E2E、状态转移和 trace。
- 增加 Harness 自测、独立评分表、质量快照、干净收尾清单和长会话交接。
- 把冲刺范围、单循环路由和可选图化判据写入仓库，并从 `AGENTS.md` 路由。

## 验证标准

- `scripts/check_harness.py` 全部通过，覆盖 WIP、依赖、证据门控和 trace。
- `check_privacy.py` 104 项通过。
- 隔离 `WID_HOME` 中 mock 全链路与 `check_boundaries.py` 27 项通过，临时目录自动清理。
- `init.sh` 在跳过重复安装的模式下跑通，并打印 `python wid.py` 标准启动命令。
- 功能只有验证成功后才由脚本标记 `passing`；失败不能产生通过证据。
- 新会话仅凭 `AGENTS.md` 和路由文件能回答五个基本问题，并找到 Harness 开工流程。

## 排除项

- 不修改 `src/` 产品逻辑，不重建或替换生产 exe，不碰真实采集数据。
- 不新增 pip 依赖，不引入 OpenTelemetry SDK、LangGraph、自动 loop 或多 agent 并行。
- 不运行会污染当天日报的 GUI 点级测试；保留并明确记录 `gui-001` 阻塞。
- 不处理工作区里已有的来源链接改动，不 reset/clean，不 commit/push/tag/Release；改动数量随会话变化，
  一律现跑 `git status --short` 现查，不写进本文件。
- 不建立重复的 `CLAUDE.md` 或 `claude-progress.md`；现有 AGENTS/PROGRESS 是唯一事实源。

## 停止条件

- 上述范围内验证全部通过并完成交接；或
- 出现需要用户决策、会破坏既有改动/真实数据、必须联网或新增依赖的要求时立即停止并标记 blocked；或
- 同一问题连续修复仍无法恢复基线时，保留真实失败证据，不宣告完成。
