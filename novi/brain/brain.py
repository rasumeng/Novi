"""Brain — cognition facade over Novi's knowledge system.

Phase B established the write pipeline:
    Runtime reports events.
    Brain decides persistence.
    Storage executes persistence.

Phase C adds extraction: observe() persists the turn, emits
ConversationObserved, then runs extraction on a buffered batch (legacy 5-turn
cadence). Extracted claims become atomic KnowledgeItems, a scenario is created
or updated (1:1 with the conversation), the conversation is linked, and
KnowledgeExtracted is emitted. All optional: without an extractor/layers, the
Brain behaves exactly as Phase B.

The legacy MemoryManager write pipeline is no longer called by observe(); it
survives only as the brain=None fallback in the runtime and WebUI, removed in
Phase G.

Architecture Rule #5 (knowledge is append-only):
    KnowledgeItems are immutable historical observations. Their
    content/form/created_at never change after creation; confidence advances
    only through the monotonic promotion monitor. Change is represented as a
    new observation linked to the old one via a typed edge (supersedes), so
    the store keeps a full history and never mutates in place. Current state
    is always derived — the newest verified, non-superseded observation —
    never a claim of absolute truth.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from ..memory.manager import get_memory_manager
from .events import (
    CONVERSATION_OBSERVED,
    KNOWLEDGE_EXTRACTED,
    ConversationObserved,
    KnowledgeExtracted,
)
from .storage.base import ConversationStore
from .types import (
    ContextResolution,
    EdgeKind,
    KnowledgeStatus,
    QueryContext,
    RecallItem,
    RecallResult,
    ReconcileReport,
    ReflectionReport,
    Relationship,
    ScenarioStatus,
    Turn,
)

log = logging.getLogger("novi.brain")

__all__ = ["Brain", "set_brain", "get_brain"]

_brain_instance: "Brain | None" = None


def set_brain(brain: "Brain | None"):
    """Register the active Brain for tool access (mirrors MemoryManager pattern)."""
    global _brain_instance
    _brain_instance = brain


def get_brain() -> "Brain | None":
    return _brain_instance

# Soft tags that mark an item as part of the accumulated Identity layer.
_IDENTITY_TAGS = frozenset({"preference", "goal", "skill", "identity"})


class Brain:
    """Facade exposing observe / recall / learn / resolve / reflect.

    Args:
        memory: MemoryManager-like component. Defaults to the process-global
            manager registered via set_memory_manager.
        project_index: ProjectIndex-like component used to resolve the active
            project and retrieve project context. No process-global exists for
            it; inject it.
        knowledge_index: KnowledgeIndex-like component used to search the
            file-backed knowledge base. Defaults to the process-global index
            registered via init_knowledge_index.
        markdown_store: OKF Markdown mirror of Brain knowledge (optional).
            When wired, every persisted knowledge item write-throughs to the
            configured ``workspace.knowledge`` directory and read-back for
            reconciliation is possible. Never CWD-relative.
        conversation_store: raw conversation persistence behind the
            ConversationStore protocol. Never generates identifiers — the
            Brain owns conversation identity.
        event_bus: object with ``emit(event_type, **data)``. Brain domain
            events are emitted here after state is persisted.
        extractor: reasoning KnowledgeExtractor (optional). When None (with
            the layers), observe() only persists + emits.
        knowledge_layer: knowledge domain manager (optional).
        scenario_layer: scenario domain manager (optional).
        relationship_store: typed edge store (optional). When present, extracted
            knowledge items get derived_from (conversation) and observed_in
            (scenario) provenance edges.
        extract_every: number of buffered turns before extraction runs
            (legacy 5-turn cadence).
    """

    def __init__(
        self,
        *,
        memory: Any = None,
        project_index: Any = None,
        knowledge_index: Any = None,
        markdown_store: Any = None,
        conversation_store: Optional[ConversationStore] = None,
        event_bus: Any = None,
        extractor: Any = None,
        knowledge_layer: Any = None,
        scenario_layer: Any = None,
        relationship_store: Any = None,
        resolver: Any = None,
        extract_every: int = 5,
        tiered_resolver: bool = True,
    ) -> None:
        self._memory = memory
        self._project_index = project_index
        self._knowledge_index = knowledge_index
        self._markdown_store = markdown_store
        self._conversation_store = conversation_store
        self._event_bus = event_bus
        self._extractor = extractor
        self._knowledge_layer = knowledge_layer
        self._scenario_layer = scenario_layer
        self._relationship_store = relationship_store
        self._resolver = resolver
        self._extract_every = max(1, extract_every)
        self._tiered_resolver = tiered_resolver
        self._pending_turns: dict[str, list[Turn]] = {}

    def observe(self, turn: Turn) -> None:
        """Capture a completed turn.

        Order: conversation persisted → ConversationObserved → extraction
        (buffered). Events are emitted only after the work they describe is
        durably done; extraction failure never blocks persistence.
        """
        conversation_id = turn.conversation_id or self._new_conversation_id(turn)
        stored = False
        if self._conversation_store is not None:
            try:
                self._conversation_store.append(turn, conversation_id)
                stored = True
            except Exception:
                log.warning("conversation store append failed", exc_info=True)
        if stored:
            self._emit_conversation_observed(conversation_id, turn)
        self._maybe_extract(conversation_id, turn)

    def recall(self, query: str, context: Optional[QueryContext] = None) -> RecallResult:
        """Retrieve knowledge relevant to a query.

        Phase E: when the layered resolver is available, retrieval walks the
        layers top-down — project → scenario → knowledge → conversation —
        scoring knowledge inside the scenario neighborhood and expanding only
        when the sufficiency gate fails. Without a resolver, behaves exactly
        as Phase A (flat memory query).
        """
        ctx = context or QueryContext()
        resolver = self._resolver or self._default_resolver()
        if resolver is not None:
            return resolver.recall(query, ctx)
        results = self._memory_manager().query(
            text=query,
            k=ctx.top_k,
            distance_threshold=ctx.distance_threshold,
            memory_types=list(ctx.memory_types) if ctx.memory_types else None,
        )
        items = tuple(
            RecallItem(
                text=str(item.get("text", "")),
                score=float(item.get("score", item.get("distance", 0.0))),
                source="memory",
                metadata=dict(item.get("metadata", {})),
            )
            for item in results
        )
        return RecallResult(query=query, items=items)

    def _default_resolver(self):
        """Build the layered resolver from the injected layers, if present.

        Pure wiring: composes the domain layers behind the resolver's read
        callables. Returns None when the layers are absent, keeping recall
        byte-identical to Phase A.
        """
        if self._knowledge_layer is None or self._scenario_layer is None:
            return None
        from .reasoning.resolver import LayeredRetrievalResolver

        return LayeredRetrievalResolver(
            load_scenario=self._scenario_layer.store.get,
            query_knowledge=self._knowledge_layer.query_scoped,
            query_memory=self._resolver_memory_query,
            neighborhood=self.neighborhood,
            fetch_knowledge=self._fetch_knowledge_hits,
            tiered=self._tiered_resolver,
        )

    def _resolver_memory_query(
        self, query: str, k: int, threshold: Optional[float]
    ) -> list[dict]:
        try:
            return self._memory_manager().query(
                text=query,
                k=k,
                distance_threshold=threshold,
                memory_types=None,
            )
        except Exception:
            return []

    def retrieve_knowledge(self, query: str, k: int = 5) -> list[dict]:
        """Search the file-backed knowledge base (Architecture Rule #6).

        The knowledge index is an internal Brain concern; the runtime never
        touches it directly. Returns the same row shape KnowledgeIndex.search
        produces, so source adapters translate byte-identically.
        """
        if self._knowledge_index is None:
            return []
        return self._knowledge_index.search(query, k=k, rerank=True)

    def retrieve_project(self, query: str, k: int = 5) -> str:
        """Retrieve project context through the Brain (Architecture Rule #6).

        Returns the same formatted string ProjectIndex.query produces.
        """
        if self._project_index is None:
            return ""
        return self._project_index.query(text=query, k=k)

    @property
    def project_root(self) -> str:
        if self._project_index is None:
            return ""
        return str(self._project_index.root)

    def set_project_index(self, project_index: Any) -> None:
        """Swap the project index (runtime set_config may update it later)."""
        self._project_index = project_index

    def learn(self, statement: str, source: Optional[str] = None) -> dict:
        """Explicitly acquire knowledge: user asks to remember, write_knowledge.

        ``source`` is reserved for provenance tagging (Phase D: derived_from
        edges; Phase F: identity tag selection; M2: source_kind mapping). When
        the knowledge layer is wired, the statement persists directly as a
        verified knowledge item — immediately discoverable by the resolver —
        then write-throughs to the OKF Markdown mirror (M2). Without layers it
        falls back to the legacy flat writer.

        Returns a small mutation report: ``{"ok", "item_id", "markdown"}``.
        A Markdown write failure is surfaced in the report and logged — the
        operation never silently claims Markdown synchronization succeeded.
        """
        if self._knowledge_layer is not None and self._scenario_layer is not None:
            tags = _tags_for_source(source)
            source_kind = _source_kind_for(source)
            item_id = self._knowledge_layer.write(
                statement, tags=tags, source_kind=source_kind
            )
            markdown = self._sync_markdown(item_id)
            return {"ok": True, "item_id": item_id, "markdown": markdown}
        self._memory_manager().store_fact(statement)
        return {
            "ok": True,
            "item_id": None,
            "markdown": {"written": False, "reason": "legacy writer"},
        }

    def resolve(self, query: str) -> ContextResolution:
        """Resolve the active project + scenario for a query.

        Phase A resolves only the project — today that is the working
        directory. Scenario resolution arrives with the scenarios layer.
        """
        if self._project_index is not None:
            return ContextResolution(project_id=str(self._project_index.root), method="cwd")
        return ContextResolution(method="none")

    def project_context(self) -> dict:
        """Derived, read-only personal-context projection (Phase F Step 4).

        Groups identity-tagged knowledge by category, ranked by the §5
        hierarchy. Always recomputed from the store — never cached. Returns an
        empty projection when no identity-tagged items exist; never invents
        attributes.
        """
        from .projection import project

        if self._knowledge_layer is None or self._scenario_layer is None:
            return {}
        items = self._knowledge_layer.list_objects()
        active_ids: set[str] = set()
        store = getattr(self._scenario_layer, "store", None)
        if store is not None:
            try:
                scenarios = store.list() if hasattr(store, "list") else ()
                for s in scenarios:
                    if getattr(s, "status", None) == ScenarioStatus.ACTIVE:
                        active_ids.add(s.id)
            except Exception:
                log.warning("failed to read active scenarios", exc_info=True)
        return project(items, active_scenario_ids=active_ids)

    def reflect(
        self,
        *,
        scenario_completed: bool = False,
        confirm_burst: bool = False,
        idle_pending: bool = False,
        on_demand: bool = True,
    ) -> ReflectionReport:
        """Run a consolidation + promotion pass over knowledge (Phase F).

        Triggers gate the knowledge pass (§8.2): it runs only when there is
        pending work AND at least one trigger fired. Defaults to ``on_demand``
        (manual ``reflect()`` invocation always honors an explicit pass).

        Without a knowledge layer this behaves exactly as Phase A (legacy
        consolidate pass, ungated).
        """
        from .reasoning import reflection

        if self._knowledge_layer is None or self._scenario_layer is None:
            merges = self._memory_manager().consolidate()
            return ReflectionReport(merges=merges)
        items = self._knowledge_layer.list_objects()
        pending = reflection.pending_count(items)
        if not reflection.should_reflect(
            pending,
            scenario_completed=scenario_completed,
            confirm_burst=confirm_burst,
            idle_pending=idle_pending,
            on_demand=on_demand,
        ):
            return ReflectionReport(touched_ids=())
        return self._reflect_knowledge(items)

    def inspect_memory(self) -> dict:
        """Trust surface (design §4.3): list what Novi remembers.

        Read-only view the user can audit. Returns a per-category projection
        plus every item's status/confidence/last_seen_at/evidence and its
        supersession/conflict edges. No writes; empty when no knowledge layer.
        """
        from .reasoning import reflection
        from .types import EdgeKind, KnowledgeStatus

        if self._knowledge_layer is None or self._scenario_layer is None:
            return {}
        items = self._knowledge_layer.list_objects()
        edges_by_source: dict[str, list[str]] = {}
        if self._relationship_store is not None:
            try:
                for edge in self._relationship_store.list():
                    edges_by_source.setdefault(edge.source_id, []).append(
                        f"{edge.kind.value}:{edge.target_id}"
                    )
            except Exception:
                log.warning("failed to read relationship edges", exc_info=True)
        projection = self.project_context()
        items_view = [
            {
                "id": i.id,
                "content": i.content,
                "status": i.status.value,
                "confidence": i.confidence,
                "importance": i.importance,
                "tags": list(i.tags),
                "scenario_id": i.scenario_id,
                "last_seen_at": reflection.last_used(i).isoformat(),
                "edges": edges_by_source.get(i.id, []),
            }
            for i in items
        ]
        return {"categories": projection, "items": items_view}

    def correct_memory(
        self,
        item_id: str | None = None,
        *,
        statement: str | None = None,
        action: str = "superseded",
        tags: tuple[str, ...] | list[str] = (),
    ) -> dict:
        """Trust surface (design §4.4): user correction, append-only.

        - ``action="superseded"`` (default): demote ``item_id`` and, when
          ``statement`` is given, record the correction as a new verified item
          linked by a ``supersedes`` edge. Idempotent for already-superseded.
        - ``action="demote"``: lower confirmation to ``corroborated``.
        - ``action="archive"``: demote to ``candidate`` (out of default
          retrieval/projection, still queryable).

        Correction outranks corroboration going forward (new verified wins).
        Never deletes. Returns a small mutation report.
        """
        from .types import KnowledgeStatus

        if self._knowledge_layer is None or self._scenario_layer is None:
            return {"ok": False, "error": "no knowledge layer"}
        if item_id is None:
            return {"ok": False, "error": "item_id required"}

        if action == "demote":
            self._knowledge_layer.update_status(item_id, KnowledgeStatus.CORROBORATED)
            self._sync_status_markdown(item_id, KnowledgeStatus.CORROBORATED)
            return {"ok": True, "demoted": item_id, "recorded": None}

        if action == "archive":
            self._knowledge_layer.update_status(item_id, KnowledgeStatus.CANDIDATE)
            self._sync_status_markdown(item_id, KnowledgeStatus.CANDIDATE)
            return {"ok": True, "archived": item_id, "recorded": None}

        self._knowledge_layer.update_status(item_id, KnowledgeStatus.SUPERSEDED)
        self._sync_status_markdown(item_id, KnowledgeStatus.SUPERSEDED)
        recorded = None
        if statement:
            new_id = self._knowledge_layer.write(statement, tags=tags)
            recorded = new_id
            self._sync_markdown(new_id)
            if self._relationship_store is not None:
                self._relationship_store.add_many(
                    [
                        Relationship(
                            source_id=new_id,
                            target_id=item_id,
                            kind=EdgeKind.SUPERSEDES,
                        )
                    ]
                )
        return {"ok": True, "superseded": item_id, "recorded": recorded}

    def reconcile_markdown(self) -> ReconcileReport:
        """Markdown → Brain reconciliation foundation (Architecture B.2).

        Detects user-authored Markdown changes and folds them into Brain
        through the *same* learning path as ``Brain.learn`` — there is exactly
        one durable knowledge-writing mechanism:

        * a note with no Brain identity, or content Brain does not know, is
          learned as new knowledge;
        * an edit to a known item that changes its *semantic* content is
          learned and the previous item is superseded (append-only);
        * formatting-only changes (whitespace, emphasis, link syntax) leave
          the semantic form unchanged and create no knowledge;
        * a Brain item whose Markdown file has been deleted is never
          hard-deleted — it remains historical and is flagged for
          supersession/decay on the next reflect pass.
        """
        if self._markdown_store is None or self._knowledge_layer is None:
            return ReconcileReport(skipped=True)
        items = self._knowledge_layer.list_objects()
        by_id = {i.id: i for i in items}
        seen_ids: set[str] = set()
        report = ReconcileReport()
        for path in self._markdown_store.list_files():
            report.scanned += 1
            meta, body = self._markdown_store.parse(path)
            norm_body = _semantic(body)
            if not norm_body:
                report.removed_claims += 1
                continue
            fid = meta.get("id")
            if fid and fid in by_id:
                seen_ids.add(fid)
                if _semantic(by_id[fid].content) == norm_body:
                    report.unchanged += 1
                    continue
                new_id = self.learn(body, source="markdown").get("item_id")
                if new_id:
                    seen_ids.add(new_id)
                self._markdown_supersede(fid, new_id)
                report.edited += 1
            else:
                new_id = self.learn(body, source="markdown").get("item_id")
                if new_id:
                    seen_ids.add(new_id)
                report.new += 1
        for item in items:
            if item.id not in seen_ids and item.status is not KnowledgeStatus.SUPERSEDED:
                report.missing_files += 1
        # WikiLink reconciliation (M3): resolve every note's links to durable
        # identities and diff `references` edges. Best-effort; never blocks the
        # markdown→brain reconciliation itself.
        try:
            self.sync_wikilinks()
        except Exception:
            log.warning("wikilink sync after reconcile failed", exc_info=True)
        return report

    def sync_wikilinks(self):
        """Reconcile WikiLink relationships across the whole knowledge base (M3).

        Resolves every note's WikiLinks to durable Brain identities and diffs the
        ``references`` edges: new links are added, stale links removed, dangling
        links recorded, ambiguous links left unresolved. Idempotent — re-running
        changes nothing once the Markdown and the edge set are consistent.

        Returns a :class:`~novi.brain.wikilinks.WikilinkSyncReport`.
        """
        from .wikilinks import WikilinkSyncReport, WikilinkSynchronizer

        if self._markdown_store is None or self._relationship_store is None:
            return WikilinkSyncReport(skipped=True)
        try:
            sync = WikilinkSynchronizer(
                self._markdown_store, self._relationship_store
            )
            return sync.sync_all()
        except Exception:
            log.warning("wikilink sync failed", exc_info=True)
            return WikilinkSyncReport(skipped=True)

    def backlinks(self, item_id: str, *, kind: EdgeKind = EdgeKind.REFERENCES) -> tuple[str, ...]:
        """Incoming relationship sources for ``item_id`` (M3 backlinks).

        Reads only the RelationshipStore incoming-edge index — no second
        backlink database. Returns the source knowledge identities that
        reference ``item_id`` via ``kind`` (``references`` by default).
        """
        if self._relationship_store is None:
            return ()
        try:
            edges = self._relationship_store.incoming(item_id, kind=kind)
        except Exception:
            log.warning("backlinks read failed for %s", item_id, exc_info=True)
            return ()
        return tuple(e.source_id for e in edges)

    def neighborhood(self, item_id: str) -> dict:
        """Retrieval-preparation graph view over a single identity (M3).

        Returns the durable identities reachable from ``item_id`` through
        ``references`` edges: outgoing references and incoming backlinks.
        Traversal uses only the RelationshipStore — no second storage system.
        """
        if self._relationship_store is None:
            return {"references": (), "backlinks": ()}
        try:
            out = self._relationship_store.outgoing(
                item_id, kind=EdgeKind.REFERENCES
            )
            inc = self._relationship_store.incoming(
                item_id, kind=EdgeKind.REFERENCES
            )
            return {
                "references": tuple(e.target_id for e in out),
                "backlinks": tuple(e.source_id for e in inc),
            }
        except Exception:
            log.warning("neighborhood read failed for %s", item_id, exc_info=True)
            return {"references": (), "backlinks": ()}

    def knowledge_items(self, item_ids) -> list:
        """Fetch durable knowledge items by Brain identity (M4 expansion).

        The read side of graph retrieval: resolves ``kn-…`` ids to
        KnowledgeItems through the knowledge layer in one batched store read
        (M4.1). Missing/deleted ids are skipped silently, and SUPERSEDED
        items are filtered out — superseded claims never re-enter retrieval
        through the graph. Returns [] (never raises) when no knowledge layer
        is wired.
        """
        if self._knowledge_layer is None or not item_ids:
            return []
        store = self._knowledge_layer.store
        wanted: list[str] = []
        seen: set[str] = set()
        for item_id in item_ids:
            iid = str(item_id)
            if iid and iid not in seen:
                seen.add(iid)
                wanted.append(iid)
        if not wanted:
            return []
        get_many = getattr(store, "get_many", None)
        try:
            rows = (
                get_many(wanted)
                if get_many is not None
                else [r for iid in wanted if (r := store.get(iid)) is not None]
            )
        except Exception:
            log.warning("batch knowledge fetch failed", exc_info=True)
            return []
        out = []
        for row in rows:
            try:
                item = store.item_from_row(row)
            except Exception:
                log.warning("knowledge row decode failed", exc_info=True)
                continue
            if item.status == KnowledgeStatus.SUPERSEDED:
                continue
            out.append(item)
        return out

    def _fetch_knowledge_hits(self, item_ids) -> list:
        """Resolver adapter: durable ids → KnowledgeHits (M4)."""
        from .types import KnowledgeHit

        return [
            KnowledgeHit(item=item)
            for item in self.knowledge_items(item_ids)
        ]

    def _markdown_supersede(self, old_id: str, new_id: str | None) -> None:
        """Append-only supersession for a reconciled claim (never a delete)."""
        if self._knowledge_layer is None:
            return
        self._knowledge_layer.update_status(old_id, KnowledgeStatus.SUPERSEDED)
        self._sync_status_markdown(old_id, KnowledgeStatus.SUPERSEDED)
        if new_id and self._relationship_store is not None:
            try:
                self._relationship_store.add_many(
                    [
                        Relationship(
                            source_id=new_id,
                            target_id=old_id,
                            kind=EdgeKind.SUPERSEDES,
                        )
                    ]
                )
            except Exception:
                log.warning(
                    "failed to write supersedes edge during reconcile", exc_info=True
                )

    def _sync_markdown(self, item_id: str) -> dict:
        """Write-through a persisted Brain item to its OKF Markdown mirror.

        Runs only when a MarkdownStore is wired. The markdown source_kind is
        read back from the stored row, so the representation never fabricates
        provenance. Failures are logged and surfaced in the returned report —
        never silently claimed as synchronized.
        """
        if self._markdown_store is None:
            return {"written": False, "reason": "no markdown store"}
        if self._knowledge_layer is None:
            return {"written": False, "reason": "no knowledge layer"}
        try:
            row = self._knowledge_layer.store.get(item_id)
        except Exception as e:
            log.warning("markdown sync: failed to read %s: %s", item_id, e)
            return {"written": False, "error": str(e)}
        if row is None:
            return {"written": False, "error": f"item {item_id} not found"}
        source_kind = str(row.get("source_kind", "explicit"))
        item = self._knowledge_layer.store.item_from_row(row)
        try:
            rel, created = self._markdown_store.write_item(
                item, source_kind=source_kind
            )
        except Exception as e:
            log.warning("markdown write failed for %s: %s", item_id, e)
            return {"written": False, "error": str(e)}
        if created:
            self._sync_markdown_wikilinks(rel)
        self._index_markdown_file(item_id, rel)
        return {"written": True, "path": rel, "created": created}

    def _sync_status_markdown(self, item_id: str, status: KnowledgeStatus) -> None:
        """Mirror a status transition to the item's Markdown file (best-effort)."""
        if self._markdown_store is None:
            return
        try:
            self._markdown_store.update_status(item_id, status)
        except Exception:
            log.warning(
                "markdown status sync failed for %s", item_id, exc_info=True
            )

    def _sync_markdown_wikilinks(self, rel: str) -> None:
        """Resolve a freshly written mirror's WikiLinks to durable identities.

        Creation-only (M2 semantics preserved): runs when a mirror file is first
        created, so re-syncing an unchanged claim never duplicates edges. Targets
        are resolved against the note index — dangling links stay as the M2
        ``note:<Title>`` form; matching notes link to the real Brain id. Full
        diff-based reconciliation across the whole knowledge base lives in
        ``sync_wikilinks`` (reconcile-driven).
        """
        if self._relationship_store is None or self._markdown_store is None:
            return
        try:
            from .wikilinks import WikilinkSynchronizer

            sync = WikilinkSynchronizer(self._markdown_store, self._relationship_store)
            sync.sync_file(rel)
        except Exception:
            log.warning(
                "failed to resolve wikilinks for %s", rel, exc_info=True
            )

    def _index_markdown_file(self, item_id: str, rel: str) -> None:
        """Re-index the affected Markdown file (mtime-aware index)."""
        if self._knowledge_index is None or self._markdown_store is None:
            return
        index_file = getattr(self._knowledge_index, "index_file", None)
        if index_file is None:
            return
        try:
            index_file(self._markdown_store.knowledge_dir / rel)
        except Exception as e:
            log.warning("markdown sync: index failed for %s: %s", rel, e)

    def _reflect_knowledge(self, items) -> ReflectionReport:
        from .reasoning import reflection
        from .types import KnowledgeStatus

        plan = reflection.make_plan(items, find_related=_find_related)
        promotes = 0
        corroborated = 0
        superseded = 0
        conflicts = 0
        decays = 0
        touched: set[str] = set()
        edges: list[Relationship] = []
        for outcome in plan:
            item = outcome.item
            if outcome.new_status == item.status:
                continue
            self._knowledge_layer.update_status(item.id, outcome.new_status)
            touched.add(item.id)
            self._sync_status_markdown(item.id, outcome.new_status)
            if outcome.new_status == KnowledgeStatus.VERIFIED:
                promotes += 1
            else:
                corroborated += 1
            if outcome.supersedes is not None:
                superseded += 1
                touched.add(outcome.supersedes.target_id)
                self._knowledge_layer.update_status(
                    outcome.supersedes.target_id, KnowledgeStatus.SUPERSEDED
                )
                self._sync_status_markdown(
                    outcome.supersedes.target_id, KnowledgeStatus.SUPERSEDED
                )
                edges.append(outcome.supersedes)
                if outcome.conflicts is not None:
                    conflicts += 1
                    edges.append(outcome.conflicts)
        for item in reflection.decay_plan(items, datetime.now()):
            if item.status in (KnowledgeStatus.VERIFIED, KnowledgeStatus.SUPERSEDED):
                continue
            self._knowledge_layer.update_status(item.id, KnowledgeStatus.CANDIDATE)
            decays += 1
            touched.add(item.id)
            self._sync_status_markdown(item.id, KnowledgeStatus.CANDIDATE)
        if edges and self._relationship_store is not None:
            try:
                self._relationship_store.add_many(edges)
            except Exception:
                log.warning("failed to write supersedes edges", exc_info=True)
        report = ReflectionReport(
            promotions=promotes,
            corroborated=corroborated,
            superseded=superseded,
            conflicts=conflicts,
            decays=decays,
            touched_ids=tuple(sorted(touched)),
        )
        if touched:
            self._emit_knowledge_promoted(report, touched)
        return report

    def _emit_knowledge_promoted(
        self, report: ReflectionReport, touched: set[str]
    ) -> None:
        """Emit a ``knowledge.promoted`` event after durable writes.

        Emitted only when at least one item was touched. Carries canonical
        Brain item ids, never storage rows or ids.
        """
        from .events import KNOWLEDGE_PROMOTED, KnowledgePromoted

        if self._event_bus is None:
            return
        event = KnowledgePromoted(
            item_ids=tuple(sorted(touched)),
            promotions=report.promotions,
            corroborated=report.corroborated,
            superseded=report.superseded,
            conflicts=report.conflicts,
        )
        try:
            self._event_bus.emit(KNOWLEDGE_PROMOTED, **event.to_payload())
        except Exception:
            log.warning("failed to emit knowledge.promoted", exc_info=True)

    def _new_conversation_id(self, turn: Turn) -> str:
        """Brain-owned conversation identity.

        Conversations are Brain concepts, not runtime or storage concepts.
        """
        return f"conv-{turn.timestamp.strftime('%Y%m%dT%H%M%S%f')}"

    def _maybe_extract(self, conversation_id: str, turn: Turn) -> None:
        """Buffer turns; run extraction when the batch reaches extract_every.

        All optional deps. Extraction is best-effort: any failure is logged
        and the buffered batch is dropped, never re-raising into observe().
        """
        if (
            self._extractor is None
            or self._knowledge_layer is None
            or self._scenario_layer is None
        ):
            return
        batch = self._pending_turns.get(conversation_id, [])
        batch.append(turn)
        if len(batch) < self._extract_every:
            self._pending_turns[conversation_id] = batch
            return
        self._pending_turns.pop(conversation_id, None)
        try:
            result = self._extractor.extract(tuple(batch))
            conversation = None
            if self._conversation_store is not None:
                conversation = self._conversation_store.get(conversation_id)
            scenario_id = self._scenario_layer.ensure_for_conversation(
                conversation, result
            )
            knowledge_ids = self._knowledge_layer.store_extracted(
                conversation_id, scenario_id, result
            )
            if self._conversation_store is not None and conversation is not None:
                if getattr(conversation, "scenario_id", None) != scenario_id:
                    self._conversation_store.set_scenario_id(
                        conversation_id, scenario_id
                    )
            if knowledge_ids:
                self._write_provenance_edges(knowledge_ids, conversation_id, scenario_id)
                self._sync_extracted_markdown(knowledge_ids)
                self._emit_knowledge_extracted(
                    tuple(knowledge_ids), conversation_id, scenario_id, result.summary
                )
        except Exception:
            log.warning("knowledge extraction failed", exc_info=True)

    def _sync_extracted_markdown(self, knowledge_ids: list[str]) -> None:
        """Write-through extracted knowledge through the same mirror as learn.

        One durable knowledge-writing mechanism (Architecture B.8): extraction
        never gets a separate Markdown path. Best-effort per item; a failed
        mirror never breaks extraction or persistence.
        """
        if self._markdown_store is None:
            return
        for kid in knowledge_ids:
            self._sync_markdown(kid)

    def _write_provenance_edges(
        self, knowledge_ids: list[str], conversation_id: str, scenario_id: str
    ) -> None:
        """Provenance as first-class relationships (Phase D).

        Each extracted item links to its source conversation (derived_from)
        and its scenario (observed_in). Best-effort; edge failure never breaks
        extraction or persistence.
        """
        if self._relationship_store is None:
            return
        try:
            now = datetime.now()
            relationships = []
            for kid in knowledge_ids:
                relationships.append(
                    Relationship(
                        source_id=kid,
                        target_id=conversation_id,
                        kind=EdgeKind.DERIVED_FROM,
                        created_at=now,
                    )
                )
                relationships.append(
                    Relationship(
                        source_id=kid,
                        target_id=scenario_id,
                        kind=EdgeKind.OBSERVED_IN,
                        created_at=now,
                    )
                )
            self._relationship_store.add_many(relationships)
        except Exception:
            log.warning("failed to write provenance edges", exc_info=True)

    def _emit_knowledge_extracted(
        self,
        knowledge_ids: tuple[str, ...],
        conversation_id: str,
        scenario_id: str,
        summary: str,
    ) -> None:
        event = KnowledgeExtracted(
            knowledge_ids=knowledge_ids,
            conversation_id=conversation_id,
            scenario_id=scenario_id,
            summary=summary,
        )
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(KNOWLEDGE_EXTRACTED, **event.to_payload())
        except Exception:
            log.warning("failed to emit %s", KNOWLEDGE_EXTRACTED, exc_info=True)

    def _emit_conversation_observed(self, conversation_id: str, turn: Turn) -> None:
        event = ConversationObserved(
            conversation_id=conversation_id,
            user=turn.user,
            assistant=turn.assistant,
            timestamp=turn.timestamp,
            tool_outputs=turn.tool_outputs,
        )
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(CONVERSATION_OBSERVED, **event.to_payload())
        except Exception:
            log.warning("failed to emit %s", CONVERSATION_OBSERVED, exc_info=True)

    def _memory_manager(self):
        manager = self._memory
        if manager is None:
            manager = get_memory_manager()
        if manager is None:
            raise RuntimeError(
                "no memory manager wired; call set_memory_manager or pass memory= to Brain()"
            )
        return manager


def _tags_for_source(source: Optional[str]) -> tuple[str, ...]:
    """Map a ``source`` label to identity-seeding tags (Phase F)."""
    if not source:
        return ()
    s = source.lower()
    if "preference" in s:
        return ("preference", "identity")
    if "goal" in s:
        return ("goal", "identity")
    if "skill" in s:
        return ("skill", "identity")
    return ()


def _source_kind_for(source: Optional[str]) -> str:
    """Map a ``source`` label to the stored source_kind (M2).

    User-directed writes (write_knowledge tool, Markdown reconciliation)
    persist as ``user_authored``; every other learn stays ``explicit`` so
    existing behavior is unchanged.
    """
    if source and source.lower() in ("knowledge", "markdown", "user", "user_authored"):
        return "user_authored"
    return "explicit"


def _semantic(text: str) -> str:
    """Canonical semantic form of a claim (shared with the Markdown writer)."""
    from ..memory.okf import semantic_normalize

    return semantic_normalize(text)


def _find_related(item, items) -> Optional[Any]:
    """Most recent VERIFIED item that overlaps this item (supersession target).

    Identity change keeps history: a newly verified claim supersedes the most
    recent verified item whose tags overlap the new one. Returns None when no
    such item exists (e.g. the very first verified preference).
    """
    from .types import KnowledgeStatus

    prospective = _IDENTITY_TAGS.intersection(item.tags)
    best = None
    for other in items:
        if other.id == item.id:
            continue
        if other.status != KnowledgeStatus.VERIFIED:
            continue
        if not _IDENTITY_TAGS.intersection(other.tags):
            continue
        if prospective and not (set(prospective) & set(other.tags)):
            continue
        if best is None or other.created_at >= best.created_at:
            best = other
    return best
