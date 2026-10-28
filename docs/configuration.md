# 配置参考

配置只有一个文件：数据目录下的 `config.yaml`，由 `config.default.yaml` 在首次运行时复制生成。

## 位置与优先级

```
数据目录\
  config.yaml            主配置
  config\api_key.txt     模型 Key
  config\bindings.yaml   手工文档绑定（可选）
```

数据目录按 `WID_HOME` → 程序目录（可写则用）→ `%LOCALAPPDATA%\What-IDid` 的顺序解析。模型 Key 的读取优先级是：环境变量 `WID_LLM_API_KEY` > `config\api_key.txt` > 界面里保存的值。

## 生效时机

| 类别 | 何时生效 |
| --- | --- |
| 隐私策略、外发开关 | 每次采集、每次请求前都会重读 |
| 采样、去重、OCR 引擎参数 | 需要重启采集进程（界面保存会自动重启） |
| 出报钟点 `report.trigger_hour` | 需要**重新注册计划任务**——触发时刻在注册时写死在系统里 |
| 模型地址、模型名、预算 | 下一次出报 |

## 通用

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `app.day_cutoff_hour` | `4` | 一天从几点开始算。凌晨 0–4 点归前一天，熬夜不劈成两天 |
| `app.timezone` | `Asia/Shanghai` | 全项目统一时区，报告里的时刻都用它 |

## 采集 `capture`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `interval_sec` | `20` | 采样间隔 |
| `jitter_sec` | `3` | 随机抖动，避免整齐得像机器人 |
| `max_width` | `1920` | 落盘宽度上限 |
| `jpeg_quality` | `62` | 可读性与体积的平衡点 |
| `thumb_width` | `480` | 缩略图宽度（日报页用） |
| `keyframe_sec` | `300` | 画面长期不变时也每 5 分钟留一张，保证时间轴不断 |
| `idle_pause_sec` | `300` | 无键鼠超过 5 分钟停止落盘 |
| `pause_on_locked` | `true` | 兼容字段。现在锁屏始终停采集，写 `false` 也无法绕过 |
| `min_free_gb` | `3` | 磁盘低于此值停止落盘，心跳标记 `low_disk` |
| `min_save_gap_sec` | `90` | 轻微变化时的最小落盘间隔 |

## 去重 `dedupe`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `hamming_threshold` | `4` | dHash 汉明距离 ≤ 4 视为相似 |
| `cell_grid` | `[12, 8]` | 网格均值差的切分粒度 |
| `cell_diff_dup` | `0.02` | 网格差 < 2% 判为完全重复 |
| `cell_diff_major` | `0.22` | 网格差 > 22% 判为大幅变化 |

## OCR `ocr`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `engine` | `auto` | `auto` / `umi_http` / `umi_cli` / `rapidocr` / `none` |
| `umi_exe` | `""` | 已装 Umi-OCR 就填它的路径，能显著提速（需在 Umi 里开启 HTTP 服务） |
| `rapidocr.max_side` | `1600` | 检测边长上限，越大越准也越慢 |
| `rapidocr.min_score` | `0.5` | 低于此置信度的行直接丢弃 |
| `warmup_on_start` | `true` | 启动时预热引擎，避免第一张图等很久 |
| `crop_active_window` | `true` | 用原生分辨率裁活动窗口再识别，小字更准 |
| `min_interval_same_window_sec` | `45` | 同一窗口两次识别的最小间隔 |
| `periodic_sample_sec` | `180` | 同一窗口停留很久也每 3 分钟抽一张 |
| `max_per_minute` | `6` | 每分钟识别上限 |
| `max_per_day` | `1200` | 每天识别上限 |
| `max_chars_per_shot` | `6000` | 单张图取多少字 |
| `timeout_sec` | `90` | 单次识别超时 |
| `recover_probe_sec` | `600` | 降级到兜底引擎后，每隔这么久重探测一次更靠前的引擎 |
| `http_url` | `http://127.0.0.1:1224/api/ocr` | 仅允许回环地址；不走系统代理、不跟随重定向 |

`auto` 的顺序是"有 Umi-OCR 就先用它，否则用内置 RapidOCR"；两者都不可用时退化为只记窗口标题。

## 隐私 `privacy`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `store_screenshots` | `true` | 设为 `false` 时不落图，正式采集也不 OCR，只留脱敏标题 |
| `capture_scope` | `active_window` | `active_window` / `all_screens`。窗口不可用时跳过，**不回退全屏** |
| `allow_llm` | `true` | `false` 禁止一切模型请求（含连接测试），本地记录与时间线日报照常 |
| `blocked_apps` | 密码管理器若干 | 精确进程名，不区分大小写。只留匿名状态，不存应用、标题、图像与 OCR |
| `skip_ocr_apps` | 微信/QQ 等 | 只记应用与观测时长：不存标题、不截图、不 OCR；重新处理旧数据时同样过滤 |
| `blocked_title_patterns` | 密码/支付/银行等 | 标题命中正则即匿名化；**非法正则会让配置失效**，不会静默忽略 |
| `redact_patterns` | `[]` | 自定义脱敏正则，落盘、历史重读、模型出口都会应用 |

