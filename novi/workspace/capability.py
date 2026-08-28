"""Workspace capability boundary — READ / WRITE / EXECUTE.

Beta only READ is enabled. Interfaces are ready for WRITE/EXECUTE
without rewriting the agent architecture.
"""

from enum import Enum


class WorkspaceCapability(str, Enum):
    READ = "READ"      # list, search, read
    WRITE = "WRITE"    # create, modify, rename, delete — requires explicit confirm
    EXECUTE = "EXECUTE"  # run commands/scripts/tests — disabled in beta

    @classmethod
    def enabled_for_beta(cls, cap: "WorkspaceCapability") -> bool:
        return cap == cls.READ

    @classmethod
    def from_str(cls, s: str) -> "WorkspaceCapability":
        try:
            return cls(s.upper())
        except ValueError:
            return cls.READ
