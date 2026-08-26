"""Phase 5 — Dynamic model intelligence & discovery tests.

Covers the canonical ModelRecord, the Ollama runtime inventory (/api/tags +
/api/show) with defensive parsing, capability evidence provenance, the
metadata cache, unknown-model participation in recommendations, and the
authoritative runtime capability check (no name inference).
"""

import pytest

from novi.configuration.model_records import (
    CapabilityEvidence,
    ModelIdentity,
    ModelRecord,
    ModelStatus,
)
from novi.configuration.model_seeds import SEED_MODEL_FACTS
from novi.configuration.evidence import assemble_capability_evidence, capability_flags
from novi.configuration.metadata_cache import ModelMetadataCache
from novi.configuration.qualification import Qualification
from novi.configuration.runtime_inventory import (
    OllamaRuntimeInventory,
    _capability_tokens_from_show,
    _context_length_from_show,
)
from novi.configuration.discovery import ModelDiscovery, invalidate_cache
from novi.configuration.resolver import recommend, WORKLOADS


# ── ModelRecord: unknown stays unknown ────────────────────────────────────


def test_record_unknown_fields_default_to_none():
    r = ModelRecord(name="m")
    assert r.context_length is None
    assert r.parameter_count is None
    assert r.size_bytes is None
    assert r.license is None
    assert r.capability_support("vision") is None


def test_record_capability_tri_state():
    r = ModelRecord(
        name="m",
        capabilities=[CapabilityEvidence("vision", True, "runtime", 0.95)],
        capability_flags={"tools": False},
    )
    assert r.capability_support("vision") is True
    assert r.capability_support("tools") is False
    assert r.capability_support("chat") is None


def test_record_capability_names_deduplicates():
    r = ModelRecord(
        name="m",
        capabilities=[
            CapabilityEvidence("vision", True, "runtime", 0.95),
            CapabilityEvidence("vision", True, "seed", 0.9),
        ],
        capability_flags={"tools": True},
    )
    assert set(r.capability_names()) == {"vision", "tools"}


def test_discoveredmodel_is_record_alias():
    from novi.configuration.discovery import DiscoveredModel
    m = DiscoveredModel(name="x", status=ModelStatus.INSTALLED, capability_flags={"chat": True})
    assert isinstance(m, ModelRecord)
    assert m.status == ModelStatus.INSTALLED


# ── Capability evidence precedence (runtime > seed > name-inference) ──────


def test_runtime_evidence_wins_over_seed():
    fact = SEED_MODEL_FACTS["qwen2.5vl:7b"]
    ev = assemble_capability_evidence(
        fact=fact, runtime_capabilities=["vision"], name="qwen2.5vl:7b")
    by_cap = {e.capability: e for e in ev}
    assert by_cap["vision"].source == "runtime"
    assert by_cap["chat"].source == "seed"  # not runtime-reported, filled by seed


def test_name_inference_only_fills_unclaimed():
    ev = assemble_capability_evidence(
        fact=None, runtime_capabilities=["chat"], name="my-llava-model",
        inference_hints=[CapabilityEvidence("vision", True, "name-inference", 0.8)])
    by_cap = {e.capability: e for e in ev}
    assert by_cap["chat"].source == "runtime"
    assert by_cap["vision"].source == "name-inference"


def test_capability_flags_view():
    ev = [
        CapabilityEvidence("vision", True, "runtime", 0.95),
        CapabilityEvidence("tools", False, "runtime", 0.95),
    ]
    assert capability_flags(ev) == {"vision": True, "tools": False}


# ── Ollama /api/tags parsing (defensive) ──────────────────────────────────


def test_tags_record_parses_details(monkeypatch):
    monkeypatch.setattr(
        "novi.configuration.runtime_inventory.query_ollama_tags",
        lambda url="", timeout=0.0: [{
            "name": "qwen2.5:7b-instruct-q4_K_M",
            "size": 4687078595,
            "details": {
                "parameter_size": "7.6B",
                "quantization_level": "Q4_K_M",
                "family": "qwen2",
                "format": "gguf",
                "families": ["qwen2"],
            },
        }],
    )
    inv = OllamaRuntimeInventory(url="http://localhost:11434")
    records = inv.list_models()
    assert len(records) == 1
    r = records[0]
    assert r.name == "qwen2.5:7b-instruct-q4_K_M"
    assert r.parameter_count == "7.6B"
    assert r.identity.quantization == "Q4_K_M"
    assert r.identity.family == "qwen2"
    assert r.format == "gguf"
    assert r.size_bytes == 4687078595
    assert r.status == ModelStatus.INSTALLED


