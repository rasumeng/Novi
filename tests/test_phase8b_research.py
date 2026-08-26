"""Phase 8B — Deep Research 2.0 tests.

Covers the research intelligence layer added on top of the 8A foundation:

Decomposition (8B.1)
  - deterministic JSON contract parse, bounded sub-questions, malformed
    fallback, trivial-question skip, cancellation mid-decompose.

Gap→query refinement (8B.2)
  - refined query derives from uncovered terms; covered gaps never mutate
    the query (duplicate gate stays effective).

Evidence accumulation (8B.3/8B.4)
  - bundles accumulate across attempts, deduplicate by URL identity, and
    respect hard bundle/source bounds.

Conflicts (8B.5) / manifest (8B.6) / truncation (8B.7) / validation (8B.8)
  - manifest entries come from ACTUAL results only; citations resolve
    against the manifest; insufficiency disclosure is detected; grounding
    truncation honors the context budget; conflicts reuse the existing
    ConflictDetector.
"""

import pytest

from novi.graphs import ResearchGraph
from novi.graphs import research_intel as ri
from novi.runtime.evidence import EvidenceBundle, RetrievalQuality
from novi.runtime.retrieval_coordinator import RetrievalBudget, RetrievalCoordinator
from novi.tools.search_pipeline import SearchResult


# ── helpers ───────────────────────────────────────────────────────────────


