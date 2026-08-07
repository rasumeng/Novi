"""Dynamic model discovery — query local Ollama / providers for what actually exists.

Returns unified ``DiscoveredModel`` records with an install status. The UI
represents the user's real machine, never assumptions.
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("cozmo.config.discovery")


class ModelStatus(str, Enum):
    INSTALLED = "installed"
    AVAILABLE = "available"   # known/modeled as installable remote
    MISSING = "missing"       # referenced in config but not found


@dataclass
class DiscoveredModel:
    name: str
    provider: str = "ollama"
    status: ModelStatus = ModelStatus.INSTALLED
    size: int | None = None
    capability_flags: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "status": self.status.value,
            "size": self.size,
            "capabilities": self.capability_flags,
        }


def query_ollama_tags(url: str = "http://localhost:11434", timeout: float = 5.0) -> list[dict]:
    """Live query of ``/api/tags``. Returns raw model dicts; empty on failure."""
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            import json
            data = json.loads(resp.read())
            return data.get("models", [])
    except Exception as e:
        log.debug("ollama tags query failed: %s", e)
        return []


class ModelDiscovery:
    """Discovers installed/summary info from providers."""

    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url

    def installed(self) -> list[DiscoveredModel]:
        out = []
        for m in query_ollama_tags(self.ollama_url):
            out.append(
                DiscoveredModel(
                    name=m.get("name", ""),
                    provider="ollama",
                    status=ModelStatus.INSTALLED,
                    size=m.get("size"),
                    capability_flags=_infer_capabilities(m.get("name", "")),
                )
            )
        return out

    def installed_names(self) -> set[str]:
        return {m.name for m in self.installed() if m.name}

    def installed_map(self) -> dict[str, DiscoveredModel]:
        return {m.name: m for m in self.installed() if m.name}


def _parse_capabilities(name: str) -> dict[str, bool]:
    return _infer_capabilities(name)


def _infer_capabilities(name: str) -> dict[str, bool]:
    """Coarse capability inference from the model name/id.

    Pure heuristic; refined by the curated catalog via cross-reference.
    """
    low = name.lower()
    flags = {"tools": True, "vision": False, "reasoning": False, "coding": False}
    if any(k in low for k in ("llava", "-vl", "vision", "minicpm", "qwen2-vl")):
        flags["vision"] = True
    if any(k in low for k in ("coder", "codegemma")):
        flags["coding"] = True
    # Fallback: most modern instruct models can follow tool schemas.
    return flags