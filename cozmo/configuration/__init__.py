"""Cozmo Configuration Framework (Milestone 4.5).

A first-class subsystem that owns configuration: registry (schema),
persistence, runtime state, validation, change events, migration, and model
discovery. Every subsystem (runtime, memory, brain, MCP, skills, providers)
integrates by registration, and the Settings UI is a presentation layer over it.

See docs/architecture/configuration-framework.md for the design.
"""

from .schema import Category, Option, Setting, SettingGroup, SettingType, Visibility
from .events import ConfigBus, ConfigEvent, get_bus
from .registry import (
    ConfigRegistry,
    DuplicateSettingError,
    UnknownSettingError,
)
from .manager import Configuration, ConfigurationError, ValidationError

__all__ = [
    "Category",
    "Option",
    "Setting",
    "SettingGroup",
    "SettingType",
    "Visibility",
    "ConfigBus",
    "ConfigEvent",
    "get_bus",
    "ConfigRegistry",
    "DuplicateSettingError",
    "UnknownSettingError",
    "Configuration",
    "ConfigurationError",
    "ValidationError",
]