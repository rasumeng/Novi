"""Milestone 1 (Phase A: Settings & Configuration Rework) tests.

Covers the M1 hardening of the configuration framework V2:
    - every orphaned/orphaned-path setting resolves through the registry
      (exact setting or registered namespace sub-path)
    - new groups: memory, integrations (telegram), agent / agents, models.agent
    - namespaces: mcp / mcp.servers, permissions, agent, agents, config roots
    - validation (non-negative int) and persistence to store
    - config events emitted + apply hooks fire
    - the legacy ``/api/config`` whole-dict + sub-path writes delegate into the
      framework (validate -> persist -> apply -> emit) instead of raw merging
    - unknown / unregistered keys surface an explicit error, never a silent write

Hermetic: in-memory / tmp_path config; no real user config, network, or services.
"""

import pytest

from novi.configuration.bootstrap import build_registry
from novi.configuration.manager import Configuration, ValidationError
from novi.configuration.registry import UnknownSettingError


def build_cfg(tmp_path, bus=None, hooks=None) -> Configuration:
    reg = build_registry()
    cfg = Configuration(reg, tmp_path / "novi.toml", bus=bus)
    cfg.initialize()
    if hooks:
        for owner, fn in hooks.items():
            reg.require_owner(owner, fn)
    return cfg


# ── M1.1/M1.2: new settings resolve + validate ─────────────────────────


def test_new_settings_resolve(tmp_path):
    cfg = build_cfg(tmp_path)
    for key in (
        # memory
        "memory.max_turns_before_summary",
        "memory.max_short_term_pairs",
        # integrations (telegram)
        "telegram.enabled",
        "telegram.bot_token",
        "telegram.allowed_chat_ids",
        # agent
        "agent",
        "agents",
        "models.agent",
        # namespaces
        "mcp.enabled",
        "mcp.servers",
        "mcp.servers.any.leaf",
        "permissions",
        "permissions.write_file",
    ):
        assert cfg.registry.has(key), f"expected '{key}' to resolve"
        assert cfg.registry.owner_for(key), f"'{key}' missing owner"
    # Task 4.1: config_roots whole-root bulk writes removed — these no longer resolve
    for retired_root in ("mcp", "models", "llm", "runtime", "memory", "embedding", "personality"):
        assert not cfg.registry.has(retired_root), f"retired config root '{retired_root}' should not resolve after Task 4.1"


def test_memory_leaves_validate(tmp_path):
    cfg = build_cfg(tmp_path)
    cfg.set("memory.max_turns_before_summary", 200, by="test")
    assert cfg.get("memory.max_turns_before_summary") == 200
    with pytest.raises(ValidationError):
        cfg.set("memory.max_turns_before_summary", -1, by="test")


def test_memory_int_rejects_float(tmp_path):
    cfg = build_cfg(tmp_path)
    with pytest.raises(ValidationError):
        cfg.set("memory.max_turns_before_summary", 4.5, by="test")


# ── M1.3: /api/configuration delegation (framework path) ──────────────


def test_whole_dict_write_delegates(tmp_path):
    cfg = build_cfg(tmp_path)
    val = {"code": {"command": "uvx mcp-server-git", "enabled": True}}
    # Task 4.1: whole-root "mcp" removed — framework path now uses the granular
    # namespace "mcp.servers" (the only intentional whole-collection write).
    cfg.set("mcp.servers", val, by="webui")
    assert cfg.get("mcp.servers.code.command") == "uvx mcp-server-git"
    assert cfg.get("mcp.servers.code.enabled") is True


def test_namespace_subpath_write_delegates(tmp_path):
    cfg = build_cfg(tmp_path)
    # legacyPatch can address a leaf under a namespace without the parent id
    if cfg.registry.has("mcp.servers.foo.command"):
        cfg.set("mcp.servers.foo.command", "echo hi", by="webui")
    assert cfg.get("mcp.servers.foo.command") == "echo hi"


def test_unknown_key_is_error_not_silent(tmp_path):
    cfg = build_cfg(tmp_path)
    # matches the PATCH else-branch: unregistered key -> explicit fail
    assert not cfg.registry.has("totally.bogus.key")
    with pytest.raises(UnknownSettingError):
        cfg.set("totally.bogus.key", 1, by="webui")


def test_config_roots_are_owned(tmp_path):
    cfg = build_cfg(tmp_path)
    # Task 4.1: config_roots removed — whole-root bulk writes no longer registered.
    # The only whole-collection that remains is the intentional mcp.servers namespace.
    for root in ("mcp", "models", "llm", "runtime", "memory", "embedding",
                 "personality"):
        assert not cfg.registry.has(root), f"retired config root '{root}' should be gone (Task 4.1)"
    assert cfg.registry.has("mcp.servers")
    assert cfg.registry.has("permissions")
    assert cfg.registry.has("agent")


# ── M1.5: persistence / events / apply ─────────────────────────────────


