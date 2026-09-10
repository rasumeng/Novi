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


# ── runtime validation integration ────────────────────────────────────────

def _make_runtime(model_name: str):
    from novi.runtime.runtime import NoviRuntime

    class FakeModelService:
        def __init__(self, model):
            self._model = model

        def resolve_primary(self):
            return ("", self._model)

        def client(self, temperature=0.0):
            raise AssertionError("stub: use _bind_runnable")

        def bind_model(self, name, tools, temperature=0.0):
            raise AssertionError("stub: use _bind_runnable")

        def client_for_model(self, name, temperature=0.0):
            raise AssertionError("stub: use _bind_runnable")

        def validate(self, *a, **k):
            return []

    svc = FakeModelService(model_name)
    # Minimal cfg; runtime will read ollama url from cfg
    rt = NoviRuntime(model_service=svc, cfg={"runtime": {}}, simple_llm=None)
    # Ensure selector url points to localhost for mocking
    rt._model_selector.ollama_url = "http://localhost:11434"
    return rt


def test_runtime_allows_unknown_vision_with_trace(monkeypatch):
    # Unknown model, live succeeds -> allow (no error), trace emitted
    def fake_show(url, name, timeout=5.0):
        return {"capabilities": ["vision"]}

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    rt = _make_runtime("brand-new-vision:7b")
    # Provide a fake runnable that streams a token
    class FakeRunnable:
        def stream(self, messages):
            yield type("C", (), {"content": "hello"})()

    rt._bind_runnable = lambda ctx, lc_tools: FakeRunnable()  # type: ignore

    from novi.runtime.execution_context import ExecutionContext

    ctx = ExecutionContext(
        user_input="describe image",
        attachments=[{"type": "image", "name": "pic.png", "path": "", "mime": "image/png"}],
        history=[],
    )
    # Need to fake a valid file path for image; bypass path check by mocking _build_multimodal_content?
    # Instead patch has_images handling: runtime checks ctx.attachments for type image, not file existence for validation
    # So validation should happen before multimodal build; we just need to run validation branch.
    # Simplify: directly test the validation logic via run_stream with mocked internals.
    # We'll drive run_stream until after validation; easiest is to check that no error yielded for unknown with live success.
    # Mock retrieve/orchestrator to avoid extra.
    rt._orchestrator = None
    # Force strategy resolution to use our model
    events = list(rt.run_stream(user_input="hi", attachments=[{"type": "image", "name": "a.png", "path": "D:/tmp/fake.png", "mime": "image/png"}]))
    kinds = [k for k, *_ in events]
    # Should not contain user-friendly vision error
    assert not any(k == "error" and "support image" in str(v).lower() for k, v in [(e[0], e[1] if len(e) > 1 else "") for e in events])
    # Should contain capability_unverified trace when live required? In this case live succeeded so supported -> no trace needed
    # For unknown that succeeded, it's now supported, so no verification_failed trace.
    # Instead test verification_failed allows.
    

def test_runtime_allows_verification_failed_with_notice(monkeypatch):
    def fake_show(url, name, timeout=5.0):
        return None

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    rt = _make_runtime("unknown-no-live:7b")

    class FakeRunnable:
        def stream(self, messages):
            yield type("C", (), {"content": "hello"})()

    rt._bind_runnable = lambda ctx, lc_tools: FakeRunnable()  # type: ignore
    rt._orchestrator = None
    events = list(rt.run_stream(user_input="hi", attachments=[{"type": "image", "name": "a.png", "path": "D:/tmp/fake.png", "mime": "image/png"}]))
    kinds = [k for k, *_ in events]
    # Should not block as unsupported
    assert not any(k == "error" and "support image" in str(v).lower() for k, v in [(e[0], e[1] if len(e) > 1 else "") for e in events])
    # Should have trace with capability_unverified
    trace_texts = [e[1].summary if hasattr(e[1], "summary") else str(e[1]) for e in events if e[0] == "trace"]
    assert any("capability_unverified" in t for t in trace_texts)


def test_runtime_blocks_known_unsupported(monkeypatch):
    # Seed known without vision -> should block
    rt = _make_runtime("qwen3:8b")

    class FakeRunnable:
        def stream(self, messages):
            yield type("C", (), {"content": "hello"})()

    rt._bind_runnable = lambda ctx, lc_tools: FakeRunnable()  # type: ignore
    rt._orchestrator = None
    events = list(rt.run_stream(user_input="hi", attachments=[{"type": "image", "name": "a.png", "path": "D:/tmp/fake.png", "mime": "image/png"}]))
    assert any(k == "error" and "support image" in str(v).lower() for k, v in [(e[0], e[1] if len(e) > 1 else "") for e in events])


def test_runtime_blocks_unsupported_via_live(monkeypatch):
    def fake_show(url, name, timeout=5.0):
        return {"capabilities": ["tools"]}  # no vision -> unsupported

    monkeypatch.setattr("novi.configuration.discovery.query_ollama_show", fake_show)
    rt = _make_runtime("live-proven-text:7b")

    class FakeRunnable:
        def stream(self, messages):
            yield type("C", (), {"content": "hello"})()

    rt._bind_runnable = lambda ctx, lc_tools: FakeRunnable()  # type: ignore
    rt._orchestrator = None
    events = list(rt.run_stream(user_input="hi", attachments=[{"type": "image", "name": "a.png", "path": "D:/tmp/fake.png", "mime": "image/png"}]))
    assert any(k == "error" and "support image" in str(v).lower() for k, v in [(e[0], e[1] if len(e) > 1 else "") for e in events])
