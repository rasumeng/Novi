"""MCP provider — thin facade over the M5.5 MCP seams.

Formerly the all-in-one ``MCPManager``. The real logic now lives in the seams
(``cozmo.runtime.mcp``):

* ``MCPLifecycle`` — which servers run, start/stop/reconnect, event loop
* ``MCPRuntimeClient`` (+ ``MCPHost``) — one server's connection/session
* ``MCPToolDiscovery`` — tool discovery + ToolRegistry registration
* ``MCPStatus`` — safe status/health observation

This class keeps the legacy public surface (``start``/``stop``/
``refresh_from_config``/``get_status``/``get_lifecycle``/``get_server_detail``/
``health_check``/``reconnect``/``refresh``) and the test-visible runtime
internals (``_loop``/``_hosts``/``_server_tools``/``_configured``) so existing
callers and regression tests keep working while ownership moved to the seams.

MCP stays runtime-only/stateless: recreation from configuration reconstructs
everything; nothing here is ever persisted.
"""

from __future__ import annotations

from . import Provider
from ..mcp.discovery import MCPToolDiscovery
from ..mcp.lifecycle import MCPLifecycle
from ..mcp.status import MCPStatus
from ..mcp_host import MCPHost


class MCPManager(Provider):
    def __init__(self, registry):
        self._registry = registry
        self._discovery = MCPToolDiscovery(registry)
        self._lifecycle = MCPLifecycle(self._discovery, host_factory=MCPHost)
        self._status = MCPStatus(self._lifecycle, self._lifecycle.discovery)

    # ── lifecycle (delegated) ──────────────────────────────────────

    def start(self, config: dict, registry=None) -> None:
        if registry is not None:
            self._registry = registry
        self._lifecycle.start(config)

    def refresh_from_config(self, config: dict) -> None:
        self._lifecycle.refresh_from_config(config)

    def stop(self) -> None:
        self._lifecycle.stop()

    def reconnect(self, server_name: str) -> bool:
        return self._lifecycle.reconnect(server_name)

    def refresh(self) -> None:
        self._lifecycle.refresh()

    # ── status (delegated) ─────────────────────────────────────────

    def get_status(self) -> dict[str, dict]:
        return self._status.get_status()

    def get_lifecycle(self) -> dict:
        return self._status.get_lifecycle()

    def get_server_detail(self, name: str) -> dict | None:
        return self._status.get_server_detail(name)

    def health_check(self) -> dict[str, str]:
        return self._status.health_check()

    # ── test-visible runtime internals (live views into the seams) ─

    @property
    def _loop(self):
        return self._lifecycle.loop

    @property
    def _hosts(self):
        return {
            name: client.host
            for name, client in self._lifecycle.clients.items()
            if client.host is not None
        }

    @property
    def _server_tools(self):
        return self._lifecycle.discovery.server_tools

    @property
    def _server_configs(self):
        return self._lifecycle.server_configs

    @property
    def _configured(self):
        return self._lifecycle.configured

    @property
    def _enabled(self):
        return self._lifecycle.enabled

    @property
    def _state(self):
        return self._lifecycle.state