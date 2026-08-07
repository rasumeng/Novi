"""Experience presets — experiences, not models.

Light / Medium / Heavy / Custom. A preset maps to a *routing intent* (which
backend role should prefer the most capable / smallest installed model),
resolved dynamically against what is actually installed — presets never embed
hardcoded model names (milestone: no hardcoded models).

Custom exposes the full routing table for direct editing in one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from .catalog import KNOWN_MODEL_FACTS

log = logging.getLogger("cozmo.config.presets")


class Experience(str, Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    CUSTOM = "custom"


# Backend routing roles a preset may assign. Ordering = priority.
_ROLES = ["chat", "coder", "planner", "vision", "classifier", "router", "orchestrator"]


@dataclass
class ExperiencePreset:
    id: str
    label: str
    description: str
    role_profile: dict[str, list[str]] = field(default_factory=dict)
    lightweight_mode: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "lightweight": self.lightweight_mode,
        }


EXPERIENCE_PRESETS: dict[str, ExperiencePreset] = {}


def _register(p: ExperiencePreset):
    EXPERIENCE_PRESETS[p.id] = p


_register(ExperiencePreset(
    id="light",
    label="Light",
    description="Fastest responses, smallest footprint. One small model for everything.",
    role_profile={
        role: ["chat"] for role in _ROLES
    },
    lightweight_mode=True,
))
_register(ExperiencePreset(
    id="medium",
    label="Medium",
    description="Best balance of speed, quality, and memory.",
    role_profile={
        "chat": ["chat", "reasoning"],
        "coder": ["coding", "chat"],
        "planner": ["reasoning", "chat"],
        "vision": ["vision", "chat"],
        "classifier": ["chat"],
        "router": ["chat"],
        "orchestrator": ["reasoning", "chat"],
    },
))
_register(ExperiencePreset(
    id="heavy",
    label="Heavy",
    description="Maximum quality using the largest capable installed models.",
    role_profile={
        "chat": ["reasoning", "chat"],
        "coder": ["coding", "chat"],
        "planner": ["reasoning", "chat"],
        "vision": ["vision", "chat"],
        "classifier": ["chat"],
        "router": ["chat"],
        "orchestrator": ["reasoning", "chat"],
    },
))


def get_presets() -> list[dict]:
    return [p.to_dict() for p in EXPERIENCE_PRESETS.values()]


def _score_model(name: str, role: str, profile: list[str]) -> int:
    """Higher = better fit of installed model ``name`` for ``role`` + profile."""
    fact = KNOWN_MODEL_FACTS.get(name)
    if fact is None:
        return -1
    score = 0
    if "reasoning" in profile:
        score += fact.approx_ram_gb
    if "vision" in profile and fact.supports_vision:
        score += 50
    coding_cap = "coding" in fact.capabilities
    if "coding" in profile or (role == "coder" and coding_cap):
        score += 50
    if "coding" not in profile and coding_cap:
        score -= 5
    if role == "chat" and fact.supports_tools:
        score += 10
    if role in ("classifier", "router") and fact.approx_ram_gb > 4:
        score -= 12
    return score


def resolve_preset(preset_id: str, installed: list) -> dict | None:
    """Resolve a preset to per-role assignments given installed models.

    Returns ``{preset, label, roles:{role: model|""}, missing, missing_any}``.
    Roles with no fitting installed model stay empty and are surfaced as
    missing — never silently substituted.
    """
    preset = EXPERIENCE_PRESETS.get(preset_id)
    if preset is None or preset_id == Experience.CUSTOM.value:
        return None

    installed_names = [m for m in installed]
    assignments: dict[str, str] = {}
    missing: list[str] = []
    for role in _ROLES:
        profile = preset.role_profile.get(role, ["chat"])
        best = ""
        best_score = -1
        for model in installed_names:
            s = _score_model(model, role, profile)
            if s > best_score:
                best_score = s
                best = model
        assignments[role] = best
        if not best:
            missing.append(role)

    return {
        "preset": preset_id,
        "label": preset.label,
        "roles": assignments,
        "missing": missing,
        "missing_any": bool(missing),
    }