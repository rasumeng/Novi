"""
Capability — a declarable unit of functionality.

Each capability bundles:
  - Tools required
  - Permissions needed
  - Preferred model capability
  - Planner strategy
  - Risk level
  - Dependencies on other capabilities
  - MCP server requirements

Capabilities are composable. A task resolves to a set of capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Capability:
    """Declarative capability definition."""

    id: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    optional_tools: list[str] = field(default_factory=list)
    required_permissions: dict = field(default_factory=dict)
    planner_strategy: str = ""
    risk: str = "low"
    dependencies: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    template_patterns: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "tools": self.tools,
            "optional_tools": self.optional_tools,
            "planner_strategy": self.planner_strategy,
            "risk": self.risk,
            "dependencies": self.dependencies,
            "mcp_servers": self.mcp_servers,
        }
