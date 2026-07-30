"""RuntimeInterface — typed contract between consumers and CozmoRuntime.

WebUI (and any other consumer) interacts with the runtime exclusively
through this Protocol.  No direct access to private (``_``-prefixed)
attributes.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RuntimeInterface(Protocol):
    """Minimal public surface of CozmoRuntime.

    Any object satisfying this Protocol can be used in place of the
    concrete runtime — useful for testing, mocking, or alternate
    implementations.
    """

    # ── lifecycle ───────────────────────────────────────────────────────

    def run_stream(self,
                   user_input: str | None = None,
                   attachments: list[dict] | None = None,
                   force_mode: str | None = None,
                   agent_runtime: object | None = None,
                   force_capability: str | None = None,
                   force_model: str | None = None,
                   execution_plan: object | None = None,
                   context: object | None = None,
                   ) -> Generator:
        """Yield (kind, text) tuples.  Unified pipeline — no mode branching."""

    def run(self,
            user_input: str,
            attachments: list[dict] | None = None,
            ) -> str:
        """Synchronous run.  Returns the final answer text."""

    def reset(self) -> None:
        """Clear conversation state (new chat)."""

    # ── configuration ──────────────────────────────────────────────────

    def set_config(self, **kwargs: Any) -> None:
        """Apply one or more configuration values.

        Accepted keys:
          force_model         – override model name
          max_steps           – max ReAct iterations
          temperature         – model temperature
          agent_system_extra  – extra system-prompt text
          project_context     – project-context string
          project_index       – ProjectIndex instance
          permission_mode     – ``"manual"`` | ``"bypass"``
          stop_event          – ``threading.Event`` to signal stop
        """

    def get_status(self) -> dict:
        """Return a snapshot of current runtime state.

        Keys: ``model``, ``permission_mode``, ``project_loaded``.
        """

    # ── permission / plan callbacks ────────────────────────────────────

    def set_permission_callback(self, callback: Any) -> None:
        """Register a callback invoked for every tool permission check.

        ``callback(tool_name, args) -> bool``
        """

    def set_plan_callback(self, callback: Any) -> None:
        """Register a callback invoked when a plan needs user approval.

        ``callback(plan_text) -> bool``
        """
