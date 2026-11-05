# 架构：分层、数据流与不变量

> 读完这份应该能回答「改一个功能要动哪几个文件」和「哪些结构不能碰」。
> 只看代码看不出来的取舍，在 `decisions.md`。

## 一、四层结构（依赖只能向下）

```
入口  wid.py  ── 23 个子命令（无参数即界面），也是打包后唯一的 exe（带子命令即 CLI，双击即界面）
  │
编排  daemon / tick / report / retention / weekly / search / manual / forget / export_safe
  │        （决定「什么时候做什么」，不实现具体算法）
  │
能力  capture → ocr → blocks → behavior → digest → llm → validate → render
  │   winapi / vision / refs / privacy / timeutil / config / paths / logging / locking
  │
输出  templates（Jinja2）→ web/（看板）+ reports/（Markdown）+ Obsidian 归档
  │
界面  gui / tray / control / status / doctor / autostart   ← 与 CLI 共用同一套判定函数
```

**原则：判定只有一处实现。** 例如「今天该不该出报」只有 `tick.pending()`；「当前什么状态」只有
`status.snapshot()`；「环境是否就绪」只有 `doctor.run()`。CLI 与 GUI 都调它们，不允许各写一套
（历史上因此出现过界面说「正常」而 CLI 说「失败」）。

## 二、模块职责

### 采集与感知
| 模块 | 职责 | 关键点 |
|---|---|---|
| `capture.py` | 前台窗口 + 截图 + 去重 + 落盘 + 事件 | 20 秒一轮；pHash/网格指纹去重；只裁活动窗口原生分辨率 |
| `winapi.py` | DPI、前台窗口、进程名、空闲时长 | 纯 ctypes，不引 pywin32；空闲用 `GetTickCount()` |
| `vision.py` | pHash、网格指纹、图像分类、降采样、原子写 | 图片只在本机流转 |
| `ocr.py` | Umi-OCR HTTP 为主、CLI 为备、内置 RapidOCR 兜底 | `ocr.engine: auto` 自动选；RapidOCR 约 5s/张 |

### 数据与配置
| 模块 | 职责 | 关键点 |
|---|---|---|
| `paths.py` | 路径单一来源 | 三级：`WID_HOME` → 程序目录（可写则便携）→ `%LOCALAPPDATA%`；只读资源走 `paths.BUNDLE` |
| `config.py` | 配置合并与类型校验 | `DEFAULTS` 必须与 `config.default.yaml` 同步 |
| `timeutil.py` | 时间口径 | 全项目 +08:00；「工作日」起点 04:00；21:00 触发 = 次日 01:00 |
| `privacy.py` | 隐私边界 | 采集许可、脱敏、外发前置校验；每次请求前重新校验 |
| `locking.py` | 单实例锁 | Windows 命名互斥量，崩溃自动释放；`wait=` 支持重试 |
| `logging_setup.py` | 日志滚动 | 窗口化 exe 里 stdout 是 None，入口兜 devnull |

### 分析与生成
| 模块 | 职责 | 关键点 |
|---|---|---|
| `blocks.py` | 事件流 → 活动块 | **`block_id` 稳定**是锚点与引用目标；分类只是粗聚类 |
| `behavior.py` | 活动块 → 行为段 | 时间/编号**本地定**，模型只填 verb/label/detail/confidence；`VERBS` 是唯一动词表 |
| `digest.py` | 块 → 模型输入 + 提示词 | 字符预算硬上限；证据三档写在 `SYSTEM_PROMPT` |
| `llm.py` | OpenAI 兼容客户端 | 一个 POST，不引 SDK；每次请求过隐私闸门 |
| `validate.py` | 模型 JSON 校验 | 引用/块 id 必须真实存在；非法 verb 回落本地猜测 |
| `render.py` | 渲染 | Markdown + 静态看板；`_fix_md()` 修 Jinja 空行 |

### 引用解析（`src/refs/`）
`extract.py` 从标题与 OCR 文本里抽可能的文档引用 → `resolve.py` 调度（手工绑定 → providers 链 → 去重）→
`providers.py` 各来源解析器。**新增一种文档来源 = 写一个 `BaseProvider` 子类并登记到
`config.refs.providers`**，不要改调度器。

### 编排
`daemon.py`（常驻采集：单实例、看门狗、兜底触发）、`tick.py`（该不该出报 + 重试配额）、
`report.py`（一天的完整链路）、`retention.py`（滚动清理原始数据）、`weekly.py`（周汇总）、
`search.py`（跨日检索）、`manual.py`（人工修订/线下补录）、`forget.py`（遗忘某段时间）、
`export_safe.py`（对外分享版）。

### 界面
`gui.py`（tkinter 四页：概览/日报/设置/诊断）、`tray.py`（ctypes 调 `Shell_NotifyIcon`）、
`control.py`（启停采集）、`status.py`、`doctor.py`、`autostart.py`（注册三条计划任务）。
`i18n.py` + `i18n_zh_cn.py`：英文是源语言，中文是词条表。

## 三、一天的数据流

```
每 20s  capture.collect()      → data/<day>/events.jsonl（窗口、标题、进程、时长）
        ocr.recognize()        → data/<day>/ocr.jsonl（裁窗口后识别，失败记 failed）
        vision 去重            → data/<day>/shots/ + thumbs/（相同画面不重复识别）

21:00   tick.pending()         → 哪些天该出报、为什么（稳定代码）
        report.generate()
          blocks.build()       → 活动块（稳定 block_id）
          refs.resolve()       → 文档引用（ref_id）
          digest.build_payload → 统计 + 块清单 + 行为段 + 重点块详情（≤24000 字符）
          llm.chat_json()      → 一个 JSON 对象
          validate.check()     → 结构、引用、block_ids 真实性
          render.render()      → reports/<day>.md + web/day/<day>.html
        export_safe / Obsidian 归档（可选）
```

## 四、数据落点

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `data/<day>/events.jsonl` | 采集事件（窗口/标题/进程/停留） | 否 |
| `data/<day>/ocr.jsonl` | 逐块识别文本 | 否 |
| `data/<day>/shots/`、`thumbs/` | 截图与缩略图 | 否 |
| `reports/<day>.json` | 日报结构化数据（唯一真相） | 否 |
| `reports/<day>.md` | Markdown 归档 | 否 |
| `web/` | 静态看板（index + day 页） | 否 |
| `state/` | 心跳、锁、修订号、重试配额 | 否 |
| `logs/` | 日志 | 否 |
| `config.yaml`、`config/` | 本机配置与密钥 | 否（模板 `config.default.yaml` 入库） |

## 五、不能碰的不变量

1. **`block_id`**：`b-YYYYMMDD-HHMM-<hash4(app+title)>`，HTML 锚点与外部引用都指着它。只增字段，不改构成。
2. **`seg_id`**：`seg-NN`，本地按连续工作切好，模型不许新增/合并/改顺序。
3. **`ref_id`**：只能来自当次给定的引用清单，模型不许自造 URI。
4. **`schema_version`**：保持 `1`；新增字段可以，改语义不行。
5. **时间**：起止时间一律本地取，模型给的时间一律丢弃。
6. **统计数字**：`stats` 由本地算好并在渲染前强制覆盖模型输出。
