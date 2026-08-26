"""EvidenceProcessor live-path integration — parity + degradation suite.

Proves the processed evidence representation preserves everything the raw
``merged_text`` grounding provided (source identity, URLs, titles, relevant
content, ordering) before the live path may prefer it, and pins the
degradation contract: a processor failure can never fabricate or erase
evidence — raw text survives untouched.

Fixtures cover the spec matrix: clean multi-source, duplicate sources,
conflicting sources, high/low confidence, temporal claims, irrelevant
evidence, empty search, malformed sources, repeated queries, mixed quality,
determinism, and failure injection.
"""

from __future__ import annotations

import re
import time
from types import SimpleNamespace

import pytest

from novi.evidence import EvidenceProcessor, render_evidence_context
from novi.runtime.evidence import EvidenceBundle, EvidenceCollector
from novi.runtime.retrieval import RetrievalExecutor
from novi.tools.search_pipeline import SearchResult

# ── fixture helpers ──────────────────────────────────────────────────────────


def _result(url, title, text, snippet=None):
    return SearchResult(
        title=title,
        url=url,
        snippet=snippet if snippet is not None else text[:200],
        source="searxng",
        freshness="2 days ago",
        score=0.8,
        full_text=text,
    )


def _bundle(query, results, error=None):
    bundle = EvidenceCollector._merge(query, results) if results else (
        EvidenceBundle(query=query)
    )
    bundle.error = error
    return bundle


def _processed(bundle):
    return EvidenceProcessor().process(bundle)


def _rendered(bundle):
    return render_evidence_context(_processed(bundle))


# ── 1. clean multi-source parity ─────────────────────────────────────────────


CLEAN = [
    _result(
        "https://docs.python.org/3/tutorial/errors.html",
        "Errors and Exceptions",
        "Python raises exceptions when errors occur at runtime. "
        "The try statement allows handling exceptions gracefully. "
        "Unhandled exceptions terminate the program with a traceback.",
    ),
    _result(
        "https://en.wikipedia.org/wiki/Exception_handling",
        "Exception handling",
        "Exception handling is a programming language construct. "
        "It provides mechanisms for responding to runtime anomalies. "
        "Many languages use try-catch blocks for this purpose.",
    ),
]


def test_clean_sources_preserve_identity_and_urls():
    bundle = _bundle("python exception handling", CLEAN)
    rendered = _rendered(bundle)
    for r in CLEAN:
        assert r.url in rendered, "URL must survive processing"
        assert r.title in rendered, "title must survive processing"
    # current pipeline preserved them too — parity baseline
    for r in CLEAN:
        assert r.url in bundle.merged_text


def test_clean_relevant_content_survives():
    bundle = _bundle("python exception handling", CLEAN)
    rendered = _rendered(bundle).lower()
    for term in ("exception", "python", "handling"):
        assert term in rendered


def test_confidence_header_and_attribution_present():
    ctx = _processed(_bundle("python exception handling", CLEAN))
    rendered = render_evidence_context(ctx)
    match = re.search(r"confidence: ([\d.]+)", rendered)
    assert match, "confidence header required"
    assert 0.0 <= float(match.group(1)) <= 1.0
    assert "[S" in rendered, "facts must carry source attribution"


# ── 2–4. duplicates / conflicts / confidence tiers ───────────────────────────


def test_duplicate_sources_collapse_in_rendering():
    body = "Identical body text about python exceptions and handling."
    dup = _result("https://example.com/dup", "Same Title", body)
    bundle = _bundle(
        "python exceptions",
        [dup, _result("https://example.com/dup", "Same Title", body)],
    )
    rendered = _rendered(bundle)
    assert rendered, "fixture must produce trusted facts"
    assert rendered.count("https://example.com/dup") == 1, "duplicate URL once"


