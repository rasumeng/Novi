"""MCP provider — persistent server connections in a background event loop."""

import asyncio
import threading
import time

from ..mcp_host import MCPHost
from . import Provider


class MCPManager(Provider):
    """Manages long-lived MCP connections and registers tools in the ToolRegistry.

    Extends Provider base class. Connects on startup, keeps connections alive
    across chat sessions, supports health checks and per-server reconnect.

    M5.3 lifecycle:
      * ``mcp.enabled`` (and per-server ``enabled``) are authoritative. When
        disabled, no loop is created, no connections are made, and no MCP tools
        are registered.
      * ``start`` / ``refresh_from_config`` reconcile runtime state against the
        configured intent; reapplying unchanged configuration is a no-op.
      * ``stop`` disconnects every host, unregisters the MCP tools it owns, and
        stops the background loop. Safe when never started; idempotent.

    MCP remains stateless/runtime-only: every field here is in-memory and
    disappears on shutdown. Configuration (config.toml) is never rewritten by
    the runtime — configuration intent is the only thing persisted.
    """

    def __init__(self, registry):
        self._registry = registry
        self._hosts: dict[str, MCPHost] = {}
        self._server_configs: dict[str, dict] = {}
        self._server_tools: dict[str, list[dict]] = {}
        self._server_startup_time: dict[str, float] = {}
        self._server_last_ping: dict[str, float] = {}
        self._server_response_time: dict[str, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._server_names: set[str] = set()
        self._configured: set[str] = set()
        self._enabled: bool = False
        self._state: str = "stopped"
        self._lock = threading.Lock()

    # ── lifecycle ──────────────────────────────────────────────────

    def start(self, config: dict, registry=None) -> None:
        """Connect configured MCP servers when ``mcp.enabled`` is true."""
        if registry is not None:
            self._registry = registry
        self.refresh_from_config(config)

    def refresh_from_config(self, config: dict) -> None:
        """Reconcile runtime connections against the configured intent."""
        with self._lock:
            self._apply_config(config)

    def _apply_config(self, config: dict) -> None:
        enabled = bool(config.get("mcp", {}).get("enabled", True))
        self._enabled = enabled
        servers = config.get("mcp", {}).get("servers", {}) or {}
        # Per-server ``enabled`` is a real config leaf (``mcp.servers.<name>``
        # is a registered namespace). A falsy value keeps that server out of
        # the runtime entirely.
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

    # ── connection helpers ─────────────────────────────────────────

    async def _connect_all(self, servers: dict) -> None:
        for name, cfg in servers.items():
            await self._connect_one(name, cfg)

    async def _connect_one(self, name: str, cfg: dict) -> None:
        try:
            now = time.time()
            host = MCPHost({"servers": {name: cfg}})
            await host.connect({name: cfg})
            self._hosts[name] = host
            self._server_configs[name] = cfg
            self._server_names.add(name)
            self._server_startup_time[name] = now
            self._server_last_ping[name] = now
            wrappers = await host.get_tool_wrappers()
            tools: list[dict] = []
            for w in wrappers:
                tools.append({"name": w.__name__, "description": w.__doc__ or ""})
                sync_fn = self._make_sync(w)
                self._registry.register(w.__name__, sync_fn, w.__doc__)
            self._server_tools[name] = tools
        except Exception:
            self._server_tools[name] = []

    def _make_sync(self, async_fn):
        def sync_fn(**kwargs):
            future = asyncio.run_coroutine_threadsafe(
                async_fn(**kwargs), self._loop
            )
            return future.result()
        return sync_fn

    def _connect_server(self, name: str, cfg: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._connect_one(name, cfg), loop
        )
        try:
            future.result()
        except Exception:
            pass

    def _disconnect_server(self, name: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._disconnect_one(name), loop
        )
        try:
            future.result()
        except Exception:
            pass

    def _reconnect_server(self, name: str, cfg: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._reconnect_one(name, cfg), loop
        )
        try:
            future.result()
        except Exception:
            pass

    async def _disconnect_one(self, name: str) -> None:
        host = self._hosts.pop(name, None)
        if host:
            try:
                await host.disconnect()
            except Exception:
                pass
        self._unregister_server_tools(name)
        self._server_names.discard(name)
        self._server_configs.pop(name, None)
        self._server_tools.pop(name, None)
        self._server_startup_time.pop(name, None)
        self._server_last_ping.pop(name, None)
        self._server_response_time.pop(name, None)

    def _disconnect_all_sync(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            self._unregister_all_tools()
            self._clear_runtime_state()
            return
        future = asyncio.run_coroutine_threadsafe(
            self._disconnect_all(), loop
        )
        try:
            future.result(timeout=10)
        except Exception:
            pass

    async def _disconnect_all(self) -> None:
        for name, host in self._hosts.items():
            try:
                await host.disconnect()
            except Exception:
                pass
        for name in list(self._server_names):
            self._unregister_server_tools(name)
        self._clear_runtime_state()

    def _clear_runtime_state(self) -> None:
        self._hosts.clear()
        self._server_configs.clear()
        self._server_tools.clear()
        self._server_startup_time.clear()
        self._server_last_ping.clear()
        self._server_response_time.clear()
        self._server_names.clear()
        self._configured = set()

    # ── tool registration cleanup ──────────────────────────────────

    def _unregister_all_tools(self) -> None:
        for name in list(self._server_tools.keys()):
            self._unregister_server_tools(name)

    def _unregister_server_tools(self, name: str) -> None:
        for tool in self._server_tools.get(name, []):
            try:
                self._registry.unregister(tool["name"])
            except Exception:
                pass

    # ── stop ───────────────────────────────────────────────────────

    def stop(self) -> None:
        """Disconnect every MCP host, drop MCP tools, stop the background loop.

        Idempotent and safe when MCP was never started.
        """
        with self._lock:
            self._disconnect_all_sync()
            self._stop_loop()
            self._state = "stopped"

    # ── status ─────────────────────────────────────────────────────

    def get_status(self) -> dict[str, dict]:
        """Return per-server status with tools list.

        Returns:
            {"<server>": {"status": "ok", "tools": [{"name":..., "description":...}]}}
        """
        with self._lock:
            loop = self._loop
            names = list(self._server_names)
            tool_cache = {n: list(self._server_tools.get(n, [])) for n in names}
        if not loop or loop.is_closed():
            keys = names or list(self._server_tools.keys())
            return {
                n: {"status": "disconnected", "tools": tool_cache.get(n, [])}
                for n in keys
            }
        future = asyncio.run_coroutine_threadsafe(
            self._get_status(names), loop
        )
        return future.result()

    async def _get_status(self, names: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for name in names:
            host = self._hosts.get(name)
            if not host:
                result[name] = {"status": "disconnected", "tools": self._server_tools.get(name, [])}
                continue
            try:
                await host.get_tool_wrappers()
                result[name] = {"status": "ok", "tools": self._server_tools.get(name, [])}
            except Exception:
                result[name] = {"status": "error", "tools": self._server_tools.get(name, [])}
        return result

    def get_lifecycle(self) -> dict:
        """Safe lifecycle summary. Never exposes config, env, or secrets."""
        with self._lock:
            return {
                "enabled": bool(self._enabled),
                "state": self._state,
                "running": self._state == "running",
                "servers": {
                    name: {
                        "enabled": True,
                        "connected": name in self._hosts,
                    }
                    for name in sorted(self._configured)
                },
            }

    # ── server detail ─────────────────────────────────────────────

    def get_server_detail(self, name: str) -> dict | None:
        """Return rich per-connector detail with diagnostics.

        Returns None if server not found.
        """
        with self._lock:
            cfg = self._server_configs.get(name)
            startup = self._server_startup_time.get(name)
            last_ping = self._server_last_ping.get(name)
            response = self._server_response_time.get(name)
            tools = list(self._server_tools.get(name, []))
            loop = self._loop
        if cfg is None:
            return None
        if not loop or loop.is_closed():
            stats = "disconnected"
        else:
            future = asyncio.run_coroutine_threadsafe(
                self._probe_server(name), loop
            )
            try:
                stats = future.result()
            except Exception:
                stats = "error"
        from datetime import datetime, timezone
        return {
            "name": name,
            "status": stats,
            "tools": tools,
            "config": {
                "command": cfg.get("command", ""),
                "args": cfg.get("args", []),
                "env": cfg.get("env", {}),
            },
            "diagnostics": {
                "transport": "stdio",
                "startup_time_ms": round((time.time() - startup) * 1000) if startup else None,
                "last_connected": datetime.fromtimestamp(startup, tz=timezone.utc).isoformat() if startup else None,
                "last_ping": datetime.fromtimestamp(last_ping, tz=timezone.utc).isoformat() if last_ping else None,
                "response_time_ms": response,
            },
        }

    async def _probe_server(self, name: str) -> str:
        host = self._hosts.get(name)
        if not host:
            return "disconnected"
        try:
            t0 = time.time()
            await host.get_tool_wrappers()
            elapsed = (time.time() - t0) * 1000
            self._server_response_time[name] = round(elapsed, 1)
            self._server_last_ping[name] = time.time()
            return "ok"
        except Exception:
            return "error"

    # ── health_check ───────────────────────────────────────────────

    def health_check(self) -> dict[str, str]:
        with self._lock:
            loop = self._loop
            names = list(self._server_names)
        if not loop or loop.is_closed():
            return {n: "disconnected" for n in names}
        future = asyncio.run_coroutine_threadsafe(
            self._health_check(names), loop
        )
        return future.result()

    async def _health_check(self, names: list[str]) -> dict[str, str]:
        status: dict[str, str] = {}
        for name in names:
            host = self._hosts.get(name)
            if not host:
                status[name] = "disconnected"
                continue
            try:
                t0 = time.time()
                await host.get_tool_wrappers()
                elapsed = (time.time() - t0) * 1000
                self._server_response_time[name] = round(elapsed, 1)
                self._server_last_ping[name] = time.time()
                status[name] = "ok"
            except Exception:
                status[name] = "error"
        return status

    # ── reconnect ──────────────────────────────────────────────────

    def reconnect(self, server_name: str) -> bool:
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        cfg = self._server_configs.get(server_name)
        if not cfg:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._reconnect_one(server_name, cfg), loop
        )
        return future.result()

    async def _reconnect_one(self, name: str, cfg: dict) -> bool:
        host = self._hosts.pop(name, None)
        if host:
            try:
                await host.disconnect()
            except Exception:
                pass
        self._unregister_server_tools(name)
        try:
            await self._connect_one(name, cfg)
            return True
        except Exception:
            return False

    # ── refresh (re-discover tools) ────────────────────────────────

    def refresh(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._refresh_tools(), loop
        )
        future.result()

    async def _refresh_tools(self) -> None:
        for name in self._server_names:
            host = self._hosts.get(name)
            if not host:
                continue
            try:
                wrappers = await host.get_tool_wrappers()
                tools: list[dict] = []
                for w in wrappers:
                    tools.append({"name": w.__name__, "description": w.__doc__ or ""})
                    sync_fn = self._make_sync(w)
                    self._registry.register(w.__name__, sync_fn, w.__doc__)
                self._server_tools[name] = tools
            except Exception:
                pass
