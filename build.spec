# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成绿色免安装的 onedir 版本。

    优先使用 scripts/build_dist.py；本 spec 也拒绝覆盖任何已存在的输出目录。

产物：dist/What-IDid/What-IDid.exe（整个文件夹可直接拷到任意 Windows 机器）

只有一个 exe：没有控制台，双击进图形界面；带子命令运行时用 AttachConsole
把父控制台接回来，所以命令行照样能用，不会挂黑窗口。
"""
import os
import stat
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve()
SRC = ROOT / "src"

# 即使绕过构建脚本直接使用 PyInstaller，也不能让 --noconfirm 删除便携数据。
OUTPUT = Path(DISTPATH).absolute() / "What-IDid"
for item in (OUTPUT, *OUTPUT.parents):
    if os.path.lexists(item):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SystemExit("构建输出路径含链接/联接点，拒绝操作")
if os.path.lexists(OUTPUT):
    raise SystemExit("输出目录已存在，拒绝覆盖；请使用安全构建脚本，或先备份并移出旧目录")

# src 下的每个模块都要显式声明：入口用 sys.path.insert 动态导入，静态分析看不到
MODULES = [
    "autostart", "behavior", "blocks", "capture", "config", "daemon", "digest", "llm",
    "logging_setup", "locking", "ocr", "paths", "privacy", "render",
    "report", "retention", "tick", "timeutil", "validate", "vision",
    "winapi", "refs", "refs.extract", "refs.providers", "refs.resolve",
    "doctor", "status", "control", "gui", "tray",
    # 人工修订 / 遗忘 / 跨日检索 / 周汇总 / 安全导出：
    # 这几个模块只在子命令里按需导入，静态分析看不到，漏写会在打包版直接 ImportError
    "manual", "forget", "search", "weekly", "export_safe",
]

datas = [
    (str(SRC / "templates"), "src/templates"),
    (str(ROOT / "config.default.yaml"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "LICENSE"), "."),
    # 整个 docs 目录一起打：新增文档不必再来改这份清单
    (str(ROOT / "docs"), "docs"),
    (str(ROOT / "assets"), "assets"),
]

# RapidOCR 把模型和配置 yaml 放在包目录里，必须一起打进去（这样才完全离线）
datas += collect_data_files("rapidocr", include_py_files=False)

hiddenimports = list(MODULES) + [
    "rapidocr", "onnxruntime", "onnxruntime.capi._pybind_state",
    "cv2", "shapely", "pyclipper", "yaml", "jinja2.ext",
]
hiddenimports += collect_submodules("rapidocr")

binaries = collect_dynamic_libs("onnxruntime") + collect_dynamic_libs("cv2")

# 明确排除的重量级模块，避免把没用的东西打进来
excludes = [
    "matplotlib", "scipy", "pandas", "IPython", "jupyter",
    "pytest", "setuptools", "pip", "wheel", "sqlite3", "PIL.ImageQt",
    "paddle", "paddleocr", "torch", "transformers", "openvino",
]

a = Analysis(
    [str(ROOT / "wid.py")],
    pathex=[str(SRC), str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

ICON = str(ROOT / "assets" / "app.ico") if (ROOT / "assets" / "app.ico").exists() else None

# 唯一的可执行文件：无控制台。双击 = 图形界面；带子命令 = 命令行（AttachConsole 接回父控制台）
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="What-IDid",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX 压缩容易被杀软误报，且对 onnx 无收益
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="What-IDid",
)
