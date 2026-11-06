# PROGRESS.md — What-IDid

> **这是唯一的进度事实来源。** 新会话从这里接续，不要凭记忆或聊天记录猜。
> 维护规则：每次会话结束前更新本文件；只写事实与下一步，不写过程感想。
> 最后更新：2026-11-06 10:05

## 当前状态

> **这一节刻意不写当前 HEAD。** HEAD 每次提交都会变，把它写进一个会被提交的文件，等于让它
> 一经提交就过期——本项目已经因此错过三次。当前值一律用下面自查第 1、2 条现查；
> 只有**已经不动了的**提交号（过去的提交、tag 目标）才写成字面量。

- **版本**：`1.3.0`（`src/gui.py:VERSION`，唯一来源）
- **仓库**：HEAD 已推送、与 `origin/main` 一致。**工作区是否干净不写在这里**——本文件与改动一起提交，
  写死「干净」立刻过期；一律现跑 `git status --short` 判断，把它的输出当唯一依据。
- **tag**：`v1.2.2`（附注标签 `85b0c67`）指向代码提交 `1a6d251`；文档纠偏提交 `227935c` 在它之后，
  所以 tag 不含这套文档——这是有意的，tag 标记的是产物来源，不是最后的文档状态。
  **`v1.3.0` 尚未打 tag**，等 iMac 侧提交后由用户打
- **v1.3.0 状态**：代码完成、`check_privacy` 110 项 + `check_boundaries` 27 项全绿；
  产物在 `release/v1.3.0/`；**未提交、未推、未打 tag、未建 Release**
- **已发布**：GitHub Release `v1.2.2`（<https://github.com/ShiyanXu/What-I-Did/releases/tag/v1.2.2>），
  资产 `What-IDid-win64-1.2.2-20261105.zip`（116,130,117 字节 / 1104 条目 / sha256 `36f1d3c4…df1e`，
  与 `release/v1.2.2/SHA256SUMS.txt` 一致）
- **自测**：`check_privacy` 110/110、`check_boundaries` 27/27（2026-11-06 09:55 实跑，
  边界项在隔离 `WID_HOME` 的夹具日上跑）；
  DPI 与 GUI 回归 1.2.2 未跑，1.3.0 亦未跑
- **文档**：`AGENTS.md`、本文件、`agents/` 下专题首次入库于 `888c30f`；两轮「克隆后五问」纠偏在
  `227935c`；此后只有本文件自身的措辞补正，不再单列提交号
- **生产实例**：`D:\What-IDid` 已换 v1.3.0（历史数据保留），采集由计划任务 `WhatIDid-Recorder`
  拉起，心跳正常
- **未收尾的元信息**：`release/v1.2.2/RELEASE_NOTES.md` 与 GitHub Release 正文的 `Source commit`
  写的是 `1e4a5ed`，应为 `1a6d251`（tag 实际目标）。本地文件可直接改，网页需人工改。

**自查（不要只信上面的断言，直接跑）**：

