"""Workspace — explicit local folder grant, READ/WRITE/EXECUTE boundary."""

from .capability import WorkspaceCapability
from .service import WorkspaceService

__all__ = ["WorkspaceService", "WorkspaceCapability"]
