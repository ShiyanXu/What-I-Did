# 架构

本文说明 What-IDid 的整体结构、数据如何在各模块间流动，以及几个不容易改的设计取舍。只想用起来读 [README](../README.zh-CN.md) 和[使用指南](usage.md) 即可。

## 设计原则

| 原则 | 具体做法 | 代价 |
| --- | --- | --- |
| 图片不出本机 | 截图、OCR 全部本地完成，只有脱敏后的文本进入模型请求 | OCR 质量受本机引擎限制；换机器要重装模型 |
| 模型只产内容，不产格式 | 模型返回固定 schema 的 JSON，排版由本地 Jinja2 模板决定 | 模板改动不受模型能力影响，但也要自己维护 |
| 时间由本地决定 | 活动块、行为段的起止与切分全部本地计算，模型只能贴标签 | 模型无法"修正"它认为错误的时间 |
| 单一判断来源 | "该不该出报"只有 `tick.pending()` 一处实现，界面、计划任务、诊断都调它 | 加新触发方式时必须复用它，不能另写一份 |
| 只增不减的兼容策略 | 报告 `schema_version` 保持 1，只加字段；旧日报缺字段按空处理 | 老数据不会自动重算，口径修正不影响历史 |
| 不引额外依赖 | 运行时只需标准库 + Pillow/numpy/requests/Jinja2/PyYAML/psutil；托盘用 ctypes 手写 | 一些功能要自己实现（托盘、去重、调度） |

## 运行形态

整个程序是一个 exe，靠命令行参数分成三种角色：

| 角色 | 启动方式 | 特点 |
| --- | --- | --- |
| 图形界面 | 双击，或 `What-IDid.exe gui` | 无控制台窗口；关窗口只收进托盘 |
| 命令行 | `What-IDid.exe <子命令>` | 以无控制台方式链接，运行时 `AttachConsole` 接回调用方控制台 |
| 常驻采集 | 程序自己拉起 `What-IDid.exe daemon` | 无窗口，单实例，带看门狗 |

几个约束：

- **单实例**：采集进程用命名互斥量保证只有一个；崩溃时由系统释放，不会留下"僵尸锁"。界面进程不占这个锁，可以重复打开，重复双击会把已收进托盘的窗口叫回来。
- **界面不负责采集**：界面只读写状态、发起命令；进程被关掉不影响记录。要停采集必须显式 `stop` 或用托盘菜单的退出。
- **托盘独立线程**：托盘窗口建在自己的线程里跑消息循环。放在 Tk 主线程里会因为 tkinter 在事件循环中释放 GIL 而崩在 `PyEval_RestoreThread`，这是踩过的坑。

系统里最多注册两个计划任务：

| 任务 | 触发 | 作用 |
| --- | --- | --- |
| `WhatIDid-Recorder` | 登录时 | 常驻采集；无执行时限，被杀后每分钟重启 |
| `WhatIDid-Report` | 每天 21:00 起每 20 分钟，共 2 小时；登录后 3 分钟；次日 04:05 | 跑 `tick`，幂等；**不依赖采集进程** |

两个任务都以"仅在用户登录时运行"注册。若改成"不管用户是否登录"，进程会落在 Session 0，那里没有桌面，截出来是全黑的。

## 数据流

| 步骤 | 输入 | 输出 | 主要模块 |
| --- | --- | --- | --- |
| 采样 | 定时器（20±3 秒） | 一条事件 + 可选截图 | `capture.py` |
| 去重 | 截图指纹 | 保留 / 丢弃 | `vision.py` |
| 识别 | 活动窗口原生分辨率截图 | 脱敏文本 | `ocr.py` |
| 分块 | 事件流 | 活动块 `b-YYYYMMDD-HHMM-xxxx` | `blocks.py` |
| 分段 | 活动块 | 行为段 `seg-NN` | `behavior.py` |
| 引用 | 标题与 OCR 文本 | `ref_id` 与可点链接 | `refs/` |
| 压缩 | 块、段、统计、引用、人工补录 | 受字符预算约束的 payload | `digest.py` |
| 推理 | payload + 提示词 | 严格 JSON | `llm.py` |
| 校验 | 模型 JSON | 结构化日报或问题清单 | `validate.py` |
| 落盘 | 日报数据 | `reports/<日期>.json/.md`、`web/` | `render.py`、`report.py` |

三段值得展开：

**采样与去重。** 每次采样读前台窗口的句柄、标题、进程名、矩形，以及空闲秒数与锁屏状态。截图默认只抓活动窗口，用 dHash 汉明距离与 12×8 网格均值差共同判断"是否变化"；变化帧落盘，画面长期不变时按 `keyframe_sec` 留关键帧，保证时间轴不断档。窗口矩形拿不到或裁剪失败时**跳过这一帧，不回退全屏**——宁可少记，不可错记。

**分块与分段。** 相邻事件间隔超过 `gap_threshold_min` 就断开；单块最长 `max_block_min`（默认 30 分钟）强制切开；块内被暂停、锁屏、名单屏蔽覆盖的区间从 `active_seconds` 中扣除，单独记为 `idle_minutes`。行为段进一步按主分类变化切分，单段最长 `max_session_min`，并补 `start_iso` / `end_iso` 以避免跨午夜错位。

