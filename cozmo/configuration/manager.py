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
    cfg.set("llm.workloads.general.model", "qwen3:8b", by="webui")
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