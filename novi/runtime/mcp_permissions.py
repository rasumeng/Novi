"""MCP server permission gate — augments, never replaces, the permission engine.

M5.4: ``mcp.servers.<name>.permissions`` (``{operation_key: bool}``) existed in
configuration but was never consumed. This gate connects that configuration
into the EXISTING permission path: ``ToolExecutor._check_permission`` consults
it and only DENIES when the governing MCP server explicitly forbids the
operation. It never force-allows — a tool that passes the gate still flows
through the unchanged PermissionResolver / tool-risk / session rules, so MCP
server permissions augment (deny within) the existing system rather than
creating a second one.

The gate is configuration-derived and stateless: it holds no connections, no
credentials, and nothing persisted. ``refresh()`` re-derives it from the current
config snapshot, so Configuration Framework V2 stays the single authority.
"""

from __future__ import annotations

import re
from typing import Optional

# Operation classification — maps an MCP tool name to the permission keys the
# UI renders (read / write / delete / execute). Conservative default is
# ``execute`` so anything ambiguous is governed only by explicit, broader rules.
_READ_HINTS = (
    "read", "list", "search", "get", "query", "fetch", "find", "peek",
    "inspect", "show", "view", "info", "status", "describe", "stat",
    "lookup", "query", "load", "open", "select",
)
_WRITE_HINTS = (
    "write", "create", "insert", "update", "upsert", "edit", "patch", "save",
    "commit", "push", "upload", "modify", "add", "set", "submit", "post",
    "send", "append",
)
_DELETE_HINTS = (
    "delete", "remove", "destroy", "drop", "purge", "clear", "terminate",
    "kill", "close", "reset", "wipe",
)

_TOKEN_RE = re.compile(r"[^a-z0-9]")


def _normalize(name: str) -> list[str]:
    return [t for t in _TOKEN_RE.split(name.lower()) if t]


def classify_operation(tool_name: str) -> str:
    """Map an MCP tool name to ``read``/``write``/``delete``/``execute``.

    Order matters: delete is the most impactful, then write, then read;
    anything unrecognised defaults to ``execute``.
    """
    tokens = _normalize(tool_name)
    for hint in _DELETE_HINTS:
        if any(hint in tok for tok in tokens):
            return "delete"
    for hint in _WRITE_HINTS:
        if any(hint in tok for tok in tokens):
            return "write"
    for hint in _READ_HINTS:
        if any(hint in tok for tok in tokens):
            return "read"
    return "execute"


class MCPPermissionGate:
    """Config-derived deny gate for ``mcp.servers.<name>.permissions``."""

    def __init__(self, mcp_cfg: Optional[dict] = None):
        self._servers: dict[str, dict[str, bool]] = {}
        self.refresh(mcp_cfg or {})

    def refresh(self, mcp_cfg: dict) -> None:
        """Re-derive server policies from the ``mcp`` config section."""
        servers: dict[str, dict[str, bool]] = {}
        raw = (mcp_cfg or {}).get("servers") if isinstance(mcp_cfg, dict) else None
        if isinstance(raw, dict):
            for name, cfg in raw.items():
                perms = cfg.get("permissions") if isinstance(cfg, dict) else None
                stored: dict[str, bool] = {}
                if isinstance(perms, dict):
                    for key, value in perms.items():
                        if isinstance(value, bool):
                            stored[str(key)] = value
                servers[str(name)] = stored
        self._servers = servers

    def decision(self, tool_name: str) -> Optional[str]:
        """``"deny"`` when the governing server forbids the operation, else None.

        ``None`` means the server does not explicitly constrain this tool and
        the caller (ToolExecutor) must continue through the existing permission
        engine. Exact tool-name keys win over operation classification.
        """
        server = self._server_for(tool_name)
        if server is None:
            return None
        perms = self._servers[server]
        if not perms:
            return None
        if tool_name in perms:
            return "deny" if perms[tool_name] is False else None
        enabled = perms.get(classify_operation(tool_name), True)
        return "deny" if enabled is False else None

    def server_for(self, tool_name: str) -> Optional[str]:
        return self._server_for(tool_name)

    def _server_for(self, tool_name: str) -> Optional[str]:
        best: Optional[str] = None
        for name in self._servers:
            if tool_name.startswith(name + "_"):
                if best is None or len(name) > len(best):
                    best = name
        return best

    def policies(self) -> dict[str, dict[str, bool]]:
        """Current derived policies (safe, boolean flags only) — diagnostic use."""
        return {name: dict(perms) for name, perms in self._servers.items()}