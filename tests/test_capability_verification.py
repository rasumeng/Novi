"""Tri-state capability verification (Task 1.1)."""

import pytest

from novi.configuration.model_records import CapabilityState
from novi.configuration.discovery import _CACHE, ModelDiscovery, cached_runtime_capabilities
from novi.runtime.model_selector import model_capability_state, model_capabilities


@pytest.fixture(autouse=True)
def _clear_cache():
    _CACHE.clear()
    yield
    _CACHE.clear()


# ── deterministic (no network) ────────────────────────────────────────────

def test_unknown_model_not_confirmed_unsupported():
    # no seed, no cached runtime caps -> unknown, never unsupported
    assert model_capability_state("qwen3-unknown:7b", "vision") in ("unknown", "verification_failed")


def test_seed_known_supported():
    # qwen2.5vl:7b seed has vision=True
    assert model_capability_state("qwen2.5vl:7b", "vision") == CapabilityState.SUPPORTED
    assert model_capabilities("qwen2.5vl:7b").supports_vision is True


def test_seed_known_unsupported():
    # qwen3:8b is trusted seed without vision
    assert model_capability_state("qwen3:8b", "vision") == CapabilityState.UNSUPPORTED
    assert model_capabilities("qwen3:8b").supports_vision is False
    # gemma4:e4b also no vision
    assert model_capability_state("gemma4:e4b", "vision") == CapabilityState.UNSUPPORTED


def test_runtime_token_supported_via_cache():
    # Simulate cached /api/show payload reporting vision + tools
    _CACHE.set("http://localhost:11434", "my-custom-vision:7b", {"capabilities": ["vision", "tools"]})
    assert model_capability_state("my-custom-vision:7b", "vision") == CapabilityState.SUPPORTED
    assert model_capability_state("my-custom-vision:7b", "tools") == CapabilityState.SUPPORTED


def test_runtime_token_unsupported_when_cached_but_absent():
    # Cached payload exists but does not list vision -> unsupported (authoritative)
    _CACHE.set("http://localhost:11434", "my-text-model:7b", {"capabilities": ["tools"]})
    assert model_capability_state("my-text-model:7b", "vision") == CapabilityState.UNSUPPORTED
    assert model_capability_state("my-text-model:7b", "tools") == CapabilityState.SUPPORTED


def test_unknown_cold_no_cache():
    assert model_capability_state("totally-unknown-xyz:99b", "vision") == CapabilityState.UNKNOWN
    assert model_capability_state("totally-unknown-xyz:99b", "audio") == CapabilityState.UNKNOWN
    assert model_capability_state("totally-unknown-xyz:99b", "tools") == CapabilityState.UNKNOWN


def test_cache_hit_returns_support():
    _CACHE.set("http://localhost:11434", "cached-model:7b", {"capabilities": ["vision"]})
    assert cached_runtime_capabilities("cached-model:7b") == ["vision"]
    assert model_capability_state("cached-model:7b", "vision") == CapabilityState.SUPPORTED


def test_never_unsupported_for_unknown():
    for cap in ("vision", "audio", "tools", "reasoning", "coding"):
        state = model_capability_state("brand-new-unknown:1b", cap)
        assert state != CapabilityState.UNSUPPORTED, f"unknown brand-new should never be unsupported for {cap}"
        assert state == CapabilityState.UNKNOWN


def test_audio_speech_alias():
    # speech token maps to audio
    _CACHE.set("http://localhost:11434", "speech-model:7b", {"capabilities": ["speech"]})
    assert model_capability_state("speech-model:7b", "audio") == CapabilityState.SUPPORTED


# ── live verify path ──────────────────────────────────────────────────────

def test_verify_live_fetch_success(monkeypatch):
    from novi.configuration import runtime_inventory as ri

    def fake_show(url, name, timeout=5.0):
        assert timeout == 3.0
        return {"capabilities": ["vision"], "details": {}, "model_info": {}}

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    disc = ModelDiscovery("http://localhost:11434", timeout=5.0)
    result = disc.verify_capabilities("live-new-model:7b", {"vision"})
    assert result["vision"] == CapabilityState.SUPPORTED
    # cached on success
    assert _CACHE.get("http://localhost:11434", "live-new-model:7b") is not None
    # subsequent deterministic check should be supported without network
    assert model_capability_state("live-new-model:7b", "vision") == CapabilityState.SUPPORTED


def test_verify_live_fetch_absent_means_unsupported(monkeypatch):
    def fake_show(url, name, timeout=5.0):
        return {"capabilities": ["tools"], "details": {}}

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    disc = ModelDiscovery("http://localhost:11434")
    result = disc.verify_capabilities("live-text-only:7b", {"vision"})
    assert result["vision"] == CapabilityState.UNSUPPORTED


def test_verify_live_fetch_failure_verification_failed(monkeypatch):
    def fake_show(url, name, timeout=5.0):
        return None  # 404 / daemon down

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    disc = ModelDiscovery("http://localhost:11434")
    result = disc.verify_capabilities("live-unknown:7b", {"vision"})
    assert result["vision"] == CapabilityState.VERIFICATION_FAILED


def test_verify_live_fetch_timeout_verification_failed(monkeypatch):
    def fake_show(url, name, timeout=5.0):
        raise TimeoutError("timed out")

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    disc = ModelDiscovery("http://localhost:11434")
    result = disc.verify_capabilities("live-timeout:7b", {"vision", "audio"})
    assert result["vision"] == CapabilityState.VERIFICATION_FAILED
    assert result["audio"] == CapabilityState.VERIFICATION_FAILED


def test_verify_does_not_fetch_when_known_seed(monkeypatch):
    called = {}

    def fake_show(url, name, timeout=5.0):
        called["yes"] = True
        return {"capabilities": ["vision"]}

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    disc = ModelDiscovery("http://localhost:11434")
    # seed known unsupported -> no live fetch
    result = disc.verify_capabilities("qwen3:8b", {"vision"})
    assert result["vision"] == CapabilityState.UNSUPPORTED
    assert "yes" not in called
    # seed known supported -> no live fetch
    result2 = disc.verify_capabilities("qwen2.5vl:7b", {"vision"})
    assert result2["vision"] == CapabilityState.SUPPORTED
    assert "yes" not in called


def test_verify_cache_hit_no_network(monkeypatch):
    _CACHE.set("http://localhost:11434", "cached-hit:7b", {"capabilities": ["vision"]})

    def fake_show(url, name, timeout=5.0):
        raise AssertionError("should not be called on cache hit")

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    disc = ModelDiscovery("http://localhost:11434")
    result = disc.verify_capabilities("cached-hit:7b", {"vision"})
    assert result["vision"] == CapabilityState.SUPPORTED


def test_verify_multiple_caps_single_fetch(monkeypatch):
    calls = []

    def fake_show(url, name, timeout=5.0):
        calls.append(name)
        return {"capabilities": ["vision", "speech"]}

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    disc = ModelDiscovery("http://localhost:11434")
    result = disc.verify_capabilities("multi-cap-model:7b", {"vision", "audio", "tools"})
    assert result["vision"] == CapabilityState.SUPPORTED
    assert result["audio"] == CapabilityState.SUPPORTED  # speech alias
    assert result["tools"] == CapabilityState.UNSUPPORTED
    assert len(calls) == 1
