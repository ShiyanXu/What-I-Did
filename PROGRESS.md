# PROGRESS.md — What-IDid

> **这是唯一的进度事实来源。** 新会话从这里接续，不要凭记忆或聊天记录猜。
> 维护规则：每次会话结束前更新本文件；只写事实与下一步，不写过程感想。
> 最后更新：2026-11-06 14:14

## 当前状态

> **这一节刻意不写当前 HEAD。** HEAD 每次提交都会变，把它写进一个会被提交的文件，等于让它
> 一经提交就过期——本项目已经因此错过三次。当前值一律用下面自查第 1、2 条现查；
> 只有**已经不动了的**提交号（过去的提交、tag 目标）才写成字面量。

- **版本**：`1.3.1`（`src/gui.py:VERSION`，唯一来源）
- **仓库**：HEAD 已推送、与 `origin/main` 一致。**工作区是否干净不写在这里**——本文件与改动一起提交，
  写死「干净」立刻过期；一律现跑 `git status --short` 判断，把它的输出当唯一依据。
- **tag**：`v1.2.2`（`85b0c67`）→ `1a6d251`、`v1.3.0`（`0f89c19`）→ `753084c`、`v1.3.1`（`4e4c67d`）
  → `092b7c4`，三个附注标签都已推送。tag 只标记**产物来源**，不标记最后的文档状态：
  `v1.3.0` 之后还有 CHANGELOG 与状态修正（`764d2c2`），这些**不在 tag 内**——有意的取舍，不是遗漏。
- **v1.3.1 状态**：**已发布**。代码入库 `092b7c4`、tag 同名、GitHub Release 2026-11-06 14:10 发布；
  产物在 `release/v1.3.1/`，生产实例已换成这一版。
- **已发布**：GitHub Release `v1.3.1`（<https://github.com/ShiyanXu/What-I-Did/releases/tag/v1.3.1>），
  资产 `What-IDid-win64-1.3.1-20261106.zip`（116,985,077 字节 / 1106 条目 / sha256 `aa85acf6…1d05`，
  与 `release/v1.3.1/SHA256SUMS.txt` 一致；GitHub 侧 `digest` 字段与本地逐位相同）。
  上一版 `v1.3.0` 的资产是 `What-IDid-win64-1.3.0-20261106.zip`（116,983,623 字节 / `297b73b1…95c2`）
- **自测**：`scripts/harness.py check` 全绿（2026-11-06 14:11 实跑，`exit_code=0`）——
  它一次跑完静态校验、Harness 自测、隐私检查、隔离 E2E；**项数随分支浮动，以命令输出为准，
  本文件不写死数字**（同一天就有过一次 104→110 的增长）。
  DPI 与 GUI 回归 2026-11-06 首次实跑并全绿：`verify_dpi.py` 14/14、`gui_regression.py` 41/41；
  `harness.py status` 当前 6/6 `passing`、`blocked` / `in_progress` 归零（14:11 实查）。
- **文档**：`AGENTS.md`、本文件、`agents/` 下专题首次入库于 `888c30f`；两轮「克隆后五问」纠偏在
  `227935c`；`07fe7f3` 把仓库地图移出入口文件（新建 `agents/layout.md`，入口从 127 行降到 97 行）
- **生产实例**：`D:\What-IDid` 已换 v1.3.1（exe md5 `5ae8148c…`、`_internal` 1104 文件、
  `doctor --full` 18 项全绿），历史数据原地保留；采集由计划任务 `WhatIDid-Recorder` 拉起，心跳正常
- **已核对的元信息**：`release/v1.2.2/RELEASE_NOTES.md` 与 GitHub Release 正文的 `Source commit`
  都是 `1a6d251`（tag 实际目标），两侧已对齐。本文件此前记成 `1e4a5ed`——那是产物生成时的 dev HEAD，
  Release 正文在 2026-11-05 16:33 已改好，只是本文件没跟上；**属本文件的过期断言，不是发布问题**。
  改这类断言前先跑 `git rev-parse v1.2.2^{commit}` 与接口查正文，别沿用上一轮结论。
  - `release/v1.3.0/RELEASE_NOTES.md` 是**手工修正**的：`Source commit` 由 `883320c` 改成 `753084c`，
    `Self-tests` 行改为 harness 口径，Notes 各补两条（旧 `config.yaml` 照旧可用、绿色版已内置 SQLite）。
    手工的原因当时是「不改 CHANGELOG、不动 tag」；后来 CHANGELOG 已在 tag 之后补上同样两条，
    所以现在重跑 `publish --force` 生成的正文与这份一致，只有 `Built` 时间与 `Source commit`
    （届时取当时的 HEAD）会不同——重跑前先想清楚要不要这两个值变。

**自查（不要只信上面的断言，直接跑）**：

