"""Startup provisioning of the canonical default embedding model."""

import pytest

from novi.configuration import install as install_mod
from novi.configuration.install import (
    DEFAULT_EMBEDDING_MODEL,
    _name_matches,
    ensure_embedding_model,
)
from novi.services.embedding_providers import OllamaEmbeddingProvider


class FakeConfig:
    def __init__(self, values):
        self._values = dict(values)
        self.written = {}

    def get(self, setting_id, default=None):
        return self._values.get(setting_id, default)

    def set(self, setting_id, value, by=""):
        self._values[setting_id] = value
        self.written[setting_id] = value


class FakePuller:
    def __init__(self):
        self.pulled = []

    def __call__(self, name, on_progress=None):
        self.pulled.append(name)
        if on_progress:
            on_progress({"name": name, "status": "progress", "pct": 50.0})
            on_progress({"name": name, "status": "done"})


@pytest.fixture
def echo():
    return []


# ── _name_matches semantics ──────────────────────────────────────────────

def test_bare_name_matches_tagged_listing():
    assert _name_matches({"nomic-embed-text:latest"}, "nomic-embed-text")


def test_pinned_tag_does_not_match_different_tag():
    assert not _name_matches({"nomic-embed-text:latest"}, "nomic-embed-text:v1.5")


def test_pinned_tag_exact_match():
    assert _name_matches({"nomic-embed-text:v1.5"}, "nomic-embed-text:v1.5")


# ── ensure_embedding_model ───────────────────────────────────────────────

def test_default_missing_triggers_pull_then_persists(monkeypatch, echo):
    monkeypatch.setattr(install_mod, "_installed_model_names", lambda url: set())
    cfg = FakeConfig({"embedding.backend": "ollama", "embedding.model": ""})
    pull = FakePuller()

    result = ensure_embedding_model(configuration=cfg, pull=pull,
                                    echo=echo.append)

    assert result == DEFAULT_EMBEDDING_MODEL
    assert pull.pulled == [DEFAULT_EMBEDDING_MODEL]
    assert cfg.written["embedding.model"] == DEFAULT_EMBEDDING_MODEL


def test_default_present_skips_pull_but_persists_selection(monkeypatch):
    monkeypatch.setattr(
        install_mod, "_installed_model_names",
        lambda url: {DEFAULT_EMBEDDING_MODEL})
    cfg = FakeConfig({"embedding.backend": "ollama", "embedding.model": ""})
    pull = FakePuller()

    result = ensure_embedding_model(configuration=cfg, pull=pull,
                                    echo=lambda s: None)

    assert result == DEFAULT_EMBEDDING_MODEL
    assert pull.pulled == []
    assert cfg.written["embedding.model"] == DEFAULT_EMBEDDING_MODEL


def test_legacy_bare_name_upgrades_to_pinned_default(monkeypatch):
    """Existing installs with untagged 'nomic-embed-text' adopt the pinned
    default; the daemon only lists :latest so a pull must occur."""
    monkeypatch.setattr(
        install_mod, "_installed_model_names",
        lambda url: {"nomic-embed-text:latest"})
    cfg = FakeConfig({"embedding.backend": "ollama",
                      "embedding.model": "nomic-embed-text"})
    pull = FakePuller()

    result = ensure_embedding_model(configuration=cfg, pull=pull,
                                    echo=lambda s: None)

    assert result == "nomic-embed-text:v1.5"
    assert pull.pulled == ["nomic-embed-text:v1.5"]
    assert cfg.written["embedding.model"] == "nomic-embed-text:v1.5"


def test_explicit_custom_model_respected_no_persist(monkeypatch):
    monkeypatch.setattr(
        install_mod, "_installed_model_names",
        lambda url: {"my-model:x"})
    cfg = FakeConfig({"embedding.backend": "ollama",
                      "embedding.model": "my-model:x"})
    pull = FakePuller()

    result = ensure_embedding_model(configuration=cfg, pull=pull,
                                    echo=lambda s: None)

    assert result == "my-model:x"
    assert pull.pulled == []
    assert cfg.written == {}


def test_unreachable_ollama_warns_and_returns_none(monkeypatch, echo):
    def boom(url):
        raise ConnectionError("refused")

    monkeypatch.setattr(install_mod, "_installed_model_names", boom)
    cfg = FakeConfig({"embedding.backend": "ollama", "embedding.model": ""})

    result = ensure_embedding_model(configuration=cfg, pull=FakePuller(),
                                    echo=echo.append)

    assert result is None
    assert any("Ollama" in line for line in echo)


def test_non_ollama_backend_is_noop(monkeypatch):
    cfg = FakeConfig({"embedding.backend": "sentence_transformers"})

    def fail(url):
        raise AssertionError("must not contact ollama")

    monkeypatch.setattr(install_mod, "_installed_model_names", fail)

    assert ensure_embedding_model(configuration=cfg,
                                  pull=FakePuller()) is None


# ── provider runtime fallback ────────────────────────────────────────────

def test_ollama_provider_falls_back_to_default_model():
    provider = OllamaEmbeddingProvider({})
    assert provider.model_name == DEFAULT_EMBEDDING_MODEL


def test_ollama_provider_keeps_configured_model():
    provider = OllamaEmbeddingProvider(
        {"embedding": {"model": "custom-emb:2"}})
    assert provider.model_name == "custom-emb:2"