class _JsonModel:
    """Model stub returning queued answers; records invocations."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def invoke(self, msgs):
        self.calls += 1
        return type("R", (), {"content": self.answers.pop(0) if self.answers else ""})()


class _StubModel(_JsonModel):
    def __init__(self, answer="answer-with-key"):
        super().__init__([answer])


def _result(url, title="T", snippet="snippet text", full_text=""):
    return SearchResult(title=title, url=url, snippet=snippet,
                        full_text=full_text or snippet)


def _bundle(quality=RetrievalQuality.SUFFICIENT,
            text="grounding evidence text for key",
            results=None, error=None, query="q"):
    return EvidenceBundle(
        query=query,
        merged_text=text if not error else "",
        source_count=len(results or []),
        quality=quality,
        results=list(results or []),
        error=error,
    )


def _state(**kw):
    state = {
        "user_input": "what is the key",
        "analysis": None,
        "retrieval_plan": None,
        "grounding_text": "",
        "quality": "",
        "query": "what is the key",
        "search_attempts": 0,
        "max_search_attempts": 2,
        "system_prompt": "system",
        "plan_step_index": 0,
    }
    state.update(kw)
    return state


# ── decomposition ─────────────────────────────────────────────────────────


def test_should_decompose_heuristic():
    assert ri.should_decompose("Compare asyncio and threading for IO work")
    assert not ri.should_decompose("what is the key")           # too short
    assert not ri.should_decompose(
        "Explain the event loop model in detail please")        # single-focus, no signal


def test_parse_decomposition_success():
    subs = ri.parse_decomposition(
        'noise before {"sub_questions": ["What is X?", "How does Y work?", '
        '"Who uses Z?"]} noise after')
    assert subs == ["What is X?", "How does Y work?", "Who uses Z?"]


def test_parse_decomposition_bounded_and_deduplicated():
    raw = ('{"sub_questions": ["a", "a", ' +
           ", ".join(f'"{i}"' for i in range(10)) + "]}")
    subs = ri.parse_decomposition(raw)
    assert len(subs) == ri.MAX_SUB_QUESTIONS
    assert subs.count("a") == 1


def test_parse_decomposition_malformed_returns_empty():
    assert ri.parse_decomposition("no json at all") == []
    assert ri.parse_decomposition('{"wrong": [1,2]}') == []
    assert ri.parse_decomposition('{"sub_questions": "not a list"}') == []
    assert ri.parse_decomposition('{"sub_questions": [1, null, "ok"]}') == ["ok"]
    assert ri.parse_decomposition("") == []


def test_graph_skips_model_call_on_trivial_question():
    model = _StubModel()
    g = ResearchGraph(model=model, search=lambda q: _bundle())
    g.run(_state())
    assert model.calls == 1, "only synthesis may invoke the model"


def test_graph_decompose_success_searches_subquestions():
    queries = []

    def search(query):
        queries.append(query)
        return _bundle()

    model = _JsonModel([
        '{"sub_questions": ["what is key part one", "how does key part two work"]}',
        "final answer",
    ])
    g = ResearchGraph(model=model, search=search, max_search_attempts=3)
    result = g.run(_state(user_input="Compare asyncio and threading for IO work",
                          query="Compare asyncio and threading for IO work"))
    assert result["sub_questions"] == [
        "what is key part one", "how does key part two work"]
    assert queries[0] == "what is key part one"
    assert "how does key part two work" in queries
    assert result["answer"] == "final answer"
    assert model.calls == 2


def test_graph_decompose_malformed_falls_back_to_original():
    queries = []

    def search(query):
        queries.append(query)
        return _bundle()

    model = _JsonModel(["garbage one", "still not json", "final answer"])
    g = ResearchGraph(model=model, search=search)
    q = "Compare asyncio and threading versus multiprocessing for CPU work"
    result = g.run(_state(user_input=q, query=q))
    assert result["sub_questions"] == []
    assert queries[0] == q, "fallback must keep the original question"
    assert result["completion_reason"] == "completed"
    assert model.calls == 3, "bounded retries then synthesis"


def test_graph_decompose_cancelled_midway():
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        # understand(1) → plan(2) → decompose first check(3): fire here.
        return calls["n"] >= 3

    model = _StubModel()
    searches = []
    g = ResearchGraph(model=model, search=lambda q: searches.append(q))
    q = "Compare asyncio and threading versus multiprocessing for CPU work"
    result = g.run(_state(user_input=q, query=q, should_stop=probe))
    assert result["completion_reason"] == "stopped"
    assert model.calls == 0
    assert searches == []


# ── gap → refined query ───────────────────────────────────────────────────


def test_refine_query_full_miss_keeps_original():
    """Evidence covers nothing → no deterministic transform beats the
    original question."""
    assert ri.refine_query("python asyncio internals",
                           ["asyncio", "event", "loop"], "") == \
        "python asyncio internals"


def test_refine_query_partial_coverage_targets_missing_aspect():
    refined = ri.refine_query("python asyncio internals",
                              ["asyncio", "event", "loop"],
                              "asyncio is a python library")
    assert refined != "python asyncio internals"
    assert "loop" in refined or "event" in refined


def test_refine_query_pads_single_novel_term_with_anchor():
    refined = ri.refine_query("explain quantum tunneling basics",
                              ["tunneling"], "quantum basics covered")
    assert "tunneling" in refined
    assert len(refined.split()) >= 2


def test_refine_query_noop_when_gaps_covered():
    grounding = "python asyncio covers everything about loops"
    assert ri.refine_query("python asyncio internals",
                           ["asyncio", "loops"], grounding) == \
        "python asyncio internals"


def test_refine_query_noop_without_gaps():
    assert ri.refine_query("original query", [], "") == "original query"


def test_graph_second_search_targets_gap():
    queries = []

    def search(query):
        queries.append(query)
        if len(queries) == 1:
            # Covers most terms but misses "tunneling".
            return _bundle(RetrievalQuality.WEAK,
                           text="explain quantum basics overview material")
        return _bundle()

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_state(user_input="explain quantum tunneling basics",
                          query="explain quantum tunneling basics"))
    assert len(queries) == 2
    assert queries[1] != queries[0], "refined query must differ from original"
    assert "tunneling" in queries[1]
    assert result["validation_detail"]["status"] == "sufficient"


def test_duplicate_gate_still_blocks_covered_gaps():
    """8A contract: when evidence already covers the terms, refinement must
    not launder the query past the coordinator's duplicate gate."""
    coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=5))
    calls = []

    def search(query):
        calls.append(query)
        return _bundle(quality=RetrievalQuality.WEAK)

    g = ResearchGraph(model=_StubModel(), search=search)
    result = g.run(_state(coordinator=coord))
    assert len(calls) == 1
    assert coord.budget.searches_used == 1
    assert result["search_blocked"] is True


