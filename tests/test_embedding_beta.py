"""Task 4 — canonical embedding default (nomic-embed-text:v1.5).

RED: builtin/bootstrap default "" and no pinned seed fact.
GREEN: embedding.model defaults resolve to DEFAULT_EMBEDDING_MODEL,
seed fact marks the embeddings-only role.
"""

from novi.configuration.install import DEFAULT_EMBEDDING_MODEL


def test_default_is_pinned():
    assert DEFAULT_EMBEDDING_MODEL == "nomic-embed-text:v1.5"


def test_unset_resolves_to_default():
    from novi.configuration.bootstrap import DEFAULT_CONFIG, build_registry
    from novi.configuration.schema import Category, Visibility

    reg = build_registry()
    setting = reg.get("embedding.model")
    assert setting.default == DEFAULT_EMBEDDING_MODEL
    assert setting.category == Category.MODELS
    assert setting.visibility == Visibility.USER
    assert DEFAULT_CONFIG["embedding"]["model"] == DEFAULT_EMBEDDING_MODEL


def test_seed_marks_embedding_role():
    from novi.configuration.evidence import assemble_capability_evidence, capability_flags
    from novi.configuration.model_seeds import SEED_MODEL_FACTS

    fact = SEED_MODEL_FACTS["nomic-embed-text:v1.5"]
    assert fact.capabilities == ["embeddings"]
    assert "chat" not in fact.capabilities
    assert fact.works_with_memory is True
    flags = capability_flags(assemble_capability_evidence(fact=fact))
    assert flags.get("embeddings") is True
