# PROGRESS.md — What-IDid

> **这是唯一的进度事实来源。** 新会话从这里接续，不要凭记忆或聊天记录猜。
> 维护规则：每次会话结束前更新本文件；只写事实与下一步，不写过程感想。
> 最后更新：2026-11-05 16:30

## 当前状态

> **这一节刻意不写当前 HEAD。** HEAD 每次提交都会变，把它写进一个会被提交的文件，等于让它
> 一经提交就过期——本项目已经因此错过三次。当前值一律用下面自查第 1、2 条现查；
> 只有**已经不动了的**提交号（过去的提交、tag 目标）才写成字面量。

- **版本**：`1.2.2`（`src/gui.py:VERSION`，唯一来源）
- **仓库**：HEAD 已推送、与 `origin/main` 一致，工作区干净
- **tag**：`v1.2.2`（附注标签 `85b0c67`）指向代码提交 `1a6d251`；文档纠偏提交 `227935c` 在它之后，
  所以 tag 不含这套文档——这是有意的，tag 标记的是产物来源，不是最后的文档状态
- **已发布**：GitHub Release `v1.2.2`（<https://github.com/ShiyanXu/What-I-Did/releases/tag/v1.2.2>），
  资产 `What-IDid-win64-1.2.2-20261105.zip`（116,130,117 字节 / 1104 条目 / sha256 `36f1d3c4…df1e`，
  与 `release/v1.2.2/SHA256SUMS.txt` 一致）
- **自测**：`check_privacy` 73/73、`check_boundaries` 27/27（2026-11-05 16:10 实跑确认）；
  DPI 与 GUI 回归**本版未跑**
- **文档**：`AGENTS.md`、本文件、`agents/` 下 6 份首次入库于 `888c30f`；两轮「克隆后五问」纠偏在
  `227935c`；此后只有本文件自身的措辞补正，不再单列提交号
- **生产实例**：`D:\What-IDid` 跑 v1.2.2，采集由计划任务 `WhatIDid-Recorder` 拉起，心跳正常
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
- [x] 建立本文件与 `AGENTS.md`、`agents/*`（仓库即事实来源），并入 `888c30f`
- [x] 1104 用新提示词重出报告（旧版备份 `reports/2026-11-04.json.bak-20261105`，仅本机）
- [x] **两轮「克隆后五问」文档纠偏**，并入 `227935c`（17 项）。做法：从 GitHub 克隆纯净副本 →
      全新会话只读它回答五个基本问题 → 按它指出的矛盾逐条核代码再改；改完再造一份「推送后快照」
      让第二个新会话复查。第一轮查出 11 处，第二轮查出 8 处（含我自己改错的两处）。
      修的类别：`dev/` 根假设、已过期的进度摘要、计划任务条数、「正式采集不 OCR」、`scripts/*` 的
      `.build/` 过期路径、写死的回归项数、`publish.py` / `WORKSPACE.md` 的本机引用；
      另补 `requirements.txt`（9 个包按实测版本钉住）并让 `build_dist.py` 按它安装。

## 进行中

无。工作区干净，没有半成品改动。

## 已知问题

1. **DPI 与 GUI 点级回归本版未跑**（`verify_dpi.py` 14 项、`gui_regression.py` 约 41 项）。
   发布说明已如实标注。没跑 GUI 回归是**故意的**：它会真开界面、真点击，而采集正在运行，
   这些动作会进当天活动块、污染日报——要跑就先 `wid.py pause`。另：`gui_regression` 的项数随
   条件分支浮动（静态约 36 个 `check()` 调用点），文档已改成「不写死数字，看通过/失败两栏」。
2. **`release/v1.2.2/RELEASE_NOTES.md` 与 GitHub Release 正文的 `Source commit` 是旧值**（`1e4a5ed`），
   应为 `1a6d251`。本地文件可直接改（不重打包，zip 与指纹都不受影响），网页需手动改。
3. **`src/doctor.py:38` 的 Python 门槛写的是 `>= (3, 9)`**，代码实际要求 3.10。
   机理说清楚：所有模块都有 `from __future__ import annotations`，所以 `str | None` 在 3.9 上不会崩，
   `doctor` 的 import 链（i18n / paths / privacy / timeutil）也不经过 `vision`——真正 3.10-only 的是
   `src/vision.py:24` 的 `int.bit_count()`。结果是 **3.9 上 `doctor` 报绿，采集阶段才炸**，
   这比直接报红更糟。改一行即可，但要重建 dist，留到下次动 `doctor.py` 时一起做。
4. **`src/paths.py:56-60` 有一段等价分支**：带 `portable.marker` 的分支与紧随其后的「可写就用程序目录」
   返回同一个值，marker 实际不起作用；`docs/architecture*` 也只写了三级路径、没提这个 marker。
   要么删掉分支，要么让 marker 真能强制便携。改它要重建 dist，留到下次动 `paths.py` 时一起做。
5. **`W:` 共享对 Windows 侧不可写已有文件**（`Permission denied`，非权限位、非占用），
   所以所有改动只能以 `.new` 中转，由 iMac 侧 `mv -f`。
6. **Windows 侧无 git 凭据**，push / tag / GitHub Release 只能 iMac 侧做。
7. **同步是手动的**（`git fetch && git reset --mixed`）。推送后不归零，下次改动的 diff 里会混进旧提交。
8. **本文件依赖人/agent 主动更新**，没有任何自动检查会发现它过期。已经错过三次：
   一次写「6 项待提交」而实际已推送、一次写「工作区干净」而实际 15 项没提交、
   一次写「17 项未提交」而当时刚提交完。**根因是写死当前 HEAD 与未提交项清单**——文件一被提交，
   这两个值必然变化。现在的做法：状态一律写成不变量 + 自查命令（见「当前状态」开头），
   字面量只留给已经不动了的提交号。改这一节时先跑自查，别凭上一次的记忆写。
9. **块清单的「分类」是本地按标题猜的**（含「文档/.md」的标题会被猜成 writing）。
   它只用于聚类，不能作为行为判定依据，prompt 里已明确写死。
10. **`dist/` 与 v1.2.2 的 zip 构建于 `227935c` 之前**。那次提交只动文档、注释、`scripts/`（不进包）
    与 `build_dist.py`（不进包），唯一进包的是 `config.default.yaml` 里的一行注释——注释只影响
    「首次运行生成 config.yaml」时的可读性，不影响任何行为。所以不重打包：同一版本号换资产会让
    已发布的 sha256 失效，代价大于收益。下一次因代码升版时才自然带上。

## 下一步

1. 改 `release/v1.2.2/RELEASE_NOTES.md` 的 `Source commit` 为 `1a6d251`，并同步改 GitHub Release 正文
   （只能人工改，本地文件改完不影响已发布的 zip 与指纹）。
2. 跑 `verify_dpi.py` 与 `gui_regression.py`：先 `wid.py pause`（否则测试会污染当天日报），
   `WID_HOME` 必须是 `.tmp_wid*`，跑完 `wid.py resume`，把通过/失败数记进本节。
3. 观察 2026-11-05 的终版日报是否守住证据档位（重点看有无把「看」写成「写」、有无把群消息写成
   「我参与了」）；若仍有偏差，按 `agents/decisions.md` 的 D-08 调 prompt，而不是加特例。
4. 下次动 `doctor.py` 时把 Python 门槛改成 `(3, 10)`（已知问题 3）。
