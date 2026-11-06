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

## [v1.3.0] - 2026-11-05

### English

Every page you visited in the report now carries the link you actually visited — still
entirely on your machine, and off until you turn it on.

#### Added

- **Source links.** At report time the tool reads the local Chrome/Edge history file,
  matches each page block to the visits that fall inside or just before it, and puts the
  matched URL under that block. Reading a page for 30 minutes leaves one visit at the start,
  so the match looks back up to an hour; blocks with nothing inside that window take the
  single most recent visit before them, not all of them. Measured on a real day: 55 of 64
  page blocks (86%) got a link, about 1.2 links per block.
- **Two fallbacks when history has nothing.** If the top of the screenshot shows a URL
  (chrome and the page area were captured with line positions), that address-bar text is
  used; failing that, the block keeps only the site domain and is labelled
  `(domain only)`, so you can still tell where you were without inventing a page.
- **A "Source links" card in Settings** with three switches: read browser history, fall
  back to the address bar, keep the query string in shared copies. The first one is **off
  by default** — reading history is a decision you make, not one the tool makes for you.
- `doctor` grew a `Source links` line that reports whether the build can read history,
  whether it is enabled, and how many browser profiles were found.

#### Changed

- **Chat apps stop hiding article pages.** A WeChat or Feishu window whose title looks
  like an article (`微信公众平台`, `mp.weixin.qq.com`, `飞书文档`, `Lark Docs`) is treated
  as a web page instead of a chat, so those blocks get links too. Chat windows, group
  names and conversation lists are still classified as chats. Note that WeChat is in
  `privacy.skip_ocr_apps` by default, so it has no title and no OCR to match; this takes
  effect there only after you take it out of that list. Feishu documents work as shipped.
- **Shared copies drop the query string by default.** `?token=…`, `?q=your search terms`
  and tracking parameters no longer travel into the copy you send to someone else. Local
  reports keep the full URL with only the sensitive values masked.
- **The minimum Python version is 3.10** (was 3.9), matching what the code already needed.

#### Fixed

- **A group chat you only read is no longer written up as your own work.** When the only
  evidence is a conversation list or someone else's message, the block used to be summarised
  as "scanned the group" and could even produce a follow-up such as "waiting for X to
  confirm the schedule" — something you never did. Those blocks now say plainly that you
  did not post anything, and no follow-up or next-day item may be derived from another
  person's message. When the model is unavailable and the label has to be guessed locally,
  a chat window falls back to "reading" instead of "communicating".

#### Notes

- Nothing is uploaded and no screenshot leaves the machine. Only the URL of a page you
  already had open is stored, in `data/<day>/urls.jsonl`, on the same retention schedule as
  the rest of that day and removed by `forget` together with it.
- Days captured before this version have no line positions, so the address-bar fallback
  only starts working on new captures.

### 中文

日报里每个看过的页面现在都带上你真正访问过的链接——仍然全在本机，而且默认不开。

#### 新增

- **来源链接**：出报时读取本机 Chrome/Edge 历史库，把每个网页块与该时段内（或稍早）的访问
  对起来，把命中网址挂到块下面。读一篇 30 分钟的文章只会在开头留下一次访问记录，所以匹配
  向前回溯最多一小时；窗内对不上的块，只取它之前**最近一次**访问，而不是把一小时前的
  无关页面全挂上去。真实一天实测：64 个网页块命中 55 个（86%），平均每块 1.2 条链接。
- **历史里没有时有两级兜底**：截图顶部若显示了网址（窗口和页面区域按行坐标采集），
  就用地址栏那行文字；再不行只保留站点域名并标注 `（仅域名）`，让你知道当时在哪，
  但不编造具体页面。
- **设置页新增「来源链接」卡片**，三个开关：读取浏览历史、退回地址栏识别、分享版保留网址参数。
  第一项**默认关闭**——要不要读历史由你决定，不由工具替你决定。
- `doctor` 新增 `Source links` 一项，报告这个构建能不能读历史、是否开启、找到几个浏览器配置。

#### 变更

- **聊天软件不再吞掉文章页**：微信、飞书窗口的标题若是文章特征（`微信公众平台`、
  `mp.weixin.qq.com`、`飞书文档`、`Lark Docs`），按网页处理，这些块同样能拿到链接；
  聊天窗、群名、会话列表仍然算聊天。注意微信默认在 `privacy.skip_ocr_apps` 里，
  没有标题也没有 OCR，所以这条对微信要先把它从该名单里去掉才生效；飞书开箱即用。
