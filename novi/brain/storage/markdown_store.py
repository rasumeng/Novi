"""MarkdownStore — canonical OKF Markdown mirror of Brain knowledge.

The durable knowledge substrate: every Brain ``KnowledgeItem`` that is
persisted through the canonical write path gets an OKF file under the
configured ``workspace.knowledge`` directory (never CWD-relative). Markdown is
a human-readable, Obsidian-compatible canonical layer — NOT a copy of LanceDB
rows. It carries identity + enough semantics to reconstruct the item; Brain
keeps the richer structured state (confidence, status, provenance, scenarios,
supersession, conflicts).

Idempotency is structural: a claim's content maps to a deterministic identity
key and, unless a matching file already exists (by identity or body), a
deterministic filename. Repeated learn/corroboration of the same claim updates
one file instead of spawning siblings.

User edits are authoritative for *representation*: when an existing file's body
has drifted away from its identity, this writer refreshes metadata but never
clobbers the user's body — reconciliation owns that merge.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from ...configuration.bootstrap import get_configuration
from ...memory.okf import identity_key, parse_okf_file, semantic_normalize
from ..types import KnowledgeForm, KnowledgeItem, KnowledgeStatus

log = logging.getLogger("novi.brain.storage.markdown")

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def extract_wiki_links(text: str) -> tuple[str, ...]:
    """Minimal safe WikiLink extraction: target titles, deduped, order kept.

    Only the title is captured (``[[Title]]`` / ``[[Title|alias]]`` -> Title).
    No resolution, no backlinks — that is the M3 WikiLink graph stage.
    """
    seen: dict[str, None] = {}
    for m in _WIKILINK_RE.finditer(text or ""):
        title = m.group(1).strip()
        if title:
            seen.setdefault(title, None)
    return tuple(seen)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _form_from(value) -> KnowledgeForm:
    try:
        return KnowledgeForm(str(value))
    except (ValueError, TypeError):
        return KnowledgeForm.ATOMIC


def _status_from(value) -> KnowledgeStatus:
    try:
        return KnowledgeStatus(str(value))
    except (ValueError, TypeError):
        return KnowledgeStatus.CANDIDATE


def _dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _title_for(content: str) -> str:
    t = (content or "").strip().splitlines()
    title = t[0].strip() if t else ""
    return (title[:80]) or "Untitled"


def _slug_for(content: str) -> str:
    words = semantic_normalize(content).split()
    words = words[:8]
    slug = "-".join(words) if words else "claim"
    return slug[:48] or "claim"


def _filename_for(content: str) -> str:
    key = identity_key(content)
    return f"{_slug_for(content)}-{key[:8]}.md"


class MarkdownStore:
    """OKF Markdown persistence for Brain knowledge items.

    Args:
        knowledge_dir: Path to the knowledge base. Defaults to the configured
            ``workspace.knowledge`` (``~/.novi/knowledge``). Never CWD-relative.
    """

    def __init__(self, knowledge_dir: str | Path | None = None):
        if knowledge_dir is None:
            knowledge_dir = get_configuration().get(
                "workspace.knowledge", "~/.novi/knowledge"
            )
        self.knowledge_dir = Path(knowledge_dir).expanduser().resolve()

    # ── directory helpers ──────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

    def list_files(self) -> tuple[Path, ...]:
        if not self.knowledge_dir.is_dir():
            return ()
        return tuple(sorted(self.knowledge_dir.rglob("*.md")))

    def parse(self, path: str | Path) -> tuple[dict, str]:
        """Parse an OKF file into (frontmatter, body)."""
        return parse_okf_file(path)

    def path_for(self, content: str) -> Path:
        """Deterministic path for a claim's content (no filesystem scan)."""
        return self.knowledge_dir / _filename_for(content)

    # ── lookup ─────────────────────────────────────────────────────────

    def find_existing(self, content: str) -> Optional[Path]:
        """Find the file that already represents ``content``.

        Match order: (1) frontmatter ``identity`` equals the content's key, or
        (2) a file whose semantic body equals the content (user-authored notes
        and un-synced files). Returns None when no file matches.
        """
        key = identity_key(content)
        norm = semantic_normalize(content)
        if not norm:
            return None
        for f in self.list_files():
            meta, body = self.parse(f)
            if meta.get("identity") == key:
                return f
        for f in self.list_files():
            meta, body = self.parse(f)
            if semantic_normalize(body) == norm:
                return f
        return None

    def find_for_id(self, item_id: str) -> Optional[Path]:
        """Find the file whose frontmatter ``id`` equals ``item_id``."""
        for f in self.list_files():
            meta, _ = self.parse(f)
            if meta.get("id") == item_id:
                return f
        return None

    # ── writes ─────────────────────────────────────────────────────────

    def write_item(
        self, item: KnowledgeItem, *, source_kind: str = "explicit"
    ) -> tuple[str, bool]:
        """Write/update the OKF mirror for ``item``.

        Returns (relative path, created). Created is True when a new file was
        written; False when an existing file was updated in place.
        """
        self._ensure_dir()
        target = self.find_existing(item.content)
        created = target is None
        if target is None:
            target = self.path_for(item.content)
        self._write_file(target, item, source_kind)
        return target.relative_to(self.knowledge_dir).as_posix(), created

    def update_status(self, item_id: str, status: KnowledgeStatus) -> bool:
        """Rewrite the frontmatter ``status`` of the file for ``item_id``."""
        target = self.find_for_id(item_id)
        if target is None:
            return False
        meta, body = self.parse(target)
        meta["status"] = status.value
        meta["updated"] = datetime.now().isoformat()
        self._write_frontmatter(target, meta, body)
        return True

    # ── reads ──────────────────────────────────────────────────────────

    def read_item(self, path: str | Path) -> Optional[KnowledgeItem]:
        """Reconstruct a KnowledgeItem from an OKF file, when it has an ``id``.

        Returns None for user-authored notes that were never synced (no Brain
        identity yet).
        """
        meta, body = self.parse(path)
        if not meta.get("id"):
            return None
        try:
            confidence = float(meta.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            importance = float(meta.get("importance", 0.0))
        except (TypeError, ValueError):
            importance = 0.0
        return KnowledgeItem(
            id=str(meta["id"]),
            form=_form_from(meta.get("type")),
            content=body,
            confidence=confidence,
            status=_status_from(meta.get("status")),
            tags=tuple(_as_list(meta.get("tags"))),
            sources=tuple(_as_list(meta.get("sources"))),
            scenario_id=meta.get("scenario_id"),
            created_at=_dt(meta.get("timestamp")),
            last_seen_at=_dt(meta.get("updated")),
            importance=importance,
        )

    # ── internals ──────────────────────────────────────────────────────

    def _write_file(
        self, target: Path, item: KnowledgeItem, source_kind: str
    ) -> None:
        meta, body = self.parse(target) if target.exists() else ({}, item.content)
        norm_body = semantic_normalize(body)
        norm_content = semantic_normalize(item.content)
        preserve_body = bool(norm_body) and norm_body != norm_content
        body_to_write = body if preserve_body else item.content
        created_ts = meta.get("timestamp") or (
            item.created_at.isoformat() if item.created_at else None
        ) or datetime.now().isoformat()
        frontmatter = {
            "type": item.form.value,
            "title": meta.get("title") or _title_for(item.content),
            "id": meta.get("id") if preserve_body else item.id,
            "identity": meta.get("identity") if preserve_body else identity_key(item.content),
            "tags": sorted(set(_as_list(meta.get("tags"))) | set(item.tags)),
            "timestamp": created_ts,
            "updated": datetime.now().isoformat(),
            "status": item.status.value,
            "confidence": round(float(item.confidence), 4),
            "importance": round(float(item.importance), 4),
            "source_kind": source_kind,
            "sources": sorted(set(_as_list(meta.get("sources"))) | set(item.sources)),
            "scenario_id": item.scenario_id,
        }
        self._write_frontmatter(target, frontmatter, body_to_write)

    def _write_frontmatter(self, target: Path, frontmatter: dict, body: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(
                frontmatter, f, default_flow_style=False, sort_keys=False,
                allow_unicode=True,
            )
            f.write("---\n\n")
            f.write(body)