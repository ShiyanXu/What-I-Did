# AGENTS.md — What-IDid

> 这是仓库的着陆页。读它能知道往哪走、哪些线不能碰；给出具体命令和行号要按文末路由展开专题，**不要一次通读全部**。
> 提交目录不是这里（`dev/` 永不 commit），发布也不在这里，见 `agents/workflow.md`。

## 这是什么

Windows 本地工具：每 20 秒截一次前台窗口 → 本地 OCR → 按停留时长切成活动块 → 每晚 21:00
把**脱敏后的文字**（不是图片）交给大模型写成 JSON 日报 → 本地模板渲染成静态看板 + Markdown 归档
+ Obsidian 归档。Python 3.10 + tkinter，无第三方 GUI 框架，单 exe 绿色版。

## 五个基本问题

| 问题 | 一句话答案 | 详见 |
|---|---|---|
| **这是什么系统** | 采集与 OCR 全在本机，模型只把「屏幕上出现过什么」翻译成结构化日报，排版、时间、统计一律本地算 | `README.md`、`agents/architecture.md` |
| **怎么组织的** | `wid.py` 单入口（23 个子命令，无参数直接开界面）/ `src/` 33 个模块，按 采集 → 分块 → 行为分段 → 摘要 → 校验 → 渲染 分层；`src/refs/` 负责文档引用解析 | `agents/architecture.md` |
| **怎么运行** | `python wid.py` 开界面；`wid.py daemon` 常驻采集；`wid.py report --date YYYY-MM-DD --no-open` 手动出某天报告 | `agents/environment.md` |
| **怎么验证** | 改任何代码先跑 `check_privacy`（73 项）+ `check_boundaries`（27 项）；发版前 `doctor --full`，必要时 `verify_dpi`（14）/ `gui_regression`（41） | `agents/verification.md` |
| **现在进度如何** | v1.2.2 已构建并换到生产实例，源码未推送；未跑 DPI/GUI 回归 | `PROGRESS.md` |

## 三条最常用命令

Python 必须是 **3.10 那个**（PATH 里的托管版 3.13 没有 tkinter）：

```bash
PY=C:/Users/xushiyan/AppData/Local/Programs/Python/Python310/python.exe
$PY wid.py doctor --full                      # 环境与链路自检
$PY scripts/check_privacy.py && $PY scripts/check_boundaries.py   # 73 + 27 项
$PY wid.py report --date 2026-11-04 --no-open # 重出某天（会调模型）
```

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
8. **`dev/` 里只改和验，不执行 `git commit` / `push` / `tag`。** 提交只在 `W:\What-I-Did`（iMac 侧）。
9. **`W:` 共享对 Windows 侧只允许新建**：改动一律 `cp` 成 `<name>.new`，由 iMac 侧 `mv -f` 覆盖。
10. **zip 永不进 git**（110 MB 超单文件限制），出口是 GitHub Release 资产。
11. **新增 `src/*.py` 必须同步加进 `build.spec:MODULES`**（`i18n*.py` 由 glob 自动收集）。
12. **不要绕过 `publish.py` 的产物检查**：包里混进 `config.yaml` / `config/` / `data/` 会被拒发，
    这时要清理而不是绕过。

## 上班 / 下班

**上班（新会话第一件事）**：读 `PROGRESS.md` → `git status --short`、`git log --oneline -3` 看最近改动 →
跑一次隐私 + 边界自测确认基线是绿的。

**下班（会话结束前）**：更新 `PROGRESS.md` 五段 → 自测全绿 → 清掉临时文件与调试残留 →
按需把改动转载到 `W:`（`.new`）→ **不提交**，把 `mv` / commit / push 的清单交给 iMac 侧。

## 专题文档（按需打开，不要全读）

| 文件 | 什么时候读 |
|---|---|
| `PROGRESS.md` | 每次会话开始、结束时 |
| `agents/architecture.md` | 要改数据流、加模块、动 block/segment/refs 结构时 |
| `agents/conventions.md` | 写新代码、动 i18n、加配置项、改模板时 |
| `agents/verification.md` | 决定「这次改动该跑哪些自测」时 |
| `agents/environment.md` | 跑不起来、OCR 不工作、计划任务/路径/代理出问题时 |
| `agents/workflow.md` | 要发布、要转载到 `W:`、要打 zip、要升版本时 |
| `agents/decisions.md` | 想推翻某个设计（锁、分段、证据档位、文件名策略）之前 |

## 仓库地图

```
dev/                       ← 唯一工作副本（改动都在这里，永不提交）
├── wid.py                 ← 单入口：23 个子命令（无参数即界面），也是打包后的唯一 exe
├── src/                   ← 33 个模块（含 i18n 词条表），见 agents/architecture.md 的分层表
│   ├── templates/         ← Jinja2：day / index / report.md / _style
│   └── refs/              ← 文档引用解析（providers / extract / resolve）
├── scripts/               ← build_dist、check_privacy、check_boundaries、dev_fixture、gui_regression…
├── docs/                  ← 面向使用者的双语文档（会打进发布包）
├── agents/                ← 本目录：给 agent 看的工程文档（不打包）
├── config.default.yaml    ← 配置模板（入库）；config.yaml 是本机实际配置（不入库）
└── data/ reports/ web/ state/ logs/   ← 运行数据（全部 .gitignore）
```
