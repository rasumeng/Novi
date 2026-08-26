"""Canonical rich model record + capability evidence (Phase 5A).

One record type composes *identity*, *runtime/source/format*, *measured
facts*, *derived capabilities with provenance*, *qualification/evidence*, and
*state*. Every field is optional/unknown-safe (``None`` when not known) and
never fabricated — mirroring the ``hardware.py`` UNKNOWN discipline.

Architecture rules locked in here:

* Identity is **classification/metadata only** — it is never routing logic.
  No production code keys behaviour off family/variant/quantization.
* Capabilities belong to **models**, not workloads. A record's capability
  evidence is descriptive and advisory.
* ``ModelRecord`` is the single canonical model representation. Older thin
  shapes (``DiscoveredModel``) are aliases of it so there is exactly one
  record type in the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .qualification import Qualification


class ModelStatus(str, Enum):
    INSTALLED = "installed"
    AVAILABLE = "available"   # known/modeled as installable remote
    MISSING = "missing"       # referenced in config but not found


@dataclass
class ModelIdentity:
    """Structured identity — classification only, never routing logic.

    All fields optional; ``None`` when not known. Name is the only field that
    is always present (it is the runtime identifier).
    """

    name: str
    family: Optional[str] = None
    variant: Optional[str] = None
    size_tier: Optional[str] = None
    quantization: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "family": self.family,
            "variant": self.variant,
            "sizeTier": self.size_tier,
            "quantization": self.quantization,
        }


@dataclass
class CapabilityEvidence:
    """A single capability claim with provenance.

    ``supported`` is tri-state: ``True`` / ``False`` / ``None`` (unknown).
    ``source`` identifies where the claim came from (``runtime``,
    ``seed``, ``name-inference``, ...). Name inference is always weak and is
    never treated as authoritative.
    """

    capability: str
    supported: Optional[bool] = None
    source: str = "unknown"
    confidence: Optional[float] = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "supported": self.supported,
            "source": self.source,
            "confidence": self.confidence,
            "note": self.note,
        }


@dataclass
class ModelRecord:
    """Single rich representation of a model.

    Composes identity, runtime/source/format, measured facts, capability
    evidence with provenance, qualification, and state. Unknown fields are
    ``None`` — never fabricated.

    ``capability_flags`` is the derived flat view (``{capability: bool}``)
    kept for back-compat with existing discovery/recommendation consumers;
    ``capabilities`` carries the provenance-rich evidence list.
    """

    name: str
    provider: str = "ollama"                 # runtime provider (ollama/openai/...)
    runtime: str = ""                        # runtime/engine identity when distinct
    status: ModelStatus = ModelStatus.INSTALLED
    identity: Optional[ModelIdentity] = None
    source_kind: str = ""                    # local-runtime | registry | config | ...
    source_url: Optional[str] = None
    format: Optional[str] = None             # gguf / safetensors / ...
    size_bytes: Optional[int] = None
    parameter_count: Optional[str] = None    # e.g. "7.6B"
    context_length: Optional[int] = None
    license: Optional[str] = None
    capabilities: list[CapabilityEvidence] = field(default_factory=list)
    capability_flags: dict[str, bool] = field(default_factory=dict)
    qualification: Qualification = Qualification.EXPERIMENTAL
    display_name: str = ""
    approx_ram_gb: Optional[float] = None
    min_vram_gb: Optional[float] = None
    caveats: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)   # runtime-specific extras
    stale: bool = False                            # served from a stale cache

    # ── back-compat aliases ────────────────────────────────────────────

    @property
    def size(self) -> Optional[int]:
        return self.size_bytes

    @property
    def size_bytes_view(self) -> Optional[int]:
        return self.size_bytes

    def capability_names(self) -> list[str]:
        """Advisory capability names this record claims, deduplicated."""
        names: list[str] = []
        for ev in self.capabilities:
            if ev.supported is True and ev.capability not in names:
                names.append(ev.capability)
        for cap, supported in self.capability_flags.items():
            if supported is True and cap not in names:
                names.append(cap)
        return names

    def capability_support(self, capability: str) -> Optional[bool]:
        """Tri-state capability answer for ``capability``.

        Prefers provenance-rich evidence; falls back to the flat flags.
        Returns ``None`` when there is no evidence either way.
        """
        for ev in self.capabilities:
            if ev.capability == capability:
                return ev.supported
        flag = self.capability_flags.get(capability)
        return flag if flag is not None else None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "runtime": self.runtime,
            "status": self.status.value,
            "identity": self.identity.to_dict() if self.identity else None,
            "source": {"kind": self.source_kind, "url": self.source_url}
            if (self.source_kind or self.source_url) else None,
            "format": self.format,
            "size": self.size_bytes,
            "parameterCount": self.parameter_count,
            "contextLength": self.context_length,
            "license": self.license,
            "capabilities": self.capability_flags,
            "capabilityEvidence": [e.to_dict() for e in self.capabilities],
            "qualification": self.qualification.value,
            "displayName": self.display_name or self.name,
            "approxRamGb": self.approx_ram_gb,
            "minVramGb": self.min_vram_gb,
            "caveats": self.caveats,
            "stale": self.stale,
        }