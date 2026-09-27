"""RuntimeInterface — typed contract for the replacement runtime.

WebUI (and any other consumer) interacts with the runtime exclusively
through the RunService contract. NoviRuntime remains only as a bounded
collaborator for research/coding retrieval; direct run_stream is retired.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RuntimeInterface(Protocol):
    """Minimal public surface — now mirrors RunService."""

    def start(self, request: Any) -> str:
        """Start a run (RunRequest) and return run_id."""

    def subscribe(self, run_id: str, after_sequence: int = 0) -> tuple:
        """Replay ordered RunEvents."""

    def snapshot(self, run_id: str) -> Any:
        """Return current RunState."""

    def respond_permission(self, request_id: str, decision: Any) -> Any:
        """Resolve a pending permission."""

    def cancel(self, run_id: str) -> Any:
        """Cancel a running run."""

    # legacy compat — retired, kept only for type checking of bounded collaborators
    def get_status(self) -> dict: ...
    def set_config(self, **kw: Any) -> None: ...