块的 ID 是 `b-YYYYMMDD-HHMM-<hash4(app+title)>`，只由当天日期、块起始分钟与应用/标题决定。**重跑、补数据、重新分块都不会改变它**，所以它可以作为 HTML 锚点，也可以被外部文档反向引用。

**压缩与校验。** `digest.py` 把一天压进 `llm.input_char_budget`（默认 24,000 字符）：先给统计、块清单、行为段、引用分配额度，逐档裁剪，最后兜底截断并注明截掉了什么。模型返回后 `validate.py` 检查每个 `block_id` / `ref_id` / `seg_id` 是否真实存在，缺失或伪造的引用记入 `issues`；JSON 结构坏掉时把错误回灌重试一次，仍失败则降级。

## 时间口径

- **工作日不是自然日**：一天从 `app.day_cutoff_hour`（默认 04:00）开始，到次日 04:00 结束。凌晨 2 点干的活算前一天，避免熬夜把一天劈成两半。所有"今天""昨天"都走同一套时间工具。
- **观测时长 ≠ 工时**：`active_seconds` 是墙钟跨度减去暂停与锁屏区间。它仍然只是"有屏幕活动"的度量，阅读、看课这类没有输入的动作会被低估。
- **行为段用墙钟**："连续工作多久被打断"看的是墙钟，不扣除空闲。
- **节奏指标按行为段算**：活动块会被 30 分钟上限截断，用它算"最长连续专注"永远只能得到 30 分钟。

## 出报调度与状态

出报状态被拆成四个独立维度，存在 `state/day/<日期>.json`：

| 字段 | 含义 |
| --- | --- |
| `coverage_until_iso` | 这份报告的数据覆盖到哪个时刻 |
| `status` | `ok` / `degraded` / `empty`，模型是否真的成功 |
| `input_hash` | 事件流与 OCR 流的指纹，迟到数据会改变它 |
| `revision` | 第几版 |

拆开的原因：只看"出过报"这一个布尔值，一份"覆盖完整但模型失败"的降级版会永远不再升级。现在四个维度分开判断，降级版在 `report.degraded_retry_hours` 内、且未超过 `report.max_upgrade_attempts` 时会自动重试升级成 AI 版。

触发链：

```
WhatIDid-Report 任务 → tick.run() → tick.pending(cfg, now, days) → 逐天 report.generate()
界面提示 / doctor  ─┘                （唯一的"该不该出报"判断）
采集进程巡检 ───────┘
```

`tick.pending()` 同时处理这些情况：已覆盖到窗口末尾就跳过；有新增覆盖且距上次出报超过 `refresh_gap_min` 才允许增量更新；存在新鲜待审内容时返回"等待审核确认"而不是"漏报"；当天完全没数据不生成空日报。

## 模块职责

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 采集 | `capture.py` | 采样、隐私判断、截图、去重、落盘、投递 OCR |
| | `winapi.py` | DPI 感知、前台窗口、进程名、空闲、锁屏、显示器几何 |
| | `vision.py` | dHash、网格指纹、分类、降采样、原子写 |
| | `ocr.py` | 引擎选择与降级、队列、配额、看门狗、恢复探测 |
| 加工 | `blocks.py` | 事件 → 活动块，稳定 ID，统计 |
| | `behavior.py` | 活动块 → 行为段；动词词表是唯一来源 |
| | `digest.py` | 压缩进预算；提示词 |
| | `refs/` | 引用抽取、解析、去重、缓存 |
| 出报 | `llm.py` | OpenAI 兼容客户端、调用预算、出口脱敏 |
| | `validate.py` | 结构校验与问题清单 |
| | `report.py` | 编排、状态、审核、降级与升级 |
| | `tick.py` | 计划任务入口与唯一判断 |
| 呈现 | `render.py` | Markdown 与静态看板渲染 |
| | `templates/` | Jinja2 模板 |
| | `gui.py` / `tray.py` | 图形界面与托盘 |
| 数据 | `paths.py` | 路径单一来源，禁止在别处拼路径 |
| | `config.py` | 配置加载、校验、原子保存、失效处置 |
| | `privacy.py` | 采集许可、匿名事件、脱敏、回环地址校验 |
| | `retention.py` | 滚动清理 |
| | `forget.py` | 遗忘 |
| | `manual.py` | 人工补录与改标 |
| | `search.py` / `weekly.py` / `export_safe.py` | 检索、周汇总、分享版 |
| 基础设施 | `daemon.py` | 常驻采集、单实例、看门狗、每日清理 |
| | `control.py` / `status.py` / `doctor.py` | 启停、状态快照、自检（CLI 与界面共用） |
| | `autostart.py` | 计划任务与注册表降级 |
| | `locking.py` / `logging_setup.py` / `timeutil.py` | 互斥量、日志、时间 |

## 数据模型

**事件**（`data/<日期>/events.jsonl`，每行一条）：

