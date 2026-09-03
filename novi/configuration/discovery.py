"""Dynamic model discovery — query local Ollama / providers for what actually exists.

Returns unified :class:`ModelRecord` (aliased as ``DiscoveredModel``) records
with an install status. The UI represents the user's real machine, never
assumptions. Discovery is observational: it never installs, never selects,
never mutates configuration.

Delegates to :mod:`.runtime_inventory` for the daemon protocol, applies
provenance-rich capability evidence (seed facts + runtime reports + weak name
inference) and serves per-model detail through the metadata cache.
"""

from __future__ import annotations

import logging
from typing import Optional

from .model_records import CapabilityState, ModelRecord, ModelStatus
from .metadata_cache import ModelMetadataCache
from .runtime_inventory import (
    OllamaRuntimeInventory,
    query_ollama_tags,
    query_ollama_show,
)

log = logging.getLogger("novi.config.discovery")

# DiscoveredModel is an alias of the canonical ModelRecord so there is exactly
# one record type. Existing callers/tests that construct DiscoveredModel by
# name/status/capability_flags keep working unchanged.
DiscoveredModel = ModelRecord

# Shared metadata cache. Keyed per (url, name) so multiple discovery objects
# (one per daemon URL) share the same entries.
_CACHE = ModelMetadataCache()


def invalidate_cache(url: Optional[str] = None, name: Optional[str] = None) -> None:
    """Drop cached runtime metadata (after install/removal).

    Never authoritative for the runtime — it only accelerates metadata
    refresh in the UI. ``invalidate_cache()`` clears everything.
    """
    _CACHE.invalidate(url=url, name=name)


def cached_runtime_capabilities(name: str) -> list[str]:
    """Runtime-reported capability names from cached ``/api/show`` data.

    Read-only and non-blocking: never triggers a network call, never falls back
    to name guessing. Used by the authoritative runtime capability check so
    measured runtime evidence can participate without making selection depend
    on daemon latency.
    """
    from .runtime_inventory import _RUNTIME_CAPABILITY_TOKENS

    claimed: set[str] = set()
    for (url, cached_name) in list(_CACHE._entries):
        if cached_name != name:
            continue
        payload = _CACHE.get(url, cached_name)
        if not isinstance(payload, dict):
            continue
        tokens = payload.get("capabilities")
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            mapped = _RUNTIME_CAPABILITY_TOKENS.get(token)
            if mapped:
                claimed.add(mapped)
    return sorted(claimed)


