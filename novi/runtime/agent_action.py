from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

_LOOP_DONE_SENTINEL = "__plan_step_done__"


class AgentActionType(Enum):
    CONTINUE = "continue"
    PROGRESS = "progress"
    FINISH = "finish"


@dataclass
class AgentAction:
    type: AgentActionType
    message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --- backward-compat tuple interface ---
    # Legacy callers treat terminal as (_LOOP_DONE, final, stop_reason, success).
    # Make AgentAction behave like that tuple for equality and indexing so
    # existing parity tests and runtime.py's `chunk[0] == _LOOP_DONE` continue
    # to work while new code uses isinstance checks.
    def __iter__(self):  # type: ignore[override]
        stop_reason = self.metadata.get("stop_reason", "completed" if self.type == AgentActionType.FINISH else "needs_continuation")
        success = self.metadata.get("success", self.type != AgentActionType.CONTINUE or self.type == AgentActionType.FINISH)
        # For CONTINUE, success may be True for continuation
        if self.type == AgentActionType.CONTINUE:
            success = self.metadata.get("success", True)
        yield _LOOP_DONE_SENTINEL
        yield self.message or ""
        yield stop_reason
        yield bool(success)

    def __getitem__(self, key):
        # String key for dict-like access (runtime_graph legacy)
        if isinstance(key, str):
            if key == "answer":
                return self.message or ""
            if key == "completion_reason":
                return self.metadata.get("stop_reason", "completed" if self.type == AgentActionType.FINISH else "needs_continuation")
            if key in self.metadata:
                return self.metadata[key]
            # Proxy to original result dict stored in metadata for legacy callers
            res = self.metadata.get("result")
            if isinstance(res, dict) and key in res:
                return res[key]
            raise KeyError(key)
        return tuple(self)[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except Exception:
            # Also check result proxy
            res = self.metadata.get("result") if isinstance(self.metadata.get("result"), dict) else None
            if res is not None and key in res:
                return res[key]
            return default

    def __len__(self):
        return 4

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple):
            try:
                return tuple(self) == other
            except Exception:
                return False
        if isinstance(other, AgentAction):
            return self.type == other.type and self.message == other.message and self.metadata == other.metadata
        return False