def test_mcp_and_memory_persist(tmp_path):
    cfg = build_cfg(tmp_path)
    cfg.set("mcp.servers", {"a": {"command": "x", "enabled": True}}, by="test")
    cfg.set("memory.max_turns_before_summary", 77, by="test")
    cfg.set("telegram.enabled", True, by="test")
    cfg2 = build_cfg(tmp_path)
    assert cfg2.get("mcp.servers.a.command") == "x"
    assert cfg2.get("memory.max_turns_before_summary") == 77
    assert cfg2.get("telegram.enabled") is True


def test_events_emitted(tmp_path):
    from novi.configuration.events import ConfigBus
    bus = ConfigBus()
    seen = []
    bus.on_any(lambda ev: seen.append(ev.path))
    cfg = build_cfg(tmp_path, bus=bus)
    cfg.set("memory.max_turns_before_summary", 50, by="test")
    cfg.set("mcp.servers.a.command", "run", by="test")
    assert seen == ["memory.max_turns_before_summary", "mcp.servers.a.command"]


def test_apply_hooks_fire(tmp_path):
    applied = []
    cfg = build_cfg(tmp_path, hooks={
        "mcp": lambda p, v, prev: applied.append(("mcp", p)),
        "memory": lambda p, v, prev: applied.append(("memory", p)),
        "integrations": lambda p, v, prev: applied.append(("integrations", p)),
    })
    cfg.set("mcp.enabled", False, by="test")
    cfg.set("mcp.servers.a.command", "go", by="test")
    cfg.set("memory.max_turns_before_summary", 10, by="test")
    cfg.set("telegram.enabled", True, by="test")
    assert ("mcp", "mcp.enabled") in applied
    assert ("mcp", "mcp.servers.a.command") in applied
    assert ("memory", "memory.max_turns_before_summary") in applied
    assert ("integrations", "telegram.enabled") in applied


def test_migrate_runs_with_new_registrations():
    # Legacy models mirror still migrates to llm.workloads even with the new
    # 'models' namespace registered (migration is data-level, not schema).
    from novi.configuration.migration import migrate
    out = migrate({"models": {"chat": "llama3", "max_tokens": 4096}})
    assert "models" not in out
    assert out["llm"]["workloads"]["general"]["model"] == "llama3"


# ── Phase 6 Task 7: retired model-configuration paths are rejected ──────


def test_retired_models_leaf_writes_are_rejected(tmp_path):
    """The 'models' namespace must not re-persist retired model paths.

    After Task 4.1 the whole-root ``models``/``llm`` namespaces are gone, so
    retired leaves are rejected as unknown. The generic write surface still
    raises UnknownSettingError.
    """
    cfg = build_cfg(tmp_path)
    # After config_roots removal these retired leaves no longer resolve — still rejected
    for k in ("models.mode", "models.custom.assign.chat", "models.roles.chat", "llm.roles"):
        assert not cfg.registry.has(k)
        with pytest.raises(UnknownSettingError):
            cfg.set(k, "x", by="webui")
    # nothing was persisted
    assert cfg.get("models.mode", None) is None
    assert cfg.get("llm.roles", None) is None


def test_retired_models_root_write_is_rejected(tmp_path):
    """Whole-dict writes must not smuggle retired keys through the models root."""
    cfg = build_cfg(tmp_path)
    # After Task 4.1 the ``models`` root itself is no longer registered — any
    # write to it is unknown, regardless of payload.
    assert not cfg.registry.has("models")
    with pytest.raises(UnknownSettingError):
        cfg.set("models", {"mode": "auto", "agent": "qwen3:8b"}, by="webui")
    with pytest.raises(UnknownSettingError):
        cfg.set("models", {"agent": "qwen3:8b"}, by="webui")
    # Granular live leaf still succeeds
    cfg.set("models.agent", "qwen3:8b", by="webui")
    assert cfg.get("models.agent") == "qwen3:8b"


def test_retired_llm_root_write_is_rejected(tmp_path):
    cfg = build_cfg(tmp_path)
    assert not cfg.registry.has("llm")
    with pytest.raises(UnknownSettingError):
        cfg.set("llm", {"roles": {"chat": "qwen3:8b"}, "max_tokens": 4096}, by="webui")
    # llm.workloads.* remains fully supported
    cfg.set("llm.workloads.general.model", "qwen3:8b", by="webui")
    assert cfg.get("llm.workloads.general.model") == "qwen3:8b"


def test_retired_models_leaves_rejected_via_framework_surface(tmp_path):
    """The generic framework write surface reports retired paths as not registered."""
    cfg = build_cfg(tmp_path)
    for k in ("models.mode", "models.custom.assign.chat", "llm.roles"):
        # After config_roots removal these no longer resolve — rejected as unknown
        assert not cfg.registry.has(k)
        with pytest.raises(UnknownSettingError):
            cfg.set(k, "x", by="webui")
