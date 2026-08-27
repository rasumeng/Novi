"""WebSearchService — selects and calls the configured search provider.

The rest of Novi depends on this service (or the ``web_search`` tool), never
on Brave/SearXNG directly. Provider choice comes from Novi's configuration
framework (``search.backend``).

No-silent-fallback guarantee: exactly one provider is attempted per call.
Failures raise typed errors that surface to the user verbatim; the service
never retries on another backend.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum

from .base import (
    AuthenticationError,
    NotConfiguredError,
    RateLimitError,
    SearchProviderError,
    WebSearchProvider,
    normalize_time_range,
)
from .brave import BraveSearchProvider
from .models import SearchResponse
from .searxng import SearXNGProvider

log = logging.getLogger("novi.search.service")

BACKEND_BRAVE = "brave"
BACKEND_SEARXNG = "searxng"


class ConnectionState(str, Enum):
    """User-facing connection states surfaced by Test Connection."""

    NOT_CONFIGURED = "not_configured"
    CONNECTED = "connected"
    AUTH_FAILED = "auth_failed"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    UNKNOWN_ERROR = "unknown_error"


def _default_get(key: str, default=None):
    from ..configuration.bootstrap import get_configuration

    return get_configuration().get(key, default)


class WebSearchService:
    """Single entry point for web search in Novi."""

    def __init__(self, config_get=None):
        """``config_get(key, default)`` injects settings; defaults to the
        process-wide configuration framework."""
        self._config_get = config_get or _default_get

    # ── provider selection ───────────────────────────────────────────

    def create_provider(self) -> WebSearchProvider:
        """Build the configured provider. Raises NotConfiguredError when no
        usable provider is selected."""
        backend = str(self._config_get("search.backend", "") or "").strip().lower()
        if backend == BACKEND_BRAVE:
            api_key = str(self._config_get("search.brave_api_key", "") or "")
            return BraveSearchProvider(api_key)
        if backend == BACKEND_SEARXNG:
            url = str(self._config_get("search.url", "") or "http://localhost:8080")
            return SearXNGProvider(url)
        if backend in ("", "none"):
            raise NotConfiguredError(
                "none",
                "Web search isn't configured. "
                "Connect a search provider in Settings -> Connectors.",
            )
        raise NotConfiguredError(
            backend,
            f"Unknown search provider '{backend}'. "
            "Pick Brave Search or SearXNG in Settings -> Connectors.",
        )

    def is_configured(self) -> bool:
        try:
            self.create_provider()
            return True
        except NotConfiguredError:
            return False

    # ── search ───────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        time_range: str | None = None,
    ) -> SearchResponse:
        """Run one search against the configured provider. No fallback."""
        provider = self.create_provider()
        response = await provider.search(
            query,
            max_results=max_results,
            time_range=normalize_time_range(time_range),
        )
        log.info(
            "web_search via %s: %d results for %r",
            provider.name,
            len(response.results),
            query[:80],
        )
        return response

    def search_sync(
        self,
        query: str,
        *,
        max_results: int = 5,
        time_range: str | None = None,
    ) -> SearchResponse:
        """Blocking wrapper for sync callers (tools, evidence pipeline)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.search(query, max_results=max_results, time_range=time_range))

        # Called from inside a running loop (e.g. async server context):
        # run the coroutine on a dedicated thread so we can block safely.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                self.search(query, max_results=max_results, time_range=time_range),
            ).result()

    # ── connection testing ───────────────────────────────────────────

    async def test_connection(self) -> dict:
        """Probe the configured provider. Returns {state, message} suitable
        for display in Settings → Connectors. Never raises."""
        try:
            provider = self.create_provider()
        except NotConfiguredError as e:
            return {"state": ConnectionState.NOT_CONFIGURED.value, "message": e.message}
        except SearchProviderError as e:
            return {"state": ConnectionState.UNKNOWN_ERROR.value, "message": e.message}

        try:
            health = await provider.health_check()
        except Exception as e:
            log.warning("unexpected error testing %s: %s", provider.name, e, exc_info=True)
            return {
                "state": ConnectionState.UNKNOWN_ERROR.value,
                "message": f"Connection test failed with an unexpected error.",
            }

        return {
            "state": health.state,
            "message": health.message,
        }