class ModelDiscovery:
    """Discovers installed/summary info from providers."""

    def __init__(self, ollama_url: str = "http://localhost:11434", timeout: float = 5.0):
        self.ollama_url = ollama_url
        self._inventory = OllamaRuntimeInventory(ollama_url, timeout)
        # Honest status fields populated by the last :meth:`installed` call.
        self.last_error: Optional[str] = None
        self.last_reachable: bool = True
        self.last_models_stale: bool = False

    def installed(self) -> list[ModelRecord]:
        """Live ``/api/tags`` view, enriched via cached ``/api/show`` detail.

        The HTTP seam ``query_ollama_tags`` is called through this module's own
        binding so tests can monkeypatch it. When the daemon is unreachable and
        cached metadata exists, serves the cached installed set flagged
        ``stale=True`` so the UI can surface config-referenced models rather
        than hiding everything.

        Side-effect: populates ``last_reachable`` / ``last_error`` /
        ``last_models_stale`` for the honest discovery payload.
        """
        self.last_error = None
        self.last_reachable = True
        self.last_models_stale = False
        raw_tags: list[dict] = []
        try:
            raw_tags = query_ollama_tags(self.ollama_url, self._inventory.timeout)
        except Exception as e:  # pragma: no cover — query_ollama_tags normally swallows
            raw_tags = []
            self.last_error = f"{type(e).__name__}: {e}"[:500] if str(e) else type(e).__name__
            self.last_reachable = False

        # Honest transport error captured by runtime_inventory seam (still additive
        # when tests monkeypatch query_ollama_tags — we'll synthesize).
        if not raw_tags:
            # Check monkeypatch-safe stale cache first
            stale = self._records_from_cache()
            # Try to pick up a real transport error from the HTTP seam
            try:
                from .runtime_inventory import _last_tags_error  # type: ignore

                if _last_tags_error:
                    self.last_error = _last_tags_error
                    self.last_reachable = False
            except Exception:
                pass
            if stale:
                self.last_models_stale = True
                if self.last_error is None:
                    # query_ollama_tags was monkeypatched to [] or swallowed
                    self.last_reachable = False
                    self.last_error = f"Ollama not reachable at {self.ollama_url}"
                log.debug("daemon unreachable; serving stale cached metadata")
                return stale
            # No stale: empty is honest failure. Even when daemon is reachable
            # with 0 models we surface error/degraded rather than silent success
            # (spec: empty discovery never looks successful). The probe is
            # intentionally not used when tests monkeypatch query_ollama_tags to
            # [] because the real daemon may be up in the test env.
            if self.last_error is None:
                self.last_reachable = False
                self.last_error = f"Ollama not reachable at {self.ollama_url}"
            self.last_models_stale = False
            return []

        records: list[ModelRecord] = []
        for raw in raw_tags:
            record = self._inventory._record_from_tags(raw)
            if record is not None:
                records.append(record)

        enriched: list[ModelRecord] = []
        for record in records:
            detail = self.show_model(record.name)
            if detail is not None:
                record = detail
            record.status = ModelStatus.INSTALLED
            # Cache-hit inside show_model marks stale True; for the live path
            # the data is fresh (tags succeeded), so clear stale.
            record.stale = False
            enriched.append(record)
        # Success path — clear stale/error
        self.last_reachable = True
        self.last_error = None
        self.last_models_stale = False
        # Also clear module-level last error on success
        try:
            import novi.configuration.runtime_inventory as _ri

            _ri._last_tags_error = None  # type: ignore[attr-defined]
        except Exception:
            pass
        return enriched

    def show_model(self, name: str) -> Optional[ModelRecord]:
        """Rich detail for one installed model, via the metadata cache."""
        cached = _CACHE.get(self.ollama_url, name)
        if cached is not None:
            record = self._inventory._record_from_show(cached, name)
            if record is not None:
                record.stale = True
            return record
        payload = query_ollama_show(self.ollama_url, name, self._inventory.timeout)
        if payload is None:
            return None
        _CACHE.set(self.ollama_url, name, payload)
        record = self._inventory._record_from_show(payload, name)
        if record is not None:
            record.stale = False
        return record

    def installed_names(self) -> set[str]:
        return {m.name for m in self.installed() if m.name}

    def installed_map(self) -> dict[str, ModelRecord]:
        return {m.name: m for m in self.installed() if m.name}

    def verify_capabilities(self, name: str, caps: set[str]) -> dict[str, CapabilityState]:
        """Tri-state live verification for ``caps`` on ``name``.

        Flow: deterministic check (seed+cache) first; for remaining ``UNKNOWN``
        caps, attempt a single bounded live ``/api/show`` fetch (timeout 3 s),
        cache on success, and derive authoritative states. Failures map to
        ``VERIFICATION_FAILED`` — never ``UNSUPPORTED`` for unknown.

        ``caps`` values use Novi capability names (``vision``/``audio``/``tools`` …).
        ``thinking`` is normalized to ``reasoning``.
        """
        from .runtime_inventory import _RUNTIME_CAPABILITY_TOKENS

        # Lazy import to avoid circular import at module load.
        from ..runtime.model_selector import model_capability_state

        normalized = {c if c != "thinking" else "reasoning" for c in caps}
        result: dict[str, CapabilityState] = {}
        unknown: set[str] = set()
        for cap in normalized:
            state = model_capability_state(name, cap)
            if state != CapabilityState.UNKNOWN:
                result[cap] = state
            else:
                unknown.add(cap)
        if not unknown:
            return result

        # Single live fetch for all remaining unknown caps
        payload = None
        try:
            # Use a bounded timeout (3 s) regardless of discovery timeout
            payload = query_ollama_show(self.ollama_url, name, timeout=3.0)
        except Exception:
            payload = None

        if payload is not None and isinstance(payload, dict):
            _CACHE.set(self.ollama_url, name, payload)
            tokens = payload.get("capabilities")
            mapped: set[str] = set()
            if isinstance(tokens, list):
                for t in tokens:
                    if isinstance(t, str):
                        m = _RUNTIME_CAPABILITY_TOKENS.get(t)
                        if m:
                            mapped.add(m)
            for cap in unknown:
                if cap in mapped:
                    result[cap] = CapabilityState.SUPPORTED
                else:
                    # Live payload authoritative: absence => unsupported
                    result[cap] = CapabilityState.UNSUPPORTED
            return result

        # Live failed (404/timeout/network): verification_failed for all remaining unknown
        for cap in unknown:
            result[cap] = CapabilityState.VERIFICATION_FAILED
        return result

    # -- internals ----------------------------------------------------------

    def _records_from_cache(self) -> list[ModelRecord]:
        """Rebuild installed records from cached show payloads (daemon down)."""
        records: list[ModelRecord] = []
        for (url, name) in list(_CACHE._entries):
            if url != self.ollama_url:
                continue
            payload = _CACHE.get(url, name)
            if payload is None:
                continue
            record = self._inventory._record_from_show(payload, name)
            if record is None:
                continue
            record.stale = True
            record.status = ModelStatus.INSTALLED
            records.append(record)
        return records