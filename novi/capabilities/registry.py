"""
CapabilityRegistry — central registry of all capabilities.

Resolves a task profile to the set of capabilities needed.
Eventually loaded from TOML files; Python-based initially.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import Capability

log = logging.getLogger("novi.capabilities")


class CapabilityRegistry:
    """Registry of capabilities. Queries: which capabilities solve this task?"""

    def __init__(self):
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability):
        self._capabilities[capability.id] = capability

    def get(self, cap_id: str) -> Optional[Capability]:
        return self._capabilities.get(cap_id)

    def list(self) -> list[Capability]:
        return list(self._capabilities.values())

    def resolve(self, capability_ids: list[str]) -> list[Capability]:
        """Resolve a list of capability IDs to their definitions,
        including resolved dependencies."""
        resolved = []
        seen = set()
        queue = list(capability_ids)
        while queue:
            cid = queue.pop(0)
            if cid in seen:
                continue
            cap = self._capabilities.get(cid)
            if cap is None:
                log.warning("unknown capability: %s", cid)
                continue
            seen.add(cid)
            resolved.append(cap)
            for dep in cap.dependencies:
                if dep not in seen:
                    queue.append(dep)
        return resolved

    def get_tool_names(self, capability_ids: list[str]) -> list[str]:
        """Get all tool names needed for the given capabilities."""
        caps = self.resolve(capability_ids)
        tools = set()
        for c in caps:
            tools.update(c.tools)
            tools.update(c.optional_tools)
        return list(tools)

    def check_conflicts(self, capability_ids: list[str]) -> list[str]:
        """Check for conflicts between capabilities. Returns conflict messages."""
        caps = self.resolve(capability_ids)
        conflicts = []
        for i, a in enumerate(caps):
            for b in caps[i + 1:]:
                if b.id in a.conflicts_with or a.id in b.conflicts_with:
                    conflicts.append(f"{a.id} conflicts with {b.id}")
        return conflicts
