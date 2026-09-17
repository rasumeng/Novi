"""Typed, provider-facing transcript contracts for a single agent run."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContentBlockType(str, Enum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ATTACHMENT = "attachment"


@dataclass(frozen=True)
class ContentBlock:
    type: ContentBlockType
    text: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    artifact_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class TranscriptMessage:
    id: str
    role: MessageRole
    blocks: tuple[ContentBlock, ...]
    visible_to_user: bool = True
    source: str = "user"
    trust: str = "trusted"

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TranscriptMessage":
        return cls(
            id=str(value["id"]),
            role=MessageRole(value["role"]),
            blocks=tuple(
                ContentBlock(
                    type=ContentBlockType(block["type"]),
                    text=block.get("text"),
                    call_id=block.get("call_id"),
                    tool_name=block.get("tool_name"),
                    arguments=block.get("arguments"),
                    result=block.get("result"),
                    artifact_id=block.get("artifact_id"),
                )
                for block in value.get("blocks", ())
            ),
            visible_to_user=bool(value.get("visible_to_user", True)),
            source=str(value.get("source", "user")),
            trust=str(value.get("trust", "trusted")),
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
