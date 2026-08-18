"""Bootstrap — the framework's process-wide instance and legacy config bridge.

``bootstrap.build_configuration()`` constructs the Configuration facade with
the default registry, migrates legacy files, and initializes state.
``bootstrap.legacy_config()`` returns a plain dict compatible with the old
``cozmo.config.load()`` callers so the migration can proceed subsystem by
subsystem without one giant atomics change.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path

from .builtin import register_defaults
from .manager import Configuration
from .migration import migrate
from .registry import ConfigRegistry

log = logging.getLogger("cozmo.config")

CONFIG_PATH = Path.home() / ".cozmo" / "config.toml"


# Defaults without hardcoded model names. Model settings default to "" and are
# resolved via discovery/selection, never silently substituted.
DEFAULT_CONFIG: dict = {
    "llm": {
        "max_tokens": 65536,
        "workloads": {workload: {"model": ""} for workload in ("general", "research", "code")},
    },
    "embedding": {"backend": "ollama", "model": "", "dimension": 768},
    "ollama": {"url": "http://localhost:11434"},
    "providers": {
        "default": "ollama",
        "ollama": {"url": "http://localhost:11434", "reasoning": True},
        "openai": {"api_key_env": "OPENAI_API_KEY"},
    },
    "memory": {"max_turns_before_summary": 5, "max_short_term_pairs": 10},
    "workspace": {"path": "~/.cozmo/workspace", "knowledge": "~/.cozmo/knowledge", "git_repo": ""},
    "personality": "",
    "search": {"url": "http://localhost:8080", "backend": "searxng"},
    "mcp": {"servers": {}},
    "runtime": {
        "max_history": 10,
        "max_steps": 8,
        "max_tool_output_chars": 8000,
        "memory_distance_threshold": 0.5,
        "max_memory_results": 3,
        "max_project_results": 3,
        "temperatures": {"chat": 0.6, "work": 0.0, "research": 0.2},
        "tool_gate": {"chat": [], "research": []},
        "force_capability": "",
        "force_model": "",
        "tools": {"fallbacks": {}},
        "planning": {"auto_threshold": 1},
        "routing": {
            "intent_capabilities": {
                "conversation": ["conversation"],
                "research": ["research", "conversation"],
                "coding": ["coding", "filesystem", "terminal"],
                "planning": ["planning", "conversation"],
                "vision": ["vision", "conversation"],
            },
        },
    },
    "agents": {
        "primary": ["build", "plan"],
        "build": {"permissions": {}},
        "plan": {"permissions": {"write_file": "deny", "edit_file": "deny", "run_command": "deny"}},
        "profiles": {},
    },
    "permissions": {"write_file": "ask", "edit_file": "ask", "run_command": {"*": "ask"}},
    "code": {"index_extensions": ["*"]},
    "desktop": {"enabled": False},
    "telegram": {"enabled": False, "bot_token": "", "allowed_chat_ids": []},
}


def _apply_env_overrides(data: dict) -> dict:
    """Environment overrides still honored (precedence above file)."""
    env_val = os.getenv("COZMO_OLLAMA_URL")
    if env_val:
        data.setdefault("ollama", {})["url"] = env_val
        data.setdefault("providers", {}).setdefault("ollama", {})["url"] = env_val
    return data


def _resolve_paths(data: dict) -> dict:
    ws = data.get("workspace")
    if isinstance(ws, dict):
        for key in ("path", "knowledge"):
            value = ws.get(key)
            if not value:
                continue
            p = Path(value).expanduser()
            if not p.is_absolute():
                p = CONFIG_PATH.parent / p
            ws[key] = str(p.resolve())
    return data


def build_registry() -> ConfigRegistry:
    reg = ConfigRegistry()
    register_defaults(reg)
    return reg


def build_configuration() -> Configuration:
    reg = build_registry()
    cfg = Configuration(reg, CONFIG_PATH, defaults=DEFAULT_CONFIG)
    cfg.initialize()
    # One-way migration of any legacy shapes in the loaded file.
    raw = cfg.state.as_dict()
    migrated = migrate(raw)
    if migrated != raw:
        cfg.store.write(migrated)
        cfg.initialize()
    _bind_apply_hooks(cfg)
    return cfg


def _bind_apply_hooks(cfg: Configuration):
    """Bind subsystem apply callbacks so runtime changes propagate live."""

    def runtime_apply(path, value, previous):
        hooks = _hooks.get("runtime") or []
        for fn in hooks:
            try:
                fn(path, value, previous)
            except Exception as e:
                log.warning("runtime apply hook failed for %s: %s", path, e)

    def memory_apply(path, value, previous):
        hooks = _hooks.get("memory") or []
        for fn in hooks:
            try:
                fn(path, value, previous)
            except Exception as e:
                log.warning("memory apply hook failed for %s: %s", path, e)

    def mcp_apply(path, value, previous):
        hooks = _hooks.get("mcp") or []
        for fn in hooks:
            try:
                fn(path, value, previous)
            except Exception as e:
                log.warning("mcp apply hook failed for %s: %s", path, e)

    def integrations_apply(path, value, previous):
        hooks = _hooks.get("integrations") or []
        for fn in hooks:
            try:
                fn(path, value, previous)
            except Exception as e:
                log.warning("integrations apply hook failed for %s: %s", path, e)

    cfg.registry.require_owner("runtime", runtime_apply)
    cfg.registry.require_owner("memory", memory_apply)
    cfg.registry.require_owner("mcp", mcp_apply)
    cfg.registry.require_owner("providers", lambda p, v, prev: None)
    cfg.registry.require_owner("tools", lambda p, v, prev: None)
    # connectors/integrations apply hooks arrive with M5 (Telegram lifecycle
    # seam). Bound here so settings under this owner persist + propagate live.
    cfg.registry.require_owner("integrations", integrations_apply)


# Subsystem hooks: namespaced lists of callables subsystems register to react
# to configuration changes (event-driven, no polling).
_hooks: dict[str, list] = {}


def register_apply_hook(subsystem: str, fn):
    _hooks.setdefault(subsystem, []).append(fn)


# Process-wide singleton.
_configuration: Configuration | None = None


def get_configuration() -> Configuration:
    global _configuration
    if _configuration is None:
        _configuration = build_configuration()
    return _configuration


def legacy_config() -> dict:
    """Return a dict-shaped snapshot for legacy consumers (migration shim).

    The snapshot is fully owned by the framework's state; callers may read it
    but must not treat it as a source of truth. New code reads via the facade.
    """
    cfg = get_configuration()
    data = cfg.snapshot()
    data = _resolve_paths(data)
    data = _apply_env_overrides(data)
    return copy.deepcopy(data)