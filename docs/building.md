# 构建与测试

面向想改代码或自己打包的人。只使用程序的话读 [README](../README.md) 就够了。

## 环境

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11 x64（依赖 Win32 的窗口、空闲、锁屏查询） |
| Python | 3.10+（源码模式） |
| 运行时依赖 | `Pillow` `numpy` `requests` `Jinja2` `PyYAML` `psutil` |
| 可选依赖 | `rapidocr` `onnxruntime`（内置离线 OCR，约 100 MB） |
| 打包依赖 | `pyinstaller` |

没有数据库、没有 Web 框架、没有任务调度库——调度用 Windows 计划任务，托盘用 ctypes 手写。

## 源码运行

```bat
python -m pip install Pillow numpy requests Jinja2 PyYAML psutil
python -m pip install rapidocr onnxruntime      :: 想用内置 OCR 才需要

python wid.py setup
python wid.py doctor
python wid.py collect --once                    :: 采一次看看
python wid.py start                             :: 后台常驻
python wid.py report --date 2026-10-19 --force
```

调试常驻逻辑用 `python wid.py daemon`，它会占住当前控制台。`wid.py` 与打包后的 exe 是同一套子命令。

**开发时务必用独立数据目录**，否则会往真实记录里写样本：

```bat
set WID_HOME=%CD%\.tmp_wid_dev
```

## 测试脚本

全部在 `scripts/` 下，都不联网、不碰真实记录、不需要模型 Key。

| 脚本 | 覆盖面 | 用法 |
| --- | --- | --- |
| `dev_fixture.py` | 造一天合成事件 + 假模型跑通全链路 | `python scripts\dev_fixture.py --day 2026-10-18 --mock`；`--clean` 删除 |
| `check_privacy.py` | 隐私与配置失效 73 项：伪造凭据注入、关闭外发后零请求、暂停/锁屏/名单、名单收紧后重分块、OCR 地址校验、裁剪失败不抓屏、构建保护 | `python scripts\check_privacy.py` |
| `check_boundaries.py` | 边界与异常路径 27 项：空关键词、日期越界、损坏 JSON、空周、重复补录、非法时间 | 需先有样本；`python scripts\check_boundaries.py` |
| `verify_dpi.py` | DPI 与多屏 14 项：抓图尺寸 = 窗口矩形、跨屏原点偏移、全屏 = 虚拟桌面、坐标不一致时拒绝落盘 | `python scripts\verify_dpi.py` |
| `gui_regression.py` | 点击级界面 41 项：四页切换、补录校验、遗忘确认、审核发送/丢弃 | 先用 `seed_gui_home.py` 准备环境 |
| `seed_gui_home.py` | 一键准备 GUI 回归环境（只允许写 `.tmp_wid*`） | `python scripts\seed_gui_home.py .tmp_wid_gui` |
| `fake_llm_server.py` | 本地 OpenAI 兼容假模型，回包的 `block_id` / `seg_id` 从请求正文现抓 | `python scripts\fake_llm_server.py 8799` |

典型一轮：

```bat
set WID_HOME=%CD%\.tmp_wid_test
python scripts\dev_fixture.py --day 2026-10-18 --mock
python scripts\check_boundaries.py
python scripts\check_privacy.py
```

测试假模型时，把 `config.llm.base_url` 指到 `http://127.0.0.1:8799/v1`，就能在**不打真实模型**的前提下走完"审核预览 → 确认发送 → 校验 → 渲染"。

## 打包

```bat
python scripts\build_dist.py            :: 增量构建，复用 .build\venv
python scripts\build_dist.py --fresh    :: 依赖变了就重建虚拟环境
```

产物：

```
dist\What-IDid\
  What-IDid.exe              约 5.5 MB，唯一入口
  _internal\                 约 244 MB：Python 运行时、Tk、内置 OCR 模型（30 MB）、模板、图标
  .wid-build-manifest.json   全部文件的 sha256 清单
```

已内置 VC++ 运行时（`vcruntime140.dll` / `msvcp140.dll`），目标机器不需要装 redist。

### 一个 exe 为什么能同时当界面和命令行

程序以「无控制台」方式链接，双击不弹黑框。带子命令运行时调 `AttachConsole` 接上调用它的控制台，输出照常打印；接不上才自己开一个。

副作用：cmd 不会等待无控制台进程，所以会「先归还提示符、再打印输出」。输出不会丢，可能与后续命令交错；要用管道就 `| more`，或用 `start /wait`。

