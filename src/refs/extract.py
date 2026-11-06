"""从窗口标题与 OCR 文本里抽"可能的文档引用"。

只抽候选，是否真能跳转由 providers 校验。宁可少抽，不要抽一堆噪声。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_KIND_URL = "url"
_KIND_PATH = "path"
_KIND_FILE = "filename"

RE_URL = re.compile(r"https?://[^\s<>\"'）)】\]]+")
RE_WINPATH = re.compile(r"[A-Za-z]:[\\/](?:[^\s:*?\"<>|\r\n]{1,150})")
RE_FILE = re.compile(
    r"(?<![\w/\\])([\w\u4e00-\u9fa5][\w\u4e00-\u9fa5 ._\-]{0,60}?"
    r"\.(?:md|markdown|py|ipynb|ts|tsx|js|jsx|vue|java|kt|go|rs|c|cc|cpp|h|hpp|cs|rb|php|sql|sh|bat|ps1|"
    r"json|ya?ml|toml|ini|conf|xlsx|xls|csv|docx?|pptx?|pdf|txt|html?|css|scss|drawio|mm|puml))\b",
    re.I,
)

# 明显不是文档的常见串
_BLACKLIST = {
    "package.json", "package-lock.json", "tsconfig.json", "yarn.lock", "requirements.txt",
    "config.yaml", "config.yml", "readme.md", "license", "dockerfile",
}

MAX_PER_KIND = 60


@dataclass
class RefCandidate:
    raw: str
    kind_hint: str
    block_ids: list[str] = field(default_factory=list)
    title_context: str = ""
    # 1.3.0：来源通道（history / address_bar / domain / ocr）。空表示从文本里抽出来的。
    source: str = ""
    # 命中通道已经知道"该显示成什么"，就不让 provider 再猜一次
    label: str = ""


def _trim_url(u: str) -> str:
    return u.rstrip(".,;:!?)]}＞》")


def extract(text: str, block_ids: list[str] | None = None) -> list[RefCandidate]:
    if not text:
        return []
    bids = block_ids or []
    out: list[RefCandidate] = []
    seen: set[str] = set()

    for m in RE_URL.finditer(text):
        raw = _trim_url(m.group(0))
        if len(raw) < 12 or raw in seen:
            continue
        seen.add(raw)
        out.append(RefCandidate(raw, _KIND_URL, list(bids)))
        if len(out) >= MAX_PER_KIND:
            break

    for m in RE_WINPATH.finditer(text):
        raw = m.group(0).rstrip(".,;:）)】] ")
        if len(raw) < 6 or raw in seen:
            continue
        seen.add(raw)
        out.append(RefCandidate(raw, _KIND_PATH, list(bids)))

    for m in RE_FILE.finditer(text):
        raw = m.group(1).strip()
        if raw.lower() in _BLACKLIST or raw in seen or len(raw) < 4:
            continue
        seen.add(raw)
        out.append(RefCandidate(raw, _KIND_FILE, list(bids)))

    return out


def extract_from_blocks(blocks) -> list[RefCandidate]:
    """每个块抽一次，带上 block_id，方便页面把引用挂回证据。

    除了从文本里抽，还会把报告期反查到的 URL（`Block.urls`）直接注入成候选：
    URL 已经是终态，不需要再走 provider 解析，只需要 provider 把它变成 ref 记录。
    """
    all_c: dict[str, RefCandidate] = {}
    for b in blocks:
        for u in (getattr(b, "urls", None) or []):
            uri = str(u.get("uri") or "")
            if not uri:
                continue
            key = uri.lower()
            cand = all_c.get(key)
            if cand is None:
                cand = RefCandidate(
                    uri, _KIND_URL, [b.id], b.title or "",
                    source=str(u.get("source") or ""), label=str(u.get("title") or ""),
                )
                all_c[key] = cand
            elif b.id not in cand.block_ids:
                cand.block_ids.append(b.id)
        text = "\n".join([b.title or "", b.text or ""])
        for c in extract(text, [b.id]):
            key = c.raw.lower()
            if key in all_c:
                if b.id not in all_c[key].block_ids:
                    all_c[key].block_ids.append(b.id)
                continue
            c.title_context = b.title or ""
            all_c[key] = c
    return list(all_c.values())
