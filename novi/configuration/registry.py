"""Configuration registry — every setting registers itself, owned by a subsystem.

Single source of truth for what configurable values exist. Enforces unique
owners, unique ids, and per-owner apply callbacks. The Settings UI is a
renderer over this registry.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .schema import Category, Setting, SettingGroup, SettingType

log = logging.getLogger("novi.config.registry")


class ConfigRegistryError(Exception):
    pass


class DuplicateSettingError(ConfigRegistryError):
    pass


class UnknownSettingError(ConfigRegistryError):
    pass


ApplyFn = Callable[[str, Any, Any], None]
"""apply(path, value, previous) -> reacts to a runtime change."""


class ConfigRegistry:
    """Registry of settings, owners, and apply callbacks."""

    def __init__(self):
        self._settings: dict[str, Setting] = {}
        self._groups: dict[str, SettingGroup] = {}
        self._apply: dict[str, ApplyFn] = {}

    # ── registration ────────────────────────────────────────────────

    def register_group(self, group: SettingGroup):
        if group.key in self._groups:
            if self._groups[group.key].owner != group.owner:
                raise DuplicateSettingError(
                    f"group '{group.key}' already owned by '{self._groups[group.key].owner}'"
                )
        self._groups[group.key] = group
        for setting in group.settings:
            self._register(setting)

    def register(self, setting: Setting):
        self._register(setting)

    def _register(self, setting: Setting):
        existing = self._settings.get(setting.id)
        if existing is None:
            self._settings[setting.id] = setting
            return
        if existing.owner != setting.owner:
            raise DuplicateSettingError(
                f"setting '{setting.id}' already owned by '{existing.owner}', "
                f"cannot register under '{setting.owner}'"
            )

    def require_owner(self, owner: str, callback: ApplyFn):
        """Bind a subsystem's runtime-apply callback to its settings."""
        self._apply[owner] = callback

    # ── reads ────────────────────────────────────────────────────────

    def has(self, setting_id: str) -> bool:
        return self._resolve(setting_id) is not None

    def resolve(self, setting_id: str) -> Setting | None:
        """Return the Setting owning ``setting_id``.

        Exact id matches first; otherwise a registered namespace owning the
        id's prefix. ``None`` when nothing owns the id.
        """
        return self._resolve(setting_id)

    def _resolve(self, setting_id: str) -> Setting | None:
        s = self._settings.get(setting_id)
        if s is not None:
            return s
        # Longest matching namespace (dynamic sub-paths under a collection).
        parts = setting_id.split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:i])
            parent = self._settings.get(prefix)
            if parent is not None and parent.namespace:
                return parent
        return None

    def get(self, setting_id: str) -> Setting:
        s = self._resolve(setting_id)
        if s is None:
            raise UnknownSettingError(
                f"unknown setting '{setting_id}'"
                if setting_id
                else "empty setting id"
            )
        return s

    def all(self) -> list[Setting]:
        return list(self._settings.values())

    def groups(self) -> list[SettingGroup]:
        return list(self._groups.values())

    def by_category(self, category: Category) -> list[Setting]:
        return [s for s in self._settings.values() if s.category == category]

    def owner_for(self, setting_id: str) -> str:
        s = self._resolve(setting_id)
        return s.owner if s else ""

    def validate(self, setting_id: str, value: Any) -> list[str]:
        setting = self.get(setting_id)
        resolve = self._resolve(setting_id)
        is_namespace_leaf = resolve is not None and resolve.namespace and resolve is not setting
        errors = []
        if not is_namespace_leaf:
            if setting.type in (SettingType.ENUM, SettingType.MODEL) and value not in (None, ""):
                if setting.options and not any(o.value == value for o in setting.options):
                    allowed = ", ".join(str(o.value) for o in setting.options)
                    errors.append(f"must be one of: {allowed}")
            if setting.type == SettingType.BOOL and not isinstance(value, bool) and value not in (None,):
                if value not in (0, 1, "true", "false"):
                    errors.append("must be a boolean")
            if setting.type == SettingType.INT and value not in (None, ""):
                if not isinstance(value, int) or isinstance(value, bool):
                    errors.append("must be an integer")
            if setting.type == SettingType.FLOAT and value not in (None, ""):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    errors.append("must be a number")
        errors.extend(setting.validate(value))
        return errors

    def apply_for(self, setting_id: str) -> ApplyFn | None:
        owner = self.owner_for(setting_id)
        return self._apply.get(owner)