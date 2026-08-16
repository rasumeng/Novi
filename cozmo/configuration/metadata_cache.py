"""In-memory TTL metadata cache for runtime model metadata (Phase 5H).

Caches the expensive ``/api/show`` lookups (one HTTP round-trip per model) so
the discovery UI doesn't hammer the daemon on every render.

Hard rules:

* **Never an authority for the runtime.** A selected model's ability to run
  is always validated live by the runtime (``models/service.py`` + registry),
  never from this cache.
* **Invalidated on install/removal** so the cached metadata set cannot go
  stale across model lifecycle changes.
* Bounded by TTL and cache size.
* Served-from-cache records are flagged ``stale=True`` so callers (and the
  UI) can tell cached metadata from a fresh runtime read.
"""

from __future__ import annotations

import time
from typing import Optional

_DEFAULT_TTL_SECONDS = 60.0
_MAX_ENTRIES = 256


class ModelMetadataCache:
    """TTL cache keyed by ``(url, name)`` for per-model metadata."""

    def __init__(self, ttl: float = _DEFAULT_TTL_SECONDS):
        self._ttl = ttl
        self._entries: dict[tuple[str, str], tuple[float, dict]] = {}

    def get(self, url: str, name: str) -> Optional[dict]:
        key = (url, name)
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, payload = entry
        if time.monotonic() - stored_at > self._ttl:
            self._entries.pop(key, None)
            return None
        return payload

    def set(self, url: str, name: str, payload: dict) -> None:
        if len(self._entries) >= _MAX_ENTRIES:
            self._entries.pop(next(iter(self._entries)))
        self._entries[(url, name)] = (time.monotonic(), payload)

    def invalidate(self, url: Optional[str] = None, name: Optional[str] = None) -> None:
        """Drop entries matching optional url/name filters.

        ``invalidate(url=None, name=None)`` clears everything (called after
        any install or removal).
        """
        if url is None and name is None:
            self._entries.clear()
            return
        keys = [
            (u, n) for (u, n) in self._entries
            if (url is None or u == url) and (name is None or n == name)
        ]
        for key in keys:
            self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)