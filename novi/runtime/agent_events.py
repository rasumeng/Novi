from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentEvent:
    type: str
    run_id: str
    conversation_id: str
    message_id: Optional[str] = None
    message: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[dict] = None
    result: Optional[str] = None
    error: Optional[str] = None
    phase: Optional[str] = None
    detail: Optional[str] = None
    call_id: Optional[str] = None
    category: Optional[str] = None
    diff: Optional[dict] = None

    def _as_legacy_tuple(self):
        if self.type == "message.delta": return ("token", self.message)
        if self.type == "reasoning": return ("reasoning", self.message)
        if self.type == "tool.started": return ("tool_call", self.tool, self.args, self.call_id, self.category)
        if self.type == "tool.completed": return ("tool_result", self.tool, self.result, self.call_id, self.diff)
        if self.type == "status": return ("thinking", self.message or "", self.detail or "", None)
        if self.type in ("message.started", "message.completed"):
            return (self.type, self.message_id, self.message)
        return (self.type, self.message)

    def __iter__(self): return iter(self._as_legacy_tuple())
    def __getitem__(self, idx): return self._as_legacy_tuple()[idx]
    def __len__(self): return len(self._as_legacy_tuple())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple) and other:
            kind = other[0]
            if kind == "token" and self.type == "message.delta": return len(other) >= 2 and other[1] == self.message
            if kind == "reasoning" and self.type == "reasoning": return len(other) >= 2 and other[1] == self.message
            if kind == "tool_call" and self.type == "tool.started": return len(other) >= 3 and other[1] == self.tool and other[2] == self.args
            if kind == "tool_result" and self.type == "tool.completed": return len(other) >= 3 and other[1] == self.tool and other[2] == self.result
            if kind == "thinking" and self.type == "status": return True
            return self._as_legacy_tuple() == other
        if isinstance(other, AgentEvent):
            return (self.type == other.type and self.message == other.message and
                    self.tool == other.tool and self.args == other.args and
                    self.result == other.result and self.detail == other.detail and
                    self.phase == other.phase)
        return False


EVENT_TYPES = {
    "run.started", "run.completed", "run.failed", "run.cancelled",
    "message.started", "message.delta", "message.completed", "tool.started",
    "tool.completed", "status", "reasoning", "context.compacting",
    "context.compacted", "plan.started", "plan.completed", "step.started",
    "step.completed",
}
