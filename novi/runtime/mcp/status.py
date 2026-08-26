"""MCP status/health seam — observe lifecycle + discovery, report safe state.

Status is a READ-ONLY observer of the lifecycle and discovery seams. It is not
a second lifecycle owner: it never starts, stops, reconnects, or mutates
runtime state (beyond updating probe timing bookkeeping already owned by the
lifecycle).

Safe surface only: server name, enabled, connected, lifecycle state, error
state, discovered tool count, tool name/description. Never env values,
credentials, tokens, sessions, subprocess internals, or raw secret config
(M5.2 redaction boundary).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from novi.configuration.bootstrap import build_registry
from novi.configuration.redaction import ConfigRedactor

from .discovery import MCPToolDiscovery
from .lifecycle import MCPLifecycle


class MCPStatus:
    def __init__(
        self,
        lifecycle: MCPLifecycle,
        discovery: MCPToolDiscovery,
        redactor: ConfigRedactor | None = None,
    ):
        self._lifecycle = lifecycle
        self._discovery = discovery
        self._redactor = redactor or ConfigRedactor(build_registry())

    # ── per-server status ──────────────────────────────────────────

    def get_status(self) -> dict[str, dict]:
        lc = self._lifecycle
        with lc.lock:
            names = sorted(set(lc.server_names) | set(lc.server_errors))
            tool_cache = {
                n: [dict(t) for t in self._discovery.tools_for(n)] for n in names
            }
            errors = dict(lc.server_errors)
        if not lc.loop or lc.loop.is_closed():
            keys = names or list(self._discovery.server_tools.keys())
            return {
                n: {"status": "disconnected", "tools": tool_cache.get(n, [])}
                for n in keys
            }
        return self._run_on_loop(self._get_status, names, tool_cache, errors)

    def health_check(self) -> dict[str, str]:
        lc = self._lifecycle
        with lc.lock:
            names = sorted(set(lc.server_names) | set(lc.server_errors))
            errors = dict(lc.server_errors)
        if not lc.loop or lc.loop.is_closed():
            return {n: "disconnected" for n in names}
        return self._run_on_loop(self._health_check, names, errors)

    async def _get_status(self, names, tool_cache, errors) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for name in names:
            if name in errors:
                result[name] = {"status": "error", "tools": tool_cache.get(name, [])}
                continue
            status = await self._lifecycle._probe_one(name)
            result[name] = {"status": status, "tools": tool_cache.get(name, [])}
        return result

    async def _health_check(self, names, errors) -> dict[str, str]:
        status: dict[str, str] = {}
        for name in names:
            status[name] = "error" if name in errors else await self._lifecycle._probe_one(name)
        return status

    # ── lifecycle summary ──────────────────────────────────────────

    def get_lifecycle(self) -> dict:
        """Safe lifecycle summary. Never exposes config, env, or secrets."""
        lc = self._lifecycle
        with lc.lock:
            return {
                "enabled": bool(lc.enabled),
                "state": lc.state,
                "running": lc.state == "running",
                "servers": {
                    name: {
                        "enabled": True,
                        "connected": name in lc.clients,
                    }
                    for name in sorted(lc.configured)
                },
            }

    # ── server detail ──────────────────────────────────────────────

    def get_server_detail(self, name: str) -> dict | None:
        """Rich per-connector detail with diagnostics.

        Returns None when the server is unknown to the runtime.
        """
        lc = self._lifecycle
        with lc.lock:
            cfg = lc.server_configs.get(name)
            startup = lc._server_startup_time.get(name)
            last_ping = lc._server_last_ping.get(name)
            response = lc._server_response_time.get(name)
            tools = [dict(t) for t in self._discovery.tools_for(name)]
        if cfg is None and not lc.server_errors.get(name):
            return None
        status = lc.probe(name) if (lc.loop and not lc.loop.is_closed()) else "disconnected"
        env = cfg.get("env", {}) if cfg else {}
        redacted_env = self._redactor.redact(f"mcp.servers.{name}.env", env)
        return {
            "name": name,
            "status": status,
            "tools": tools,
            "config": {
                "command": cfg.get("command", "") if cfg else "",
                "args": cfg.get("args", []) if cfg else [],
                "env": redacted_env,
            },
            "diagnostics": {
                "transport": "stdio",
                "startup_time_ms": round((time.time() - startup) * 1000) if startup else None,
                "last_connected": datetime.fromtimestamp(startup, tz=timezone.utc).isoformat() if startup else None,
                "last_ping": datetime.fromtimestamp(last_ping, tz=timezone.utc).isoformat() if last_ping else None,
                "response_time_ms": response,
            },
        }

    # ── helper ─────────────────────────────────────────────────────

    def _run_on_loop(self, fn, *args):
        return self._lifecycle.run_coro(fn(*args))