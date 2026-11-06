# 仓库地图

> 从 `AGENTS.md` 转来：入口只留路由，目录树与「哪些路径不属于仓库」放在这里。
> 路径一律相对**仓库根**；标了「本机」的在克隆里不存在。

## 一、仓库根

```
.                          ← 仓库根（AGENTS.md 所在处）
├── wid.py                 ← 单入口：23 个子命令（无参数即界面），也是打包后的唯一 exe
├── src/                   ← 34 个模块（含 i18n 词条表），见 architecture.md 的分层表
│   ├── templates/         ← Jinja2：day / index / report.md / _style
│   └── refs/              ← 文档引用解析（providers / extract / resolve）
├── scripts/               ← harness、build_dist、check_privacy、check_boundaries、dev_fixture…
├── feature_list.json      ← 机器可读功能状态；只能由 scripts/harness.py 改状态
├── init.sh                ← 依赖 + 完整检查 + 标准启动入口
├── sprint-contract.md     ← 冲刺目标、范围、验证标准、排除项与停止条件
├── clean-state-checklist.md   ← 收尾逐项自检
├── session-handoff.md     ← 长会话交接（短会话写 PROGRESS.md 即可）
├── evaluator-rubric.md    ← 独立验收评分表
├── quality-document.md    ← 各区域质量快照（变强还是变弱）
├── graph.md               ← 单循环路由蓝图与图化升级判据
├── docs/                  ← 面向使用者的双语文档（会打进发布包）
├── agents/                ← 本目录：给 agent 看的工程文档（不打包）
├── requirements.txt       ← 运行与构建依赖的版本基线
├── config.default.yaml    ← 配置模板（入库）；config.yaml 是本机实际配置（不入库）
└── data/ reports/ web/ state/ logs/ dist/   ← 运行产物，全部 .gitignore，克隆里没有
```

## 二、本机（所有者机器）额外多出来的东西

克隆里不存在这些：

- 工作副本 `dev/`（改代码、自测、构建都在这里，永不 commit / push / tag）
- 发布产物 `release/v<版本>/`
- 打包脚本 `publish.py`
- 工作区分工说明 `WORKSPACE.md`
- 生产实例 `D:\What-IDid`（含真实数据）
- SMB 上的提交源 `W:\What-I-Did`（由 iMac 侧提交）

要从头复现发布流程，凭 `workflow.md` 第五节即可，不需要这些文件。

## 三、`git status` 里出现未跟踪或已修改的文件

若 `git status --short` 里出现上表文件是未跟踪（`??`）或已修改（`M`），只说明**本机改动还没经提交侧入库**：
以磁盘为准继续工作，不要回退、不要重建、也不要据此认为文档失实；入库流程见 `workflow.md`。

## 四、共享盘上的裸仓库

`W:\What-I-Did` 本机可见，但它是 SMB 共享（服务端 macOS），两侧看到的权限位不一致——
`init.sh` 这类文件会长期显示 `M`。本仓库已设 `core.fileMode false` 关掉权限位比较，
只比较内容。**这是仓库级本地设置，不随仓库分发**；新克隆若也落在这种共享盘上，同样执行一次。
