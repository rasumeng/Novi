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
        "mcp",
        "mcp.enabled",
        "mcp.servers",
        "mcp.servers.any.leaf",
        "permissions",
        "permissions.write_file",
        # config roots
        "models",
        "llm",
        "runtime",
        "memory",
        "embedding",
        "personality",
    ):
        assert cfg.registry.has(key), f"expected '{key}' to resolve"
        assert cfg.registry.owner_for(key), f"'{key}' missing owner"


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
    val = {"servers": {"code": {"command": "uvx mcp-server-git",
                                "enabled": True}}}
    # PATCH /api/configuration: if registry has k -> configuration.set(k, v)
    if cfg.registry.has("mcp"):
        cfg.set("mcp", val, by="webui")
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
    for root in ("mcp", "models", "llm", "runtime", "memory", "embedding",
                 "personality"):
        assert cfg.registry.has(root), f"config root '{root}' not registered"


# ── M1.5: persistence / events / apply ─────────────────────────────────


def test_mcp_and_memory_persist(tmp_path):
    cfg = build_cfg(tmp_path)
    cfg.set("mcp", {"servers": {"a": {"command": "x", "enabled": True}}}, by="test")
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

    The generic write surface (patch_configuration / set_configuration_value)
    funnels through ``Configuration.set``; a retired path that
    merely resolves via the 'models' namespace would otherwise be persisted.
    """
    cfg = build_cfg(tmp_path)
    # retired leaves resolve via the namespace but are rejected on write
    assert cfg.registry.has("models.mode")
    with pytest.raises(UnknownSettingError):
        cfg.set("models.mode", "auto", by="webui")
    with pytest.raises(UnknownSettingError):
        cfg.set("models.custom.assign.chat", "qwen3:8b", by="webui")
    with pytest.raises(UnknownSettingError):
        cfg.set("models.roles.chat", "qwen3:8b", by="webui")
    with pytest.raises(UnknownSettingError):
        cfg.set("llm.roles", {"chat": "qwen3:8b"}, by="webui")
    # nothing was persisted
    assert cfg.get("models.mode", None) is None
    assert cfg.get("llm.roles", None) is None


def test_retired_models_root_write_is_rejected(tmp_path):
    """Whole-dict writes must not smuggle retired keys through the models root."""
    cfg = build_cfg(tmp_path)
    with pytest.raises(UnknownSettingError):
        cfg.set("models", {"mode": "auto", "agent": "qwen3:8b"}, by="webui")
    # a clean whole-dict write (only live keys) still succeeds
    cfg.set("models", {"agent": "qwen3:8b"}, by="webui")
    assert cfg.get("models.agent") == "qwen3:8b"


def test_retired_llm_root_write_is_rejected(tmp_path):
    cfg = build_cfg(tmp_path)
    with pytest.raises(UnknownSettingError):
        cfg.set("llm", {"roles": {"chat": "qwen3:8b"}, "max_tokens": 4096}, by="webui")
    # llm.workloads.* remains fully supported
    cfg.set("llm.workloads.general.model", "qwen3:8b", by="webui")
    assert cfg.get("llm.workloads.general.model") == "qwen3:8b"


def test_retired_models_leaves_rejected_via_framework_surface(tmp_path):
    """The generic framework write surface reports retired paths as not registered."""
    cfg = build_cfg(tmp_path)
    for k in ("models.mode", "models.custom.assign.chat", "llm.roles"):
        # the path resolves through a registered namespace, so the rejection
        # must come from the retired-path guard, not the registry lookup
        assert cfg.registry.has(k)
        with pytest.raises(UnknownSettingError):
            cfg.set(k, "x", by="webui")
