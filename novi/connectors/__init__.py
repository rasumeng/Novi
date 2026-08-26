"""Connector Registry (M5.4).

Thin architectural seam that describes, registers, locates, enumerates, and
exposes the status of external systems (MCP, Telegram). Deliberately NOT an
integration manager, a second tool registry, a permission engine, or a
persistence layer — connector-specific lifecycle/runtime code stays inside the
connector implementations (``MCPManager``, ``TelegramLifecycle``).
"""

from .registry import (
    ConnectorAlreadyRegisteredError,
    ConnectorDefinition,
    ConnectorRegistry,
    UnknownConnectorError,
)

__all__ = [
    "ConnectorDefinition",
    "ConnectorRegistry",
    "ConnectorAlreadyRegisteredError",
    "UnknownConnectorError",
]