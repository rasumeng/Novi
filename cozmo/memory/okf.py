"""OKF — Obsidian-compatible knowledge format parsing and normalization.

The knowledge base is a set of Markdown files with YAML frontmatter
(``type``/``title``/``tags``/``timestamp``) plus a body. This module is the
single home for:

* ``parse_okf`` / ``parse_okf_file`` — frontmatter + body splitting
* ``semantic_normalize`` — canonical form used to detect formatting-only
  changes (whitespace, emphasis, link syntax) so reconciliation never treats
  them as new knowledge
* ``identity_key`` — deterministic identity for a claim's content, used to
  keep Markdown↔Brain synchronization idempotent

KnowledgeIndex (markdown scanner) and MarkdownStore (markdown mirror) both
depend on this module so OKF parsing has exactly one implementation.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# [text](url)  ->  text  (keep the link label, drop the URL)
_MDLINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# [[Title]] / [[Title|alias]]  ->  Title
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
# Markdown emphasis / code / heading markers carry no semantic content.
_MD_FORMAT_RE = re.compile(r"(\*\*|__|\*|_|`|~~|~|#+)")


def parse_okf(text: str) -> tuple[dict, str]:
    """Split OKF markdown text into (frontmatter dict, body)."""
    m = _FRONTMATTER_RE.match(text or "")
    if not m:
        return {}, (text or "")
    import yaml

    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[m.end():].strip()


def parse_okf_file(filepath: str | Path) -> tuple[dict, str]:
    """Read + split an OKF file. Falls back to a reference note when the file
    has no frontmatter, mirroring the legacy scanner behavior."""
    filepath = Path(filepath)
    text = filepath.read_text("utf-8", errors="replace")
    meta, body = parse_okf(text)
    if not meta:
        meta = {"type": "Reference", "title": filepath.stem, "tags": []}
    return meta, body


def semantic_normalize(text: str) -> str:
    """Canonical semantic form of a claim's text.

    Strips markdown formatting (emphasis, code, headings, link syntax) and
    collapses whitespace/punctuation. Formatting-only edits normalize to the
    same value; real content changes do not.
    """
    t = text or ""
    t = _MDLINK_RE.sub(r"\1", t)
    t = _WIKILINK_RE.sub(r"\1", t)
    t = _MD_FORMAT_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t.lower())
    return " ".join(t.split())


def identity_key(text: str) -> str:
    """Deterministic identity for a claim's content (sha1 of the normalized
    form). Identical claims share one key → one Markdown file."""
    return hashlib.sha1(semantic_normalize(text).encode("utf-8")).hexdigest()
