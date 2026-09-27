"""Strict typed AgentAction — no tuple/dict emulation."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class AgentActionType(Enum):
    CONTINUE = "continue"
    PROGRESS = "progress"
    FINISH = "finish"


@dataclass(frozen=True)
class AgentAction:
    type: AgentActionType
    message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