def test_tags_record_malformed_fields_are_none(monkeypatch):
    monkeypatch.setattr(
        "novi.configuration.runtime_inventory.query_ollama_tags",
        lambda url="", timeout=0.0: [{
            "name": "weird:model",
            "size": "not-an-int",
            "details": {"parameter_size": None},
        }],
    )
    records = OllamaRuntimeInventory().list_models()
    r = records[0]
    assert r.parameter_count is None
    assert r.size_bytes is None


def test_tags_skips_missing_name_and_non_dicts(monkeypatch):
    monkeypatch.setattr(
        "novi.configuration.runtime_inventory.query_ollama_tags",
        lambda url="", timeout=0.0: [{"size": 1}, None, "nope", {"name": 42}, {"name": "ok"}],
    )
    records = OllamaRuntimeInventory().list_models()
    assert [r.name for r in records] == ["ok"]


def test_tags_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "novi.configuration.runtime_inventory.query_ollama_tags",
        lambda url="", timeout=0.0: [],
    )
    assert OllamaRuntimeInventory().list_models() == []


# ── Ollama /api/show parsing (Phase 5C) ───────────────────────────────────


def test_show_capability_tokens_mapped_11():
    payload = {"capabilities": ["tools", "vision", "embedding", "reasoning", "mystery"]}
    mapped = _capability_tokens_from_show(payload)
    assert set(mapped) == {"tools", "vision", "embeddings", "reasoning"}


def test_show_capability_tokens_malformed():
    assert _capability_tokens_from_show({}) == []
    assert _capability_tokens_from_show({"capabilities": "tools"}) == []


def test_show_context_length_scans_model_info():
    assert _context_length_from_show({"model_info": {"llama.context_length": 8192}}) == 8192
    assert _context_length_from_show({"model_info": {"model.context_length": 16384}}) == 16384
    # malformed / missing -> None, never fabricated
    assert _context_length_from_show({"model_info": {"llama.context_length": "huge"}}) is None
    assert _context_length_from_show({"model_info": {}}) is None
    assert _context_length_from_show({}) is None
    assert _context_length_from_show({"model_info": {"llama.context_length": True}}) is None


def test_show_record_merges_detail(monkeypatch):
    monkeypatch.setattr(
        "novi.configuration.runtime_inventory.query_ollama_show",
        lambda url, name, timeout=0.0: {
            "model_info": {"llama.context_length": 131072},
            "license": "Apache-2.0",
            "parameters": "32.8B",
            "capabilities": ["tools", "reasoning"],
            "details": {"quantization_level": "Q4_K_M"},
        },
    )
    record = OllamaRuntimeInventory().show_model("qwen3:8b")
    assert record.context_length == 131072
    assert record.license == "Apache-2.0"
    assert record.parameter_count == "32.8B"
    assert record.capability_support("tools") is True
    assert record.capability_support("reasoning") is True


def test_show_unknown_model_returns_none(monkeypatch):
    monkeypatch.setattr(
        "novi.configuration.runtime_inventory.query_ollama_show",
        lambda url, name, timeout=0.0: None,
    )
    assert OllamaRuntimeInventory().show_model("ghost:model") is None


# ── Discovery + metadata cache (Phase 5H) ─────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def test_discovery_enriches_and_caches_show(monkeypatch):
    tags = [{"name": "qwen3:8b", "size": 1}]
    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_tags",
        lambda url="", timeout=0.0: tags)
    calls = []
    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_show",
        lambda url, name, timeout=0.0: (calls.append(name),
                                        {"capabilities": ["tools"],
                                         "model_info": {"llama.context_length": 131072}})[1])
    d = ModelDiscovery("http://localhost:11434")
    records = d.installed()
    assert len(records) == 1
    assert records[0].capability_support("tools") is True
    assert records[0].context_length == 131072
    # second discovery uses the cache — no new show calls
    ModelDiscovery("http://localhost:11434").installed()
    assert len(calls) == 1


