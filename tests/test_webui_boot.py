"""WebUI boot smoke test.

Regression for the ``set_brain`` integration bug: ``cozmo.services.context``
imports ``set_brain`` from ``cozmo.brain`` (which must export it), then
``WebUIBackend`` drives the full composition root through ``warmup()`` and
registers the active Brain that tools/WebUI later read back via ``get_brain()``.

Hermetic: the default embedding backend (Ollama) and model discovery are
stubbed so no network and no user home directory is touched.
"""

import hashlib

import pytest

from cozmo.brain import get_brain
from cozmo.services.context import CozmoContext


@pytest.fixture
def cfg(tmp_path):
    return {
        "llm": {
            "max_tokens": 65536,
            "default_model": "qwen3:8b",
            "roles": {
                "classifier": {"model": ""},
                "router": {"model": ""},
                "orchestrator": {"model": ""},
                "chat": {"model": ""},
                "coder": {"model": ""},
                "planner": {"model": ""},
                "vision": {"model": ""},
            },
        },
        "embedding": {
            "backend": "ollama",
            "model": "nomic-embed-text",
            "dimension": 768,
        },
        "reranker": {"backend": "none", "model": ""},
        "ollama": {"url": "http://localhost:11434"},
        "providers": {
            "default": "ollama",
            "ollama": {"url": "http://localhost:11434"},
        },
        "memory": {"max_turns_before_summary": 5, "max_short_term_pairs": 10},
        "workspace": {
            "path": str(tmp_path / "workspace"),
            "knowledge": str(tmp_path / "knowledge"),
            "git_repo": "",
        },
        "mcp": {"servers": {}},
    }


@pytest.fixture(autouse=True)
def _isolate_user_home(tmp_path, monkeypatch):
    """Redirect Path.home() to tmp so nothing touches the real user home."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    # Point the config module at a nonexistent file so load() returns
    # defaults (ollama embedding backend) instead of the real user config.
    import cozmo.config as cozmo_config

    monkeypatch.setattr(cozmo_config, "CONFIG_DIR", tmp_path / ".cozmo")
    monkeypatch.setattr(cozmo_config, "CONFIG_PATH", tmp_path / ".cozmo" / "config.toml")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Stub Ollama embedding calls + model discovery for hermetic boots."""
    from cozmo.models.service import ModelService
    from cozmo.services.embedding_providers import OllamaEmbeddingProvider

    def fake_embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = list(digest[:768])
        vec = [b / 255.0 for b in raw]
        vec = vec * (768 // len(vec)) + vec[: 768 % len(vec)]
        return vec

    monkeypatch.setattr(OllamaEmbeddingProvider, "_embed", fake_embed)
    monkeypatch.setattr(ModelService, "refresh", lambda self: None)


def test_webui_boot_creates_brain(cfg):
    from cozmo.webui import WebUIBackend

    backend = WebUIBackend(cfg)
    built = backend.build_backend()

    brain = built["brain"]
    assert brain is not None
    # The composition root registers the process-global Brain via set_brain.
    assert get_brain() is brain

    # A recall that never hits the network returns cleanly through the Brain.
    result = brain.recall("what does the user prefer")
    assert result.query == "what does the user prefer"
    assert isinstance(result.items, tuple)


def test_context_warmup_registers_brain(cfg):
    ctx = CozmoContext(cfg)
    ctx.warmup()
    assert get_brain() is not None
    assert get_brain() is ctx.brain