def test_duplicate_facts_merge_across_sources():
    a = _result("https://a.example.com", "A", "The library ships version two point zero today.")
    b = _result("https://b.example.com", "B", "The library ships version two point zero today!")
    ctx = _processed(_bundle("library version", [a, b]))
    statements = [f.statement.lower() for f in ctx.facts]
    assert len(statements) == len(set(statements)), "near-identical facts merge"
    merged = [f for f in ctx.facts if len(f.sources) == 2]
    assert merged, "merged fact keeps both source attributions"


def test_conflicting_polarity_surfaced_with_resolution():
    yes = _result("https://yes.example.com", "Yes", "Python supports multiple inheritance natively today.")
    no = _result("https://no.example.com", "No", "Python does not support multiple inheritance natively today.")
    ctx = _processed(_bundle("multiple inheritance", [yes, no]))
    rendered = render_evidence_context(ctx)
    assert "Conflicting claims:" in rendered, "conflicts must be explicit"


def test_low_confidence_degrades_to_raw_text():
    irrelevant = _result(
        "https://cooking.example.com", "Soup Recipe",
        "Chop onions finely. Simmer stock for hours. Season with salt.",
    )
    ctx = _processed(_bundle("quantum chromodynamics", [irrelevant]))
    assert ctx.fallback is True, "irrelevant content must not fabricate facts"


def test_temporal_claim_kept_with_freshness_metadata():
    fresh = _result(
        "https://news.example.com/release", "Release Notes",
        "Version three was released yesterday with major performance gains.",
    )
    fresh.freshness = "1 day ago"
    ctx = _processed(_bundle("release performance", [fresh]))
    assert any(s.freshness is not None for s in ctx.sources), \
        "temporal metadata flows into structured sources"


# ── 5–7. empty / malformed / repeated ────────────────────────────────────────


def test_empty_bundle_produces_fallback_not_fabrication():
    ctx = _processed(EvidenceBundle(query="anything"))
    assert ctx.fallback is True
    assert ctx.facts == ()
    assert render_evidence_context(ctx) == ""


def test_malformed_source_degrades_safely():
    broken = SearchResult(
        title="", url="", snippet="", source="searxng",
        freshness="", score=0.0, full_text="   ",
    )
    good = CLEAN[0]
    ctx = _processed(_bundle("python exception handling", [broken, good]))
    rendered = render_evidence_context(ctx)
    assert good.url in rendered, "healthy source unaffected by malformed peer"


def test_repeated_processing_is_deterministic():
    bundle = _bundle("python exception handling", CLEAN)
    first = _rendered(bundle)
    second = _rendered(bundle)
    assert first == second
    c1, c2 = _processed(bundle), _processed(bundle)
    assert c1 == c2, "EvidenceContext equality is structural determinism"


def test_mixed_quality_ranks_trusted_first():
    wiki = _result("https://en.wikipedia.org/wiki/Python", "Python", "Python python language entry.")
    blog = _result("https://random-blog.example.net/post", "Post", "Random musings about python.")
    ctx = _processed(_bundle("python", [blog, wiki]))
    urls = [s.url for s in ctx.sources]
    assert urls.index("https://en.wikipedia.org/wiki/Python") < urls.index(
        "https://random-blog.example.net/post"
    ), "authority ranking orders sources deterministically"


# ── 8. size + performance measurement (recorded, generous bounds) ────────────


BIG_TEXT = ("Python exception handling matters. " * 40)


def test_processed_context_bounded_smaller_than_raw():
    results = [
        _result(f"https://src{i}.example.com", f"Src {i}", BIG_TEXT)
        for i in range(5)
    ]
    bundle = _bundle("python exception handling", results)
    raw_len = len(bundle.merged_text)

    t0 = time.perf_counter()
    rendered = _rendered(bundle)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert len(rendered) < raw_len, "compression target"
    assert elapsed_ms < 2000, "processing stays interactive"
    print(
        f"\n[evidence perf] raw={raw_len} chars processed={len(rendered)} chars "
        f"ratio={len(rendered)/raw_len:.2f} latency={elapsed_ms:.2f}ms"
    )


