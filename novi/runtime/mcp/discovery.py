"""MCP tool discovery seam — convert discovered tools into Novi registrations.

Owns the server → tool association in runtime memory:

* asking a connected server (its runtime client) for its tools
* converting each discovered wrapper into a Novi registration
* registering into the EXISTING ToolRegistry (never a second registry)
* tracking which tools belong to which server
* removing/replacing a server's registrations on disconnect/refresh

Naming stays ``{server}_{tool}`` (the host already produces prefixed names).

Discovery is idempotent: re-discovering the same server replaces its previous
registrations instead of duplicating them, and stopping a server unregisters
exactly the tools it contributed.

This seam does NOT execute tools, evaluate permissions, or persist anything.
"""

from __future__ import annotations

from typing import Callable

from ..tool_registry import ToolRegistry


class MCPToolDiscovery:
    """Register/unregister the tools of connected MCP servers.

    ``make_sync`` bridges a discovered async wrapper to the synchronous
    signature the ToolRegistry/runtime expects. The lifecycle seam supplies the
    loop-bound bridge; tests may pass an identity bridge.
    """

    def __init__(self, registry: ToolRegistry, make_sync: Callable | None = None):
        self._registry = registry
        self._make_sync = make_sync or (lambda fn: fn)
        self._server_tools: dict[str, list[dict]] = {}

    # ── discovery ───────────────────────────────────────────────────

    def discover(self, server_name: str, wrappers: list[Callable]) -> list[dict]:
        """Register every wrapper for ``server_name`` (replace semantics).

        Any tools previously registered for the same server are unregistered
        first, so repeated/refreshed discovery never duplicates registrations.
        """
        tools: list[dict] = []
        for wrapper in wrappers or []:
            name = getattr(wrapper, "__name__", "") or ""
            description = getattr(wrapper, "__doc__", "") or ""
            tools.append({"name": name, "description": description})
            self._registry.register(name, self._make_sync(wrapper), description)
        self._server_tools[server_name] = tools
        return tools

    def unregister_server(self, server_name: str) -> None:
        """Remove every registration contributed by ``server_name``."""
        for tool in self._server_tools.pop(server_name, []):
            try:
                self._registry.unregister(tool["name"])
            except Exception:
                pass

    def unregister_all(self) -> None:
        for server_name in list(self._server_tools):
            self.unregister_server(server_name)

    # ── observation (in-memory, runtime only) ───────────────────────

    def tools_for(self, server_name: str) -> list[dict]:
        return list(self._server_tools.get(server_name, []))

    @property
    def server_tools(self) -> dict[str, list[dict]]:
        return self._server_tools