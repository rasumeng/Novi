"""Connector Registry — identity/registration/lookup/description for connectors.

M5.4 architectural seam: Novi represents every external system (MCP, Telegram,
and future connectors) as a registered :class:`ConnectorDefinition`. The
registry only owns:

* identity (``connector_id``, ``connector_type``, ``label``)
* registration / unregistration / lookup / enumeration
* safe status association (a per-connector callable the registry merely relays)

It intentionally does NOT own: configuration persistence, credentials, secret
storage, lifecycle implementation, runtime clients, tool execution, tool
registration, or permission evaluation. Those stay with the connector code.

Status callbacks must return SECRET-FREE dicts. The registry never reads the
persisted configuration itself and never returns raw connector configuration.

Derived ``enabled``/``identity`` values may be refreshed from a configuration
snapshot by the composition root (see ``ConnectorDefinition.update``) but the
registry never writes configuration back — runtime state only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

StatusFn = Callable[[], dict]


class ConnectorRegistryError(Exception):
    """Base error for Connector Registry problems."""


class ConnectorAlreadyRegisteredError(ConnectorRegistryError):
    """A connector with the same id is already registered."""


class UnknownConnectorError(ConnectorRegistryError):
    """Lookup/update for a connector id that is not registered."""


@dataclass
class ConnectorDefinition:
    """Metadata + safe status association for one connector.

    Only identity/description fields belong here. Holding a ``status_fn`` is a
    runtime reference to the connector's own (safe) status surface — it is
    never serialized or persisted and never exposes raw configuration.
    """

    connector_id: str
    connector_type: str
    label: str = ""
    enabled: bool = False
    # Optional: a callable returning a SECRET-FREE status dict. The registry
    # only relays it; it never fabricates or enriches the payload.
    status_fn: Optional[StatusFn] = None
    # Optional safe connector-specific identity (e.g. configured MCP server
    # names). Must never contain credentials, env values, or secrets.
    identity: dict = field(default_factory=dict)

    def update(
        self,
        *,
        enabled: Optional[bool] = None,
        label: Optional[str] = None,
        identity: Optional[dict] = None,
    ) -> None:
        """Update derived fields from a configuration snapshot (runtime only)."""
        if enabled is not None:
            self.enabled = bool(enabled)
        if label is not None:
            self.label = label
        if identity is not None:
            self.identity = dict(identity)

    def status(self) -> dict:
        """Current safe status. Falls back to an enabled-only summary."""
        if self.status_fn is None:
            return {"enabled": self.enabled}
        try:
            value = self.status_fn()
        except Exception:
            return {"enabled": self.enabled, "state": "error"}
        if not isinstance(value, dict):
            return {"enabled": self.enabled}
        return value

    def describe(self) -> dict:
        """Safe serializable snapshot: identity/type/enabled + live status."""
        return {
            "connector_id": self.connector_id,
            "connector_type": self.connector_type,
            "label": self.label,
            "enabled": self.enabled,
            "identity": dict(self.identity),
            "status": self.status(),
        }


class ConnectorRegistry:
    """In-memory registry of connector definitions.

    Thread-safety: registration/refresh are guarded by a lock; status reads
    call the per-connector status callable outside the lock so a slow or
    failing status fn never blocks registry operations.
    """

    def __init__(self):
        self._connectors: dict[str, ConnectorDefinition] = {}
        self._lock = __import__("threading").Lock()

    # ── registration ────────────────────────────────────────────────

    def register(
        self,
        connector: ConnectorDefinition,
        replace: bool = False,
    ) -> ConnectorDefinition:
        """Register a connector.

        Duplicate ids raise :class:`ConnectorAlreadyRegisteredError` unless
        ``replace=True`` (idempotent re-registration for a stable owner).
        """
        with self._lock:
            existing = self._connectors.get(connector.connector_id)
            if existing is not None and not replace:
                raise ConnectorAlreadyRegisteredError(connector.connector_id)
            self._connectors[connector.connector_id] = connector
            return connector

    def unregister(self, connector_id: str) -> None:
        """Remove a registered connector (no-op when unknown)."""
        with self._lock:
            self._connectors.pop(connector_id, None)

    # ── lookup / enumeration ────────────────────────────────────────

    def get(self, connector_id: str) -> ConnectorDefinition | None:
        """Return the definition or None when unknown."""
        with self._lock:
            return self._connectors.get(connector_id)

    def require(self, connector_id: str) -> ConnectorDefinition:
        """Return the definition, raising when unknown."""
        connector = self.get(connector_id)
        if connector is None:
            raise UnknownConnectorError(connector_id)
        return connector

    def list(self) -> list[ConnectorDefinition]:
        """Enumerate registered connectors (insertion order)."""
        with self._lock:
            return list(self._connectors.values())

    def types(self) -> list[str]:
        """Distinct connector types, sorted."""
        with self._lock:
            return sorted({c.connector_type for c in self._connectors.values()})

    def has(self, connector_id: str) -> bool:
        return self.get(connector_id) is not None

    # ── status ──────────────────────────────────────────────────────

    def statuses(self) -> dict[str, dict]:
        """Safe status for every registered connector, keyed by id."""
        return {c.connector_id: c.status() for c in self.list()}