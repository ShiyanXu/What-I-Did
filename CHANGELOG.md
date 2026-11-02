# Changelog · 变更记录

This file records **user-visible behaviour changes**, not internal refactors. The version
number matches `VERSION` in `src/gui.py`.

`publish.py` copies the current version's section into `release/v<版本>/RELEASE_NOTES.md`,
so the text below appears verbatim in the GitHub Release body — write it for the people
downloading the build, not for yourself.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); sections are
`Added / Changed / Fixed / Removed`. Every release section carries English first, then
Chinese (`### English` / `### 中文`).

---

本文件记录**用户可见的行为变化**，不记录内部重构。版本号与 `src/gui.py` 的 `VERSION` 保持一致。

发布时 `publish.py` 会截取当前版本段落，拼进 `release/v<版本>/RELEASE_NOTES.md`，
所以这里的文字会原样出现在 GitHub Release 正文里——写给下载的人看，不是写给自己看的。

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，分节用 `Added / Changed / Fixed / Removed`。
每个版本段落英文在前、中文在后（`### English` / `### 中文`）。

## [v1.2] - 2026-11-03

### English

English is the default now, with a language switch in Settings. The window and the report
skeleton (headings, badges, metric labels, empty states) follow that setting; the text the
model writes keeps whatever language the model produces. Every public document also ships
an English file plus a `.zh-CN` sibling.

#### Added

- **English by default.** Every string in the window, the CLI and the report skeleton is
  English. `config.yaml` gains `ui.language: en`; existing installs keep their data.
- **Language switch.** Settings → Language → Display language offers `English` and
  `简体中文`. Saving rebuilds the whole window, so no half-translated layout is left behind.
- **A new language is one file.** Copy `src/i18n_zh_cn.py` to `i18n_<code>.py`, translate
  the values, register the code in `i18n.LOCALES`. A missing key falls back to English
  instead of showing a key or a blank.
- **`WID_LANG` environment variable** overrides the configured language, for scripts and
  one-off runs (`WID_LANG=zh-CN`).
- **Bilingual documentation.** `docs/*.md` plus `docs/*.zh-CN.md`; `CONTRIBUTING` and
  `SECURITY` the same way. This changelog and every release note carry an English section
  followed by a Chinese one.

#### Changed

- **Report reason labels are stable codes** (`auto` / `manual` / `catchup` / `finalize`).
  The badge on the day page used to fall back to a raw, untranslated string whenever it did
  not recognise the value.
- **`build.spec` collects `src/i18n*.py` automatically**, so a language module added later
  cannot be silently left out of the portable build.
- Date, duration and weekday formats follow the language (`Oct 18` / `10 月 18 日`).

#### Fixed

Carried over from the unpublished v1.1.2:

- **The 04:00 final report is no longer lost.** Cleanup sometimes took the "report is being
  written" lock in the same tick that spawned the report. The report now waits up to ten
  seconds before giving up, and cleanup runs before the report is started.
- **Automatic retries are counted by progress, not by attempts.** The old counter treated
  the 90-minute incremental updates as failures, so by 04:00 the day had no attempts left.
  The counter now resets whenever a new revision lands, and a final report keeps retrying on
  an hourly back-off even after the quota is used up.
- **An older missed day is no longer starved by a newer one.** When the newest day ran out
  of quota, earlier days still waiting for a final report were blocked with it. Each day is
  now checked on its own.
- **Autostart registers even where `schtasks` is blocked.** Some security policies refuse to
  execute `schtasks.exe`, so the task was never created and capture never came back after a
  reboot. Registration now goes through `Register-ScheduledTask` (RPC only) and falls back
  to `schtasks`; delete and query work the same way.

### 中文

默认语言改为英文，设置里可以切回中文。界面与日报骨架（标题、徽章、指标名、空状态）
跟随该设置；模型生成的正文保持模型自己给出的语言。所有公开文档都配了英文版与
`.zh-CN` 版本。

#### 新增

- **默认英文**：界面、命令行、日报骨架的文案全部改成英文。`config.yaml` 增加
  `ui.language: en`，已有安装的数据不受影响。
- **语言切换**：设置 → 语言 → 显示语言，可选 `English` 与 `简体中文`。保存后整窗重建，
  不会留下半中半英的排版。
- **加一门语言只要一个文件**：把 `src/i18n_zh_cn.py` 复制成 `i18n_<代码>.py`，
  翻译右边的值，再到 `i18n.LOCALES` 里登记一行。漏翻的词条回落英文，
  不会显示成 key 或空白。