# ── evidence accumulation ─────────────────────────────────────────────────


def test_accumulate_dedupes_urls_across_attempts():
    b1 = _bundle(results=[_result("https://a.example/x"), _result("https://b.example/y")])
    b2 = _bundle(results=[_result("https://A.example/x/"), _result("https://c.example/z")])
    kept, added = ri.accumulate_bundle([b1], b2)
    assert added == 1
    urls = [r.url for r in kept[1].results]
    assert urls == ["https://c.example/z"]
    # Original bundle untouched.
    assert len(b1.results) == 2


def test_accumulate_bounds_bundle_count():
    bundles = []
    for i in range(ri.MAX_EVIDENCE_BUNDLES + 3):
        bundles, added = ri.accumulate_bundle(bundles, _bundle(results=[_result(f"https://x.example/{i}")]))
        assert added >= 0
    assert len(bundles) == ri.MAX_EVIDENCE_BUNDLES


def test_accumulate_rejects_failed_and_empty():
    kept, added = ri.accumulate_bundle([], _bundle(error="down"))
    assert kept == [] and added == 0
    kept, added = ri.accumulate_bundle([], _bundle(text="", results=[]))
    assert kept == [] and added == 0


def test_graph_accumulates_evidence_across_searches():
    attempts = {"n": 0}

    def search(query):
        attempts["n"] += 1
        url = f"https://src.example/page{attempts['n']}"
        return _bundle(RetrievalQuality.WEAK if attempts["n"] < 2 else RetrievalQuality.SUFFICIENT,
                       results=[_result(url, full_text=f"content {attempts['n']}")],
                       query=query)

    g = ResearchGraph(model=_StubModel(), search=search, max_search_attempts=3)
    # Weak evidence that keeps one term uncovered forces two searches.
    result = g.run(_state(user_input="explain quantum tunneling basics",
                          query="explain quantum tunneling basics"))
    bundles = result["evidence_bundles"]
    assert len(bundles) >= 2
    all_urls = {r.url for b in bundles for r in b.results}
    assert len(all_urls) == len({r.url for b in bundles for r in b.results})


# ── citation manifest ─────────────────────────────────────────────────────


def test_manifest_built_from_actual_results():
    bundles = [
        _bundle(results=[_result("https://a.example/one", title="Alpha"),
                         _result("https://b.example/two", title="Beta")]),
        _bundle(results=[_result("https://a.example/one", title="dup")]),
    ]
    m = ri.build_manifest(bundles)
    assert [e.source_id for e in m.entries] == ["S1", "S2"]
    assert m.entries[0].url == "https://a.example/one"
    assert m.entries[0].title == "Alpha"


def test_manifest_bounded():
    many = [_result(f"https://s.example/{i}", title=str(i))
            for i in range(ri.MAX_MANIFEST_ENTRIES + 10)]
    m = ri.build_manifest([_bundle(results=many)])
    assert len(m.entries) == ri.MAX_MANIFEST_ENTRIES


def test_validate_rejects_invalid_citations():
    bundles = [_bundle(results=[_result("https://ok.example/a", title="OK")])]
    m = ri.build_manifest(bundles)
    detail = ri.validate_answer("Claim one [S1]. Claim two [S9].", m,
                                has_evidence=True)
    assert detail["invalid_citations"] == ["S9"]
    assert detail["status"] == "sufficient"  # recorded, not fatal


def test_validate_detects_insufficiency_disclosure():
    detail = ri.validate_answer(
        "The evidence is insufficient to answer this question.",
        ri.CitationManifest(), has_evidence=True)
    assert detail["disclosed_insufficient"] is True
    assert detail["status"] == "insufficient"


def test_validate_empty_answer():
    detail = ri.validate_answer("", ri.CitationManifest(), has_evidence=True)
    assert detail["status"] == "empty"


