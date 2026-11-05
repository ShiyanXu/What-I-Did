# PROGRESS.md — What-IDid

> **这是唯一的进度事实来源。** 新会话从这里接续，不要凭记忆或聊天记录猜。
> 维护规则：每次会话结束前更新本文件；只写事实与下一步，不写过程感想。
> 最后更新：2026-11-05 16:15

## 当前状态

- **版本**：`1.2.2`（`src/gui.py:VERSION`，唯一来源）
- **仓库**：HEAD `888c30f` = `origin/main`；tag `v1.2.2`（附注标签 `85b0c67`）指向 `1a6d251`
- **工作区**：**不干净**——本轮文档纠偏共 17 项未提交（16 改 + 1 新增），明细见「进行中」；
  除此之外没有其他改动
- **已发布**：GitHub Release `v1.2.2`（<https://github.com/ShiyanXu/What-I-Did/releases/tag/v1.2.2>），
  资产 `What-IDid-win64-1.2.2-20261105.zip`（116,130,117 字节 / 1104 条目 / sha256 `36f1d3c4…df1e`，
  与 `release/v1.2.2/SHA256SUMS.txt` 一致）
- **自测**：`check_privacy` 73/73、`check_boundaries` 27/27（2026-11-05 16:10 实跑确认）；
  DPI 与 GUI 回归**本版未跑**
- **文档**：`AGENTS.md`、本文件、`agents/` 下 6 份已随 `888c30f` 入库；本轮又做了一轮纠偏（未提交）
- **生产实例**：`D:\What-IDid` 跑 v1.2.2，采集由计划任务 `WhatIDid-Recorder` 拉起，心跳正常
- **唯一未收尾的元信息**：`release/v1.2.2/RELEASE_NOTES.md` 的 `Source commit` 仍写 `1e4a5ed`
  （推送前 HEAD）+「含未推送改动」，应改为 `1a6d251`；GitHub Release 正文同样还是旧值

**自查（不要只信上面的断言，直接跑）**：

```bash
git log --oneline -1                 # 应为 888c30f
git status --short                   # 应只有「进行中」列出的那些文件，不该有别的
git tag -l "v1.2*"                   # 应含 v1.2.2
git rev-parse v1.2.2^{commit}        # 应为 1a6d251（不是 888c30f，文档提交在 tag 之后）
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

## 进行中

- [ ] **文档纠偏（两轮，未提交）**。做法：从 GitHub 克隆纯净副本 → 全新会话只读它回答五个基本问题 →
  按它指出的矛盾逐条核代码再改。第一轮查出 11 处，第二轮复查又查出 8 处（含我自己改错的两处）。
  待提交的 17 项：

  ```
  M  AGENTS.md                    M  docs/configuration.md       M  scripts/build_dist.py
  M  PROGRESS.md                  M  docs/configuration.zh-CN.md M  scripts/fake_llm_server.py
  M  agents/architecture.md       M  config.default.yaml         M  scripts/gui_regression.py
  M  agents/environment.md        M  docs/building.md            M  scripts/seed_gui_home.py
  M  agents/verification.md       M  docs/building.zh-CN.md      M  scripts/verify_dpi.py
  M  agents/workflow.md           ?? requirements.txt
  ```

  改了什么：`dev/` 根假设改成仓库根、进度摘要改成已发布、「三条计划任务」改成两条（两处）、
  「正式采集不 OCR」改成「OCR 只对落盘帧做」（三处）、`scripts/*` 的 `.build/` 过期路径、
  `publish.py` / `WORKSPACE.md` 标注为本机且给出等价手工步骤、`gui_regression` 去掉写死项数（三处）、
  `requirements.txt` 落盘并让 `build_dist.py` 按它安装、`docs/building*` 的 `bat` 块 heredoc 改单行、
  两个脚本的 CRLF 转回 LF。
- [ ] **提交方式**：改动量已超出「转载 `.new`」的舒适区（17 项）。建议这次直接在提交侧
  `git add -A` 一次提交，而不是逐个 `.new` 搬运。

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
8. **本文件依赖人/agent 主动更新**，没有任何自动检查会发现它过期。看到版本号与 `src/gui.py:VERSION`
   对不上时，先修这里；看到 git 状态段与 `git status` 对不上时，同样先修这里——这一条已经错过两次
   （一次写「6 项待提交」实际已推送，一次写「工作区干净」实际 15 项没提交）。
9. **块清单的「分类」是本地按标题猜的**（含「文档/.md」的标题会被猜成 writing）。
   它只用于聚类，不能作为行为判定依据，prompt 里已明确写死。
10. **`dist/` 与 v1.2.2 的 zip 构建于本轮改动之前**。本轮只动文档、注释、`scripts/`（不进包）与
    `build_dist.py`（不进包），唯一进包的是 `config.default.yaml` 里的一行注释——而注释只影响
    「首次运行生成 config.yaml」时的可读性，不影响任何行为。所以不重打包：同一版本号换资产会让
    已发布的 sha256 失效，代价大于收益。下一次因代码升版时才自然带上。

## 下一步

1. 把本轮文档纠偏 + `requirements.txt` 提交推送（一次 commit），并顺手把
   `release/v1.2.2/RELEASE_NOTES.md` 的 `Source commit` 改成 `1a6d251`、同步改 GitHub Release 正文。
2. 跑 `verify_dpi.py` 与 `gui_regression.py`：先 `wid.py pause`（否则测试会污染当天日报），
   `WID_HOME` 必须是 `.tmp_wid*`，跑完 `wid.py resume`，把通过/失败数记进本节。
3. 观察 2026-11-05 的终版日报是否守住证据档位（重点看有无把「看」写成「写」、有无把群消息写成
   「我参与了」）；若仍有偏差，按 `agents/decisions.md` 的 D-08 调 prompt，而不是加特例。
4. 下次动 `doctor.py` 时把 Python 门槛改成 `(3, 10)`（已知问题 3）。
