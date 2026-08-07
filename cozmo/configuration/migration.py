"""Configuration migration — one-way, idempotent upgrades.

Normalizes legacy ``[models]`` config into the framework's ``llm.roles`` shape
and records removed/renamed keys. Runs at startup on the merged file.
"""

from __future__ import annotations

import logging

log = logging.getLogger("cozmo.config.migration")

_OLD_MODEL_ROLE_MAP = {
    "chat": "chat",
    "agent": "chat",
    "coder": "coder",
    "vision": "vision",
    "research": "planner",
    "classifier": "classifier",
    "router": "router",
    "orchestrator": "orchestrator",
}


def migrate(data: dict) -> dict:
    """Migrate a loaded config dict in place (does not write). Idempotent."""
    if "models" in data and "llm" not in data:
        data = _migrate_old_models(data)
    _drop_legacy_backcompat(data)
    return data


def _migrate_old_models(cfg: dict) -> dict:
    old_models = cfg.pop("models", {})
    max_tokens = old_models.pop("max_tokens", None)
    roles = {}
    for old_key, model_name in old_models.items():
        new_role = _OLD_MODEL_ROLE_MAP.get(old_key, old_key)
        if isinstance(model_name, dict):
            roles[new_role] = model_name
        elif isinstance(model_name, str) and model_name.strip():
            roles[new_role] = {"model": model_name}
        else:
            roles[new_role] = {"model": ""}
    existing_llm = cfg.get("llm", {})
    existing_roles = existing_llm.get("roles", {}) if isinstance(existing_llm, dict) else {}
    merged = {**roles, **existing_roles}
    cfg["llm"] = {
        "max_tokens": max_tokens if max_tokens is not None else existing_llm.get("max_tokens") if isinstance(existing_llm, dict) else None,
        "roles": merged,
    }
    log.info("migrated config from [models] to [llm.roles]")
    return cfg


def _drop_legacy_backcompat(cfg: dict):
    """Remove the synthesized virtual ``models`` mirror (single source of truth)."""
    if "llm" in cfg and "models" in cfg:
        cfg.pop("models", None)
        log.info("dropped legacy 'models' mirror; llm.roles is the source")