"""SearXNG provider — self-hosted meta-search behind the provider contract.

Talks to a SearXNG instance's JSON API at the configured endpoint. This
provider does NOT manage Docker; starting SearXNG stays a developer workflow
(``novi.searxng_util`` / docker compose). If the endpoint is unreachable the
error surfaces as ``UnavailableError`` — never a silent fallback.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import MalformedResponseError, SearchProviderError, UnavailableError
from .models import ProviderHealth, SearchResponse, SearchResult

log = logging.getLogger("novi.search.searxng")

_REQUEST_TIMEOUT = 10


class SearXNGProvider:
    """Web search via a SearXNG instance's JSON endpoint."""

    name = "searxng"

    def __init__(self, url: str = "http://localhost:8080"):
        self._base_url = (url or "").rstrip("/")

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        time_range: str | None = None,
    ) -> SearchResponse:
        if not self._base_url:
            raise UnavailableError(
                "searxng",
                "SearXNG endpoint is not configured. "
                "Set it in Settings -> Connectors -> Web Search.",
            )
        if not query or not query.strip():
            return SearchResponse(query=query, results=[], provider=self.name)

        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "language": "en",
        })
        if time_range:
            params += f"&time_range={time_range}"

        url = f"{self._base_url}/search?{params}"
        request = urllib.request.Request(url, headers={"User-Agent": "Novi/1.0"})

        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise UnavailableError(
                "searxng",
                f"SearXNG returned HTTP {e.code} ({e.reason}). "
                "Is JSON format enabled on the instance?",
            ) from e
        except urllib.error.URLError as e:
            raise UnavailableError(
                "searxng",
                f"SearXNG is not reachable at {self._base_url} ({e.reason}). "
                "Start the SearXNG container or switch providers.",
            ) from e
        except TimeoutError as e:
            raise UnavailableError(
                "searxng",
                f"SearXNG at {self._base_url} timed out.",
            ) from e

        elapsed_ms = round((time.monotonic() - started) * 1000, 2)

        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise MalformedResponseError(
                "searxng",
                "SearXNG returned an unreadable response (is JSON format enabled?).",
            ) from e

        items = data.get("results") if isinstance(data, dict) else None
        if items is None:
            raise MalformedResponseError(
                "searxng",
                "SearXNG response was malformed (missing results).",
            )

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url_value = str(item.get("url", "")).strip()
            if not url_value:
                continue
            results.append(SearchResult(
                title=str(item.get("title", "")),
                url=url_value,
                snippet=str(item.get("content", "")),
                source="searxng",
                published_at=item.get("publishedDate"),
            ))
            if len(results) >= max_results:
                break

        return SearchResponse(
            query=query,
            results=results,
            provider=self.name,
            search_time_ms=elapsed_ms,
        )

    async def health_check(self) -> ProviderHealth:
        try:
            await self.search("novi health check", max_results=1)
        except SearchProviderError as e:
            return ProviderHealth(ok=False, state="unavailable", message=e.message)
        except Exception as e:
            log.debug("searxng health check failed: %s", e)
            return ProviderHealth(
                ok=False,
                state="unknown_error",
                message="SearXNG failed with an unexpected error.",
            )
        return ProviderHealth(ok=True, state="connected", message="SearXNG connected.")
