"""Provider-agnostic web search.

Public surface:
    WebSearchService   — provider selection + execution (no silent fallback)
    BraveSearchProvider / SearXNGProvider — concrete backends
    SearchResult / SearchResponse       — normalized models
    ProviderHealth                      — connection probe result
    SearchProviderError family          — typed, user-safe failures
"""

from __future__ import annotations

from .base import (
    AuthenticationError,
    MalformedResponseError,
    NotConfiguredError,
    RateLimitError,
    SearchProviderError,
    UnavailableError,
    WebSearchProvider,
)
from .brave import BraveSearchProvider
from .models import ProviderHealth, SearchResponse, SearchResult
from .searxng import SearXNGProvider
from .service import ConnectionState, WebSearchService

__all__ = [
    "AuthenticationError",
    "BraveSearchProvider",
    "ConnectionState",
    "MalformedResponseError",
    "NotConfiguredError",
    "ProviderHealth",
    "RateLimitError",
    "SearchProviderError",
    "SearchResponse",
    "SearchResult",
    "SearXNGProvider",
    "UnavailableError",
    "WebSearchProvider",
    "WebSearchService",
]
