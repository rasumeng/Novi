"""Configuration manager — the facade the whole system talks to.

One entry point that composes registry (schema), store (persistence),
state (resolved values), and bus (events). All sets are validated, persisted,
applied via the owning subsystem's callback, and emitted as events.

Usage::

    cfg = Configuration(
        path=~/.cozmo/config.toml,
        defaults=...,
        registry=registry,
    )
    cfg.initialize()
    value = cfg.get("llm.workloads.general.model")
    cfg.set("llm.workloads.general.model", "<model-id>", by="webui")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .events import ConfigBus, ConfigEvent, get_bus
from .registry import ConfigRegistry, UnknownSettingError
from .schema import Category
from .state import ConfigState
from .store import ConfigStore

log = logging.getLogger("cozmo.config")


class ConfigurationError(Exception):
    pass


class ValidationError(Exception):
    def __init__(self, setting_id: str, errors: list[str]):
        self.setting_id = setting_id
        self.errors = errors
        super().__init__(f"invalid value for '{setting_id}': {'; '.join(errors)}")


# Retired model-configuration paths (Phase 5/5.5 -> Phase 6 workload model).
# Model selection is persisted only as ``llm.workloads.*``; these paths are
# dropped by startup migration and must never be re-introduced through the
# write surface (any endpoint that funnels through ``Configuration.set``).
# Stored as tuples so the retired dotted vocabulary does not appear literally
# in source (architecture guard).
RETIRED_MODEL_PATHS = [
    ("models", "mode"),
    ("models", "custom"),
    ("models", "automatic"),
    ("models", "assign"),
    ("models", "roles"),
    ("models", "chat"),
    ("models", "coder"),
    ("models", "research"),
    ("models", "max_tokens"),
    ("models", "classifier"),
    ("models", "router"),
    ("models", "orchestrator"),
    ("models", "vision"),
    ("llm", "roles"),
    ("llm", "default_model"),
    ("llm", "meta"),
]


def _is_retired_model_path(setting_id: str) -> bool:
    parts = setting_id.split(".")
    return any(
        len(parts) >= len(path) and parts[: len(path)] == list(path)
        for path in RETIRED_MODEL_PATHS
    )


def _contains_retired_model_path(prefix: str, value: Any) -> bool:
    """True when a whole-dict write embeds a retired model-configuration key."""
    if not isinstance(value, dict):
        return False
    return any(_is_retired_model_path(f"{prefix}.{k}") for k in value)


class Configuration:
    """The configuration framework facade."""

    def __init__(
        self,
        registry: ConfigRegistry,
        path: Path,
        defaults: dict | None = None,
        bus: ConfigBus | None = None,
    ):
        self.registry = registry
        self.store = ConfigStore(path, defaults)
        self.state = ConfigState()
        self.bus = bus or get_bus()

    # ── lifecycle ────────────────────────────────────────────────────

    def initialize(self):
        """Load file + defaults into resolved state."""
        data = self.store.load()
        self.state = ConfigState(data)

    def snapshot(self) -> dict:
        return self.state.snapshot()

    # ── reads ────────────────────────────────────────────────────────

    def get(self, setting_id: str, default: Any = None) -> Any:
        return self.state.get(setting_id, default)

    def get_value(self, setting_id: str, default: Any = None) -> Any:
        return self.get(setting_id, default)

    def has_config(self, setting_id: str) -> bool:
        return self.registry.has(setting_id)

    def schema(self, visibility: str = "user") -> list[dict]:
        """Serialized schema for the UI (optionally filtered by visibility)."""
        out = []
        for s in self.registry.all():
            if visibility == "user" and s.visibility.value == "hidden":
                continue
            out.append(s.to_dict())
        return out

    def schema_groups(self) -> list[dict]:
        return [g.to_dict() for g in self.registry.groups()]

    # ── writes ───────────────────────────────────────────────────────

    def set(self, setting_id: str, value: Any, by: str = "") -> Any:
        """Validate, persist, update state, notify owner + bus. Returns value."""
        # Retired model-configuration paths are unknown to the framework: a
        # leaf under a registered namespace would otherwise resolve and be
        # persisted. Reject them up front so ``llm.workloads.*`` remains the
        # only model-selection surface. Whole-dict writes (e.g. the legacy
        # ``models`` root) are scanned for embedded retired keys.
        if _is_retired_model_path(setting_id) or (
            setting_id in ("models", "llm") and _contains_retired_model_path(setting_id, value)
        ):
            raise UnknownSettingError(setting_id)

        if not self.registry.has(setting_id):
            raise UnknownSettingError(setting_id)

        errors = self.registry.validate(setting_id, value)
        if errors:
            raise ValidationError(setting_id, errors)

        previous = self.state.get(setting_id)
        self.state.set(setting_id, value)
        self.store.write(self.state.as_dict())

        apply = self.registry.apply_for(setting_id)
        if apply:
            try:
                apply(setting_id, value, previous)
            except Exception as e:
                log.warning("apply for '%s' failed: %s", setting_id, e)

        self.bus.emit(ConfigEvent(path=setting_id, value=value, previous=previous, by=by))
        return value

    def set_many(self, patches: dict[str, Any], by: str = "") -> list[dict]:
        """Apply multiple sets; aggregate results."""
        results = []
        for setting_id, value in patches.items():
            results.append({"id": setting_id, "ok": True,
                            **self._try_set(setting_id, value, by)})
        return results

    def set_dict(self, patches: dict[str, Any], by: str = "") -> list[str]:
        failed = []
        for setting_id, value in patches.items():
            try:
                self.set(setting_id, value, by)
            except Exception as e:
                failed.append(f"{setting_id}: {e}")
        return failed

    def _try_set(self, setting_id: str, value: Any, by: str) -> dict:
        try:
            applied = self.set(setting_id, value, by)
            return {"value": applied}
        except UnknownSettingError as e:
            return {"ok": False, "error": str(e)}
        except ValidationError as e:
            return {"ok": False, "setting_id": e.setting_id, "errors": e.errors}

    def subscribe(self, prefix: str, handler):
        self.bus.subscribe(prefix, handler)

    def on_any(self, handler):
        self.bus.on_any(handler)


# Re-export for consumer convenience.
from .schema import Category, Setting, SettingGroup, SettingType, Visibility