# ── 9. live executor wiring: gated upgrade + safe degradation ────────────────


def _ctx_stub():
    return SimpleNamespace(grounding_text="", evidence_context=None,
                           grounding_error=None)


def test_executor_upgrades_grounding_when_trusted():
    exe = RetrievalExecutor()
    ctx = _ctx_stub()
    bundle = _bundle("python exception handling", CLEAN)
    exe._apply_web_evidence(ctx, bundle)
    assert "**Evidence Summary**" in ctx.grounding_text
    assert ctx.evidence_context is not None
    assert ctx.evidence_context.fallback is False
    for r in CLEAN:
        assert r.url in ctx.grounding_text, "live upgrade preserves URLs"


def test_executor_keeps_raw_when_extraction_falls_back():
    exe = RetrievalExecutor()
    ctx = _ctx_stub()
    soup = _result("https://soup.example.com", "Soup", "Chop onions. Simmer stock. Add salt.")
    bundle = _bundle("quantum chromodynamics theory", [soup])
    exe._apply_web_evidence(ctx, bundle)
    assert ctx.grounding_text == bundle.merged_text, "fallback → byte-identical raw"
    assert ctx.evidence_context is not None, "context still recorded observationally"


def test_executor_keeps_raw_on_processor_exception(monkeypatch):
    exe = RetrievalExecutor()
    ctx = _ctx_stub()
    bundle = _bundle("python exception handling", CLEAN)

    def boom(self):
        raise RuntimeError("processor exploded")

    monkeypatch.setattr(EvidenceProcessor, "process", boom)
    exe._apply_web_evidence(ctx, bundle)
    assert ctx.grounding_text == bundle.merged_text, "failure never erases evidence"
    assert ctx.evidence_context is None, "no partial context leaks out"


def test_executor_error_bundle_untouched():
    exe = RetrievalExecutor()
    ctx = _ctx_stub()
    bundle = _bundle("q", [], error="search api down")
    exe._apply_web_evidence(ctx, bundle)
    assert ctx.grounding_text == ""
    assert ctx.evidence_context is None


def test_executor_empty_results_no_processing():
    exe = RetrievalExecutor()
    ctx = _ctx_stub()
    exe._apply_web_evidence(ctx, EvidenceBundle(query="q"))
    assert ctx.grounding_text == ""
    assert ctx.evidence_context is None


# ── 10. architecture guards ──────────────────────────────────────────────────


def test_evidence_package_stays_pure():
    """No LangGraph, no storage, no model selection inside novi/evidence."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    forbidden = ("langgraph", "lancedb", "sqlite3", "LanceStore",
                 "VectorStore", "RelationshipStore", "ModelSelector",
                 "ModelService")
    write_shape = re.compile(
        r"\b(store|table|conn|db)\w*\.(add|add_many|update|delete|insert)\b"
    )
    evidence_dir = root / "novi" / "evidence"
    for pyfile in evidence_dir.rglob("*.py"):
        text = pyfile.read_text(encoding="utf-8", errors="replace")
        for token in forbidden:
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if token in line and "import" in line:
                    raise AssertionError(
                        f"{pyfile.name}:{i}: forbidden dependency '{token}'"
                    )
        for i, line in enumerate(text.splitlines(), 1):
            if write_shape.search(line) and not line.strip().startswith("#"):
                raise AssertionError(f"{pyfile.name}:{i}: storage-shaped write")


def test_evidence_never_touches_brain_knowledge_paths():
    """Web evidence must never be persisted as durable knowledge."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    banned = ("Brain(", "brain.learn", "knowledge_layer", "_sync_markdown",
              "markdown_store", "relationship_store")
    for pyfile in (root / "novi" / "evidence").rglob("*.py"):
        text = pyfile.read_text(encoding="utf-8", errors="replace")
        for token in banned:
            assert token not in text.replace('"""', ""), (
                f"{pyfile.name}: evidence package references Brain storage "
                f"'{token}' — boundary violation"
            )
