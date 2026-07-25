"""Provider abstractions for tool sources (MCP)."""

from abc import ABC, abstractmethod
from ..tool_registry import ToolRegistry


class Provider(ABC):
    """Base class for tool providers.

    Each provider contributes tools to the ToolRegistry.
    The runtime never knows which provider a tool came from.
    """

    @abstractmethod
    def start(self, config: dict, registry: ToolRegistry) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def refresh(self) -> None:
        ...


__all__ = ["Provider"]
