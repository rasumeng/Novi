"""Phase C — extraction (pure reasoning) tests.

The reasoning tier must be storage-free: pure objects in, pure objects out.
These tests pin extraction, classification, and summarization with and without
injected LLM hooks.
"""

from novi.brain.reasoning.extraction import (
    ExtractedClaim,
    KnowledgeExtractor,
    LayerClassifier,
    Summarizer,
    _normalize,
)
from novi.brain.types import Turn


def make_turns(*pairs):
    return tuple(Turn(user=u, assistant=a) for u, a in pairs)


def test_extracts_content_claims_heuristically():
    extractor = KnowledgeExtractor()
    turns = make_turns(
        (
            "I prefer Python over Java for backend work because it is simpler.",
            "That is a good choice, Python has great tooling for this project.",
        ),
    )
    result = extractor.extract(turns)
    assert result.claims
    assert all(0.4 <= c.confidence <= 1.0 for c in result.claims)
    assert all(c.statement for c in result.claims)


def test_empty_turns_fallback():
    result = KnowledgeExtractor().extract(())
    assert result.fallback
    assert result.claims == ()


def test_classifier_hook_is_used():
    calls = []

    def hook(sentences):
        calls.append(sentences)
        return [(0.95, ("preference",)) for _ in sentences]

    extractor = KnowledgeExtractor(classifier=hook)
    result = extractor.extract(
        make_turns(("I prefer Python over Java.", "I like its type hints."))
    )
    assert calls
    assert result.claims
    assert all(c.confidence == 0.95 for c in result.claims)
    assert all("preference" in c.tags for c in result.claims)


def test_classifier_hook_failure_falls_back():
    def hook(sentences):
        raise RuntimeError("llm down")

    extractor = KnowledgeExtractor(classifier=hook)
    result = extractor.extract(
        make_turns(("I prefer Python over Java for backend work.", ""))
    )
    assert result.claims


def test_malformed_classifier_hook_falls_back():
    extractor = KnowledgeExtractor(classifier=lambda s: "not a list")
    result = extractor.extract(
        make_turns(("I prefer Python over Java for backend work.", ""))
    )
    assert result.claims


def test_min_confidence_filters_claims():
    def hook(sentences):
        return [(0.1, ()) for _ in sentences]

    extractor = KnowledgeExtractor(classifier=hook, min_confidence=0.4)
    result = extractor.extract(
        make_turns(("I prefer Python over Java for backend work.", ""))
    )
    assert result.fallback
    assert result.claims == ()


def test_dedup_claims():
    extractor = KnowledgeExtractor()
    text = "We use uv for builds. We use uv for builds because it is fast. It works well."
    result = extractor.extract(make_turns((text, "")))
    statements = [c.statement for c in result.claims]
    assert len(statements) == len(set(_normalize(s) for s in statements))


def test_summary_via_llm_hook():
    def llm(prompt):
        return "Prefers Python for backend. Enjoys type hints."

    extractor = KnowledgeExtractor(summarizer=Summarizer(llm=llm))
    result = extractor.extract(
        make_turns(("I prefer Python for backend work.", "I enjoy type hints."))
    )
    assert "Prefers Python" in result.summary


def test_heuristic_summary_without_llm():
    extractor = KnowledgeExtractor()
    result = extractor.extract(
        make_turns(("I prefer Python over Java for backend work.", ""))
    )
    assert result.summary


def test_name_from_first_sentence():
    extractor = KnowledgeExtractor()
    result = extractor.extract(
        make_turns(("We migrated the build to uv yesterday.", ""))
    )
    assert "uv" in result.name


def test_layer_classifier_tags_and_layer():
    clf = LayerClassifier()
    tags, layer = clf.classify("The user prefers Rust over C++ for systems programming.")
    assert "preference" in tags
    assert layer == "identity"
    tags, layer = clf.classify("We refactored the database schema for the payments project.")
    assert "project" in tags
    assert layer == "scenario"


def test_layer_classifier_hook_used():
    clf = LayerClassifier(hook=lambda text: ("goal", "motivation"))
    tags, layer = clf.classify("I want to finish the migration by Friday.")
    assert tags == ("goal", "motivation")


def test_turns_include_tool_outputs():
    extractor = KnowledgeExtractor()
    turn = Turn(
        user="Check the build status",
        assistant="Build failed on the test step.",
        tool_outputs=("npm test exited with code 1",),
    )
    result = extractor.extract((turn,))
    assert result.claims
    assert any("test" in c.statement.lower() for c in result.claims)


def test_normalize_dedup_key():
    assert _normalize("  Python  is  Great! ") == "python is great"


def test_extracted_claim_frozen():
    claim = ExtractedClaim("statement", 0.5, ("preference",))
    assert claim.statement == "statement"
    assert claim.confidence == 0.5
    assert claim.tags == ("preference",)