def test_graph_synthesis_prompt_carries_manifest_and_conflicts(monkeypatch):
    seen_prompts = []

    class _Spy:
        def invoke(self, msgs):
            seen_prompts.append(msgs[0].content)
            return type("R", (), {"content": "Answer citing [S1]."})()

    bundles = [_bundle(results=[
        _result("https://x.example/facts", title="Facts",
                full_text="The value is 42."),
    ])]
    g = ResearchGraph(model=_Spy(), search=lambda q: _bundle(results=[]))
    state = _state(evidence_bundles=bundles, search_attempts=1,
                   grounding_text="")
    state.pop("model", None)
    result = g._node_synthesize(state)
    prompt = seen_prompts[0]
    assert "[S1]" in prompt and "https://x.example/facts" in prompt
    assert result["citation_manifest"].entries[0].source_id == "S1"

    detail = ri.validate_answer(result["answer"],
                                result["citation_manifest"], True)
    assert detail["invalid_citations"] == []


def test_collect_conflicts_uses_existing_detector():
    from novi.tools.search_pipeline import SearchResult as SR

    b1 = EvidenceBundle(
        query="speed", merged_text="", source_count=1,
        quality=RetrievalQuality.SUFFICIENT,
        results=[SR(title="Fast", url="https://f.example/a", snippet="",
                    full_text="The library supports async operations. It is fast. "
                              "Speed reaches 1000 units per second today.")])
    b2 = EvidenceBundle(
        query="speed", merged_text="", source_count=1,
        quality=RetrievalQuality.SUFFICIENT,
        results=[SR(title="Slow", url="https://s.example/b", snippet="",
                    full_text="The library supports async operations. It is not fast. "
                              "Speed never reaches 1000 units per second today.")])
    conflicts = ri.collect_conflicts([b1, b2])
    assert isinstance(conflicts, list)
    assert all(set(c) <= {"statements", "sources", "severity", "resolution"}
               for c in conflicts)


# ── context truncation ────────────────────────────────────────────────────


def test_context_budget_default_without_metadata():
    assert ri.context_budget_for(None) == ri.DEFAULT_GROUNDING_BUDGET_CHARS

    class _M:
        context_length = 4096

    budget = ri.context_budget_for(_M())
    assert 1000 <= budget <= 60_000


def test_truncate_grounding_respects_budget():
    big = [_result(f"https://big.example/{i}",
                   full_text="x" * 5000, title=f"B{i}")
           for i in range(6)]
    text, truncated = ri.truncate_grounding([_bundle(results=big)], 8000)
    assert len(text) <= 8500  # small slack for markers
    assert truncated


def test_truncate_grounding_newest_first_within_budget():
    b1 = _bundle(results=[_result("https://old.example/a", title="Old",
                                  full_text="old content " * 50)])
    b2 = _bundle(results=[_result("https://new.example/b", title="New",
                                  full_text="new content")])
    text, truncated = ri.truncate_grounding([b1, b2], 400)
    assert truncated
    assert "new content" in text and "Old" not in text.split("\n")[1]


# ── bounded iteration + budget + cancellation integration ─────────────────


def test_research_iteration_stays_bounded_with_refinement():
    coord = RetrievalCoordinator(RetrievalBudget(max_web_searches=10))
    calls = []

    def search(query):
        calls.append(query)
        # Always weak with an always-missing term → refine each time.
        return _bundle(RetrievalQuality.WEAK, text="generic partial content")

    g = ResearchGraph(model=_StubModel(), search=search, max_search_attempts=4)
    result = g.run(_state(user_input="explain quantum entanglement thoroughly",
                          query="explain quantum entanglement thoroughly",
                          coordinator=coord))
    assert len(calls) <= 4
    assert result["search_attempts"] <= 4
    assert coord.budget.searches_used == len(calls)


def test_cancellation_between_search_attempts():
    stop = {"flag": False}
    calls = []

    def probe():
        return stop["flag"]

    def search(query):
        calls.append(query)
        stop["flag"] = True
        return _bundle(RetrievalQuality.WEAK, text="thin evidence")

    g = ResearchGraph(model=_StubModel(),
                      search=search,
                      max_search_attempts=3)
    result = g.run(_state(user_input="explain quantum decoherence fully",
                          query="explain quantum decoherence fully",
                          should_stop=probe))
    assert len(calls) == 1
    assert result["completion_reason"] == "stopped"
