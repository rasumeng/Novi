"""Memory database rebuild tests.

Rebuilding drops embedding-incompatible LanceDB vector stores and re-indexes
the knowledge base. Non-vector persistence (simulated with plain files) must
survive the rebuild.
"""

import hashlib

import pytest

from cozmo.memory.rebuild import _find_lancedb_dirs, rebuild


@pytest.fixture
def profile(tmp_path):
    base = tmp_path
    (base / "memory" / "lancedb").mkdir(parents=True)
    (base / "memory" / "lancedb" / "cozmo_memories.lance").write_text("old")
    (base / "knowledge_index" / "lancedb").mkdir(parents=True)
    (base / "brain").mkdir(parents=True)
    (base / "brain" / "relationships.sqlite").write_text("keep-me")
    (base / "knowledge").mkdir(parents=True)
    (base / "knowledge" / "facts.md").write_text(
        "notes: User prefers python over java.\n"
    )
    return base


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Force the ollama backend + deterministic embeddings; no network."""
    from cozmo.memory import rebuild as rebuild_mod
    from cozmo.services.embedding_providers import OllamaEmbeddingProvider

    def cfg():
        return {
            "embedding": {
                "backend": "ollama",
                "model": "nomic-embed-text",
                "dimension": 768,
            },
            "ollama": {"url": "http://localhost:11434"},
        }

    monkeypatch.setattr(rebuild_mod.cozmo_config, "load", cfg)

    def fake_embed(self, text):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [b / 255.0 for b in digest[:768]]
        return vec * (768 // len(vec)) + vec[: 768 % len(vec)]

    monkeypatch.setattr(OllamaEmbeddingProvider, "_embed", fake_embed)


def test_find_lancedb_dirs_lists_all(profile):
    dirs = _find_lancedb_dirs(profile)
    paths = {str(p) for p in dirs}
    assert str(profile / "memory" / "lancedb") in paths
    assert str(profile / "knowledge_index" / "lancedb") in paths


def test_rebuild_drops_vector_stores_preserves_others(profile):
    report = rebuild(profile)

    removed = set(report["removed"])
    assert str(profile / "memory" / "lancedb") in removed
    assert str(profile / "knowledge_index" / "lancedb") in removed

    # Vector dirs are gone; non-vector file survives.
    assert not (profile / "memory" / "lancedb").exists()
    assert (profile / "brain" / "relationships.sqlite").read_text() == "keep-me"

    # Knowledge was re-indexed into a fresh 768-dim store.
    assert report["reindexed"] >= 1
    assert (profile / "knowledge_index" / "lancedb").exists()
