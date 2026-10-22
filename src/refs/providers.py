"""引用解析器。新增一种文档来源 = 新写一个 provider，别动 schema 和页面。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import paths
from refs.extract import RefCandidate

SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".idea", ".vscode", "dist", "build",
    "data", "logs", "state", "site-packages", "AppData", "Windows", "Temp", ".workbuddy",
    "target", "bin", "obj", ".next", "coverage",
}


@dataclass
class ResolvedRef:
    ref_id: str
    kind: str          # file | obsidian | url | kb
    uri: str
    label: str
    confidence: float = 0.5
    block_ids: list[str] = field(default_factory=list)
    why: str = ""


class FileIndex:
    """按需构建"文件名 -> 绝对路径"索引，带磁盘缓存，避免每次 rglob 全盘。"""

    MAX_FILES = 30000
    MAX_DEPTH = 5
    TTL = 86400

    def __init__(self, roots: list[str], cache_path: Path):
        self.roots = [Path(r) for r in roots if r and Path(r).exists()]
        self.cache_path = cache_path
        self._index: dict[str, str] | None = None
        self._built_at = 0.0

    def _load_cache(self) -> bool:
        try:
            if not self.cache_path.exists():
                return False
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("ts", 0)) > self.TTL:
                return False
            if data.get("roots") != [str(r) for r in self.roots]:
                return False
            self._index = {k.lower(): v for k, v in data.get("files", {}).items()}
            self._built_at = float(data.get("ts", 0))
            return bool(self._index)
        except Exception:
            return False

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"ts": time.time(), "roots": [str(r) for r in self.roots],
                       "files": self._index or {}}
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except Exception:
            pass

    def build(self, deadline_sec: float = 4.0) -> dict[str, str]:
        if self._index is not None:
            return self._index
        if self._load_cache():
            return self._index or {}
        index: dict[str, str] = {}
        t0 = time.time()
        for root in self.roots:
            if time.time() - t0 > deadline_sec:
                break
            base_depth = len(root.parts)
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    if time.time() - t0 > deadline_sec:
                        break
                    cur = Path(dirpath)
                    if len(cur.parts) - base_depth >= self.MAX_DEPTH:
                        dirnames[:] = []
                    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                    for fn in filenames:
                        if len(index) >= self.MAX_FILES:
                            break
                        key = fn.lower()
                        if key not in index:
                            index[key] = str(cur / fn)
            except OSError:
                continue
        self._index = index
        self._built_at = time.time()
        self._save_cache()
        return index

    def lookup(self, name: str) -> str:
        idx = self.build()
        return idx.get(Path(name).name.lower(), "")


class BaseProvider:
    name = "base"
    scheme = "none"

    def probe(self, cand: RefCandidate) -> bool:
        return False

    def resolve(self, cand: RefCandidate) -> ResolvedRef | None:
        return None


class UrlProvider(BaseProvider):
    name = "url"
    scheme = "https"

    def probe(self, cand: RefCandidate) -> bool:
        return cand.kind_hint == "url"

    def resolve(self, cand: RefCandidate) -> ResolvedRef | None:
        if not self.probe(cand):
            return None
        from urllib.parse import urlparse

        p = urlparse(cand.raw)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        label = (p.netloc + p.path)[:80] or cand.raw
        return ResolvedRef("", "url", cand.raw, label, 0.9, list(cand.block_ids), "OCR 中出现的链接")


class LocalFileProvider(BaseProvider):
    name = "local_file"
    scheme = "file"

    def __init__(self, cfg, index: FileIndex):
        self.index = index
        self.allow = bool(cfg.get("refs.allow_unresolved_links", False))

    def probe(self, cand: RefCandidate) -> bool:
        return cand.kind_hint in ("path", "filename")

    def resolve(self, cand: RefCandidate) -> ResolvedRef | None:
        if not self.probe(cand):
            return None
        p = Path(cand.raw)
        target: Path | None = None
        if p.is_absolute():
            if p.exists() and p.is_file():
                target = p
            else:
                return None  # 绝对路径写着却不存在，直接判定无效，避免幻觉链接
        else:
            hit = self.index.lookup(cand.raw)
            if hit:
                target = Path(hit)
            elif self.allow:
                return ResolvedRef("", "file", "", cand.raw, 0.2, list(cand.block_ids), "未找到对应文件")
            else:
                return None
        try:
            uri = target.as_uri()
        except ValueError:
            return None
        return ResolvedRef("", "file", uri, target.name, 0.75, list(cand.block_ids), "记录中出现的本地文件")


class ObsidianProvider(BaseProvider):
    name = "obsidian"
    scheme = "obsidian"

    def __init__(self, cfg, index: FileIndex):
        self.vault = Path(cfg.get("refs.vault_dir", "") or "")
        self.vault_name = cfg.get("refs.vault_name", "") or (self.vault.name if self.vault else "")
        self.enabled = bool(self.vault and self.vault.exists())
        self.index = index

    def probe(self, cand: RefCandidate) -> bool:
        return self.enabled and cand.kind_hint == "filename" and cand.raw.lower().endswith(".md")

    def resolve(self, cand: RefCandidate) -> ResolvedRef | None:
        if not self.probe(cand):
            return None
        hit = self.index.lookup(cand.raw)
        if not hit:
            return None
        p = Path(hit)
        try:
            rel = p.relative_to(self.vault).with_suffix("").as_posix()
        except ValueError:
            return None
        uri = f"obsidian://open?vault={quote(self.vault_name)}&file={quote(rel)}"
        return ResolvedRef("", "obsidian", uri, p.stem, 0.85, list(cand.block_ids), "命中 Obsidian 笔记库")


class KBProvider(BaseProvider):
    """知识库：本期只注册，不启用。将来实现 resolve() 即可，不动任何上游。"""

    name = "kb"
    scheme = "kb"

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg.get("refs.kb.enabled", False))
        self.url_template = cfg.get("refs.kb.url_template", "") or ""

    def probe(self, cand: RefCandidate) -> bool:
        return self.enabled

    def resolve(self, cand: RefCandidate) -> ResolvedRef | None:
        raise NotImplementedError("kb provider 尚未实现：接入知识库时在这里实现检索，返回 kb://<collection>/<doc_id>#<anchor>")


def build_providers(cfg, index: FileIndex) -> list[BaseProvider]:
    registry = {
        "local_file": lambda: LocalFileProvider(cfg, index),
        "obsidian": lambda: ObsidianProvider(cfg, index),
        "url": lambda: UrlProvider(),
        "kb": lambda: KBProvider(cfg),
    }
    names = cfg.get("refs.providers", ["local_file", "obsidian", "url"]) or []
    out: list[BaseProvider] = []
    for n in names:
        f = registry.get(n)
        if f:
            out.append(f())
    return out