def test_discovery_daemon_down_serves_stale_cache(monkeypatch):
    # Prime the cache with a show payload for one model.
    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_tags",
        lambda url="", timeout=0.0: [{"name": "llama3.1:8b", "size": 1}])
    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_show",
        lambda url, name, timeout=0.0: {"capabilities": ["tools"]})
    d = ModelDiscovery("http://localhost:11434")
    assert len(d.installed()) == 1

    # Daemon down: tags empty, but cached metadata is served, marked stale.
    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_tags",
        lambda url="", timeout=0.0: [])
    records = d.installed()
    assert len(records) == 1
    assert records[0].stale is True
    assert records[0].status == ModelStatus.INSTALLED


def test_invalidate_clears_cache_after_install(monkeypatch):
    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_tags",
        lambda url="", timeout=0.0: [{"name": "qwen3:8b", "size": 1}])
    calls = []
    monkeypatch.setattr(
        "novi.configuration.discovery.query_ollama_show",
        lambda url, name, timeout=0.0: (calls.append(name),
                                        {"capabilities": ["tools"]})[1])
    ModelDiscovery("http://localhost:11434").installed()
    assert len(calls) == 1
    invalidate_cache()
    ModelDiscovery("http://localhost:11434").installed()
    assert len(calls) == 2


def test_metadata_cache_ttl_and_invalidate():
    c = ModelMetadataCache(ttl=0.1)
    c.set("u", "m", {"k": 1})
    assert c.get("u", "m") == {"k": 1}
    c.invalidate(url="u", name="m")
    assert c.get("u", "m") is None
    c.set("u", "m", {"k": 1})
    c.invalidate()
    assert c.get("u", "m") is None


# ── Unknown models participate in recommendations (Phase 5E/5I) ───────────


def test_unknown_model_with_runtime_evidence_is_recommended():
    installed = [
        {"name": "gemma4:e4b", "capability_names": ["chat", "tools"]},
        {"name": "mystery-llava:7b", "capability_names": ["chat", "vision"]},
    ]
    r = recommend(hardware=None, installed=installed)
    assert r.workloads["general"].model in {"gemma4:e4b", "mystery-llava:7b"}
    # the vision-capable unknown participates with derived evidence
    assert r.workloads["general"].capabilities


def test_name_inference_only_model_ranks_last():
    from novi.configuration.model_records import CapabilityEvidence, ModelRecord
    records = [
        ModelRecord(
            name="trusted-coder", status=ModelStatus.INSTALLED,
            capabilities=[
                CapabilityEvidence("chat", True, "seed", 0.9),
                CapabilityEvidence("coding", True, "seed", 0.9),
            ],
            capability_flags={"chat": True, "coding": True},
            qualification=Qualification.TRUSTED,
        ),
        ModelRecord(
            name="guess-coder", status=ModelStatus.INSTALLED,
            capabilities=[
                CapabilityEvidence("coding", True, "name-inference", 0.7),
            ],
            capability_flags={"coding": True},
        ),
    ]
    r = recommend(hardware=None, installed=records)
    assert r.workloads["code"].model == "trusted-coder"


def test_recommend_never_picks_unknown_without_evidence():
    r = recommend(hardware=None, installed=["random-unknown:99b"])
    assert all(r.workloads[w].model == "" for w in WORKLOADS)


# ── Runtime capability check is authoritative (no name inference) ─────────


def test_model_capabilities_unknown_stays_unknown():
    from novi.runtime.model_selector import model_capabilities
    caps = model_capabilities("some-llava-brand:7b")
    assert caps.supports_vision is False
    assert caps.capabilities == frozenset()


def test_model_capabilities_seed_based():
    from novi.runtime.model_selector import model_capabilities
    caps = model_capabilities("qwen2.5vl:7b")
    assert caps.supports_vision is True
    assert caps.supports_tools is True


def test_model_capabilities_merges_measured_runtime_evidence():
    invalidate_cache()
    from novi.configuration.discovery import _CACHE
    _CACHE.set("http://localhost:11434", "probe:model",
               {"capabilities": ["vision", "tools"]})
    try:
        from novi.runtime.model_selector import model_capabilities
        caps = model_capabilities("probe:model")
        assert caps.supports_vision is True
        assert caps.supports_tools is True
    finally:
        invalidate_cache()