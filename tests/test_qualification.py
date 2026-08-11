"""M2.2 — Model qualification infrastructure tests.

Covers the evidence/qualification layer: four grades (trusted/supported/
experimental/incompatible), the trusted seed models, unknown->experimental
default, Gemma 4's hardware caveat, and the independence of qualification from
installation status and hardware fit.
"""

import pytest

from cozmo.configuration.qualification import Qualification
from cozmo.configuration.catalog import (
    KNOWN_MODEL_FACTS,
    ModelFact,
    ModelRecommendationEngine,
    build_catalog_payload,
)
from cozmo.configuration.discovery import DiscoveredModel, ModelStatus


# ── All four grades exist ─────────────────────────────────────────────────


def test_all_four_qualification_grades():
    expected = {
        Qualification.TRUSTED,
        Qualification.SUPPORTED,
        Qualification.EXPERIMENTAL,
        Qualification.INCOMPATIBLE,
    }
    assert set(Qualification) == expected


def test_trusted_supported_auto_selectable_experimental_incompatible_not():
    assert Qualification.TRUSTED.automatically_selectable is True
    assert Qualification.SUPPORTED.automatically_selectable is True
    assert Qualification.EXPERIMENTAL.automatically_selectable is False
    assert Qualification.INCOMPATIBLE.automatically_selectable is False


# ── Trusted seed models ───────────────────────────────────────────────────


TRUSTED_SEEDS = ["gemma4:e4b", "qwen3:8b", "qwen2.5vl:7b", "gemma4"]


def test_trusted_seed_models_present_and_graded_trusted():
    for name in TRUSTED_SEEDS:
        fact = KNOWN_MODEL_FACTS.get(name)
        assert fact is not None, f"{name} must be seeded"
        assert fact.qualification == Qualification.TRUSTED, f"{name} must be trusted"


def test_trusted_seeds_are_auto_selectable():
    for name in TRUSTED_SEEDS:
        assert KNOWN_MODEL_FACTS[name].qualification.automatically_selectable


# ── Gemma 4 hardware caveat ───────────────────────────────────────────────


def test_gemma4_caveat_present():
    gemma4 = KNOWN_MODEL_FACTS["gemma4"]
    assert gemma4.qualification == Qualification.TRUSTED
    assert len(gemma4.caveats) >= 1
    joined = " ".join(gemma4.caveats).lower()
    assert "8 gb vram" in joined
    assert "gemma4:e4b" in joined


# ── Unknown models default to experimental, never promoted ───────────────


def test_unknown_model_defaults_experimental_not_recommended():
    engine = ModelRecommendationEngine()
    rec = engine.for_model("some.strange:99b", "installed")
    assert rec["qualification"] == Qualification.EXPERIMENTAL.value
    assert rec["tier"] == "experimental"
    assert rec["recommended"] is False


def test_unknown_model_has_no_fabricated_facts():
    # Unknown models remain unqualified: no approx RAM, no reasons beyond untested
    # (a fact would only exist if curated; unknown must never be auto-promoted).
    fact = KNOWN_MODEL_FACTS.get("some.strange:99b")
    assert fact is None
    # They must not appear as trusted/supported anywhere.
    for f in KNOWN_MODEL_FACTS.values():
        assert f.name != "some.strange:99b"


# ── Incompatible never trusted/supported ─────────────────────────────────


def test_incompatible_never_trusted_or_supported():
    engine = ModelRecommendationEngine()
    fact = ModelFact("bad-model:9b", qualification=Qualification.INCOMPATIBLE)
    KNOWN_MODEL_FACTS["bad-model:9b"] = fact
    try:
        assert fact.qualification.automatically_selectable is False
        assert fact.qualification.has_evidence is False
        rec = engine.for_model("bad-model:9b", "installed")
        assert rec["qualification"] == Qualification.INCOMPATIBLE.value
        assert rec["recommended"] is False
        assert rec["tier"] == "experimental"  # never surfaced as supported
    finally:
        del KNOWN_MODEL_FACTS["bad-model:9b"]


def test_incompatible_drilldown_all():
    for g in Qualification:
        assert g not in (Qualification.TRUSTED, Qualification.SUPPORTED) or \
               g.automatically_selectable


# ── Qualification independent from installation status ───────────────────


def test_qualification_independent_from_installation():
    # Same model, different install status -> qualification unchanged.
    engine = ModelRecommendationEngine()
    for status in (ModelStatus.INSTALLED, ModelStatus.MISSING):
        rec = engine.for_model("gemma4:e4b", status.value)
        assert rec["qualification"] == Qualification.TRUSTED.value


def test_qualification_field_distinct_from_status_in_payload():
    payload = build_catalog_payload([
        DiscoveredModel(name="gemma4:e4b", status=ModelStatus.INSTALLED),
        DiscoveredModel(name="unknown:xyz", status=ModelStatus.INSTALLED),
    ])
    entry = {m["name"]: m for m in payload["models"]}
    assert entry["gemma4:e4b"]["qualification"] == "trusted"
    assert entry["gemma4:e4b"]["status"] == "installed"
    assert entry["unknown:xyz"]["qualification"] == "experimental"


# ── Qualification independent from hardware fit ──────────────────────────


def test_qualification_independent_from_hardware():
    from cozmo.configuration.hardware import HardwareProfile
    # Even with unknown/empty hardware, qualification is unchanged (the catalog
    # carries evidence; fit is a separate, later concern).
    engine = ModelRecommendationEngine(hardware=HardwareProfile())
    rec = engine.for_model("qwen3:8b", "installed")
    assert rec["qualification"] == Qualification.TRUSTED.value


# ── Catalog serialization / API payload compat ───────────────────────────


def test_payload_keeps_legacy_tier_for_compat():
    payload = build_catalog_payload([
        DiscoveredModel(name="gemma4:e4b", status=ModelStatus.INSTALLED),
        DiscoveredModel(name="llama3.1:8b", status=ModelStatus.INSTALLED),
        DiscoveredModel(name="unknown:x", status=ModelStatus.INSTALLED),
    ])
    for m in payload["models"]:
        assert m["tier"] in ("supported", "experimental")

def test_payload_round_trip_fields():
    payload = build_catalog_payload([
        DiscoveredModel(name="gemma4", status=ModelStatus.INSTALLED),
    ])
    m = payload["models"][0]
    assert "qualification" in m
    assert "caveats" in m
    assert m["caveats"]
    assert m["displayName"] == "Gemma 4"
