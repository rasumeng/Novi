"""M3 — WikiLink resolution + durable knowledge relationship edges.

Turns WikiLinks from creation-only placeholders into a resolvable relationship
graph over the RelationshipStore:

    [[Title]] / [[Title|Alias]]
            ↓   deterministic resolution (no fuzzy match)
            ↓
    references edge: source-knowledge  →  target-knowledge

Resolution order (Architecture contract A.5 / B.4), each step tried across every
note before dropping to the next:

1. exact note identity/path — frontmatter ``id`` / ``identity`` or the file
   rel-path (with or without ``.md``).
2. canonical title        — frontmatter ``title`` (exact string match).
3. normalized title       — semantic-normalized title OR stem.
4. knowledge identity metadata — frontmatter ``aliases`` (exact, then
   normalized).

Never fuzzy-match: a link that could bind to more than one note is left
unresolved (ambiguous) with a warning. A link that matches nothing is
``dangling``: represented as a ``note:<Title>`` edge so it is durable and
recoverable the instant a matching note appears.

Every *resolved* edge targets the actual durable knowledge identity (the Brain
item id carried in frontmatter ``id``), never only ``note:<Title>``. The
``note:<Title>`` form survives only as the unresolved/dangling representation
(M2 convention preserved).

Re-indexing is diff-based and idempotent: add new, remove stale, preserve
unrelated. Deleting a WikiLink removes its edge; deleting a source note removes
its outgoing ``references`` (orphan sweep); deleting a target note degrades the
edge to dangling and is recoverable on re-index.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..memory.okf import semantic_normalize
from .types import EdgeKind, Relationship

log = logging.getLogger("cozmo.brain.wikilinks")

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

# Unresolved/dangling link target form (M2 convention). A resolved link targets
# the real Brain item id (``kn-…``) instead.
_NOTE_PREFIX = "note:"

# Upper bound on the orphan sweep so a pathological relationship table can't
# make a single sync O(n)+memory-bounded but unbounded scan.
_SWEEP_LIMIT = 5000


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    DANGLING = "dangling"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class WikiLink:
    title: str
    alias: Optional[str] = None


@dataclass(frozen=True)
class NoteRef:
    """Identity of one Markdown note as seen by the WikiLink graph."""

    rel_path: str
    rel_path_with_ext: str
    stem: str
    title: str
    item_id: Optional[str]
    identity_key: Optional[str]
    aliases: tuple[str, ...]
    semantic_title: str
    semantic_stem: str


@dataclass
class Resolution:
    """Outcome of resolving a single WikiLink title."""

    title: str
    alias: Optional[str]
    status: ResolutionStatus
    target_id: Optional[str] = None
    note: Optional[NoteRef] = None


@dataclass
class WikilinkSyncReport:
    """Tally of a full wikilink reconciliation pass."""

    scanned: int = 0
    resolved: int = 0
    dangling: int = 0
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    ambiguous: set[str] = field(default_factory=set)
    skipped: bool = False


def _as_list(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def _str_id(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    return str(value)


def _note_ref_from(path, meta: dict, knowledge_dir) -> NoteRef:
    rel = path.relative_to(knowledge_dir).as_posix()
    rel_noext = rel[:-3] if rel.endswith(".md") else rel
    stem = path.stem
    title = str(meta.get("title") or stem)
    aliases = _as_list(meta.get("aliases"))
    return NoteRef(
        rel_path=rel_noext,
        rel_path_with_ext=rel,
        stem=stem,
        title=title,
        item_id=_str_id(meta.get("id")),
        identity_key=_str_id(meta.get("identity")),
        aliases=aliases,
        semantic_title=semantic_normalize(title),
        semantic_stem=semantic_normalize(stem),
    )


def parse_wikilinks(text: str) -> tuple[WikiLink, ...]:
    """Parse `[[Title]]` / `[[Title|Alias]]` from markdown body.

    Order-preserving, de-duplicated by (title, alias). Aliases are retained for
    presentation but play no role in relationship resolution.
    """
    seen: dict[tuple[str, Optional[str]], None] = {}
    out: list[WikiLink] = []
    for m in _WIKILINK_RE.finditer(text or ""):
        title = m.group(1).strip()
        alias = m.group(2).strip() if m.group(2) else None
        if not title:
            continue
        key = (title, alias)
        if key in seen:
            continue
        seen[key] = None
        out.append(WikiLink(title=title, alias=alias))
    return tuple(out)


class NoteIndex:
    """Resolved, deterministic view of every note in the knowledge base.

    A NoteIndex is built from an OKF markdown scan — there is no separate
    identity index database. It is rebuildable at any time from Markdown.
    """

    def __init__(self, notes: tuple[NoteRef, ...]):
        self._notes = notes

    @property
    def notes(self) -> tuple[NoteRef, ...]:
        return self._notes

    def resolve(self, link: WikiLink) -> Resolution:
        """Resolve a WikiLink title to a durable identity (spec §2)."""
        title = link.title

        matches = self._match(lambda n: (
            title == n.rel_path
            or title == n.rel_path_with_ext
            or (n.item_id is not None and title == n.item_id)
            or (n.identity_key is not None and title == n.identity_key)
        ))
        if matches:
            return self._finish(link, matches)

        # 2. canonical title (exact).
        matches = self._match(lambda n: title == n.title)
        if matches:
            return self._finish(link, matches)

        # 3. normalized title.
        norm = semantic_normalize(title)
        if norm:
            matches = self._match(
                lambda n: norm == n.semantic_title or norm == n.semantic_stem
            )
            if matches:
                return self._finish(link, matches)

        # 4. knowledge identity metadata (aliases).
        matches = self._match(lambda n: title in n.aliases)
        if matches:
            return self._finish(link, matches)
        if norm:
            matches = self._match(
                lambda n: any(semantic_normalize(a) == norm for a in n.aliases)
            )
            if matches:
                return self._finish(link, matches)

        return Resolution(
            title=title,
            alias=link.alias,
            status=ResolutionStatus.DANGLING,
            target_id=f"{_NOTE_PREFIX}{title}",
        )

    def _match(self, predicate) -> list[NoteRef]:
        return [n for n in self._notes if predicate(n)]

    def _finish(self, link: WikiLink, matches: list[NoteRef]) -> Resolution:
        if len(matches) > 1:
            log.warning(
                "ambiguous wikilink %r matches %d notes; left unresolved",
                link.title,
                len(matches),
            )
            return Resolution(
                title=link.title,
                alias=link.alias,
                status=ResolutionStatus.AMBIGUOUS,
            )
        note = matches[0]
        target = note.item_id or f"{_NOTE_PREFIX}{link.title}"
        return Resolution(
            title=link.title,
            alias=link.alias,
            status=ResolutionStatus.RESOLVED,
            target_id=target,
            note=note,
        )


def build_note_index(markdown_store) -> NoteIndex:
    """Scan the knowledge base into a NoteIndex.

    Single source of truth for note identity; rebuildable from Markdown, never
    a second store.
    """
    notes: list[NoteRef] = []
    for path in markdown_store.list_files():
        meta, _ = markdown_store.parse(path)
        notes.append(_note_ref_from(path, meta, markdown_store.knowledge_dir))
    return NoteIndex(tuple(notes))


class WikilinkSynchronizer:
    """Diff Markdown WikiLinks against RelationshipStore ``references`` edges.

    Wired into the Brain's durable write path (creation-time, M2 semantics)
    and the reconciliation path (full diff, M3). Re-indexing is idempotent.
    """

    def __init__(self, markdown_store, relationship_store):
        self._md = markdown_store
        self._rels = relationship_store

    def build_index(self) -> NoteIndex:
        return build_note_index(self._md)

    # ── full reconciliation ────────────────────────────────────────────────

    def sync_all(self, index: Optional[NoteIndex] = None) -> WikilinkSyncReport:
        """Reconcile ``references`` edges across every note (diff + orphan sweep)."""
        report = WikilinkSyncReport()
        try:
            files = list(self._md.list_files())
            parsed = [(p, self._md.parse(p)) for p in files]
        except Exception:
            log.warning("wikilink scan failed", exc_info=True)
            return report
        index = index or NoteIndex(
            tuple(_note_ref_from(p, meta, self._md.knowledge_dir)
                  for p, (meta, _) in parsed)
        )
        for path, (meta, body) in parsed:
            item_id = meta.get("id")
            if not item_id:
                continue
            report.scanned += 1
            self._diff_note(index, str(item_id), body, report)

        # Orphan sweep: drop `references` edges whose source is no longer any
        # note's id (deleted/churned source note). Provenance edges keep their
        # own (source, target, kind) — only `references` is swept.
        current_ids = {n.item_id for n in index.notes if n.item_id}
        try:
            for edge in self._rels.list(kind=EdgeKind.REFERENCES, limit=_SWEEP_LIMIT):
                if edge.source_id not in current_ids:
                    self._rels.remove(
                        source_id=edge.source_id,
                        target_id=edge.target_id,
                        kind=EdgeKind.REFERENCES,
                    )
                    report.removed += 1
        except Exception:
            log.warning("orphan sweep failed", exc_info=True)
        return report

    # ── single-note sync (creation-time path) ──────────────────────────────

    def sync_file(
        self, rel: str, index: Optional[NoteIndex] = None
    ) -> WikilinkSyncReport:
        """Diff `references` edges for a single note relative path."""
        report = WikilinkSyncReport()
        path = self._md.knowledge_dir / rel
        if not path.is_file():
            return report
        meta, body = self._md.parse(path)
        item_id = meta.get("id")
        if not item_id:
            return report
        report.scanned += 1
        index = index or self.build_index()
        self._diff_note(index, str(item_id), body, report)
        return report

    # ── internals ──────────────────────────────────────────────────────────

    def _diff_note(
        self,
        index: NoteIndex,
        item_id: str,
        body: str,
        report: WikilinkSyncReport,
    ) -> None:
        desired: set[str] = set()
        for link in parse_wikilinks(body):
            res = index.resolve(link)
            if res.status is ResolutionStatus.AMBIGUOUS:
                report.ambiguous.add(link.title)
                continue
            desired.add(res.target_id)
            if res.status is ResolutionStatus.DANGLING:
                report.dangling += 1
            else:
                report.resolved += 1

        existing = {
            e.target_id
            for e in self._rels.outgoing(item_id, kind=EdgeKind.REFERENCES)
        }
        to_add = desired - existing
        to_remove = existing - desired

        if to_remove:
            for target in to_remove:
                self._rels.remove(
                    source_id=item_id,
                    target_id=target,
                    kind=EdgeKind.REFERENCES,
                )
        if to_add:
            self._rels.add_many(
                [
                    Relationship(
                        source_id=item_id,
                        target_id=target,
                        kind=EdgeKind.REFERENCES,
                    )
                    for target in sorted(to_add)
                ]
            )
        report.added += len(to_add)
        report.removed += len(to_remove)
        report.unchanged += len(existing & desired)
