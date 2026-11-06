# Harness 工作系统

> 本文件把入口规则、机器状态、执行反馈和会话交接串起来。改功能前先读；改产品架构仍须按 `AGENTS.md` 路由到对应专题。
> 结构依据 [Harness Engineering 全部讲义](https://walkinglabs.github.io/learn-harness-engineering/zh/) 适配；具体取舍见 `agents/decisions.md` D-15。

## 一、唯一事实来源与分工

| 文件 | 回答的问题 | 维护方式 |
|---|---|---|
| `AGENTS.md` | 这是什么、怎么跑、哪些红线不能碰 | 人工/agent 随架构变更同步 |
| `feature_list.json` | 该做什么、依赖什么、当前状态、完成证据 | 状态只能由 `scripts/harness.py` 改 |
| `PROGRESS.md` | 项目整体进度、已知问题、下一步 | 每次会话结束更新；不写当前 HEAD |
| `agents/decisions.md` | 为什么这样设计、否决过什么 | 新决策或推翻旧决策时更新 |
| `session-handoff.md` | 最近一次长会话具体交了什么 | 长会话结束更新；短会话可只更新 PROGRESS |
| `quality-document.md` | 各产品区域是在变强还是变弱 | 重要会话、基准对比或清理后更新 |
| `sprint-contract.md` | 这一轮做什么、不做什么、什么算完成 | 每轮开工前写定，范围变了先改它 |
| `clean-state-checklist.md` | 收尾还差哪几项 | 每次下班逐项过 |
| `evaluator-rubric.md` | 独立评审按什么打分、什么算 Block | 评审前读，结论写回评审记录 |
| `graph.md` | 当前流程的显式路由，以及何时才值得图化 | 流程或判据变化时更新 |
| `state/harness/` | 完整命令输出与运行轨迹 | 自动写入，本机保留，git 忽略 |

课程模板里的 `claude-progress.md` 在本仓库由 `PROGRESS.md` 承担，`AGENTS.md` 同时服务 Codex 与 Claude Code；不要另建两份会漂移的进度或指令。

## 二、上班：初始化与接班

```bash
PY=/path/to/python3.10 ./init.sh              # 依赖 + 完整 Harness 检查 + 打印启动命令
PY=/path/to/python3.10; "$PY" scripts/harness.py status
```

所有者机器可用 `SKIP_INSTALL=1 PY=C:/Users/xushiyan/AppData/Local/Programs/Python/Python310/python.exe ./init.sh` 跳过重复安装。任何机器都可设 `RUN_START_COMMAND=1` 让脚本最后启动界面。

随后：

1. 读 `PROGRESS.md` 的当前状态、已知问题和下一步；涉及既有设计时读 `agents/decisions.md`。
2. 当场运行 `git status --short` 与 `git log --oneline -3`，不沿用旧会话的工作区结论。
3. 基线失败时先修基础状态，不在坏基线上叠功能。
4. 从 `harness.py status` 读取当前项或下一可调度项，不凭聊天记录重新猜范围。

## 三、单功能推进：WIP=1

```bash
$PY scripts/harness.py start <feature-id>
$PY scripts/harness.py verify <feature-id>
$PY scripts/harness.py block <feature-id> --reason "具体阻塞与解除条件"
$PY scripts/harness.py resume <feature-id> --reason "阻塞已解除的证据"
$PY scripts/harness.py status
```

- 任意时刻最多一个 `in_progress`；依赖未全通过、已有功能进行中或目标已 `passing` 时，`start` 必须拒绝。只有 `blocked` 项可用 `resume` 恢复，并记录阻塞解除证据。
- 实现中不顺手重构其他功能，不悄悄降低验证标准。当前项未通过时，不开新项。
- `verify` 逐项执行 `verification_commands`；任何命令失败都保持 `in_progress`，只有全成功才写 `passing` 与 evidence。
- `passing` 不可逆。发现回归时应新增修复项，并在 `PROGRESS.md` 记录，不把旧证据改写成新结论。
- 功能粒度以“一次会话能完成一个用户可观察行为”为准；过粗先拆，过细合并。

## 四、完成定义：验证与确认双闸门

一次标准 `check` 按顺序执行：

1. `check_harness.py`：状态机、依赖、证据与 trace 的静态/单元检查。
2. `check_privacy.py`：采集、模型出口和链接的隐私边界。
3. 隔离 E2E：临时 `WID_HOME` 中运行 `dev_fixture.py --mock`，再跑 `check_boundaries.py`，结束自动清理。

跨组件、GUI、打包或真实语义改动还要按 `agents/verification.md` 加跑对应测试。单元测试全绿、文件已生成或 agent 自述完成，都不能替代适用的端到端路径。

完成前由未参与实现的人或独立 agent 按 `evaluator-rubric.md` 复验；评审必须实际运行命令、查看 diff 与 trace，不能沿用实现者总结。

## 五、可观测性与隐私

每次 `check/e2e/start/verify/block` 创建 JSONL trace，每条命令是独立 span，验证命令带父 span；完整 stdout/stderr 保存到同目录日志。字段包含时间、trace/span ID、事件、状态、耗时、退出码和日志路径，可据此重放“做了什么、哪步失败”。

这些文件只在本机 `state/harness/`，不得提交。不得把环境变量、配置、API Key、截图或 OCR 文本写进 trace。`verification_commands` 里一旦出现疑似硬编码凭据（`sk-…`、`api_key=…`、Bearer token 等），静态校验会直接拒绝——清单会随仓库提交，命令参数还会原样进 trace。失败反馈必须说明：什么失败、为什么、修哪里。

`feature_list.json` 里的 `evidence[].trace_path` / `log_path` 是**本机相对路径**：克隆里看不到对应文件，只能看到「何时、跑了什么命令、退出码、耗时」。要复核证据必须在本机重跑该功能的 `verification_commands`，不要把这些路径当成可下载的归档。

## 六、下班：干净状态与交接

1. 逐项过 `clean-state-checklist.md`，清理本次临时文件后再跑必要验证。
2. 更新 `feature_list.json`（通过命令）、`PROGRESS.md` 和必要的 `agents/decisions.md`。
3. 长会话更新 `session-handoff.md`，重要会话更新 `quality-document.md`。
4. 本机 `dev/` 永不 commit/push/tag；按既有流程转交提交侧。已提交也不等于已交接。

## 七、Loop 与 Graph 的边界

当前只提供可靠单循环，不安装自动守护循环或图引擎。自动化前必须有：明确目标、独立验证、机器可检查的停止条件、最大轮次/预算、失败回滚或人工升级规则。并行任务必须隔离工作区，数量不得超过人的审阅带宽。

`graph.md` 是当前流程的显式蓝图和升级判据，不代表已引入 LangGraph 等依赖。只有分支、回退、并行、checkpoint 恢复等需求中至少三项真实出现时，才考虑图化。
