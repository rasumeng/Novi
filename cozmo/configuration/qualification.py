"""Model qualification (M2.2) — the evidence layer Automatic selection consumes.

Qualification is about *how much evidence Cozmo has that a model delivers a
reliable Cozmo experience*. It is deliberately independent of:

* installation status (installed / missing / available)
* hardware fit (computed later from M2.1 hardware facts + capabilities)
* recommendation (derived later from qualification + hardware + capabilities)

Do not collapse these into one status field. The future Automatic resolver
combines: ``hardware facts + model qualification + model capabilities +
installed models``.
"""

from __future__ import annotations

from enum import Enum


class Qualification(str, Enum):
    """Evidence grade for a model in the Cozmo catalog.

    - ``TRUSTED``       — explicit evidence of a reliable Cozmo experience;
                          eligible and preferred for Automatic selection.
    - ``SUPPORTED``     — known to work and reasonable for Cozmo, below
                          trusted priority.
    - ``EXPERIMENTAL``  — may work, but insufficient evidence for proactive
                          Automatic selection. Usable for Custom and as a
                          last-resort Automatic fallback when no
                          trusted/supported model is available.
    - ``INCOMPATIBLE``  — never automatically selected; still visible in
                          diagnostics / Developer surfaces.
    """

    TRUSTED = "trusted"
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    INCOMPATIBLE = "incompatible"

    @property
    def automatically_selectable(self) -> bool:
        """Qualification levels eligible for proactive Automatic selection.

        Incompatible is never selectable; experimental is only a last-resort
        fallback (handled later by the resolver), so it is not a primary
        proactive candidate.
        """
        return self in (Qualification.TRUSTED, Qualification.SUPPORTED)

    @property
    def has_evidence(self) -> bool:
        """Whether Cozmo has direct evidence for this model."""
        return self in (Qualification.TRUSTED, Qualification.SUPPORTED)
