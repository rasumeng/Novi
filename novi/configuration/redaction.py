"""Configuration redaction — mask secret values on every read surface.

Secrets are classified from the configuration schema, never by scattered
endpoint checks:

* an exact registered :class:`Setting` whose ``type`` is ``SECRET``
  (e.g. ``telegram.bot_token``), and
* any descendant of a registered namespace Setting whose ``secret_segments``
  include one of the path segments (e.g. ``mcp.servers.<name>.env``).

The redactor renders a leaf secret as a write-only placeholder
``{"configured": bool, "masked": true}`` and keeps the surrounding tree shape
so environment-variable *names* and non-secret diagnostics stay visible. It
never touches storage: persisted secrets remain in the TOML file and are only
masked at API / event / discovery boundaries. A later milestone can replace
the underlying secret storage with a CredentialProvider without changing this
read contract.
"""

from __future__ import annotations

from typing import Any

from .events import ConfigEvent
from .registry import ConfigRegistry
from .schema import SettingType

MASKED_KEY = "masked"
CONFIGURED_KEY = "configured"


class ConfigRedactor:
    """Reusable schema-driven redactor for configuration read surfaces."""

    def __init__(self, registry: ConfigRegistry):
        self._registry = registry

    def is_secret(self, path: str) -> bool:
        """True when a value located at ``path`` must never be returned raw."""
        setting = self._registry.resolve(path)
        if setting is None:
            return False
        if setting.type == SettingType.SECRET:
            return True
        if setting.namespace and setting.secret_segments:
            prefix = setting.id + "."
            if path.startswith(prefix):
                segments = path[len(prefix):].split(".")
                return any(seg in setting.secret_segments for seg in segments)
        return False

    @staticmethod
    def mask(value: Any) -> dict:
        """Write-only placeholder: preserves configured state, never the value."""
        configured = bool(value) if value is not None else False
        return {CONFIGURED_KEY: configured, MASKED_KEY: True}

    @staticmethod
    def is_masked(value: Any) -> bool:
        """True when ``value`` is a masked placeholder returned by this redactor."""
        return isinstance(value, dict) and value.get(MASKED_KEY) is True

    def redact(self, path: str, value: Any) -> Any:
        """Redact ``value`` located at ``path`` (dotted, relative to config root).

        Secret leaves become masked placeholders; secret subtrees keep their
        keys with masked leaf values; everything else passes through unchanged.
        """
        if self.is_secret(path):
            return self._mask_subtree(value)
        if isinstance(value, dict):
            return self._walk(value, path)
        return value

    def redact_tree(self, data: dict) -> dict:
        """Redact a whole configuration snapshot / dict recursively."""
        return self._walk(data, "")

    def redact_event(self, event: ConfigEvent) -> dict:
        """Redact a config change event for broadcast (never leak the value)."""
        out = event.to_dict()
        out["value"] = self.redact(event.path, event.value)
        out["previous"] = self.redact(event.path, event.previous)
        return out

    # ── internals ─────────────────────────────────────────────────────

    def _walk(self, data: dict, prefix: str) -> dict:
        out: dict = {}
        for k, v in data.items():
            p = f"{prefix}.{k}" if prefix else k
            out[k] = self.redact(p, v)
        return out

    def _mask_subtree(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._mask_subtree(v) for k, v in value.items()}
        return self.mask(value)