```bash
git status --short                              # 必须为空
git rev-list --left-right --count @{u}...HEAD   # 必须是 "0	0"（不领先也不落后）
git rev-parse v1.2.2^{commit}                   # 应为 1a6d251，tag 不会移动
git tag -l "v1.2*"                              # 应含 v1.2.2
PY=.../Python310/python.exe; $PY scripts/check_privacy.py && $PY scripts/check_boundaries.py
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

## 进行中

- **Harness 工作系统已建成**：`python scripts/harness.py check` 全绿。功能层面的推进状态以
  `python scripts/harness.py status` 为准（`gui-001` 仍为 `blocked`），本文件不复制功能清单，
  也不记录「几项待提交」这类一提交就过期的数字。
- 提交与推送仍由 iMac 侧按 `agents/workflow.md` 执行；本机只改与验。

## 已知问题

1. **DPI 与 GUI 点级回归本版未跑**（`verify_dpi.py` 14 项、`gui_regression.py` 约 41 项）。
   发布说明已如实标注。没跑 GUI 回归是**故意的**：它会真开界面、真点击，而采集正在运行，
   这些动作会进当天活动块、污染日报——要跑就先 `wid.py pause`。另：`gui_regression` 的项数随
   条件分支浮动（静态约 36 个 `check()` 调用点），文档已改成「不写死数字，看通过/失败两栏」。
2. **`src/paths.py:56-60` 有一段等价分支**：带 `portable.marker` 的分支与紧随其后的「可写就用程序目录」
   返回同一个值，marker 实际不起作用；`docs/architecture*` 也只写了三级路径、没提这个 marker。
   要么删掉分支，要么让 marker 真能强制便携。改它要重建 dist，留到下次动 `paths.py` 时一起做。
3. **`W:` 共享对 Windows 侧不可写已有文件**（`Permission denied`，非权限位、非占用），
   所以所有改动只能以 `.new` 中转，由 iMac 侧 `mv -f`。
4. **Windows 侧无 git 凭据**，push / tag / GitHub Release 只能 iMac 侧做。
5. **同步是手动的**（`git fetch && git reset --mixed`）。推送后不归零，下次改动的 diff 里会混进旧提交。
6. **本文件依赖人/agent 主动更新**，没有任何自动检查会发现它过期。已经错过三次：
   一次写「6 项待提交」而实际已推送、一次写「工作区干净」而实际 15 项没提交、
   一次写「17 项未提交」而当时刚提交完。**根因是写死当前 HEAD 与未提交项清单**——文件一被提交，
   这两个值必然变化。现在的做法：状态一律写成不变量 + 自查命令（见「当前状态」开头），
   字面量只留给已经不动了的提交号。改这一节时先跑自查，别凭上一次的记忆写。
7. **块清单的「分类」是本地按标题猜的**（含「文档/.md」的标题会被猜成 writing）。
   它只用于聚类，不能作为行为判定依据，prompt 里已明确写死。
8. **`dist/` 与 v1.2.2 的 zip 构建于 `227935c` 之前**。那次提交只动文档、注释、`scripts/`（不进包）
   与 `build_dist.py`（不进包），唯一进包的是 `config.default.yaml` 里的一行注释——注释只影响
   「首次运行生成 config.yaml」时的可读性，不影响任何行为。所以不重打包：同一版本号换资产会让
   已发布的 sha256 失效，代价大于收益。下一次因代码升版时才自然带上。
9. **本机**：`release/v1.3.0/RELEASE_NOTES.md` 的 `Source commit` 写的是 `883320c`，而该提交里
   没有 `src/browse.py`、`src/gui.py` 还是 `1.2.2`——v1.3.0 代码尚未入库，产物无法从该提交复现。
   入库后必须回填真实提交号；在那之前不要把 v1.3.0 当成可复现版本。
10. **本文件与 `feature_list.json` 是两条独立维护的线，没有脚本比对**：产品行为可以在本文件里
    标成已完成，而功能项仍是 `not_started`（`source-links-001` 现在就是这种状态）。
    **功能状态一律以 `harness.py status` 为准**，本文件只记项目级事实，不复制状态计数。

## 下一步

1. 把 `source-links-001` 登记成证据：`harness.py start source-links-001` → `harness.py verify source-links-001`
   （它的验证命令就是隐私自测里的来源链接用例，当前已通过）。
2. 跑 `verify_dpi.py` 与 `gui_regression.py`：先 `wid.py pause`（否则测试会污染当天日报），
   `WID_HOME` 必须是 `.tmp_wid*`，跑完 `wid.py resume`，把通过/失败数记进本节。
   走 Harness 时对应 `harness.py resume gui-001 --reason "..."` → `harness.py verify gui-001`，
   证据由脚本写入，不手写 passing。
3. 观察 2026-11-05 的终版日报是否守住证据档位（重点看有无把「看」写成「写」、有无把群消息写成
   「我参与了」）；若仍有偏差，按 `agents/decisions.md` 的 D-08 调 prompt，而不是加特例。
4. 提交侧入库时按 `agents/workflow.md` 走，并回填 `release/v1.3.0/RELEASE_NOTES.md` 的
   `Source commit`（已知问题 9）。
