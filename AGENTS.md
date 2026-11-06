# AGENTS.md — What-IDid

> 这是仓库的着陆页。读它能知道往哪走、哪些线不能碰；给出具体命令和行号要按文末路由展开专题，**不要一次通读全部**。
> 本文件位于**仓库根**，下文所有相对路径都相对仓库根。克隆下来就是全部代码与文档；`data/`、`dist/`、
> `release/`、`config.yaml` 都不在仓库里，属于运行产物。
> 所有者机器上的工作区多一层（工作副本在 `dev/`，另有 `release/` 与 `publish.py` 在工作区根），
> 那些是**本机流程**，不是仓库内容——凡是标了「本机」的段落，克隆里看不到对应路径。

## 这是什么

Windows 本地工具：每 20 秒截一次前台窗口 → 本地 OCR → 按停留时长切成活动块 → 每晚 21:00
把**脱敏后的文字**（不是图片）交给大模型写成 JSON 日报 → 本地模板渲染成静态看板 + Markdown 归档
+ Obsidian 归档。Python 3.10 + tkinter，无第三方 GUI 框架，单 exe 绿色版。

## 五个基本问题

| 问题 | 一句话答案 | 详见 |
|---|---|---|
| **这是什么系统** | 采集与 OCR 全在本机，模型只把「屏幕上出现过什么」翻译成结构化日报，排版、时间、统计一律本地算 | `README.md`、`agents/architecture.md` |
| **怎么组织的** | `wid.py` 单入口（23 个子命令，无参数直接开界面）/ `src/` 34 个模块，按 采集 → 分块 → 行为分段 → 摘要 → 校验 → 渲染 分层；`src/refs/` 负责文档引用解析 | `agents/architecture.md` |
| **怎么运行** | `python wid.py` 开界面；`wid.py daemon` 常驻采集；`wid.py report --date YYYY-MM-DD --no-open` 手动出某天报告 | `agents/environment.md` |
| **怎么验证** | 先跑 `scripts/harness.py check`（Harness 自测 + 隐私 + 隔离 E2E）；发版前 `doctor --full`，必要时按改动加跑 DPI / GUI 回归 | `agents/harness.md`、`agents/verification.md` |
| **现在进度如何** | v1.3.0 已发布（tag `753084c`，Release 资产 1106 条目）；`gui-001` 仍 blocked | `PROGRESS.md` |

## 常用命令

Python 必须是 **3.10 那个**（PATH 里的托管版 3.13 没有 tkinter）：

```bash
PY=C:/Users/xushiyan/AppData/Local/Programs/Python/Python310/python.exe   # 换成你本机带 tkinter 的 3.10
$PY scripts/harness.py check                  # Harness + 隐私 + 隔离 E2E（项数以输出为准）
$PY wid.py doctor --full                      # 环境与链路自检
$PY scripts/check_privacy.py                  # 只跑隐私（不需样本、不联网）

# 边界自测必须隔离：它会清掉并重写样本日的 manual.json、还会写周汇总
mkdir -p .tmp_wid_test
WID_HOME="$PWD/.tmp_wid_test" $PY scripts/dev_fixture.py --day 2026-10-18 --mock
WID_HOME="$PWD/.tmp_wid_test" $PY scripts/check_boundaries.py
rm -rf .tmp_wid_test

$PY wid.py report --date 2026-11-04 --no-open # 重出某天（会调模型）
```

`check_boundaries` 不设 `WID_HOME` 会直接返回 2——这是有意的门禁，别为了跑通去绕过；在真实数据目录里
裸跑会毁掉当天的人工补录。**这些命令在任何机器上都成立**，`PY` 那条路径只是所有者机器的取值。

## 硬约束（违反会出事故，不是建议）

1. **图片永不出本机。** 只发 OCR 文本，且发送前强制脱敏（密钥/手机号/身份证/邮箱）。
2. **零新增 pip 依赖。** 只用标准库 + Pillow / numpy / requests / Jinja2 / PyYAML / psutil。
3. **模型只输出 JSON，排版永远在本地模板。** 模型不许写 URI，只能从给定 `ref_id` 里挑。
4. **`block_id` 必须稳定**：`b-YYYYMMDD-HHMM-<hash4>`，它是 HTML 锚点与外部反引目标；结构只增字段，
   `schema_version` 保持 1。
5. **新配置项必须同时写进 `config.default.yaml` 与 `src/config.py:DEFAULTS`**，否则不做类型校验，
   字符串 `"false"` 会被当成真值。
6. **`t()` / `tn()` 不能被同名局部变量遮蔽**：`t = ...`、`t, s = ...`（解包）、`for t in ...` 会让整个
   函数里的翻译全废，且运行期才炸。
7. **模块级 `t()` 会在导入时冻住首次语言**：常量只存英文源，显示时再翻译（切语言是进程内重建窗口，
   不重新 import）。
8. **本机流程**：所有者机器上只有工作副本 `dev/` 用于改和验，**不做 `git commit` / `push` / `tag`**；
   提交在 `W:\What-I-Did`（iMac 侧）。通用原则是：自测没全绿不提交，提交里不夹带运行产物。
