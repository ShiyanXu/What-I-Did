"""引用解析调度：手工绑定优先 -> providers 链 -> 去重 + 稳定 ref_id + 缓存。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import paths
import yaml
from refs.extract import RefCandidate, extract_from_blocks
from refs.providers import FileIndex, ResolvedRef, build_providers


def _ref_id(kind: str, uri: str) -> str:
    return "r-" + hashlib.sha1(f"{kind}|{uri}".encode("utf-8")).hexdigest()[:8]


class RefResolver:
    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self.log = log
        self.enabled = bool(cfg.get("refs.enabled", True))
        self.budget = float(cfg.get("refs.resolve_timeout_sec", 15))
        roots = [str(r) for r in (cfg.get("refs.search_roots", []) or [])]
        vault = cfg.get("refs.vault_dir", "") or ""
        if vault and vault not in roots:
            roots.append(vault)
        self.index = FileIndex(roots, paths.STATE / "file_index.json")
        self.providers = build_providers(cfg, self.index)
        self.bindings = self._load_bindings()
        self.cache = self._load_cache()
        self.ttl = float(cfg.get("refs.cache_ttl_days", 30)) * 86400

    # ------------------------------------------------------------ 配置/缓存
    def _load_bindings(self) -> list[dict]:
        if not paths.BINDINGS_PATH.exists():
            return []
        try:
            data = yaml.safe_load(paths.BINDINGS_PATH.read_text(encoding="utf-8")) or []
            if isinstance(data, dict):
                data = data.get("bindings", [])
            return [x for x in data if isinstance(x, dict) and x.get("uri")]
        except Exception as exc:
            if self.log:
                self.log.warning("bindings.yaml 解析失败: %s", exc)
            return []

    def _load_cache(self) -> dict[str, dict]:
        cache: dict[str, dict] = {}
        p = paths.REF_CACHE
        if not p.exists():
            return cache
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("raw"):
                        cache[rec["raw"].lower()] = rec
        except OSError:
            pass
        return cache

    def _save_cache(self, new_records: list[dict]) -> None:
        if not new_records:
            return
        try:
            paths.SHARED.mkdir(parents=True, exist_ok=True)
            with paths.REF_CACHE.open("a", encoding="utf-8") as f:
                for rec in new_records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ 手工绑定
    def _match_binding(self, cand: RefCandidate) -> ResolvedRef | None:
        ctx = f"{cand.title_context} {cand.raw}"
        for b in self.bindings:
            match = b.get("match") or {}
            ok = True
            if "title_contains" in match and match["title_contains"] not in ctx:
                ok = False
            if ok and "app" in match and match["app"].lower() not in ctx.lower():
                ok = False
            if ok and "raw_contains" in match and match["raw_contains"] not in cand.raw:
                ok = False
            if not ok:
                continue
            uri = str(b["uri"])
            kind = self._kind_of_uri(uri)
            return ResolvedRef("", kind, uri, str(b.get("label") or uri), 1.0,
                               list(cand.block_ids), "bindings.yaml 手工绑定")
        return None

    @staticmethod
    def _kind_of_uri(uri: str) -> str:
        u = uri.lower()
        if u.startswith("obsidian://"):
            return "obsidian"
        if u.startswith("file://"):
            return "file"
        if u.startswith("kb://"):
            return "kb"
        return "url"

    # ------------------------------------------------------------ 主流程
    def resolve(self, candidates: list[RefCandidate]) -> list[dict]:
        if not self.enabled or not candidates:
            return []
        t0 = time.time()
        out: list[dict] = []
        seen_uri: set[str] = set()
        new_cache: list[dict] = []
        failures = 0

        for cand in candidates:
            if time.time() - t0 > self.budget:
                if self.log:
                    self.log.warning("引用解析超时（%.0fs），剩余 %d 条放弃", self.budget,
                                     len(candidates) - len(out))
                break

            resolved = self._match_binding(cand)
            if resolved is None:
                key = cand.raw.lower()
                hit = self.cache.get(key)
                if hit and time.time() - float(hit.get("ts", 0)) < self.ttl:
                    if hit.get("kind") != "file" or Path(hit.get("path", "")).exists():
                        resolved = ResolvedRef("", hit.get("kind", ""), hit.get("uri", ""),
                                               hit.get("label", ""), 0.7, list(cand.block_ids), "缓存命中")
                if resolved is None:
                    for prov in self.providers:
                        try:
                            if not prov.probe(cand):
                                continue
                            resolved = prov.resolve(cand)
                            if resolved is not None:
                                break
                        except NotImplementedError:
                            continue  # kb 空壳：静默跳过，不影响出报
                        except Exception as exc:
                            failures += 1
                            if self.log and failures <= 3:
                                self.log.warning("provider %s 失败: %s", prov.name, exc)
                            continue

            if resolved is None or not resolved.uri:
                continue
            if resolved.uri in seen_uri:
                # 同一目标被多个块提到，合并 block_ids
                for r in out:
                    if r["uri"] == resolved.uri:
                        for b in cand.block_ids:
                            if b not in r["block_ids"]:
                                r["block_ids"].append(b)
                        break
                continue
            seen_uri.add(resolved.uri)

            rec = {
                "ref_id": _ref_id(resolved.kind, resolved.uri),
                "kind": resolved.kind,
                "uri": resolved.uri,
                "label": resolved.label or resolved.uri,
                "confidence": resolved.confidence,
                "block_ids": list(dict.fromkeys(resolved.block_ids)),
                "why": resolved.why,
                "clickable": self._clickable(resolved.kind, resolved.uri),
            }
            out.append(rec)
            if not resolved.why.startswith("缓存命中"):
                new_cache.append({
                    "raw": cand.raw.lower(),
                    "kind": resolved.kind,
                    "uri": resolved.uri,
                    "label": resolved.label,
                    "path": self._local_path(resolved.uri),
                    "ts": time.time(),
                })

        self._save_cache(new_cache)
        # 按置信度与块数排序，页面上先展示更可靠的
        out.sort(key=lambda r: (-r["confidence"], -len(r["block_ids"])))
        return out

    def _local_path(self, uri: str) -> str:
        if not uri.startswith("file://"):
            return ""
        try:
            from urllib.parse import unquote, urlparse

            p = urlparse(uri)
            s = unquote(p.path)
            if s.startswith("/") and len(s) > 3 and s[2] == ":":
                s = s[1:]
            return s
        except Exception:
            return ""

    def _clickable(self, kind: str, uri: str) -> bool:
        if kind == "kb":
            return bool(self.cfg.get("refs.kb.url_template", ""))
        return bool(uri)

    def resolve_blocks(self, blocks) -> list[dict]:
        cands = extract_from_blocks(blocks)
        return self.resolve(cands)


def resolve_for_report(cfg, blocks, log=None) -> list[dict]:
    resolver = RefResolver(cfg, log)
    return resolver.resolve_blocks(blocks)
