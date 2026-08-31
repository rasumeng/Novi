"""StableState — re-export shim for backwards compatibility.

Canonical definition lives in novi/common/execution_state.py.
This module re-exports it so existing imports keep working.
"""

from novi.common.execution_state import StableState  # noqa: F401

__all__ = ["StableState"]