```bash
git status --short                              # 必须为空
git rev-list --left-right --count @{u}...HEAD   # 必须是 "0	0"（不领先也不落后）
git rev-parse v1.2.2^{commit}                   # 应为 1a6d251，tag 不会移动
git rev-parse v1.3.0^{commit}                   # 应为 753084c，tag 不会移动
git rev-parse v1.3.1^{commit}                   # 应为 092b7c4，tag 不会移动
git tag -l "v1.3*"                              # 应含 v1.3.0 与 v1.3.1（本机缺 tag 时先 git fetch --tags）
PY=.../Python310/python.exe; $PY scripts/harness.py check   # 一次跑完静态+Harness+隐私+隔离E2E
```

**不在仓库里、只能现场查的**：`D:\What-IDid` 的运行状态与心跳、`dist/` 是否比源码旧、
`release/` 目录是否还在、iMac 侧是否还有未消费的 `.new`。这些写进文件只会立刻过期。

## 已完成

- [x] v1.0 首次公开发布（采集 + OCR + 日报 + 看板）
- [x] v1.1 日报改为「成果简报」：成果卡、待办与卡点、工作主线、时间色带、证据折叠
- [x] v1.2 界面英文化 + 中文可切换；文档全双语
- [x] v1.2.1 修复切语言后侧栏/图例不跟随（模块级 `t()` 固化）
- [x] v1.2.2 日报引入证据分档：写作类动作必须有输入态或文字增量证据；聊天软件只认「已发出」
- [x] v1.2.2 的 zip 落盘、生产实例替换、push、tag、GitHub Release 全部完成
- [x] v1.3.0 来源链接（R1）：`src/browse.py` 读本机 Chrome/Edge 历史、地址栏兜底、仅域名兜底；
      设置页三个开关；分享版去 query；`forget` 与保留期同步覆盖 `urls.jsonl`；`doctor` 新增检查项
- [x] v1.3.0 打包修复：`build.spec` 曾把 `sqlite3` 写进 `excludes`，已移入 `hiddenimports`，
      否则只有绿色版会坏
- [x] v1.3.0 聊天证据收口：11-05 日报把「只看了群会话列表」写成「扫群消息」并推出
      「等别人确认排期」的待跟进，用户纠正后改了三处——报告数据按 `manual.json` 覆盖重写、
      提示词把禁令扩到 label/detail/followups/tomorrow、本地兜底把聊天窗口映射为 reading
- [x] 建立本文件与 `AGENTS.md`、`agents/*`（仓库即事实来源），并入 `888c30f`
- [x] **Harness 工作系统**（2026-11-05）：`feature_list.json` 机器状态 + `scripts/harness.py`
  状态机（start/verify/block/resume，WIP=1、依赖门控、passing 不可逆、失败不写通过证据）+
  `scripts/check_harness.py` 24 项隔离自测 + `init.sh` 统一初始化 + 本机 `state/harness/` JSONL 轨迹；
  配套 `agents/harness.md`（路由与分工）、`sprint-contract.md`、`clean-state-checklist.md`、
  `session-handoff.md`、`evaluator-rubric.md`、`quality-document.md`、`graph.md`（进阶蓝图）
- [x] 1104 用新提示词重出报告（旧版备份 `reports/2026-11-04.json.bak-20261105`，仅本机）
- [x] **两轮「克隆后五问」文档纠偏**，并入 `227935c`（17 项）。做法：从 GitHub 克隆纯净副本 →
      全新会话只读它回答五个基本问题 → 按它指出的矛盾逐条核代码再改；改完再造一份「推送后快照」
      让第二个新会话复查。第一轮查出 11 处，第二轮查出 8 处（含我自己改错的两处）。
      修的类别：`dev/` 根假设、已过期的进度摘要、计划任务条数、「正式采集不 OCR」、`scripts/*` 的
      `.build/` 过期路径、写死的回归项数、`publish.py` / `WORKSPACE.md` 的本机引用；
      另补 `requirements.txt`（9 个包按实测版本钉住）并让 `build_dist.py` 按它安装。
- [x] **v1.3.0 入库与发布**（2026-11-06）：`7f4e02c`（Harness）、`753084c`（v1.3.0），
      附注标签 `v1.3.0` 已推送；`764d2c2` 补 CHANGELOG 与状态修正（不在 tag 内，沿用 v1.2.2 的取舍）。
      GitHub Release 于 10:35 发布，资产与本地 sha256 一致。
- [x] **v1.3.1 入库与发布**（2026-11-06）：`07fe7f3`（仓库地图移出入口）、`2ac33c9`（Harness 证据去本机路径）、
      `60cc91e`（操作按钮独占一行 + `gui-001` 登记）、`092b7c4`（版本号与 CHANGELOG）；附注标签 `v1.3.1` 已推送。
      Release 于 14:10 发布，资产与本地 sha256 一致（GitHub `digest` 逐位相同）。
      修的是真 bug：`badge_box` 实际 520 宽、内容需 677 宽，pack 把末位「审核待发内容」压成 1×1，
      `winfo_ismapped()` 为 0——只看得见「找不到按钮」，看不见「按钮在但点不到」。
      回归断言因此从「找得到」改成「看得到」，`find_op()` 统一在 `page.ops_row` 里找并断言可见。