`sk-`、GitHub token（含 `github_pat_`）、身份证、手机号、邮箱有不可关闭的内置规则。正则不能识别全部敏感内容，也不会给截图像素打码——敏感应用请屏蔽或暂停。

## 保留与清理 `retention`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `shots_days` | `7` | 截图与缩略图保留天数 |
| `events_days` | `30` | 事件流与脱敏 OCR 保留天数 |
| `report_excerpt_days` | `null` | 报告附录里的 OCR 摘录：`null` 跟随 `events_days`；`0` 表示不写摘录并在清理时清旧副本 |
| `keyframes_days` | `30` | 兼容字段，关键帧尚未单独留存 |
| `max_total_gb` | `20` | 清理时按最旧优先删截图，**不是所有数据的硬上限** |
| `clean_hour` | `4` | 采集进程每天此钟点后清理；采集没在跑就不会自动清理 |

清理由运行中的采集进程执行，不是独立的计划任务。清理预览不写文件；与出报互斥，忙或部分失败会提示重试。

## 模型 `llm`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `base_url` | `https://api.deepseek.com/v1` | 任何 OpenAI 兼容端点 |
| `model` | `deepseek-chat` | 模型名 |
| `api_key_file` | `config/api_key.txt` | 相对数据目录 |
| `api_key_env` | `WID_LLM_API_KEY` | 优先级高于文件 |
| `timeout_sec` | `180` | 单次请求超时 |
| `max_retries` | `3` | 每轮最多尝试次数（含第一次） |
| `input_char_budget` | `24000` | 输入字符**硬上限**，固定区也会被逐档裁剪，最后兜底截断 |
| `max_calls_per_report` | `12` | 单份日报的调用数上限（含修复轮），超限直接降级 |
| `temperature` | `0.2` | 低温度，减少叙述漂移 |

换成通义、智谱、硅基流动或本地 Ollama，只改 `base_url` 与 `model`。

## 日报 `report`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `trigger_hour` | `21` | 每晚几点出报。改完必须重新注册计划任务 |
| `catchup_days` | `7` | 开机后最多补几天漏报 |
| `min_active_minutes` | `20` | 有观测但活跃不足时生成空日说明；完全无观测则跳过 |
| `open_browser` | `true` | 出报后是否弹看板。自动出报**仅终版弹**（21:00 首版与晚间增量更新不弹，一次补多天也只弹最新那天）；手动 `report` 每次生成都弹，`--no-open` 可禁。**这不是外发审批**，禁止外发请用 `privacy.allow_llm` |
| `review_mode` | `false` | `true` 时先把待发内容落盘，确认后才真正外发 |
| `max_upgrade_attempts` | `3` | 降级日报最多自动升级几次，防止断网时反复调用 |
| `degraded_retry_hours` | `12` | 降级日报在这个时间窗内才允许自动重试升级 |
| `gap_threshold_min` | `10` | 间隔超过 10 分钟断块 |
| `max_block_min` | `30` | 单块最长 30 分钟 |
| `max_session_min` | `60` | 行为段最长 60 分钟 |
| `refresh_gap_min` | `90` | 已出报后至少再过这么久才允许增量更新 |

## 文档引用 `refs` 与笔记同步 `obsidian`

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `refs.enabled` | `true` | 是否解析文档引用 |
| `refs.providers` | `[local_file, obsidian, url]` | 启用的解析器；`kb` 已注册但默认不启用 |
| `refs.allow_unresolved_links` | `false` | 文件不存在就不生成链接，避免死链 |
| `refs.cache_ttl_days` | `30` | 解析结果缓存天数 |
| `refs.resolve_timeout_sec` | `15` | 单次解析超时 |
| `refs.search_roots` | `[]` | 自动解析时搜索的目录；空则只认绝对路径 |
| `refs.vault_dir` / `vault_name` | `""` | Obsidian 库位置与显示名 |
| `refs.kb.*` | 关闭 | 知识库预留项，当前为空实现 |
| `obsidian.enabled` / `vault_dir` | `false` / `""` | 日报同步到笔记库 |

## 配置失效

无效 YAML、已知字段类型错误（例如把布尔写成字符串 `"false"`）或非法隐私正则，会让程序**停止内容采集与外发**，而不是静默退回默认值：

- 界面与 `doctor` 显示具体原因，`status` 的 JSON 里带 `privacy_error`；
- 不会覆盖你损坏的原配置；
- 已有运行数据却缺少配置文件时，拒绝自动重建默认值，避免"看起来一切正常、其实换了套配置"。

保存前会先校验、再单文件原子替换，所以不会写到一半坏掉。

## 常见调整

```yaml
# 换一个模型服务
llm:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model: "qwen-plus"

# 改成 22:00 出报（改完必须重注册计划任务）
report:
  trigger_hour: 22

# 只看得见当前窗口，绝不抓全屏（默认值，显式写出来更放心）
privacy:
  capture_scope: active_window

# 完全不外发，只用本地时间线
privacy:
  allow_llm: false

# 先审后发
report:
  review_mode: true

# 截图只留 3 天，事件留 14 天
retention:
  shots_days: 3
  events_days: 14
```

改出报钟点后要重新注册，因为计划任务的触发时刻是注册时写进系统的：

```bat
What-IDid.exe autostart --remove
What-IDid.exe autostart --install
```
