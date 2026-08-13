"""MCP lifecycle seam — decide which servers run and drive their runtime.

Owns the lifecycle of configured MCP servers:

* determine which configured servers should be running (config is authority)
* start enabled servers / stop disabled or removed servers
* reconnect when configuration requires it
* coordinate runtime client creation/destruction
* own the background event loop the clients live on
* expose lifecycle state and shut down cleanly

It does NOT implement MCP protocol calls (that is the runtime client seam /
MCPHost), execute tools, evaluate permissions, persist configuration, or own
the Connector Registry. Discovery of tools is delegated to the discovery seam.

MCP remains runtime-only: every field here is in-memory and disappears on
shutdown. This code never writes configuration back.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

from ..mcp_host import MCPHost
from .discovery import MCPToolDiscovery
from .runtime_client import MCPRuntimeClient


class MCPLifecycle:
    def __init__(
        self,
        discovery: MCPToolDiscovery,
        host_factory: Callable = MCPHost,
        runtime_client_factory: Callable = MCPRuntimeClient,
    ):
        self.discovery = discovery
        discovery._make_sync = self._make_sync
        self._host_factory = host_factory
        self._client_factory = runtime_client_factory
        self._clients: dict[str, MCPRuntimeClient] = {}
        self._server_configs: dict[str, dict] = {}
        self._server_names: set[str] = set()
        self._server_errors: dict[str, str] = {}
        self._server_startup_time: dict[str, float] = {}
        self._server_last_ping: dict[str, float] = {}
        self._server_response_time: dict[str, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._configured: set[str] = set()
        self._enabled: bool = False
        self._state: str = "stopped"
        self._lock = threading.Lock()

    # ── lifecycle entry points ─────────────────────────────────────

    def start(self, config: dict) -> None:
        """Reconcile runtime against the configured intent."""
        self.refresh_from_config(config)

    def refresh_from_config(self, config: dict) -> None:
        """Reconcile connections against the configured intent. Idempotent."""
        with self._lock:
            self._apply_config(config)

    def stop(self) -> None:
        """Disconnect every client, drop their tools, stop the loop.

        Idempotent and safe when never started or partially initialized.
        """
        with self._lock:
            self._disconnect_all_sync()
            self._stop_loop()
            self._state = "stopped"

    # ── reconciliation ─────────────────────────────────────────────

    def _apply_config(self, config: dict) -> None:
        enabled = bool(config.get("mcp", {}).get("enabled", True))
        self._enabled = enabled
        servers = config.get("mcp", {}).get("servers", {}) or {}
        active = {
            name: cfg for name, cfg in servers.items() if cfg.get("enabled", True)
        }
        self._configured = set(active.keys())

        if not enabled or not active:
            self._disconnect_all_sync()
            self._stop_loop()
            self._state = "stopped"
            return

        if self._loop is None or self._loop.is_closed():
            self._state = "starting"
            self._ensure_loop()

        new_names = set(active.keys())
        current_names = set(self._server_names)

        for name in sorted(current_names - new_names):
            self._disconnect_server(name)
        for name in sorted(new_names - current_names):
            self._connect_server(name, active[name])
        for name in sorted(new_names & current_names):
            if self._server_configs.get(name) != active[name]:
                self._reconnect_server(name, active[name])
        self._state = "running"

    # ── loop ownership ─────────────────────────────────────────────

    def _ensure_loop(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            return
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

    def _stop_loop(self) -> None:
        loop, thread = self._loop, self._loop_thread
        self._loop = None
        self._loop_thread = None
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            return
        if thread:
            thread.join(timeout=5)

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_coro(self, coro) -> object:
        """Schedule a coroutine on the lifecycle loop and wait for it.

        Raises RuntimeError when the loop is not running.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError("MCP lifecycle loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def _make_sync(self, async_fn) -> Callable:
        def sync_fn(**kwargs):
            future = asyncio.run_coroutine_threadsafe(async_fn(**kwargs), self._loop)
            return future.result()
        return sync_fn

    # ── connection coordination ────────────────────────────────────

    def _connect_server(self, name: str, cfg: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._connect_one(name, cfg), loop)
        try:
            future.result()
        except Exception:
            pass

    def _disconnect_server(self, name: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._disconnect_one(name), loop)
        try:
            future.result()
        except Exception:
            pass

    def _reconnect_server(self, name: str, cfg: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._reconnect_one(name, cfg), loop)
        try:
            future.result()
        except Exception:
            pass

    async def _connect_one(self, name: str, cfg: dict) -> None:
        client = self._client_factory(name, host_factory=self._host_factory)
        try:
            await client.connect(cfg)
        except Exception:
            self._server_errors[name] = client.last_error or "connect failed"
            self._server_tools_empty(name)
            return
        now = time.time()
        self._clients[name] = client
        self._server_configs[name] = cfg
        self._server_names.add(name)
        self._server_errors.pop(name, None)
        self._server_startup_time[name] = now
        self._server_last_ping[name] = now
        try:
            wrappers = await client.list_tools()
            self.discovery.discover(name, wrappers)
        except Exception:
            self._server_errors[name] = "tool discovery failed"
            self._server_tools_empty(name)

    def _server_tools_empty(self, name: str) -> None:
        self.discovery.unregister_server(name)
        self.discovery.server_tools[name] = []

    async def _disconnect_one(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
        self.discovery.unregister_server(name)
        self._server_names.discard(name)
        self._server_configs.pop(name, None)
        self._server_errors.pop(name, None)
        self._server_startup_time.pop(name, None)
        self._server_last_ping.pop(name, None)
        self._server_response_time.pop(name, None)

    async def _reconnect_one(self, name: str, cfg: dict) -> bool:
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
        self.discovery.unregister_server(name)
        self._server_names.discard(name)
        self._server_configs.pop(name, None)
        try:
            await self._connect_one(name, cfg)
            return True
        except Exception:
            return False

    async def _disconnect_all(self) -> None:
        for name, client in self._clients.items():
            try:
                await client.close()
            except Exception:
                pass
        self.discovery.unregister_all()
        self._clear_runtime_state()

    def _disconnect_all_sync(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            self.discovery.unregister_all()
            self._clear_runtime_state()
            return
        future = asyncio.run_coroutine_threadsafe(self._disconnect_all(), loop)
        try:
            future.result(timeout=10)
        except Exception:
            pass

    def _clear_runtime_state(self) -> None:
        self._clients.clear()
        self._server_configs.clear()
        self._server_names.clear()
        self._server_errors.clear()
        self._server_startup_time.clear()
        self._server_last_ping.clear()
        self._server_response_time.clear()
        self._configured = set()

    # ── operational helpers (used by the status seam) ──────────────

    def probe(self, name: str) -> str:
        """Health probe for one server on the lifecycle loop."""
        try:
            return self.run_coro(self._probe_one(name))
        except Exception:
            return "error"

    async def _probe_one(self, name: str) -> str:
        client = self._clients.get(name)
        if client is None or client.host is None:
            return "disconnected"
        try:
            t0 = time.time()
            await client.list_tools()
            elapsed = (time.time() - t0) * 1000
            self._server_response_time[name] = round(elapsed, 1)
            self._server_last_ping[name] = time.time()
            return "ok"
        except Exception:
            return "error"

    def reconnect(self, server_name: str) -> bool:
        """Manually reconnect one server. Returns success."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        cfg = self._server_configs.get(server_name)
        if not cfg:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._reconnect_one(server_name, cfg), loop
        )
        try:
            return future.result()
        except Exception:
            return False

    def refresh(self) -> None:
        """Re-discover tools for all connected servers. Idempotent."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._refresh_tools(), loop)
        try:
            future.result()
        except Exception:
            pass

    async def _refresh_tools(self) -> None:
        for name in list(self._server_names):
            client = self._clients.get(name)
            if client is None:
                continue
            try:
                wrappers = await client.list_tools()
                self.discovery.discover(name, wrappers)
            except Exception:
                self.discovery.unregister_server(name)

    # ── status observation (read-only) ─────────────────────────────

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    @property
    def clients(self) -> dict[str, MCPRuntimeClient]:
        return self._clients

    @property
    def server_names(self) -> set[str]:
        return self._server_names

    @property
    def server_configs(self) -> dict[str, dict]:
        return self._server_configs

    @property
    def server_errors(self) -> dict[str, str]:
        return self._server_errors

    @property
    def configured(self) -> set[str]:
        return self._configured

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def state(self) -> str:
        return self._state

    @property
    def lock(self) -> threading.Lock:
        return self._lock