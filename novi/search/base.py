"""WebSearchProvider contract and typed failure modes.

Providers implement the protocol and raise the typed exceptions below; they
must never return sentinel values or silently degrade. ``SearchProviderError``
messages are user-facing — write them for display in settings/chat, not as
stack traces.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ProviderHealth, SearchResponse


class SearchProviderError(Exception):
    """Base class for provider failures. ``message`` is user-safe."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        self.message = message
        super().__init__(message)


class NotConfiguredError(SearchProviderError):
    """No provider selected or required credentials missing."""


class AuthenticationError(SearchProviderError):
    """Provider rejected credentials (bad/expired API key)."""


class RateLimitError(SearchProviderError):
    """Provider quota or rate limit exhausted."""


class UnavailableError(SearchProviderError):
    """Provider unreachable or returned a server-side error."""


class MalformedResponseError(SearchProviderError):
    """Provider responded but the payload was unusable."""


@runtime_checkable
class WebSearchProvider(Protocol):
    """Contract every search backend implements.

    ``search`` returns normalized results or raises a ``SearchProviderError``
    subclass. It never falls back to another provider and never returns
    placeholder results on failure.
    """

    name: str

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        time_range: str | None = None,
    ) -> SearchResponse: ...

    async def health_check(self) -> ProviderHealth:
        """Configuration/liveness probe returning a display-ready state."""
        ...


# Normalized time-range vocabulary shared by all providers. Legacy short codes
# (d/w/m/y) map onto these words at the tool layer.
TIME_RANGES: dict[str, str] = {
    "d": "day",
    "w": "week",
    "m": "month",
    "y": "year",
}


def normalize_time_range(time_range: str | None) -> str | None:
    """Map a user/tool-supplied range to the canonical word form.

    Accepts canonical words verbatim and legacy short codes (``d``/``w``/
    ``m``/``y``). Unknown values return None (no filter) rather than failing
    the whole search.
    """
    if not time_range:
        return None
    lowered = str(time_range).strip().lower()
    return TIME_RANGES.get(lowered, lowered if lowered in TIME_RANGES.values() else None)
