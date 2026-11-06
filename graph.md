# Harness 单循环图蓝图

> 这是当前流程的可审计蓝图，不是图引擎配置。当前不引入 LangGraph、并行 agent 或自动 commit；节点间共享仓库文件，不共享实现者的完整对话。

## 节点与主干

```text
init → select → implement → verify → handoff
  ↑        ↑          ↑          │
  │        │          └──────────┤ fail
  │        └─────────────────────┤ needs re-scope
  └──────────────────────────────┴ environment failure
```

| 节点 | 类型 | 输入 | 输出/写入 | 完成条件 |
|---|---|---|---|---|
| `init` | 确定性脚本 | 仓库、Python 3.10 | 基线结果、启动命令、trace | Harness/隐私/隔离 E2E 全绿 |
| `select` | 确定性脚本 | feature 状态与依赖 | 唯一 `in_progress` | WIP=1 且依赖全 passing |
| `implement` | agent | 当前行为、范围、仓库规则 | 仅限当前功能的改动 | 实现可供验证；不自行判 passing |
| `verify` | 独立命令或 evaluator | 当前 diff、验证命令 | 通过 evidence 或失败 trace | 所有适用验证层级成功 |
| `handoff` | agent + 脚本 | 状态、trace、风险 | PROGRESS、功能状态、交接 | 干净收尾清单全部适用项通过 |

`blocked` 是显式人工节点：记录原因和解除条件，停止自动推进；解除后只能经 `resume` 重新通过 WIP 与依赖门控回到实现。`merge/commit/push` 不在本机图中，仍由既有 iMac 提交侧执行。

## 边与路由

| 当前节点 | 条件 | 下一节点 |
|---|---|---|
| `init` | 任一基线失败 | `init` 修复环境；禁止进入新功能 |
| `select` | 已有进行中项 | 返回该项，不激活新项 |
| `select` | 依赖未通过或目标 passing | 拒绝启动，重新选择 |
| `implement` | 范围不清 | `select` 重新拆分/界定，不扩大实现 |
| `verify` | 任一命令失败 | `implement`，保持 `in_progress` |
| `verify` | 全部成功 | `handoff`，由验证器写 passing/evidence |
| 任意节点 | 需人工决策、数据风险或权限边界 | `blocked` 并停止 |
| `handoff` | 状态或清理不合格 | 返回对应节点；不得直接结束 |

每个 agent 节点的内部重试必须有任务级上限；当前合同没有授权无限循环。失败证据保留，不能靠重试覆盖历史。

## 共享状态与合并规则

| 字段/文件 | 写入者 | 合并规则 |
|---|---|---|
| `feature_list.json:status` | `harness.py` | 单 writer 原子替换；passing 不可逆 |
| `feature_list.json:evidence` | `harness.py` | 仅全验证成功后追加；失败不写通过证据 |
| `PROGRESS.md` | 当前会话 | 会话结束整体更新；并行会话不得共写 |
| `agents/decisions.md` | 当前会话 | 新决策追加；修改旧决策必须写明替代关系 |
| `state/harness/*.jsonl` | 当前 trace | 只追加；每次运行独立 trace_id |
| `session-handoff.md` | 当前长会话 | 最新交接整体替换，历史进度仍在 PROGRESS/git |

当前禁止并行写共享状态，因此不需要复杂 reducer。未来若引入并行，必须先定义字段级覆盖/追加/求和规则和冲突处理。

## Checkpoint、观测与人工审批

- `init/select/verify/block` 的状态变化落盘；命令轨迹写 JSONL，完整输出写同目录日志。
- trace_id 区分运行，span_id 区分命令，parent_span_id 表示验证从属；可从最近状态继续，而不是重放实现者私有上下文。
- checkpoint 不能修正错误目标或错误指标；独立 evaluator 和人工抽查仍是现实锚点。
- 涉及真实数据、外发、发布、删除或用户既有改动时，必须停下人工审批。

## 何时才升级为图工程

以下至少三项真实出现时再评估图引擎：可独立并行的工作单元；值得显式声明的分支/回退；中间状态需要停止恢复；每节点都有自动完成标准；协作收益大于协调与审阅成本。线性流程或仅步骤较多，不构成图化理由。
