"""Configuration migration — one-way, idempotent upgrades.

Pre-beta, no compatibility guarantees. Drops the retired multi-workload
model surface (``llm.workloads.*`` retired) and legacy role/model keys. Ensures the
canonical ``llm.primary_model`` key exists.
"""

from __future__ import annotations

import logging

log = logging.getLogger("novi.config.migration")


def migrate(data: dict) -> dict:
    """Migrate a loaded config dict in place (does not write). Idempotent."""
    _migrate_primary_model(data)
    _migrate_runtime_temperature(data)
    _migrate_memory_recall_threshold(data)
    _drop_legacy_backcompat(data)
    return data


def _migrate_primary_model(cfg: dict):
    llm = cfg.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        cfg["llm"] = llm
    if "primary_model" not in llm or not isinstance(llm.get("primary_model"), str):
        llm["primary_model"] = ""
    # Retired multi-workload surface is dropped outright — no carry-over.
    llm.pop("workloads", None)


def _drop_legacy_backcompat(cfg: dict):
    """Remove every legacy/derived key the primary-model architecture retired."""
    llm = cfg.get("llm", {})
    if isinstance(llm, dict):
        llm.pop("roles", None)
        llm.pop("default_model", None)
        llm.pop("meta", None)
        llm.pop("workloads", None)
    cfg.pop("experience", None)
    runtime = cfg.get("runtime", {})
    if isinstance(runtime, dict):
        runtime.pop("lightweight_mode", None)
    models = cfg.get("models", {})
    if isinstance(models, dict):
        for key in ("mode", "custom", "automatic", "assign", "roles", "chat",
                    "coder", "research", "max_tokens", "classifier", "router",
                    "orchestrator", "vision"):
            models.pop(key, None)
        if not models:
            cfg.pop("models", None)
    log.info("dropped legacy model config keys (workloads/roles/mode/custom)")


def _migrate_runtime_temperature(cfg: dict):
    """Collapse legacy runtime.temperatures.* into runtime.temperature."""
    runtime = cfg.get("runtime")
    if not isinstance(runtime, dict):
        return
    if "temperature" in runtime:
        runtime.pop("temperatures", None)
        return
    temps = runtime.get("temperatures")
    if isinstance(temps, dict):
        picked = temps.get("chat")
        if picked is None:
            for v in temps.values():
                if isinstance(v, (int, float)):
                    picked = v
                    break
        if isinstance(picked, (int, float)):
            runtime["temperature"] = float(picked)
        runtime.pop("temperatures", None)
        if "temperature" in runtime:
            log.info("migrated runtime.temperatures -> runtime.temperature")


def _migrate_memory_recall_threshold(cfg: dict):
    """Replace the pre-Beta cutoff that discarded qualified real embeddings."""
    runtime = cfg.get("runtime")
    if isinstance(runtime, dict) and runtime.get("memory_distance_threshold") == 0.5:
        runtime["memory_distance_threshold"] = 0.8
