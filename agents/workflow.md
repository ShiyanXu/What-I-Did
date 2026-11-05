# 工作流：改 → 验证 → 转载 → 发布

> 三个位置的分工见工作区根目录的 `WORKSPACE.md`；这份是**执行顺序**，照着做不会漏步。

## 一、三个位置，一句话

| 位置 | 角色 | 允许的操作 |
|---|---|---|
| `E:\Self\Project\What-I-Did\dev\` | 唯一工作副本 | 改代码、跑自测、构建、跑实例。**禁止 commit / push / tag** |
| `W:\What-I-Did\` | 提交源（iMac 侧） | Windows 侧只能**新建**文件（`.new`）；`mv` / commit / push 由 iMac 侧做 |
| `E:\Self\Project\What-I-Did\release\v<版本>\` | 不可变产物 | 只由 `publish.py` 写入，写完不再改 |

## 二、改完代码的标准流水线（一次做完再汇报）

```bash
cd /e/Self/Project/What-I-Did/dev
PY=C:/Users/xushiyan/AppData/Local/Programs/Python/Python310/python.exe

# 1. 同步：确认工作区只剩本次改动
git fetch origin && git reset --mixed origin/main
git status --short

# 2. 自测
$PY scripts/check_privacy.py && $PY scripts/check_boundaries.py

# 3. 按需重建（有输出才重建，60–80 秒）
find src wid.py config.default.yaml scripts build.spec -newer dist/What-IDid/What-IDid.exe
$PY scripts/build_dist.py            # 以 [4/4] 与 .wid-build-manifest.json 为准

# 4. 落 release（约 20 秒）
cd /e/Self/Project/What-I-Did && $PY publish.py --no-build

# 5. 换生产实例（保留数据）见第四节；6. 转载 W: 见第三节
```

**判定标准**：自测 0 failed、`[4/4]`、`release/v<版本>/` 三件套齐全、生产 exe 的 sha256 与
`dev/dist` 一致、采集心跳在前进。

## 三、转载到 `W:`

```bash
cd /e/Self/Project/What-I-Did/dev
cp src/digest.py /w/What-I-Did/src/digest.py.new      # 改过的文件一律 .new
cp NEW_FILE.md   /w/What-I-Did/NEW_FILE.md            # 仓库里原本没有的，直接 cp
sha256sum src/digest.py /w/What-I-Did/src/digest.py.new   # 必须一致
cp ../release/v1.2.2/What-IDid-win64-*.zip /w/            # zip 放共享盘根目录
```

转载前检查：`find /w/What-I-Did -name "*.new"` 必须为空（残留会让 iMac 侧 `mv` 到错内容）；
`W:` 的 git 工作区应是干净的。转载后把 **`mv -f` 清单**（每个 `.new` 对应的目标路径）写给用户。

## 四、换生产实例 `D:\What-IDid`（保留数据）

1. `./What-IDid.exe stop` 停采集（只杀 daemon）；界面另用 `psutil.Process(pid).terminate()`。
2. `mv What-IDid.exe What-IDid.exe.old && mv _internal _internal.old`
3. `cp -a dev/dist/What-IDid/. /d/What-IDid/`（不带 data/reports/web/config）
4. 计划任务拉起：`Start-ScheduledTask WhatIDid-Recorder`（需要界面再加 `WhatIDid-LaunchGui`）
5. 校验：exe sha256 与 `dev/dist` 一致、`wid.py status` 心跳前进、历史数据还在
6. `rm -rf *.old`

## 五、发布（`publish.py`）

| 命令 | 用途 |
|---|---|
| `publish.py` | 全流程：自测 → 构建 → 校验产物 → 打 zip → 落 `release/` |
| `--no-build` | 复用 `dev/dist`，只重新打 zip 与落盘（日常用这个） |
| `--dry-run` | 只打印计划 |
| `--full` | 额外跑 DPI + GUI 回归（慢几分钟） |
| `--force` | 覆盖已存在的同版本目录（只改说明、不升号时用） |

`check_repo()` 要求「dev 干净且与 `origin/main` 同步」。**改完还没推送时它必然拦下**，
这时不要绕过，直接调底层函数：

```python
import publish
v = publish.read_version(); notes = publish.read_changelog(v)
publish.verify_artifact(); zp = publish.make_zip(v); n = publish.verify_zip(zp)
publish.publish(zp, v, publish.git('rev-parse','--short','HEAD').strip(), n, False, True, notes)
```

## 六、版本与发布节奏

- **每次代码更新都要在 `release/v<当前 VERSION>/` 生成完整产物**（zip + `SHA256SUMS.txt` +
  `RELEASE_NOTES.md`），不等用户发话。变更段取自 `CHANGELOG.md` 的 `## [v<版本>]`。
- **落 `release/` ≠ 发版**。发版的唯一判据是 tag `v<版本>` 与 GitHub Release 页面，这两步永远由用户做。
- **版本号唯一来源是 `src/gui.py:VERSION`**；改了它就要在 `CHANGELOG.md` 加同号段落（英文在前、中文在后）。
- **zip 名带版本**：`What-IDid-win64-<版本>-<YYYYMMDD>.zip`。只带日期会同日撞名，
  `make_zip` 会 unlink 旧包。
- **zip 永不进 git**（110 MB 超单文件限制），出口是 GitHub Release 资产。
- **提交号会在推送后变错**：代码未推时落盘，`Source commit` 记的是推送前 HEAD。
  用户说「已提交」后必须 `git fetch` 取真实提交号，改 `RELEASE_NOTES.md`，并请用户同步改 Release 正文
  （Windows 侧只能改本地文件）。
- 只有用户明确说「要发版 / 合并 release note」时，才把中间未入库的版本段并入本次版本段；
  合并后若没升号，必须 `publish.py --force` 重生成。

## 七、会话结束时（清洁状态）

按顺序做，缺一项都不算「做完」：

1. 自测全绿（隐私 + 边界）。
2. `PROGRESS.md` 五段已更新（当前状态 / 已完成 / 进行中 / 已知问题 / 下一步）。
3. 临时目录、调试输出、`.tmp_*`、被注释掉的代码清干净。
4. 需要交给 iMac 侧的改动已转载成 `.new`，`mv` 清单已写出。
5. 构建与运行路径可用：`wid.py doctor --full` 无 failed，采集心跳在动。