```json
{"v": 1, "ts": "2026-10-19T09:12:00+08:00", "day": "2026-10-19",
 "app": "code.exe", "title": "report.py - What-IDid", "pid": 1001,
 "rect": [-8, -8, 1928, 1048], "idle": 12.0, "state": "active",
 "seq": 1, "cls": "tick", "diff": 0.01, "hash": "f2a74de452e6b438", "kind": "tick"}
```

`state` 取值包括 `active` / `idle` / `paused` / `locked`；暂停与屏蔽状态下只写匿名刻度，不含应用名与标题。

**活动块**（`reports/<日期>.json` 的 `blocks[]`）：

```json
{"id": "b-20261019-0912-0d56",
 "start": "2026-10-19T09:12:00+08:00", "end": "2026-10-19T09:41:40+08:00",
 "app": "code.exe", "title": "report.py - What-IDid", "category": "dev",
 "minutes": 30, "content_policy": "allowed", "shots": [], "text": "", "switch_count": 0}
```

`content_policy` 三档：`allowed`（完整）、`metadata_only`（只有应用与时长）、`none`（匿名）。它在块生成时就算好，重新出报时按**当前**隐私配置重算。

**日报**（`reports/<日期>.json`）顶层：`schema_version`、`day`、`weekday`、`generated_at`、`revision`、`status`、`reason`、`coverage`、`report`、`stats`、`gaps`、`blocks`、`refs`、`manual`、`meta`。

其中 `report` 是模型产出的结构化内容（`headline`、`opening_md`、`summary_md`、`behaviors`、`highlights`、`followups`、`friction`、`insight`、`timeline`、`accomplishments` 等），`meta` 记录模型、调用次数、token、payload 大小与校验问题。

阅读顺序由渲染层派生，不写死在模型输出里。`render.reading_view()` 是唯一的派生入口：

- `highlights` 每条带 `status`（`done|advanced|waiting|blocked|unresolved`）与 `next`，渲染成首屏成果卡；旧日报没有这两个字段时按文字推断，判定函数只有 `behavior.outcome_status()` 一处。
- `followups` 是需要行动的事；旧日报没有时由 `friction`（未解决）与 `tomorrow` 合成。
- 主时间轴由 `behavior.group_tasks()` 把 `behaviors` 合并成 4–6 段（同动词、断档 ≤25 分钟、单段 ≤150 分钟），锁屏/离开段排除。
- 活动块、原始色带、全部文档、OCR、数据缺口、生成信息一律进页面底部折叠附录；正文只保留挂在成果卡上的文档与最多 3 张截图。

**人工补录**（`data/<日期>/manual.json`）：

```json
{"offline": [{"start": "10:00", "end": "11:00", "label": "线下客户沟通", "note": ""}],
 "behavior_overrides": [{"seg_id": "seg-03", "verb": "meeting", "label": "复盘会"}],
 "notes": "今天在等接口"}
```

它与自动产物完全分离：重跑、清理都不会动它，每次出报重新套用一遍，**用户修订永远赢**。

## 存储布局

```
<数据目录>\
  config.yaml              配置源
  config\api_key.txt       模型 Key（或环境变量 WID_LLM_API_KEY）
  config\bindings.yaml     手工把某个项目固定挂到某份文档
  data\<日期>\
    events.jsonl           事件流
    ocr.jsonl              脱敏后的识别结果
    manual.json            人工补录与改标
    shots\ thumbs\         变化帧与缩略图
  reports\<日期>.md/.json  日报
  reports\share\           对外分享版
  reports\weekly\          周汇总
  web\                     静态看板（index.html 与 day\*.html）
  state\                   心跳、每日状态、待审预览、锁、暂停标记
  logs\                    采集与出报日志（5 MB 滚动）
```

数据目录的解析顺序：`WID_HOME` → 程序目录（可写则用，便携）→ `%LOCALAPPDATA%\What-IDid`。程序资源（模板、默认配置、内置模型）走只读的安装目录，运行数据全部写入上述目录，两者不混。

## 扩展点

| 想做什么 | 改哪里 |
| --- | --- |
| 加一种文档来源 | 在 `src/refs/providers.py` 写一个 `BaseProvider` 子类，实现 `probe()` / `resolve()`，把名字加进 `refs.providers`。数据 schema、页面、日报逻辑都不用动 |
| 接知识库 | 实现 `KBProvider.resolve()` 返回 `kb://<collection>/<doc_id>#<anchor>`，再填 `refs.kb.url_template` |
| 手工绑定文档 | 在 `config/bindings.yaml` 写 `title_contains` 规则，优先级高于自动解析 |
| 加一种采集方式 | `capture.py` 的采样入口是唯一入口，把新来源拼成同样的事件结构即可，后续模块不关心来源 |
| 换排版 | 只改 `src/templates/`。模板里不能用 `trim_blocks` 看不见的换行，排版空行交给渲染后的后处理 |
| 加子命令 | `wid.py` 里注册，并在 `build.spec` 的 `MODULES` 里补上新模块名——那份清单是手写的，静态分析看不到按需导入 |
