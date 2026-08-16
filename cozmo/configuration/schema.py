"""Configuration schema — declarative definition of every configurable value.

A Setting is the single authoritative description of one configurable value.
The Settings UI renders registered settings; the runtime consumes registered
settings; validation and persistence are driven by the schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Category(str, Enum):
    GENERAL = "general"        # overview / status
    MODELS = "models"          # model selection + discovery
    AGENT = "agent"            # visibility + autonomy controls
    MEMORY = "memory"          # memory behavior + data controls
    SKILLS = "skills"          # skill management
    CONNECTORS = "connectors"  # connector management
    PERMISSIONS = "permissions"  # tool / permission policy
    DEVELOPER = "developer"    # expert / diagnostics / experimental
    # ADVANCED retained only for migration/back-compat of legacy registrations.
    # No first-party setting uses it today; expert/internal settings live under
    # DEVELOPER. Do not surface this category in the user-facing Settings IA.
    ADVANCED = "advanced"      # legacy bucket (migration only)


class SettingType(str, Enum):
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"
    MODEL = "model"
    SECRET = "secret"
    JSON = "json"


class Visibility(str, Enum):
    USER = "user"
    ADVANCED = "advanced"
    DEVELOPER = "developer"
    HIDDEN = "hidden"


class Option:
    def __init__(self, value: Any, label: str, description: str = ""):
        self.value = value
        self.label = label
        self.description = description

    def to_dict(self) -> dict:
        return {"value": self.value, "label": self.label, "description": self.description}


Validator = Callable[[Any], str | None]


def require_nonempty(value: Any) -> str | None:
    if value is None or value == "":
        return "value is required"
    return None


def require_number(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "expected a number"
    if not isinstance(value, (int, float)):
        return f"expected a number, got {type(value).__name__}"
    return None


def require_nonnegative_int(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return "expected an integer"
    if value < 0:
        return "must be zero or greater"
    return None


@dataclass
class Setting:
    """One configurable value, owned by exactly one subsystem."""

    id: str
    category: Category
    owner: str
    label: str = ""
    type: SettingType = SettingType.STRING
    description: str = ""
    default: Any = None
    validation: list[Validator] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    restart_required: bool = False
    depends: list[str] = field(default_factory=list)
    visibility: Visibility = Visibility.USER
    # sensor: how the value enters the system. Only "direct" exists today.
    sensor: str = "direct"
    # namespace: when True, the setting id owns every descendant sub-path
    # (``<id>.<any>.<leaf>``). Used for dynamic collections such as per-server
    # MCP config or per-tool permissions whose leaves cannot be pre-registered.
    namespace: bool = False
    # Namespace secret classification (M5.2): when ``namespace`` is True, every
    # descendant value whose path includes one of these segments is treated as
    # a secret and masked on read surfaces (e.g. ``mcp.servers.<name>.env``).
    # Keeps dynamic collections redacted without registering every leaf.
    secret_segments: list[str] = field(default_factory=list)

    def validate(self, value: Any) -> list[str]:
        errors = []
        if value is None:
            return errors
        for v in self.validation:
            msg = v(value)
            if msg:
                errors.append(msg)
        return errors

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "category": self.category.value,
            "owner": self.owner,
            "type": self.type.value,
            "default": self.default,
            "options": [o.to_dict() for o in self.options],
            "restart_required": self.restart_required,
            "depends": self.depends,
            "visibility": self.visibility.value,
            "namespace": self.namespace,
            "secret_segments": list(self.secret_segments),
        }


@dataclass
class SettingGroup:
    """A named group of settings (e.g. ``llm.workloads``), registered by one owner."""

    key: str
    label: str
    category: Category
    owner: str
    description: str = ""
    settings: list[Setting] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "category": self.category.value,
            "owner": self.owner,
            "settings": [s.to_dict() for s in self.settings],
        }