### 构建保护

`build_dist.py` 在下载依赖之前就检查输出目录，遇到下列情况直接拒绝：

- 目录里有运行数据（`data\`、`reports\`、`config\` 等未知顶层内容）；
- 有目录链接或联接点；
- 旧产物缺少安全清单，或清单与内容不再一致（说明被改过）。

它只在"确认是纯构建产物"时才把旧目录挪到 `.build\trash`，`--keep-old` 控制的是成功之后是否保留备份，不是跳过备份。直接用 `build.spec` 打包也拒绝覆盖已存在的目标目录。

要更新一个装着个人数据的便携目录，先停掉采集与界面，手动备份并移出整个旧目录，再构建到空目录——不要删掉这些保护。

### 加代码时必须同步的地方

`build.spec` 里的 `MODULES` 是**手写清单**。PyInstaller 的静态分析看不到按需导入的模块，漏写会在打包版直接 `ImportError`，而源码模式完全正常。新增 `src/*.py` 后：

```bat
:: 比对 src 模块与 MODULES 是否一致
python - <<"EOF"
import pathlib, re
src = {p.stem for p in pathlib.Path("src").glob("*.py")} - {"__init__"}
spec = pathlib.Path("build.spec").read_text(encoding="utf-8")
mods = set(re.findall(r'"([a-z_]+)"', spec.split("MODULES")[1].split("]")[0]))
print("缺:", sorted(src - mods), " 多余:", sorted(mods - src))
EOF
```

新增配置项时要同时改两处：`config.default.yaml`（给人看）和 `src/config.py` 的 `DEFAULTS`（参与类型校验与默认合并）。只改前者不会进校验，字符串 `"false"` 会被当成真值。

## 分发

把 `dist\What-IDid\` 整个目录压成 zip 即可，解压后双击运行。目录里不要预置 `config\` 与 `data\`——那是使用者自己的东西。

分发包建议同时给出 SHA256，方便使用者校验。

## 常见陷阱

| 现象 | 原因与处理 |
| --- | --- |
| 截图全黑 | 计划任务被设成「不管用户是否登录都运行」，进程落在 Session 0。必须用「仅在用户登录时运行」 |
| 打包版启动即崩、提示 stdout 为 None | 无控制台程序的 `sys.stdout` 是 `None`，任何 `print` 都会炸。入口处与日志初始化都要判空 |
| 子进程卡住父命令 | `subprocess.Popen` 拉起的后台进程必须把 stdout/stderr 全部指向 `DEVNULL`，否则父进程等管道关闭 |
| 本机 HTTP 请求走代理失败 | 系统可能配了 `HTTP_PROXY`。本机请求统一用 `requests.Session(trust_env=False)` |
| RapidOCR 输出一堆 INFO 盖住自己的日志 | 它在构造时会把 logger 重新配回 INFO，必须在构造前后各压一次级别 |
| 托盘图标不在常驻区 | Windows 10/11 默认把新图标放进「^」溢出区，没有 API 能强制常驻，只能提示用户拖出来一次 |
| 托盘菜单点外面不消失 | 弹菜单前要 `SetForegroundWindow`，弹完补一个 `WM_NULL` |
| DPI 感知"设了但没生效" | `ctypes.windll` 不检查 HRESULT，失败不抛异常。要显式看返回值，并留桌面级兜底 |
| 计划任务注册失败 | 本仓库的 `autostart.py` 优先用户级计划任务，失败降级到 `HKCU\...\Run`；两者都不行时提示手动 |
| `tar -a -cf x.zip` 打出来不是 zip | Git for Windows 的 tar 不生成 zip，用 Python 的 `zipfile` 或 7-Zip |
| 打包目录被占用 | 有 `What-IDid.exe` 在跑。先 `What-IDid.exe stop` 并退出托盘 |

## 目录约定

| 目录 | 内容 | 是否入库 |
| --- | --- | --- |
| `src/` `scripts/` `docs/` | 源码、脚本、文档 | 是 |
| `config.default.yaml` | 配置模板 | 是 |
| `config/` | 本机的 Key 与绑定 | 否 |
| `data/` `reports/` `web/` `state/` `logs/` | 运行数据 | 否 |
| `.build/` `dist/` | 构建中间产物与成品 | 否 |
| `.tmp_wid*/` | 测试用隔离数据目录 | 否 |