9. **本机流程**：`W:` 共享对 Windows 侧**不能改名/删除**（`git` 的 write-temp+rename 因此必失败）——
   改动一律 `cp` 成 `<name>.new`，由 iMac 侧 `mv -f` 覆盖。普通文件可以原地覆盖，但覆盖会丢可执行位。
   成批改动（十几项以上）改打 `tar.gz` 一次性投放，iMac 侧 `tar -xzf` 解包，比逐个 `mv` 不易漏。
10. **zip 永不进 git**（110 MB 超单文件限制），出口是 GitHub Release 资产。
11. **新增 `src/*.py` 必须同步加进 `build.spec:MODULES`**（`i18n*.py` 由 glob 自动收集）。
12. **本机流程**：不要绕过 `publish.py` 的产物检查——包里混进 `config.yaml` / `config/` / `data/` 会被拒发，
    这时要清理而不是绕过。等价的手工检查：zip 内不得出现这三类路径。

## 上班 / 下班

**上班（新会话第一件事）**：读 `PROGRESS.md` → `git status --short`、`git log --oneline -3` 看最近改动 →
按 `agents/harness.md` 运行 `init.sh` 与 `harness.py status`，从功能清单接续，不凭聊天记录猜。

**下班（会话结束前）**：逐项过 `clean-state-checklist.md` → 更新 `PROGRESS.md` 五段与必要的
`session-handoff.md` → 自测全绿 → 清掉临时文件与调试残留 →
需要交付的改动转到提交侧（本机是转载成 `W:` 上的 `.new`）→ **不自行 commit**，把 `mv` / commit / push
的清单写清楚交给能提交的人。

## 专题文档（按需打开，不要全读）

| 文件 | 什么时候读 |
|---|---|
| `PROGRESS.md` | 每次会话开始、结束时 |
| `agents/harness.md` | 初始化、选功能、改功能状态、留验证证据或交接前 |
| `sprint-contract.md` | 开工前定这一轮的范围、排除项和停止条件；范围变了先改它 |
| `clean-state-checklist.md` / `session-handoff.md` | 收尾自检；长会话交接前 |
| `evaluator-rubric.md` / `quality-document.md` | 独立评审打分；重要会话后调质量评级 |
| `graph.md` | 想引入自动循环、并行或步骤恢复之前 |
| `agents/architecture.md` | 要改数据流、加模块、动 block/segment/refs 结构时 |
| `agents/conventions.md` | 写新代码、动 i18n、加配置项、改模板时 |
| `agents/verification.md` | 决定「这次改动该跑哪些自测」时 |
| `agents/environment.md` | 跑不起来、OCR 不工作、计划任务/路径/代理出问题时 |
| `agents/workflow.md` | 要发布、要转载到 `W:`、要打 zip、要升版本时 |
| `agents/decisions.md` | 想推翻某个设计（锁、分段、证据档位、文件名策略）之前 |

## 仓库地图

```
.                          ← 仓库根（本文件所在处）
├── wid.py                 ← 单入口：23 个子命令（无参数即界面），也是打包后的唯一 exe
├── src/                   ← 34 个模块（含 i18n 词条表），见 agents/architecture.md 的分层表
│   ├── templates/         ← Jinja2：day / index / report.md / _style
│   └── refs/              ← 文档引用解析（providers / extract / resolve）
├── scripts/               ← harness、build_dist、check_privacy、check_boundaries、dev_fixture…
├── feature_list.json      ← 机器可读功能状态；只能由 scripts/harness.py 改状态
├── init.sh                ← 依赖 + 完整检查 + 标准启动入口
├── sprint-contract.md     ← 冲刺目标、范围、验证标准、排除项与停止条件
├── clean-state-checklist.md   ← 收尾逐项自检
├── session-handoff.md     ← 长会话交接（短会话写 PROGRESS.md 即可）
├── evaluator-rubric.md    ← 独立验收评分表
├── quality-document.md    ← 各区域质量快照（变强还是变弱）
├── graph.md               ← 单循环路由蓝图与图化升级判据
├── docs/                  ← 面向使用者的双语文档（会打进发布包）
├── agents/                ← 本目录：给 agent 看的工程文档（不打包）
├── requirements.txt       ← 运行与构建依赖的版本基线
├── config.default.yaml    ← 配置模板（入库）；config.yaml 是本机实际配置（不入库）
└── data/ reports/ web/ state/ logs/ dist/   ← 运行产物，全部 .gitignore，克隆里没有
```

**本机（所有者机器）额外多出来的东西，克隆里不存在**：工作副本 `dev/`、发布产物 `release/v<版本>/`、
打包脚本 `publish.py`、工作区分工说明 `WORKSPACE.md`、生产实例 `D:\What-IDid`、SMB 上的提交源
`W:\What-I-Did`。要从头复现发布流程，凭仓库里的 `agents/workflow.md` 第五节即可，不需要这些文件。

若 `git status --short` 里出现上表文件是未跟踪（`??`）或已修改（`M`），只说明**本机改动还没经提交侧入库**：
以磁盘为准继续工作，不要回退、不要重建、也不要据此认为文档失实；入库流程见 `agents/workflow.md`。