- **`WID_LANG` 环境变量**可临时覆盖配置里的语言，供脚本和一次性运行使用。
- **文档双语**：`docs/*.md` 与 `docs/*.zh-CN.md` 成对，`CONTRIBUTING`、`SECURITY` 同样处理。
  本变更记录与每份发布说明都是英文段在前、中文段在后。

#### 变更

- **出报原因改成稳定代码**（`auto` / `manual` / `catchup` / `finalize`）。此前页面徽章
  遇到不认识的值会把中文原文直接吐出来。
- **`build.spec` 自动收集 `src/i18n*.py`**：以后新增语言模块不会漏进绿色版。
- 日期、时长、星期几的格式跟随语言（`Oct 18` / `10 月 18 日`）。

#### 修复

以下四条来自未发布的 v1.1.2，一并并入本版：

- **04:00 的终版不再丢**：补终版与当日清理同时启动时，清理先拿到「正在写日报」的锁，
  出报进程一秒后醒来发现被占用就直接放弃。现在出报最多等 10 秒再判忙，清理也排到出报之前启动。
- **自动重试配额按「有没有产出」算**：原来按拉起次数计，晚上每 90 分钟一次的增量更新
  正好把「同一天 6 次」用光，到 04:00 已经没机会重试。现在只要出了新版就重新计数；
  终版用光配额后仍按 1 小时退避继续尝试，宁可多试也不整天缺终版。
- **老的漏报日不再被新的拖死**：最新那天配额用完时，更早还在等终版的日子会被一起卡住。
  现在逐个检查，谁还有配额就先出谁。
- **开机自启能真正注册上了**：有些安全策略把 `schtasks.exe` 列进程序黑名单，连执行都不允许，
  于是任务永远建不出来（设置页一直提示"没有注册开机自启"），重启后采集也不会自己回来。
  现在先走 ScheduledTasks 模块的 `Register-ScheduledTask`（只走 RPC，不受黑名单影响），
  失败再退回 `schtasks /create`；删除、查询同样双路。

## [v1.1] - 2026-10-30


日报页从「采集审计」改成「成果简报」：首屏是 3–5 张成果卡，接着是待办与卡点、
合并成 4–6 段的工作主线、一条时间色带，原始活动块与截图全部折叠进附录。

### 新增

- **成果卡**：每张只有四个字段——状态、结果、意义、下一步。状态分四类：已完成 / 已推进 / 待外部 / 未解决。
- **待办与卡点**：单独成块，区分「待外部」「卡点」「待办」，不再埋在一段叙述里。
- **工作主线**：由本地把行为段合并成任务段（同动词、断档 ≤25 分钟、单段 ≤150 分钟，锁屏与离开自动排除），每段右侧接它产出的结果。
- **证据折叠**：点结果后面的证据编号，会自动展开对应的附录块。

### 变更

- 阅读顺序改为：成果 → 待办与卡点 → 工作主线 → 时间分配 → 洞察/卡点/明天 → 附录。
- 首屏指标换成「今日成果 / 待跟进 / 有效活动 / 会议」，采集量（活动块数、文档引用数）移到附录。
- 正文每天最多留 3 张证据截图，优先挑有错误码、版本号、命令、且停留 ≥3 分钟的画面。
- 文档只挂在模型确认支撑的成果下，弱相关与重复链接收进附录。
- 分享版 Markdown、GUI 日报页、周汇总标题同步成同一顺序与用词。

### 修复

- 数据缺口只报活动时段内、≥20 分钟、相邻合并的，夜间与正常午休不再被当成采集异常。
- 结束时间正好等于当天窗口起点（04:00）时，缺口被误算成当天，导致收工后的空白被报成记录中断。
- 次日补出的终版不再写「当日未结束」，改为标注覆盖到几点。
- 列表页的「关键进展」列改为「成果」，与页面用词一致。

### 模型侧

- `highlights` 增加 `status` 与 `next` 字段，新增 `followups`；模型没给状态的旧日报按文本推断，可直接重渲染。
- `headline` 明确禁止写「全天会议 138 分」这类统计，要求写具体对象和结果。

## [v1.0] - 2026-10-22

首个绿色版：截屏时间线采集、本地 OCR、每晚 21:00 出日报、静态看板与 Markdown 归档。