## 进行中

- **Harness 工作系统已建成**：`python scripts/harness.py check` 全绿。功能层面的推进状态以
  `python scripts/harness.py status` 为准（当前 6 项全部 `passing`、`blocked` 归零），本文件不复制功能清单，
  也不记录「几项待提交」这类一提交就过期的数字。
- 提交与推送仍由 iMac 侧按 `agents/workflow.md` 执行；本机只改与验。

## 已知问题

1. **`src/paths.py:56-60` 有一段等价分支**：带 `portable.marker` 的分支与紧随其后的「可写就用程序目录」
   返回同一个值，marker 实际不起作用；`docs/architecture*` 也只写了三级路径、没提这个 marker。
   要么删掉分支，要么让 marker 真能强制便携。改它要重建 dist，留到下次动 `paths.py` 时一起做。
2. **`W:` 共享的权限边界（实测修正，原表述「不可写已有文件」是错的）**：根目录可写可删；
   仓库子目录允许**新建文件**，也允许对已有文件**原地截断写/追加**；真正被禁的是**改名与删除**
   ——git 提交要 write-temp + rename，所以 Windows 侧 commit / push 必失败。
   改过的文件过去一律用 `.new` 中转，是当初误判「不可写」时留下的习惯；普通文件可以直接覆盖，
   但覆盖会丢可执行位（`init.sh` 就这么从 100755 变成 100644，工作区因此显示脏），
   有 x 位的文件仍走 `.new` + iMac 侧 `mv -f`。路径大小写也要写准：`What-IDid` 能 `ls`
   却 `cd` 不进、写入报 No such file。
3. **Windows 侧无 git 凭据**，push / tag / GitHub Release 只能 iMac 侧做。
4. **同步是手动的**（`git fetch && git reset --mixed`）。推送后不归零，下次改动的 diff 里会混进旧提交。
5. **本文件依赖人/agent 主动更新**，没有任何自动检查会发现它过期。已经错过三次：
   一次写「6 项待提交」而实际已推送、一次写「工作区干净」而实际 15 项没提交、
   一次写「17 项未提交」而当时刚提交完。**根因是写死当前 HEAD 与未提交项清单**——文件一被提交，
   这两个值必然变化。现在的做法：状态一律写成不变量 + 自查命令（见「当前状态」开头），
   字面量只留给已经不动了的提交号。改这一节时先跑自查，别凭上一次的记忆写。
6. **块清单的「分类」是本地按标题猜的**（含「文档/.md」的标题会被猜成 writing）。
   它只用于聚类，不能作为行为判定依据，prompt 里已明确写死。
7. **`dist/` 与 v1.2.2 的 zip 构建于 `227935c` 之前**。那次提交只动文档、注释、`scripts/`（不进包）
   与 `build_dist.py`（不进包），唯一进包的是 `config.default.yaml` 里的一行注释——注释只影响
   「首次运行生成 config.yaml」时的可读性，不影响任何行为。所以不重打包：同一版本号换资产会让
   已发布的 sha256 失效，代价大于收益。下一次因代码升版时才自然带上。
8. **本文件与 `feature_list.json` 是两条独立维护的线，没有脚本比对**：产品行为可以在本文件里
   标成已完成，而功能项仍是 `not_started`（曾经 `source-links-001` 就是这样，现在已登记）。
   **功能状态一律以 `harness.py status` 为准**，本文件只记项目级事实，不复制状态计数。
9. **`v1.3.0` 的 tag 里少两条 CHANGELOG 说明**：tag 指向 `753084c`，其 CHANGELOG 标题是
    `2026-11-05` 且没有「旧 config.yaml 照旧可用」「内置 SQLite 组件」两条；这两条是 tag 之后
    的文档提交 `764d2c2` 补上的，已写进 GitHub Release 正文。**tag 已随 Release 发布，不再移动**——
    移动一个已发布 Release 的 tag，代价大于「checkout 后 CHANGELOG 少两行」。
    **`v1.3.1` 的 tag 已带上完整版本**：v1.3.0 段落日期是 `2026-11-06`，两条说明中英俱全
    （`git show 092b7c4:CHANGELOG.md` 可验），所以此后从最新 tag checkout 不再缺内容；
    残留的只有 `v1.3.0` 那个 tag 自身。

## 下一步

1. 观察 11-06 起的日报是否守住证据档位（重点看有无把「看」写成「写」、有无把群消息写成
   「我参与了」）；若仍有偏差，按 `agents/decisions.md` 的 D-08 调 prompt，而不是加特例。
