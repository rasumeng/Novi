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

from .model_records import ModelRecord, ModelStatus
from .metadata_cache import ModelMetadataCache
from .runtime_inventory import (
    OllamaRuntimeInventory,
    query_ollama_tags,
    query_ollama_show,
)

log = logging.getLogger("cozmo.config.discovery")

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

    def installed(self) -> list[ModelRecord]:
        """Live ``/api/tags`` view, enriched via cached ``/api/show`` detail.

        The HTTP seam ``query_ollama_tags`` is called through this module's own
        binding so tests can monkeypatch it. When the daemon is unreachable and
        cached metadata exists, serves the cached installed set flagged
        ``stale=True`` so the UI can surface config-referenced models rather
        than hiding everything.
        """
        raw_tags = query_ollama_tags(self.ollama_url, self._inventory.timeout)
        if not raw_tags:
            stale = self._records_from_cache()
            if stale:
                log.debug("daemon unreachable; serving stale cached metadata")
                return stale
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
            enriched.append(record)
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