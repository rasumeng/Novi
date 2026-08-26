"""Configuration migration — one-way, idempotent upgrades.

Normalizes legacy role/auto-custom model configuration into the framework's
``llm.workloads.{general,research,code}.model`` shape. Runs at startup on the
merged file.

Migration is pre-release and discard-style: concepts removed by the workload
architecture (vision-as-a-slot, automatic/custom mode, roles, experience,
presets) are dropped, not reconciled. Selection is derived deterministically
from the first source that names a model, per workload, in priority order:

* ``general`` ← legacy custom-assign chat | legacy role chat | legacy ``models.chat``
* ``research`` ← legacy custom-assign reasoning | legacy role planner | legacy ``models.research``
* ``code`` ← legacy custom-assign coding | legacy role coder | legacy ``models.coder``

There is no reconciliation: a vision-only setup migrates to an empty ``general``
selection. Kept verbatim: ``models.agent`` (real agent setting) and the
dismissed-recommendation UI state, which now lives at
``models.recommendations.dismissed`` (renamed from the legacy
``models.automatic.setup.dismissed``).
"""

from __future__ import annotations

import logging

log = logging.getLogger("novi.config.migration")

# Per workload, the ordered list of (section, key) sources to consult. The
# first source that yields a non-empty model name wins.
_WORKLOAD_SOURCES = {
    "general": [("assign", "chat"), ("roles", "chat"), ("models", "chat")],
    "research": [("assign", "reasoning"), ("roles", "planner"), ("models", "research")],
    "code": [("assign", "coding"), ("roles", "coder"), ("models", "coder")],
}

# Legacy ``models.<key>`` names that no longer map to a workload and are dropped.
# ``agent`` is not discarded: ``models.agent`` is a real Agent setting.
_DISCARDED_ROLES = {"classifier", "router", "orchestrator", "vision"}

# Legacy ``models.<key>`` names fully consumed into workloads (or retired).
_LEGACY_MODEL_KEYS = {"chat", "coder", "research", "max_tokens"}


def migrate(data: dict) -> dict:
    """Migrate a loaded config dict in place (does not write). Idempotent."""
    _migrate_workloads(data)
    _drop_legacy_backcompat(data)
    return data


def _migrate_workloads(cfg: dict):
    """Fold assign/roles/legacy-model sources into llm.workloads.*."""
    llm = cfg.get("llm", {})
    if not isinstance(llm, dict):
        llm = {}
    models = cfg.get("models", {})
    if not isinstance(models, dict):
        models = {}

    max_tokens = llm.get("max_tokens")
    if max_tokens is None:
        max_tokens = models.get("max_tokens")

    existing_workloads = {}
    raw_workloads = llm.get("workloads", {})
    if isinstance(raw_workloads, dict):
        for w, spec in raw_workloads.items():
            if isinstance(spec, dict):
                existing_workloads[w] = spec.get("model", "") or ""
            elif isinstance(spec, str):
                existing_workloads[w] = spec

    assign = models.get("assign", {})
    if not isinstance(assign, dict):
        assign = {}
    roles = llm.get("roles", {})
    if not isinstance(roles, dict):
        roles = {}

    workloads = dict(existing_workloads)
    for workload, sources in _WORKLOAD_SOURCES.items():
        # The persisted workload surface is authoritative; legacy sources only
        # fill workloads that are not already set.
        picked = workloads.get(workload, "")
        if not picked:
            for section, key in sources:
                source = {
                    "assign": assign,
                    "roles": roles,
                    "models": models,
                }[section]
                value = source.get(key)
                if isinstance(value, dict):
                    value = value.get("model")
                if isinstance(value, str) and value.strip():
                    picked = value.strip()
                    break
        workloads[workload] = picked

    new_llm = {"max_tokens": max_tokens} if max_tokens is not None else {}
    new_llm["workloads"] = {w: {"model": workloads.get(w, "")} for w in ("general", "research", "code")}
    cfg["llm"] = new_llm

    # Keep the working-agent model (real Agent setting, not a legacy role key).
    agent_model = models.get("agent")
    if isinstance(agent_model, dict):
        agent_model = agent_model.get("model")
    if agent_model:
        cfg.setdefault("models", {})["agent"] = agent_model

    # Preserve dismissed-recommendation UI state (renamed key).
    setup = models.get("automatic", {})
    if isinstance(setup, dict):
        dismissed = setup.get("setup", {}).get("dismissed") if isinstance(setup.get("setup"), dict) else None
        if dismissed:
            cfg.setdefault("models", {})["recommendations"] = {"dismissed": dismissed}

    log.info("migrated model config to [llm.workloads]")


def _drop_legacy_backcompat(cfg: dict):
    """Remove every legacy/derived key the workload architecture retired."""
    llm = cfg.get("llm", {})
    if isinstance(llm, dict):
        llm.pop("roles", None)
        llm.pop("default_model", None)
        llm.pop("meta", None)
    cfg.pop("experience", None)
    runtime = cfg.get("runtime", {})
    if isinstance(runtime, dict):
        runtime.pop("lightweight_mode", None)
    models = cfg.get("models", {})
    if isinstance(models, dict):
        models.pop("mode", None)
        models.pop("custom", None)
        models.pop("automatic", None)
        for role in _DISCARDED_ROLES:
            models.pop(role, None)
        for key in _LEGACY_MODEL_KEYS:
            models.pop(key, None)
        if not models:
            cfg.pop("models", None)
    log.info("dropped legacy model config keys (roles/mode/custom/experience/lightweight_mode)")
