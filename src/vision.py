"""图像处理：pHash / 网格指纹 / 分类 / 降采样 / 原子写。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def dhash(img: Image.Image, size: int = 8) -> int:
    """差值哈希，返回 64bit 整数。对缩放、轻微压缩不敏感。"""
    small = img.convert("L").resize((size + 1, size), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.int16)
    diff = arr[:, 1:] > arr[:, :-1]
    out = 0
    for bit in diff.flatten():
        out = (out << 1) | int(bit)
    return out


def hamming(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


def grid_signature(img: Image.Image, cols: int = 12, rows: int = 8) -> np.ndarray:
    """把画面压成 cols×rows 的灰度均值，用于衡量"变化面积"。"""
    small = img.convert("L").resize((cols, rows), Image.Resampling.BOX)
    return np.asarray(small, dtype=np.float32) / 255.0


def grid_diff(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None:
        return 1.0
    if a.shape != b.shape:
        return 1.0
    return float(np.abs(a - b).mean())


def classify(
    prev_hash: int | None,
    prev_grid: np.ndarray | None,
    cur_hash: int,
    cur_grid: np.ndarray,
    same_window: bool,
    dup_threshold: int = 4,
    diff_dup: float = 0.02,
    diff_major: float = 0.22,
) -> tuple[str, float]:
    """返回 (类别, 变化率)。类别: new / duplicate / minor / major"""
    if prev_hash is None:
        return "new", 1.0
    hd = hamming(prev_hash, cur_hash)
    gd = grid_diff(prev_grid, cur_grid)
    if not same_window:
        return "new", max(gd, 0.0)
    if hd <= dup_threshold and gd < diff_dup:
        return "duplicate", gd
    if gd >= diff_major:
        return "major", gd
    return "minor", gd


def downscale(img: Image.Image, max_width: int) -> Image.Image:
    w, h = img.size
    if w <= max_width:
        return img
    ratio = max_width / float(w)
    return img.resize((max_width, max(1, int(h * ratio))), Image.Resampling.LANCZOS)


def crop_region(img: Image.Image, rect: tuple[int, int, int, int], origin: tuple[int, int]) -> Image.Image | None:
    """按物理坐标裁窗口。rect 是窗口绝对坐标，(left,top,right,bottom)；
    origin 是截图的虚拟桌面左上角。坐标越界就裁到边界内，实在无效就返回 None。"""
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return None
    ox, oy = origin
    x1, y1 = left - ox, top - oy
    x2, y2 = right - ox, bottom - oy
    W, H = img.size
    x1, y1 = max(0, min(W - 1, x1)), max(0, min(H - 1, y1))
    x2, y2 = max(1, min(W, x2)), max(1, min(H, y2))
    if x2 - x1 < 80 or y2 - y1 < 60:
        return None
    return img.crop((x1, y1, x2, y2))


def save_jpeg_atomic(img: Image.Image, path: Path, quality: int = 62) -> int:
    """原子写：先写同目录临时文件，再 replace。返回字节数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".jpg")
    os.close(fd)
    try:
        img.convert("RGB").save(tmp, "JPEG", quality=quality, optimize=True, subsampling=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path.stat().st_size


def save_png_atomic(img: Image.Image, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".png")
    os.close(fd)
    try:
        img.save(tmp, "PNG", compress_level=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path.stat().st_size


def thumb(img: Image.Image, width: int = 480) -> Image.Image:
    return downscale(img, width)
