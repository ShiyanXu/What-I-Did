# 参与开发

## 环境

Windows 10/11 x64 + Python 3.10+：

```bat
python -m pip install Pillow numpy requests Jinja2 PyYAML psutil
python -m pip install rapidocr onnxruntime      :: 需要内置 OCR 时
```

**开发时始终用独立数据目录**，否则样本会写进你的真实记录：

```bat
set WID_HOME=%CD%\.tmp_wid_dev
```

## 代码约定

- **不引入新依赖**。运行时的依赖清单是固定的：标准库 + `Pillow numpy requests Jinja2 PyYAML psutil`。托盘、去重、调度这些都用标准库或 ctypes 自己实现，新增依赖需要充分理由。
- **路径只从 `src/paths.py` 取**，不要在别处拼路径。
- **判断只有一个来源**。"该不该出报"是 `tick.pending()`；采集启停看 `control.py`；状态快照看 `status.py`；自检看 `doctor.py`。界面和命令行都调这些，不要另写一份。
- **模型只产 JSON，排版在本地**。新增版面改 `src/templates/`，不要指望模型输出格式。
- **结构变化只加字段**，`schema_version` 保持 1；旧数据缺字段按空处理。
- **新增配置项要同时改两处**：`config.default.yaml`（给人看）和 `src/config.py` 的 `DEFAULTS`（参与类型校验与默认合并）。只改前者，`"false"` 这样的字符串会被当成真值。
- **新增 `src/*.py` 要同步 `build.spec` 的 `MODULES`**。那份清单是手写的，静态分析看不到按需导入，漏写会让打包版 `ImportError`，而源码模式完全正常。

## 提交前自测

```bat
python -m py_compile wid.py src\*.py src\refs\*.py scripts\*.py

set WID_HOME=%CD%\.tmp_wid_test
python scripts\dev_fixture.py --day 2026-10-18 --mock
python scripts\check_boundaries.py
python scripts\check_privacy.py
python scripts\verify_dpi.py
```

改到界面还要跑点击级回归：

```bat
python scripts\seed_gui_home.py .tmp_wid_gui
set WID_HOME=%CD%\.tmp_wid_gui
python scripts\gui_regression.py
```

这些脚本都不联网、不需要模型 Key、不碰真实记录。细节见[构建与测试](docs/building.md)。

## 提交信息

一个提交做一件事，说明"为什么"而不只是"改了什么"。涉及行为变化时，在提交正文里写清对旧数据与旧配置的影响。

## 文档

用户可见的行为变化要同步文档：功能改 `docs/usage.md`，配置项改 `docs/configuration.md`，隐私边界改 `docs/privacy.md`，架构或数据模型改 `docs/architecture.md`。

## 隐私红线

任何让图片离开本机的改动都不会被接受。任何绕过"暂停 / 锁屏 / 名单 / 无效配置"的采集路径同样不会——`scripts/check_privacy.py` 会拦住这类改动。