- **分享版默认去掉网址参数**：`?token=…`、`?q=你的搜索词` 以及各种跟踪参数不再随分享副本
  外流。本地日报保留完整网址，只对敏感参数打码。
- **最低 Python 版本改为 3.10**（原 3.9），与代码实际需求对齐。

#### 修复

- **只读过、没有发言的群，不再被写成你的工作。** 证据只有会话列表或别人的消息的段落，
  以前会被总结成「扫群消息」，甚至推出「等某人确认排期」这种你并没有做过的待跟进；
  现在这类段落明确写「未发言」，并且不允许从别人的消息里推出待跟进或明日待办。
  模型不可用、只能本地兜底时，聊天窗口按「阅读」处理，不再算成「沟通」。

#### 说明

- 不上传任何内容，截图不出本机。存下来的只有你本来就已经打开的页面网址，写在
  `data/<日>/urls.jsonl`，与当天数据同一套保留周期，`forget` 一并删除。
- 本版之前采集的日期没有行坐标，地址栏兜底要等新采集才会生效。

## [v1.2.2] - 2026-11-05

### English

The daily report no longer mistakes reading for writing, and no longer turns other people's
chat messages into your own conversations.

#### Changed

- **Evidence is graded before any verb is chosen.** A document is only reported as written when
  the screenshots show an input-method candidate window, text that visibly grew between two
  moments, or a save/commit action. Everything else is reported as reading, checking or
  comparing. A section number such as `1.3.1.1` appearing on screen no longer counts as proof
  that you wrote that section — it only means you were looking at it.
- **Chat tools count only when you actually sent something.** A conversation list, group
  history, someone else's message, a pinned doc or an @-mention no longer becomes "discussed X
  with Y", and no longer feeds highlights or follow-ups. Only your own message bubble, an input
  box that was cleared, or something in the Sent folder counts.
- **The narrative states what happened, not how sure the model was.** Evidence caveats moved
  into the report's own note field, so the summary no longer argues with itself.

### 中文

日报不再把「看」写成「写」，也不再把你没参与的群消息写成你在讨论。

#### 变更

- **先给证据分档，再选动词。** 只有截图里出现输入法候选框、相邻两个时点的文字确实增加、
  或出现保存/提交动作，才判为在写文档；其余一律记为阅读、核对、比对。屏幕上出现
  `1.3.1.1` 这样的章节号，只说明你在看这一节，不再当成你写了这一节。
- **聊天软件只认真正发出去的消息。** 会话列表、群历史、别人的消息、置顶文档、@提醒，
  都不再被写成「和某人讨论了某事」，也不再进入成果卡与待办。只有自己的消息气泡、
  被清空的输入框、或「已发送」里的邮件才算。
- **正文只陈述发生了什么，不解释判定过程。** 证据不足的说明全部收进报告的备注字段，
  不再让总结一边叙述一边自我辩解。

## [v1.2.1] - 2026-11-02

### English

Fix a language switch that only half worked: the page you were looking at turned Chinese,
the sidebar stayed English.

#### Fixed

- **The sidebar menu now follows the language.** It was built once when the module was
  imported, and switching the language rebuilds the window inside the same process without
  re-importing anything, so the four labels kept the language they were first built in.
  They are now computed every time the window is built.
- **Category labels and the colour legend follow too.** The time-split line
  (`Meetings 138 min`) and the legend under the timeline (`Coding / Debug / …`) had the
  same problem, and the legend also showed the raw English verb names in Chinese mode.
- **The reports empty-state hint** (`Pick a report on the left…`) was frozen the same way.

No behaviour change otherwise: the same data, the same layout, only the strings that were
one language behind now match the rest of the window.

### 中文

修一个「只切了一半」的语言问题：正在看的页面变成中文了，左侧菜单还是英文。

#### 修复

- **左侧菜单跟着语言走**：它是在模块导入时算好的一串常量，而切换语言是**同一个进程内**
  重建窗口、不会重新导入模块，于是四个标签停在第一次构建时的语言。现在每次建窗口时现算。
- **分类名与色带图例同样修好**：时间分配那行（`Meetings 138 min`）和时间轴下方的图例
  （`Coding / Debug / …`）是同一个成因；图例此前在中文界面下还会直接显示英文动词原名。
- **日报页的空状态提示**（`Pick a report on the left…`）也一起修了，同样是导入时固化。

其余行为不变：数据、布局、交互都相同，只有落后一个语言的那些文案现在跟窗口一致了。

## [v1.2] - 2026-11-02

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
