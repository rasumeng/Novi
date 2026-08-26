"""MCP seams (M5.5) — decomposition of the former all-in-one MCPManager.

Architecture:

    Connector Registry (thin identity/status, M5.4)
            │
            ▼
      MCPLifecycle (lifecycle seam)
            │
       ┌────┴───────────┐
       ▼                ▼
  MCPRuntimeClient   MCPToolDiscovery
   (runtime seam)     (discovery seam)
       │                │
       ▼                ▼
    MCPHost          ToolRegistry

MCPStatus observes lifecycle + runtime + discovery; it never owns lifecycle.

Configuration stays authoritative and MCP stays runtime-only: the seams hold
no persisted state, write nothing on their own, and reconstruct everything
from a configuration snapshot on start/reconcile.
"""

from .discovery import MCPToolDiscovery
from .lifecycle import MCPLifecycle
from .runtime_client import MCPRuntimeClient
from .status import MCPStatus

__all__ = [
    "MCPLifecycle",
    "MCPRuntimeClient",
    "MCPToolDiscovery",
    "MCPStatus",
]