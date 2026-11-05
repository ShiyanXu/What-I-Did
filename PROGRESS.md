# PROGRESS.md — What-IDid

> **这是唯一的进度事实来源。** 新会话从这里接续，不要凭记忆或聊天记录猜。
> 维护规则：每次会话结束前更新本文件；只写事实与下一步，不写过程感想。
> 最后更新：2026-11-05 15:50

## 当前状态

- **版本**：`1.2.2`（`src/gui.py:VERSION`，唯一来源）
- **dev 工作区**：HEAD `1e4a5ed`（= 远端 `origin/main`）
  - 已跟踪文件改动 3 个：`src/digest.py`、`src/gui.py`、`CHANGELOG.md`
  - 新增未跟踪 3 项：`AGENTS.md`、`PROGRESS.md`、`agents/`（本套工程文档）
- **自测**：`check_privacy` 73/73 通过，`check_boundaries` 27/27 通过；DPI 与 GUI 回归**未跑**
- **构建**：`dev/dist/What-IDid/` 已重建 `[4/4]`，247 MB，exe sha256 `7f898a2d…a4737f`
- **生产实例**：`D:\What-IDid` 已换 v1.2.2，采集由计划任务 `WhatIDid-Recorder` 拉起，心跳正常
- **发布产物**：`release/v1.2.2/` 三件套齐全（zip 116,130,117 字节 / 1104 条目 / sha256 `36f1d3c4…`）
- **未完成的外部件**：commit、push、tag `v1.2.2`、GitHub Release —— 都在 iMac 侧，需人工执行

**自查（不要只信上面的断言，直接跑）**：

```bash
git -C dev status --short        # 应为本节列出的 6 项
git -C dev log --oneline -1      # 应为 1e4a5ed
PY=.../Python310/python.exe; $PY wid.py status   # 心跳 ts 应在前进
ls release/v1.2.2                # 应有 zip + SHA256SUMS.txt + RELEASE_NOTES.md
sha256sum D:/What-IDid/What-IDid.exe dev/dist/What-IDid/What-IDid.exe  # 两者应相同
```

**生产实例与 iMac 侧进度不在仓库里**（`D:\What-IDid` 的运行状态、`W:` 上 `.new` 是否已被 `mv`、
是否已 tag/push），只能现场查。

## 已完成

- [x] v1.0 首次公开发布（采集 + OCR + 日报 + 看板）
- [x] v1.1 日报改为「成果简报」：成果卡、待办与卡点、工作主线、时间色带、证据折叠
- [x] v1.2 界面英文化 + 中文可切换；文档全双语
- [x] v1.2.1 修复切语言后侧栏/图例不跟随（模块级 `t()` 固化）
- [x] v1.2.2 日报引入证据分档：写作类动作必须有输入态或文字增量证据；聊天软件只认「已发出」
- [x] 1104 用新提示词重出报告（旧版备份 `reports/2026-11-04.json.bak-20261105`）
- [x] 建立本文件与 `AGENTS.md`、`agents/*`（仓库即事实来源）

## 进行中

- [ ] **v1.2.2 入库**（约 90%）：文件已在 `W:` 的 `.new`，等 iMac 侧 `mv -f` → commit → push → tag → Release
- [ ] **`RELEASE_NOTES.md` 的 `Source commit` 待更正**：现写 `1e4a5ed` +「含未推送改动」；
      推送后要用真实提交号重生成（dev 与远端同步后可直接 `publish.py --force`；
      dev 还脏时按 `agents/workflow.md` 第五节调底层函数，不要绕过 `check_repo()`）

## 已知问题

1. **DPI 与 GUI 点级回归未跑**（`verify_dpi.py` 14 项、`gui_regression.py` 41 项）。发布说明里已如实标注。
2. **`W:` 共享对 Windows 侧不可写已有文件**（`Permission denied`，非权限位、非占用），
   所以所有改动只能以 `.new` 中转，由 iMac 侧 `mv -f`。
3. **Windows 侧无 git 凭据**，push / tag / GitHub Release 只能 iMac 侧做。
4. **`dev/` 与 `origin/main` 的同步是手动的**（`git fetch && git reset --mixed`），
   用户推送后必须手动归零，否则下次改动的 diff 里会混进旧提交。
5. **`PROGRESS.md` 依赖人/agent 主动更新**——没有任何自动检查会发现它过期。
   看到一个版本号对不上 `src/gui.py:VERSION` 时，先修这里。
6. **块清单的「分类」是本地按标题猜的**（含「文档/.md」的标题会被猜成 writing）。
   它只用于聚类，不能作为行为判定依据，prompt 里已明确写死。
7. **`WORKSPACE.md` 曾有若干过时描述**（版本号行号、zip 命名、`check_repo` 冲突），
   已于 2026-11-05 校正；同类陈述以代码为准。

## 下一步

1. iMac 侧：`mv -f src/digest.py.new src/digest.py`、`mv -f src/gui.py.new src/gui.py`、
   `mv -f CHANGELOG.md.new CHANGELOG.md`，然后 `git add -A` 提交并 push `main`。
2. 打 tag `v1.2.2` 并推送；建 GitHub Release，资产用 `W:/What-IDid-win64-1.2.2-20261105.zip`。
3. 推送后回报：Windows 侧 `git fetch` → `git reset --mixed` 归零 → 用真实提交号重生成
   `release/v1.2.2/RELEASE_NOTES.md`（`publish.py --force`），同步告知改 Release 正文。
4. 补跑 `verify_dpi.py` 与 `gui_regression.py`，把结果记进本节，消除「已知问题 1」。
5. 观察 2026-11-05 的终版日报是否守住证据档位（重点看有无把「看」写成「写」、
   有无把群消息写成「我参与了」）；若仍有偏差，按 `agents/decisions.md` 里 D-08 的思路调 prompt，
   而不是加特例。
