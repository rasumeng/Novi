"""MCP runtime client seam — one server's live connection/session.

The runtime client owns the actual MCP connection behavior for a single
configured server:

* subprocess / client creation (via ``MCPHost``)
* connection + initialization
* ``list_tools`` (returning tool wrappers/callables)
* request/response interaction (through the host)
* close/disconnect

It is the runtime-only half of MCP: it holds an in-memory ``MCPHost`` and its
``ClientSession`` for exactly one server and throws it all away on close. It
never persists anything, never reads configuration on its own, and does not
decide whether it should be running (that is the lifecycle seam's job).
"""

from __future__ import annotations

from typing import Callable, Optional

from ..mcp_host import MCPHost


class MCPRuntimeClient:
    """Own the live connection/session for one MCP server.

    The host factory is injectable so hermetic tests can substitute a fake
    host. Everything here lives in memory only and is disposable.
    """

    def __init__(self, server_name: str, host_factory: Callable = MCPHost):
        self.server_name = server_name
        self._host_factory = host_factory
        self._host: Optional[MCPHost] = None
        self.connected: bool = False
        self.disconnected: bool = False
        self.last_error: Optional[str] = None
        self._config: dict = {}

    # ── introspection (safe, in-memory only) ────────────────────────

    @property
    def host(self) -> Optional[MCPHost]:
        """The underlying MCPHost, or None when not connected."""
        return self._host

    @property
    def name(self) -> str:
        return self.server_name

    # ── connection ──────────────────────────────────────────────────

    async def connect(self, cfg: dict) -> None:
        """Create the host for ``cfg`` and connect it.

        On failure the client records ``last_error``, restores a disconnected
        state, and re-raises so the lifecycle can decide how to represent the
        attempt.
        """
        self._config = dict(cfg)
        self.disconnected = False
        host = self._host_factory({"servers": {self.server_name: cfg}})
        try:
            await host.connect({self.server_name: cfg})
        except Exception as e:
            self._host = None
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"
            try:
                await host.disconnect()
            except Exception:
                pass
            raise
        self._host = host
        self.connected = True
        self.last_error = None

    async def list_tools(self) -> list[Callable]:
        """Ask the connected server for its tool wrappers."""
        if self._host is None:
            return []
        return await self._host.get_tool_wrappers()

    async def close(self) -> None:
        """Disconnect + drop the session. Idempotent and never raises."""
        host, self._host = self._host, None
        if host is not None:
            try:
                await host.disconnect()
            except Exception:
                pass
        self.connected = False
        self.disconnected = True
        self._config = {}