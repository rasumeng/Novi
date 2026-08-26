"""M4 evaluation — baseline semantic retrieval vs WikiLink-expanded retrieval.

Standalone harness (run directly): builds a synthetic linked corpus in real
stores (VectorStore + RelationshipStore, deterministic fake embeddings), then
compares:

    baseline   semantic retrieval only (expansion unwired)
    expanded   semantic retrieval + bounded WikiLink neighborhood expansion

Measured per arm (spec M4 Evaluation):
    - relevant knowledge discovered        (recall of planted targets)
    - irrelevant knowledge introduced      (false positives)
    - duplicate rate                       (duplicate durable ids per result)
    - context size                         (items and characters returned)
    - retrieval latency                    (mean ms per query)
    - query success                        (all expected targets present)

The embedder makes every query vector orthogonal to every document vector, so
semantic scores always fail the sufficiency gate: the comparison isolates what
the graph stage adds when the semantic stage alone cannot satisfy the query.

Usage:
    venv\\Scripts\\python.exe tests\\evaluate_retrieval_expansion.py [--json PATH]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from novi.brain import Brain, EdgeKind, QueryContext, Relationship
from novi.brain.layers.knowledge import KnowledgeLayer
from novi.brain.layers.scenarios import ScenarioLayer
from novi.brain.reasoning.resolver import LayeredRetrievalResolver
from novi.brain.storage.relationship_store import RelationshipStore
from novi.brain.storage.scenario_store import ScenarioStore
from novi.brain.storage.vector_store import VectorStore
from novi.brain.types import KnowledgeStatus
from novi.runtime.retrieval_budget import ContextAllocation
from novi.runtime.sources import KnowledgeRetrievalSource
from novi.services.embedding import EmbeddingService

# ── deterministic fake embedding ─────────────────────────────────────────────


class OrthogonalEmbed(EmbeddingService):
    """Queries embed orthogonally to documents → semantic gate always fails."""

    def __init__(self, dim: int = 16):
        super().__init__({"embedding": {"model": "fake-embed"}})
        self._dim = dim

    @property
    def model_name(self):
        return "fake-embed"

    def encode(self, text, normalize=True):
        v = [0.0] * self._dim
        if "needle" in (text or "").lower():
            v[1] = 1.0
        else:
            v[0] = 1.0
        return v

    @property
    def dimension(self):
        return self._dim


class StubMemory:
    def store_fact(self, statement):
        pass

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        return []


class RowIndex:
    """Controlled semantic rows for the source-surface arm."""

    def __init__(self):
        self.rows = []

    def index_file(self, path):
        pass

    def search(self, query, k=5, rerank=True):
        return self.rows[:k]


# ── corpus ───────────────────────────────────────────────────────────────────

CLUSTERS = 12          # each cluster: seed note linking two target notes
DISTRACTORS = 12       # unlinked notes no query should ever pull in


@dataclass
class Case:
    query: str
    expected: tuple[str, ...]
    seed_row: dict
    # Source arm retrieves the seed as a path-chunked row (id ``<rel>::0``),
    # so its expectation covers exactly what expansion must add: the targets.
    source_expected: tuple[str, ...] = ()
    # Linked-but-superseded twin: must never surface in any arm.
    dead_id: str = ""


def build_brain(tmp: Path):
    store = VectorStore(persist_dir=tmp / "brain", embed_model=OrthogonalEmbed())
    rels = RelationshipStore(persist_dir=tmp / "rels")
    idx = RowIndex()
    brain = Brain(
        memory=StubMemory(),
        knowledge_index=idx,
        knowledge_layer=KnowledgeLayer(store),
        scenario_layer=ScenarioLayer(ScenarioStore(persist_dir=tmp / "brain")),
        relationship_store=rels,
    )
    return brain, rels, idx


def build_corpus(brain, rels, idx) -> list[Case]:
    """Plant CLUSTERS linked triples (+ one superseded twin each) + distractors."""
    cases: list[Case] = []
    for c in range(CLUSTERS):
        seed_id = brain.learn(f"Cluster {c} seed overview hub note.")["item_id"]
        targets = []
        for t in range(2):
            tid = brain.learn(
                f"Cluster {c} target {t} deep specialized material."
            )["item_id"]
            rels.add(Relationship(source_id=seed_id, target_id=tid,
                                  kind=EdgeKind.REFERENCES))
            targets.append(tid)
        # Superseded twin: linked like a real target but status=superseded.
        # Must never leak into any arm's context (M4.1 boundary + M5 filter).
        dead_id = brain.learn(
            f"Cluster {c} target outdated stale material."
        )["item_id"]
        brain._knowledge_layer.update_status(dead_id, KnowledgeStatus.SUPERSEDED)
        rels.add(Relationship(source_id=seed_id, target_id=dead_id,
                              kind=EdgeKind.REFERENCES))
        cases.append(
            Case(
                query=f"needle cluster {c} question",
                expected=(seed_id, *targets),
                source_expected=tuple(targets),
                dead_id=dead_id,
                seed_row={
                    "id": f"cluster-{c}.md::0",
                    "text": f"Cluster {c} seed overview hub note.",
                    "score": 0.05,  # below the sufficiency gate
                    "metadata": {"path": f"cluster-{c}.md",
                                 "item_id": seed_id, "type": "knowledge"},
                },
            )
        )
    for d in range(DISTRACTORS):
        brain.learn(f"Distractor {d} unrelated filler body.")["item_id"]
    return cases


# ── arms ─────────────────────────────────────────────────────────────────────


def run_resolver_arm(brain, cases, *, expanded: bool):
    """Semantic stage returns each cluster's hub seed at a gate-failing score.

    The embedder cannot produce that regime honestly (identical doc vectors
    score 1.0), so the semantic callable is controlled — same isolation as the
    source arm. What the measurement isolates is exactly what the graph stage
    adds on top of a weak-but-present semantic hit.
    """
    from novi.brain.types import KnowledgeHit

    seed_items = {
        c.seed_row["metadata"]["item_id"]: brain.knowledge_items(
            [c.seed_row["metadata"]["item_id"]]
        )[0]
        for c in cases
    }

    def fake_query(q, scenario_id=None, k=5, distance_threshold=None):
        case = next((c for c in cases if c.query == q), None)
        if case is None:
            return []
        item = seed_items[case.seed_row["metadata"]["item_id"]]
        return [KnowledgeHit(item=item, score=case.seed_row["score"])]

    kw = {}
    if expanded:
        kw = {
            "neighborhood": brain.neighborhood,
            "fetch_knowledge": lambda ids: _hits(brain, ids),
        }
    resolver = LayeredRetrievalResolver(
        load_scenario=lambda sid: None,
        query_knowledge=fake_query,
        query_memory=lambda q, k, t: [],
        **kw,
    )
    # warmup outside timing (LanceDB/store init would skew the first arm)
    resolver.recall("warmup needle", QueryContext(top_k=3,
                                                  distance_threshold=None))
    latencies, results = [], []
    for case in cases:
        t0 = time.perf_counter()
        res = resolver.recall(case.query, QueryContext(top_k=3,
                                                       distance_threshold=None))
        latencies.append((time.perf_counter() - t0) * 1000.0)
        results.append([
            (i.metadata.get("id"), i.text)
            for i in res.items if i.source == "knowledge"
        ])
    return results, latencies


class _HitShim:
    def __init__(self, item):
        self.item = item
        self.score = 0.0


def _hits(brain, ids):
    return [_HitShim(i) for i in brain.knowledge_items(ids)]


def run_source_arm(brain, idx, cases, *, expanded: bool):
    src = KnowledgeRetrievalSource(brain, expand_related=expanded)
    budget = ContextAllocation(max_results=3)
    src.retrieve("warmup needle", budget)
    latencies, results = [], []
    for case in cases:
        idx.rows = [case.seed_row]
        t0 = time.perf_counter()
        res = src.retrieve(case.query, budget)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        results.append([(i.id, i.text) for i in res.items])
    return results, latencies


def run_unified_arm(brain, idx, cases):
    """M5 arm: unified pool (knowledge+graph) → merge → rank → budget select.

    Metrics here measure the FINAL selected context — the minimum-sufficient
    pick — not the full candidate pool.
    """
    from novi.runtime.sources import KnowledgeRetrievalSource
    from novi.runtime.unified_retrieval import SourceBinding, UnifiedRetriever

    src = KnowledgeRetrievalSource(brain)
    retriever = UnifiedRetriever()
    alloc = ContextAllocation(max_results=3, max_context_chars=1500)
    retriever.retrieve("warmup needle", alloc,
                       [SourceBinding("knowledge", src)])
    latencies, ranking_ms, results = [], [], []
    for case in cases:
        idx.rows = [case.seed_row]
        t0 = time.perf_counter()
        outcome = retriever.retrieve(
            case.query, alloc, [SourceBinding("knowledge", src)])
        latencies.append((time.perf_counter() - t0) * 1000.0)
        ranking_ms.append(outcome.merged.metrics["ranking_latency_ms"])
        results.append([(i.id, i.text) for i in outcome.selected])
    return results, latencies, ranking_ms


# ── metrics ──────────────────────────────────────────────────────────────────


@dataclass
class ArmMetrics:
    arm: str
    relevant_discovered: int = 0
    relevant_possible: int = 0
    irrelevant_introduced: int = 0
    duplicates: int = 0
    total_items: int = 0
    total_chars: int = 0
    relevant_chars: int = 0
    superseded_leakage: int = 0
    successes: int = 0
    queries: int = 0
    latency_ms: list = field(default_factory=list)
    ranking_ms: list = field(default_factory=list)

    def as_row(self):
        recall = (self.relevant_discovered / self.relevant_possible) if self.relevant_possible else 0.0
        dup_rate = (self.duplicates / self.total_items) if self.total_items else 0.0
        mean_ms = statistics.fmean(self.latency_ms) if self.latency_ms else 0.0
        p95_ms = sorted(self.latency_ms)[int(len(self.latency_ms) * 0.95) - 1] if self.latency_ms else 0.0
        rank_ms = statistics.fmean(self.ranking_ms) if self.ranking_ms else 0.0
        efficiency = (self.relevant_chars / self.total_chars) if self.total_chars else 0.0
        return {
            "arm": self.arm,
            "query_success": f"{self.successes}/{self.queries}",
            "relevant_recall": round(recall, 4),
            "irrelevant_introduced": self.irrelevant_introduced,
            "superseded_leakage": self.superseded_leakage,
            "duplicate_rate": round(dup_rate, 4),
            "context_items": self.total_items,
            "context_chars": self.total_chars,
            "context_efficiency": round(efficiency, 4),
            "latency_mean_ms": round(mean_ms, 3),
            "latency_p95_ms": round(p95_ms, 3),
            "ranking_latency_mean_ms": round(rank_ms, 3),
        }


def measure(arm: str, cases, results, latencies, *, source: bool = False,
            ranking_ms=None) -> ArmMetrics:
    """Score one arm's retrieved/selected pairs against its expectations.

    ``source=True`` measures the source-surface expectation (targets only);
    the default measures the full durable triple.
    """
    m = ArmMetrics(arm=arm, queries=len(cases))
    m.latency_ms = latencies
    m.ranking_ms = list(ranking_ms or [])
    for case, pairs in zip(cases, results):
        expected = case.source_expected if source else case.expected
        seen: set[str] = set()
        unique_hits: set[str] = set()
        for iid, text in pairs:
            m.total_items += 1
            m.total_chars += len(text or "")
            if iid in seen:
                m.duplicates += 1
            seen.add(iid)
            if iid in expected:
                m.relevant_discovered += 1
                m.relevant_chars += len(text or "")
                unique_hits.add(iid)
            else:
                m.irrelevant_introduced += 1
            if case.dead_id and iid == case.dead_id:
                m.superseded_leakage += 1
        m.relevant_possible += len(expected)
        m.successes += int(unique_hits == set(expected))
    return m


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None, help="also write results JSON")
    args = parser.parse_args()

    tmp = Path(__file__).resolve().parent / ".m4_eval_tmp"
    tmp.mkdir(exist_ok=True)
    brain, rels, idx = build_brain(tmp)
    cases = build_corpus(brain, rels, idx)

    arms = []
    r_base, r_base_lat = run_resolver_arm(brain, cases, expanded=False)
    arms.append(measure("resolver: semantic only", cases, r_base, r_base_lat))
    r_exp, r_exp_lat = run_resolver_arm(brain, cases, expanded=True)
    arms.append(measure("resolver: semantic+wikilink", cases, r_exp, r_exp_lat))

    s_base, s_base_lat = run_source_arm(brain, idx, cases, expanded=False)
    arms.append(measure("source: semantic only", cases, s_base, s_base_lat,
                        source=True))
    s_exp, s_exp_lat = run_source_arm(brain, idx, cases, expanded=True)
    arms.append(measure("source: semantic+wikilink", cases, s_exp, s_exp_lat,
                        source=True))

    u_res, u_lat, u_rank = run_unified_arm(brain, idx, cases)
    arms.append(measure("M5 unified (selected context)", cases, u_res, u_lat,
                        source=True, ranking_ms=u_rank))

    rows = [a.as_row() for a in arms]
    width = max(len(k) for k in rows[0])
    header = list(rows[0].keys())
    print(f"M4 retrieval expansion evaluation — "
          f"{len(cases)} queries, {CLUSTERS} linked clusters, "
          f"{DISTRACTORS} distractors\n")
    print(f"{'metric'.ljust(width)} | " + " | ".join(r["arm"] for r in rows))
    print("-" * (width + 3 + sum(len(r["arm"]) + 3 for r in rows)))
    for key in header:
        if key == "arm":
            continue
        print(f"{key.ljust(width)} | "
              + " | ".join(str(r[key]).ljust(len(r["arm"])) for r in rows))

    # cleanup temp stores unless persisting for inspection
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nresults written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
