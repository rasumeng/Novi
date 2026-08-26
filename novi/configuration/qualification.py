"""Model qualification — the evidence grade layer for recommendation.

Qualification is about *how much evidence Novi has that a model delivers a
reliable Novi experience*. It is deliberately independent of:

* installation status (installed / missing / available)
* hardware fit (computed later from hardware facts + capabilities)
* recommendation (derived later from qualification + hardware + capabilities)

Do not collapse these into one status field.
"""

from __future__ import annotations

from enum import Enum


class Qualification(str, Enum):
    """Evidence grade for a model in the Novi catalog.

    - ``TRUSTED``       — explicit evidence of a reliable Novi experience;
                          preferred in recommendations.
    - ``SUPPORTED``     — known to work and reasonable for Novi, below
                          trusted priority.
    - ``EXPERIMENTAL``  — may work, but insufficient evidence for confident
                          recommendation. Usable and recommendable as a
                          last resort when no trusted/supported model is
                          installed.
    - ``INCOMPATIBLE``  — never recommended; still visible in diagnostics /
                          Developer surfaces.
    """

    TRUSTED = "trusted"
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    INCOMPATIBLE = "incompatible"

    @property
    def has_evidence(self) -> bool:
        """Whether Novi has direct evidence for this model."""
        return self in (Qualification.TRUSTED, Qualification.SUPPORTED)
