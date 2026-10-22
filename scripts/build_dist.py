"""一键构建绿色免安装版（开发者用，不是运行时依赖）。

    python scripts/build_dist.py              # 增量构建（复用 .build/venv）
    python scripts/build_dist.py --fresh      # 重建虚拟环境（依赖变化时用）

产物：dist/What-IDid/
    What-IDid.exe        唯一入口：双击进界面，带子命令当命令行用
    _internal/           运行时、内置 OCR 模型、模板、默认配置

安全边界：仅可自动替换带完整清单、未改动的纯构建产物。
含便携数据、未知文件、链接或无清单的旧版目录一律拒绝；先自行备份并移出。
旧产物始终先保留；--keep-old 只控制成功后是否清理备份，不绕过保护。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".build" / "venv"
DIST = ROOT / "dist"
OUT = DIST / "What-IDid"
TRASH = ROOT / ".build" / "trash"
MANIFEST = ".wid-build-manifest.json"
ARTIFACT_NAMES = {"What-IDid.exe", "_internal", MANIFEST}

PKGS = [
    "rapidocr", "onnxruntime",
    "Pillow", "numpy", "requests", "Jinja2", "PyYAML", "psutil",
]
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


class BuildSafetyError(RuntimeError):
    pass


def _check_link(path: Path) -> None:
    if not os.path.lexists(path):
        return
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
        raise BuildSafetyError(f"拒绝操作链接/联接点：{path}")


def _check_parents(path: Path) -> None:
    for item in (path, *path.parents):
        _check_link(item)


def _layout(path: Path, complete: bool = True) -> None:
    _check_parents(path)
    if not path.is_dir():
        raise BuildSafetyError(f"产物路径不是普通目录：{path}")
    unknown = {p.name for p in path.iterdir()} - ARTIFACT_NAMES
    if unknown:
        raise BuildSafetyError(
            "旧输出含运行数据或未知顶层文件，拒绝移动/覆盖/删除。"
            "请停止全部程序及计划任务，备份并移出整个旧目录；后续使用独立 WID_HOME。"
        )
    if complete and (not (path / "What-IDid.exe").is_file()
                     or not (path / "_internal").is_dir()
                     or (path / "What-IDid.exe").stat().st_size == 0):
        raise BuildSafetyError("产物不完整，保留旧目录，不执行自动清理")


def _inventory(path: Path) -> dict:
    """哈希覆盖所有内部文件；不能把 _internal 当成任意可删除的黑箱。"""
    files: dict[str, str] = {}
    dirs: list[str] = []
    pending = [path]
    while pending:
        parent = pending.pop()
        for item in sorted(parent.iterdir()):
            _check_link(item)
            rel = item.relative_to(path).as_posix()
            if item.is_dir():
                dirs.append(rel)
                pending.append(item)
            elif item.is_file():
                if rel == MANIFEST:
                    continue
                digest = hashlib.sha256()
                with item.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                files[rel] = digest.hexdigest()
            else:
                raise BuildSafetyError("产物含非普通文件，拒绝自动操作")
    return {"version": 1, "files": files, "dirs": sorted(dirs)}


def verify_artifact(path: Path) -> None:
    _layout(path)
    manifest = path / MANIFEST
    _check_link(manifest)
    if not manifest.is_file():
        raise BuildSafetyError(
            "旧版产物没有安全清单，不能确认其中是否有用户数据。"
            "请自行备份并移出整个旧目录后再构建；不会自动补清单或删除旧版。"
        )
    try:
        expected = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BuildSafetyError("产物清单不可读，保留目录") from exc
    if expected != _inventory(path):
        raise BuildSafetyError("产物文件、内容或目录已改变，可能包含用户数据；拒绝自动清理")


def write_manifest() -> None:
    _layout(OUT)
    manifest = OUT / MANIFEST
    data = _inventory(OUT)
    # 只为本次新产物建立清单，绝不替未知旧目录补发可删除资格。
    with manifest.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2))
        stream.flush()
        os.fsync(stream.fileno())


def check_destination() -> None:
    _check_parents(OUT)
    if os.path.lexists(OUT):
        verify_artifact(OUT)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv(fresh: bool) -> Path:
    _check_parents(VENV)
    if fresh and VENV.exists():
        print("[1/4] 重建虚拟环境 …")
        shutil.rmtree(VENV, ignore_errors=True)
    if not venv_python().exists():
        print("[1/4] 创建虚拟环境 .build/venv …")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    py = venv_python()
    print(f"      解释器 {py}")
    return py


def pip_install(py: Path) -> None:
    print("[2/4] 安装构建依赖 …")
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip", "-i", MIRROR], check=False)
    subprocess.run([str(py), "-m", "pip", "install", "-q", "-i", MIRROR, *PKGS], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-q", "-i", MIRROR, "pyinstaller"], check=True)


def move_aside() -> Path | None:
    """只移动已核验的纯产物，含数据目录连自动移动也不做。"""
    check_destination()
    if not os.path.lexists(OUT):
        return None
    _check_parents(TRASH)
    TRASH.mkdir(parents=True, exist_ok=True)
    dest = TRASH / f"what-idid-{int(time.time())}-{uuid.uuid4().hex}"
    try:
        OUT.rename(dest)
    except PermissionError:
        # 最常见的原因是上一轮的 exe 还在跑（托盘常驻或界面没关），文件被映射住了
        raise SystemExit(
            "旧产物正被占用，挪不动。先停掉正在运行的 What-IDid.exe（托盘右键退出，"
            "或执行 dist/What-IDid/What-IDid.exe stop），再重新构建。"
        )
    print(f"      旧产物已挪到 {dest.relative_to(ROOT)}")
    return dest


def purge(path: Path) -> None:
    """只清理本构建器备份的、内容仍与清单一致的纯产物。"""
    if not os.path.lexists(path):
        return
    if path.parent != TRASH or not path.name.startswith("what-idid-"):
        raise BuildSafetyError("清理目标不是受管理的产物备份目录")
    verify_artifact(path)
    try:
        shutil.rmtree(path)
    except OSError:
        print(f"      ! 旧产物未完全清理，保留于 {path}；不要删除整个 .build/trash")
    else:
        print("      旧产物已清理")


def build(py: Path) -> None:
    _check_parents(OUT)
    if os.path.lexists(OUT):
        raise BuildSafetyError("打包目标仍然存在，拒绝让 PyInstaller 覆盖任何目录")
    print("[3/4] PyInstaller 打包 …")
    r = subprocess.run(
        [str(py), "-m", "PyInstaller", "build.spec", "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / ".build" / "pyi")],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        raise SystemExit("打包失败，看上面的 PyInstaller 输出")


def report() -> None:
    print("[4/4] 产物检查 …")
    verify_artifact(OUT)
    exe = OUT / "What-IDid.exe"
    print(f"      What-IDid.exe  OK  {exe.stat().st_size / 1024 ** 2:.1f} MB")
    models = list(OUT.rglob("*.onnx"))
    print(f"      内置 OCR 模型 {len(models)} 个 {sum(m.stat().st_size for m in models) / 1024 ** 2:.0f} MB")
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"      整包体积 {total / 1024 ** 2:.0f} MB")


def restore_old(old: Path | None) -> None:
    if old is None:
        return
    try:
        _check_parents(OUT)
        _check_parents(TRASH)
        if os.path.lexists(OUT):
            # 不删除失败产物；未知文件或链接出现时两份都原地保留，交给人工确认。
            _layout(OUT, complete=False)
            _inventory(OUT)
            failed = TRASH / f"failed-{int(time.time())}-{uuid.uuid4().hex}"
            OUT.rename(failed)
            print(f"      失败产物保留在 {failed}")
        old.rename(OUT)
        print("      已恢复构建前的旧产物（未删除数据）")
    except (OSError, BuildSafetyError) as exc:
        print(f"      未自动回滚：{exc}\n      旧产物仍在 {old}，请先备份再人工恢复")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="重建虚拟环境")
    ap.add_argument("--keep-old", action="store_true", help="成功后仍保留已核验旧产物的备份")
    args = ap.parse_args()

    check_destination()  # 下载依赖前就拒绝危险目标；不能用 --keep-old 绕过。
    py = ensure_venv(args.fresh)
    pip_install(py)
    old = move_aside()
    try:
        build(py)
        write_manifest()
        report()
    except BaseException:
        restore_old(old)
        raise
    if old is not None:
        if args.keep_old:
            print(f"      按 --keep-old 保留旧产物：{old}")
        else:
            try:
                purge(old)
            except (BuildSafetyError, OSError) as exc:
                print(f"      保留旧产物：{exc}\n      位置：{old}")
    print("\n完成。把 dist/What-IDid 整个文件夹拷到目标机器即可运行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
