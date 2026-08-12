"""M2.4 — Automatic Resolution Layer.

Owns the single full-resolution entry point:

    resolve_automatic(hardware, installed, catalog) -> full resolved role map

It combines the M2.1 hardware detector, M2.2 qualification/catalog, and M2.3
eligibility/evidence into a deterministic, complete runtime-role map that is
persisted to ``llm.roles.*`` — the existing runtime-consumed surface.

Design constraints honoured here:
* Automatic resolves runtime roles: chat, coder, planner, vision, classifier,
  router, orchestrator. Embeddings resolve separately to ``embedding.model``.
* It never modifies the runtime's role-selection/ReAct logic; it only writes
  ``llm.roles.*`` values that the runtime already consumes.
* Selection priority: installed+trusted > installed+supported >
  experimental (last-resort, explicitly marked) — never incompatible.
* Best overall experience, not simply the smallest model (qualification, then
  capability breadth, then stable name order).
* Hardware confidence behaviour from M2.1 is preserved. VRAM is never invented;
  a missing requirement stays unknown. Curated VRAM caveats are respected via
  the ``min_vram_gb`` hint (incompatible-with-low-VRAM candidates are demoted,
  not silently chosen just because they are trusted).
* Provenance is recorded as ``llm.meta.source = "automatic"``. Recommendation /
  eligibility / hardware-fit results are derived runtime output and are NOT
  persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .catalog import KNOWN_MODEL_FACTS, ModelFact
from .discovery import ModelStatus
from .eligibility import HardwareFit, evaluate_eligibility
from .hardware import DetectionConfidence, HardwareProfile, detect_hardware
from .qualification import Qualification


# Runtime roles Automatic must fill (they must never be left empty).
ALL_ROLES = ["chat", "coder", "planner", "vision",
             "classifier", "router", "orchestrator"]

# Capability required for each user-facing role.
# Capabilities are a higher-level intent layer ABOVE llm.roles.*; they select a
# qualified installed model that provides the capability and then assign it to
# the corresponding runtime role. They do NOT replace runtime roles.
_ROLE_CAPABILITY = {
    "chat": "chat",
    "coder": "coding",
    "planner": "reasoning",
    "vision": "vision",
}

# Internal roles take the preferred reasoning/coding model (deterministic and
# conservative), never exposed as user capabilities.
_INTERNAL_TO_PREFERRED = ["classifier", "router", "orchestrator"]


@dataclass
class RoleSelection:
    """Metadata for one resolved role (derived, not persisted)."""

    role: str
    model: str
    capability: str = ""
    source: str = ""
    qualification: Optional[Qualification] = None
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "model": self.model,
            "capability": self.capability,
            "source": self.source,
            "qualification": self.qualification.value if self.qualification else "",
            "hardwareConfidence": self.hardware_confidence.value,
            "reasons": self.reasons,
            "caveats": self.caveats,
        }


@dataclass
class AutomaticResolution:
    """Result of running the Automatic resolver.

    ``role_map`` keys are runtime roles mapped to model names — this is what is
    written to ``llm.roles.<role>.model``. ``roles`` holds per-role metadata.
    None of this is persisted except the role map + ``llm.meta.source``.
    """

    role_map: dict[str, str] = field(default_factory=dict)
    roles: dict[str, RoleSelection] = field(default_factory=dict)
    embedding_model: str = ""
    embedding_roles: list[RoleSelection] = field(default_factory=list)
    mode: str = "automatic"
    provisional: bool = False
    hardware_confidence: DetectionConfidence = DetectionConfidence.UNKNOWN

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "provisional": self.provisional,
            "hardwareConfidence": self.hardware_confidence.value,
            "roleMap": dict(self.role_map),
            "roles": {r: s.to_dict() for r, s in self.roles.items()},
            "embeddingModel": self.embedding_model,
            "meta": {"source": "automatic"},
        }


def _is_installed(installed, name: str) -> bool:
    return name in installed


def _vram_known(hw: HardwareProfile) -> bool:
    return isinstance(hw.gpu.vram_total_gb, (int, float))


def _vram_available(hw: HardwareProfile) -> Optional[float]:
    return hw.gpu.vram_total_gb if _vram_known(hw) else None


def _supports(fact: ModelFact, capability: str) -> bool:
    return capability in fact.capabilities


def _qualified_order(qual: Qualification) -> int:
    # trusted > supported > experimental. Incompatible never ranked.
    return {
        Qualification.TRUSTED: 0,
        Qualification.SUPPORTED: 1,
        Qualification.EXPERIMENTAL: 2,
    }.get(qual, 99)


def _candidate_rank(
    name: str, fact: Optional[ModelFact], capability: str,
    hw: HardwareProfile, installed: set[str],
) -> Optional[tuple]:
    """Return sortable primary rank for an installed candidate, or None.

    Never incompatible. If a candidate's curated ``min_vram_gb`` hint exceeds
    known VRAM, it is demoted (conservative) but not eliminated — it may still
    serve as a last-resort fallback.

    Rank order (hard constraints first): VRAM demotion, then qualification, then
    capability breadth. A VRAM-demoted model never wins over a fitting model
    merely because it is trusted ("not silently chosen just because they are
    trusted" — the curated caveat). Reuse-avoidance and name are applied as
    final tiebreaks in ``_pick_role_model``.
    """
    if not _is_installed(installed, name):
        return None
    if fact is None:
        return None
    if fact.qualification == Qualification.INCOMPATIBLE:
        return None
    if not _supports(fact, capability):
        return None

    vram_penalty = 0
    if _vram_known(hw) and fact.min_vram_gb is not None:
        if _vram_available(hw) < fact.min_vram_gb:
            vram_penalty = 1

    # Prefer broader capability coverage (better overall experience over
    # smallest). Fewer capabilities => worse (higher) rank.
    breadth = -len(fact.capabilities)

    return (vram_penalty, _qualified_order(fact.qualification), breadth,
            name)


def _pick_role_model(
    capability: str, hw: HardwareProfile, installed: set[str],
    catalog: dict[str, ModelFact], seen: set[str],
) -> Optional[tuple[str, RoleSelection]]:
    """Deterministically pick the best installed model for ``capability``.

    Returns (model_name, RoleSelection) or None if no usable model.
    ``seen`` tracks models already assigned; a not-yet-used model with an
    identical rank is preferred (better coverage), but reuse-avoidance never
    outranks VRAM/qualification/breadth constraints.
    """
    ranked = []
    for name in installed:
        fact = catalog.get(name)
        key = _candidate_rank(name, fact, capability, hw, installed)
        if key is not None:
            ranked.append((key, name, fact))
    ranked.sort(key=lambda x: (x[0][0], x[0][1], x[0][2], x[1] in seen, x[0][3]))

    if not ranked:
        return None

    _, name, fact = ranked[0]
    qual = fact.qualification
    reasons = [f"capability '{capability}'"]
    if qual == Qualification.TRUSTED:
        reasons.append("trusted by Cozmo")
    elif qual == Qualification.SUPPORTED:
        reasons.append("supported by Cozmo")
    else:
        reasons.append("experimental / unverified (last resort)")
    caveats = list(fact.caveats)
    if qual == Qualification.EXPERIMENTAL:
        caveats.append("No trusted/supported candidate installed; using "
                       "experimental model as last resort.")

    selection = RoleSelection(
        role=capability,
        model=name,
        capability=capability,
        source="automatic",
        qualification=qual,
        hardware_confidence=hw.confidence,
        reasons=reasons,
        caveats=caveats,
    )
    return name, selection


def _resolve_embeddings(installed: set[str], catalog: dict[str, ModelFact],
                        hw: HardwareProfile) -> tuple[str, list[RoleSelection]]:
    """Embeddings resolve independently to ``embedding.model``."""
    # Prefer a known embedding-orientated installed model; else first model
    # advertising the embeddings capability; else empty.
    embed_candidates = [
        name for name in installed
        if (fact := catalog.get(name)) is not None and "embeddings" in fact.capabilities
    ]
    # Deterministic preference: keep stable by name (no invented quality).
    embed_candidates.sort()
    chosen = embed_candidates[0] if embed_candidates else ""
    metas = []
    if chosen:
        fact = catalog[chosen]
        metas.append(RoleSelection(
            role="embedding", model=chosen, capability="embeddings",
            source="automatic", qualification=fact.qualification,
            hardware_confidence=hw.confidence,
            reasons=["capability 'embeddings'"],
        ))
    return chosen, metas


def resolve_automatic(
    hardware: Optional[HardwareProfile] = None,
    installed=None,
    catalog: Optional[dict[str, ModelFact]] = None,
) -> AutomaticResolution:
    """Resolve all runtime roles (and ``embedding.model``) from installed models.

    ``installed``: iterable of model names (strings) or DiscoveredModel objects
    (their ``.name`` is used). ``catalog`` defaults to ``KNOWN_MODEL_FACTS``.
    """
    if catalog is None:
        catalog = KNOWN_MODEL_FACTS
    if hardware is None:
        hardware = detect_hardware()

    installed_names = set()
    for m in (installed or []):
        name = m.name if hasattr(m, "name") else m
        if name:
            installed_names.add(name)

    resolution = AutomaticResolution(hardware_confidence=hardware.confidence)

    # Low/unknown hardware => conservative + provisional flag.
    if hardware.confidence in (DetectionConfidence.LOW,
                               DetectionConfidence.UNKNOWN):
        resolution.provisional = True

    seen: set[str] = set()
    resolution.roles = {}
    resolution.role_map = {}

    # Primary user-facing roles.
    for role, capability in _ROLE_CAPABILITY.items():
        result = _pick_role_model(capability, hardware, installed_names, catalog, seen)
        if result is None:
            # No candidate: leave empty but never fabricate. Could be a
            # non-coded empty; caller decides. We record it as empty.
            selection = RoleSelection(
                role=role, model="", capability=capability,
                source="automatic", hardware_confidence=hardware.confidence,
                reasons=["no installed candidate for capability"],
            )
            resolution.roles[role] = selection
            resolution.role_map[role] = ""
            continue
        name, selection = result
        selection.role = role
        seen.add(name)
        resolution.roles[role] = selection
        resolution.role_map[role] = selection.model

    # Internal roles: never empty; derived deterministically from the preferred
    # reasoning (planner) or coding (coder) model, falling back to chat.
    preferred = (
        resolution.role_map.get("coder")
        or resolution.role_map.get("planner")
        or resolution.role_map.get("chat")
    )
    for role in _INTERNAL_TO_PREFERRED:
        if not preferred:
            sel = RoleSelection(
                role=role, model="", source="automatic",
                hardware_confidence=hardware.confidence,
                reasons=["no model available for internal role"],
            )
        else:
            qual = None
            fact = catalog.get(preferred)
            if fact:
                qual = fact.qualification
            sel = RoleSelection(
                role=role, model=preferred, capability="reasoning/coding",
                source="automatic", qualification=qual,
                hardware_confidence=hardware.confidence,
                reasons=[f"derived from preferred model '{preferred}'"],
                caveats=list(fact.caveats) if fact else [],
            )
        resolution.roles[role] = sel
        resolution.role_map[role] = sel.model

    # Embeddings separate.
    resolution.embedding_model, resolution.embedding_roles = _resolve_embeddings(
        installed_names, catalog, hardware)

    return resolution


def apply_automatic(configuration, installed=None, hardware=None):
    """Resolve Automatic and persist the result through the configuration
    framework (the single authoritative validate -> persist -> apply -> emit
    path).

    Writes, via ``configuration.set``:
        llm.roles.<role>.model   (chat/coder/planner/vision/classifier/router/
                                   orchestrator)
        embedding.model
        models.mode              = "automatic"
        llm.meta.source          = "automatic"

    Returns the ``AutomaticResolution``. Derivation/evidence is NOT persisted —
    only the runtime-consumed role map + provenance are written.
    """
    resolution = resolve_automatic(hardware=hardware, installed=installed)

    by = "automatic"
    for role, model in resolution.role_map.items():
        configuration.set(f"llm.roles.{role}.model", model, by=by)
    if resolution.embedding_model:
        configuration.set("embedding.model", resolution.embedding_model, by=by)
    configuration.set("models.mode", resolution.mode, by=by)
    configuration.set("llm.meta.source", "automatic", by=by)
    return resolution


# ── M3.2 — Custom capability assignment resolution ────────────────────────
#
# The user-facing configuration surface is exactly four capabilities. Custom
# intent is persisted under ``models.custom.assign.*`` and is resolved to the
# runtime-consumed ``llm.roles.*`` layer BELOW it. Internal runtime roles are
# derived here and are never surfaced as user selectors.

# Capability -> primary runtime role (locked architecture).
CAPABILITY_TO_ROLE = {
    "chat": "chat",
    "reasoning": "planner",
    "coding": "coder",
    "vision": "vision",
}

USER_CAPABILITIES = list(CAPABILITY_TO_ROLE)

# Internal roles are never empty; they derive from the resolved reasoning model
# (falling back to coding, then chat) precisely like the Automatic layer.
INTERNAL_DERIVED_ROLES = ["router", "orchestrator", "classifier"]


def _installed_names(installed) -> set[str]:
    names = set()
    for m in (installed or []):
        name = m.name if hasattr(m, "name") else m
        if name:
            names.add(name)
    return names


def resolve_custom(configuration, hardware=None, installed=None,
                   catalog: Optional[dict[str, ModelFact]] = None):
    """Resolve user Custom capability assignments into ``llm.roles.*``.

    ``models.custom.assign.*`` is the user's explicit intent (capability ->
    model). Resolution overlays that intent on top of the Automatic baseline:

    * Explicit capability assignments become ``llm.roles.<role>.model``.
    * An unset capability falls back to the last effective Automatic resolution
      (never left empty).
    * A Custom model that is no longer installed is preserved as intent, but the
      runtime role temporarily falls back to the safe Automatic baseline.
    * Internal roles (router/orchestrator/classifier) derive from reasoning ->
      coding -> chat so they are never empty.

    Custom intent is NEVER written back from a fallback: it lives solely in
    ``models.custom.assign.*``.
    """
    if catalog is None:
        catalog = KNOWN_MODEL_FACTS
    installed_names = _installed_names(installed)

    auto = resolve_automatic(hardware=hardware, installed=installed_names,
                             catalog=catalog)
    baseline = dict(auto.role_map)
    assign = configuration.get("models.custom.assign", {}) or {}

    role_map = dict(baseline)
    roles = {}
    for cap, role in CAPABILITY_TO_ROLE.items():
        model = assign.get(cap) or ""
        if not model:
            # no explicit intent -> inherit last effective Automatic baseline
            roles[role] = RoleSelection(
                role=role, model=baseline.get(role, ""), capability=cap,
                source="automatic",
                hardware_confidence=auto.hardware_confidence,
                reasons=["inherited from Automatic resolution"])
            continue
        effective = model
        caveats = []
        if installed_names and model not in installed_names:
            # preserve the user's intent; runtime temporarily uses a safe fallback
            effective = baseline.get(role) or model
            caveats = [f"'{model}' is not installed — temporarily using '{effective}'"]
        role_map[role] = effective
        roles[role] = RoleSelection(
            role=role, model=model, capability=cap, source="custom",
            qualification=None, hardware_confidence=auto.hardware_confidence,
            reasons=["selected by you"], caveats=caveats)

    preferred = (role_map.get("planner")
                 or role_map.get("coder")
                 or role_map.get("chat"))
    for role in INTERNAL_DERIVED_ROLES:
        role_map[role] = preferred or ""
        roles[role] = RoleSelection(
            role=role, model=preferred or "", capability="reasoning/coding",
            source="custom", hardware_confidence=auto.hardware_confidence,
            reasons=(["derived from preferred model"] if preferred
                     else ["no model available for internal role"]))

    return AutomaticResolution(
        role_map=role_map, roles=roles, mode="custom",
        embedding_model=auto.embedding_model,
        embedding_roles=auto.embedding_roles,
        provisional=auto.provisional,
        hardware_confidence=auto.hardware_confidence)


def apply_custom(configuration, installed=None, hardware=None):
    """Resolve Custom capability assignments and persist through the framework.

    Writes, via ``configuration.set``:
        models.custom.assign.*   (already persisted by the caller, unresolved)
        llm.roles.<role>.model   (chat/coder/planner/vision/router/orchestrator/
                                   classifier)
        embedding.model          (resolved internally, unchanged call)
        models.mode              = "custom"
        llm.meta.source          = "custom"

    Returns the ``AutomaticResolution`` for Custom. Custom intent in
    ``models.custom.assign.*`` is never modified here.
    """
    by = "custom"
    if configuration.get("models.mode", "automatic") != "custom":
        configuration.set("models.mode", "custom", by=by)
        configuration.set("llm.meta.source", "custom", by=by)

    resolution = resolve_custom(configuration, hardware=hardware, installed=installed)
    for role, model in resolution.role_map.items():
        configuration.set(f"llm.roles.{role}.model", model, by=by)
    if resolution.embedding_model:
        configuration.set("embedding.model", resolution.embedding_model, by=by)
    configuration.set("llm.meta.source", "custom", by=by)
    return resolution
