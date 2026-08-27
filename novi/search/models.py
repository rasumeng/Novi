"""Normalized web-search models — provider-independent result shapes.

Every provider (Brave, SearXNG, ...) converts its raw API payloads into
``SearchResult``/``SearchResponse``. Downstream consumers (tools, evidence
pipeline, UI) only ever see these shapes and never provider-specific ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """One normalized search hit. ``url`` is always preserved verbatim."""

    title: str
    url: str
    snippet: str
    source: str = ""
    published_at: str | None = None


@dataclass
class SearchResponse:
    """Provider response for one query."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    provider: str = ""
    search_time_ms: float | None = None


@dataclass
class ProviderHealth:
    """Result of a provider liveness/configuration probe.

    ``state`` uses the shared connection vocabulary (see
    ``novi.search.service.ConnectionState``): not_configured, connected,
    auth_failed, unavailable, rate_limited, unknown_error.
    """

    ok: bool
    state: str
    message: str = ""
