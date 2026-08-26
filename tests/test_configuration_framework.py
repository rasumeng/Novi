"""Configuration Framework tests.

Covers the Milestone 4.5 architecture guarantees: single owner, no hardcoded
defaults, validation, persistence, event-driven propagation, and migration.
"""

from pathlib import Path

import pytest

from novi.configuration.bootstrap import DEFAULT_CONFIG
from novi.configuration.manager import Configuration, ValidationError
from novi.configuration.registry import (
    ConfigRegistry,
    DuplicateSettingError,
    UnknownSettingError,
)
from novi.configuration.schema import Category, Setting, SettingType
from novi.configuration.migration import migrate


def mk(id, owner="runtime", typ=None, category=Category.DEVELOPER,
       default=None, **kw):
    if typ is None:
        typ = kw.pop("type", SettingType.STRING)
    return Setting(id=id, owner=owner, category=category, type=typ,
                   default=default, **kw)


@pytest.fixture
def registry():
    return ConfigRegistry()


# ── Single owner / source of truth ────────────────────────────────────


def test_unique_owner_enforced(registry):
    registry.register(mk("llm.workloads.general.model", owner="runtime"))
    with pytest.raises(DuplicateSettingError):
        registry.register(mk("llm.workloads.general.model", owner="memory"))


def test_unknown_setting_raises(registry):
    with pytest.raises(UnknownSettingError):
        registry.get("nope.nope")


# ── No hardcoded model names in defaults ──────────────────────────────


def test_no_hardcoded_model_names():
    llm = DEFAULT_CONFIG.get("llm", {})
    for workload, spec in llm.get("workloads", {}).items():
        assert spec.get("model", "") == "", \
            f"workload {workload} default must be empty"


# ── Validation ────────────────────────────────────────────────────────


def _enum_registry() -> ConfigRegistry:
    reg = ConfigRegistry()
    reg.register(mk("permissions.write_file", owner="tools",
                    type=SettingType.ENUM, default="ask",
                    options=[type("O", (), {"value": "ask", "label": "Ask"})(),
                             type("O", (), {"value": "allow", "label": "Allow"})()]))
    return reg


def test_validation_rejects_unknown_enum(tmp_path):
    reg = _enum_registry()
    cfg = Configuration(reg, tmp_path / "c.toml")
    cfg.initialize()
    with pytest.raises(ValidationError):
        cfg.set("permissions.write_file", "not-an-option", by="test")


def test_validation_accepts_known_enum(tmp_path):
    reg = _enum_registry()
    cfg = Configuration(reg, tmp_path / "c.toml")
    cfg.initialize()
    cfg.set("permissions.write_file", "allow", by="test")
    assert cfg.get("permissions.write_file") == "allow"


# ── Persistence + state ──────────────────────────────────────────────


def test_set_persists(tmp_path):
    reg = ConfigRegistry()
    reg.register(mk("embedding.model", owner="memory"))
    cfg = Configuration(reg, tmp_path / "cfg.toml")
    cfg.initialize()
    cfg.set("embedding.model", "nomic-embed-text", by="test")
    assert cfg.get("embedding.model") == "nomic-embed-text"
    cfg2 = Configuration(reg, tmp_path / "cfg.toml")
    cfg2.initialize()
    assert cfg2.get("embedding.model") == "nomic-embed-text"


# ── Events ────────────────────────────────────────────────────────────


def test_change_emits_event(tmp_path):
    reg = ConfigRegistry()
    reg.register(mk("runtime.max_steps", owner="runtime", type=SettingType.INT))
    cfg = Configuration(reg, tmp_path / "c.toml")
    cfg.initialize()
    seen = []
    cfg.subscribe("runtime.max_steps", lambda ev: seen.append(ev))
    cfg.set("runtime.max_steps", 12, by="test")
    assert len(seen) == 1
    assert seen[0].value == 12


def test_apply_hook_invoked(tmp_path):
    reg = ConfigRegistry()
    reg.register(mk("mcp.enabled", owner="mcp", type=SettingType.BOOL))
    applied = []
    reg.require_owner("mcp", lambda p, v, prev: applied.append((p, v)))
    cfg = Configuration(reg, tmp_path / "c.toml")
    cfg.initialize()
    cfg.set("mcp.enabled", False, by="test")
    assert applied == [("mcp.enabled", False)]


# ── Migration ─────────────────────────────────────────────────────────


def test_migrate_old_models_to_workloads():
    cfg = {"models": {"chat": "llama3", "max_tokens": 4096}}
    out = migrate(dict(cfg))
    assert "models" not in out, "legacy models mirror must be dropped"
    assert out["llm"]["workloads"]["general"]["model"] == "llama3"
    assert out["llm"]["workloads"]["research"]["model"] == ""
    assert out["llm"]["max_tokens"] == 4096


def test_migrate_roles_to_workloads():
    src = {"llm": {"roles": {"chat": {"model": "x"}}}, "embedding": {}}
    out = migrate(dict(src))
    assert "models" not in out
    assert "roles" not in out["llm"], "legacy llm.roles must be dropped"
    assert out["llm"]["workloads"]["general"]["model"] == "x"


def test_migrate_planner_and_coder_roles_map_to_research_and_code():
    src = {"llm": {"roles": {
        "chat": {"model": "a"}, "planner": {"model": "b"},
        "coder": {"model": "c"}}}}
    out = migrate(dict(src))
    assert out["llm"]["workloads"]["general"]["model"] == "a"
    assert out["llm"]["workloads"]["research"]["model"] == "b"
    assert out["llm"]["workloads"]["code"]["model"] == "c"