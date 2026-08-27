"""Brave Search provider — official Web Search API (api.search.brave.com).

Requires a user-supplied API key (Brave Data for AI plan). The key comes from
Novi configuration (``search.brave_api_key``); it is never hardcoded or
auto-provisioned here.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import (
    AuthenticationError,
    MalformedResponseError,
    RateLimitError,
    SearchProviderError,
    UnavailableError,
)
from .models import ProviderHealth, SearchResponse, SearchResult

log = logging.getLogger("novi.search.brave")

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"

# Canonical time-range word → Brave freshness filter.
_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}

_REQUEST_TIMEOUT = 15


class BraveSearchProvider:
    """Web search via the official Brave Search API."""

    name = "brave"

    def __init__(self, api_key: str):
        self._api_key = (api_key or "").strip()

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        time_range: str | None = None,
    ) -> SearchResponse:
        if not self._api_key:
            raise AuthenticationError(
                "brave",
                "Brave Search has no API key configured. "
                "Add your key in Settings -> Connectors -> Web Search.",
            )
        if not query or not query.strip():
            return SearchResponse(query=query, results=[], provider=self.name)

        params: dict[str, str] = {"q": query, "count": str(max_results)}
        freshness = _FRESHNESS.get(time_range or "")
        if freshness:
            params["freshness"] = freshness

        url = f"{BRAVE_API_URL}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self._api_key,
            },
        )

        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            if e.code in (401, 403):
                raise AuthenticationError(
                    "brave",
                    "Brave Search authentication failed. Check your API key.",
                ) from e
            if e.code == 429:
                raise RateLimitError(
                    "brave",
                    "Brave Search rate limit reached. Wait before searching again.",
                ) from e
            raise UnavailableError(
                "brave",
                f"Brave Search returned HTTP {e.code}. {detail}".strip(),
            ) from e
        except urllib.error.URLError as e:
            raise UnavailableError(
                "brave",
                f"Could not reach Brave Search: {e.reason}",
            ) from e
        except TimeoutError as e:
            raise UnavailableError("brave", "Brave Search timed out.") from e

        data = self._parse(raw)
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)

        web = data.get("web") if isinstance(data, dict) else None
        items = web.get("results") if isinstance(web, dict) else None
        if not isinstance(items, list):
            raise MalformedResponseError(
                "brave",
                "Brave Search response was malformed (missing result list).",
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
                snippet=str(item.get("description", "")),
                source="brave",
                published_at=item.get("page_age") or item.get("age"),
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
        """A valid-key probe: Brave answers 200 on a tiny authenticated query."""
        try:
            await self.search("novi health check", max_results=1)
        except AuthenticationError as e:
            return ProviderHealth(ok=False, state="auth_failed", message=e.message)
        except RateLimitError as e:
            return ProviderHealth(ok=False, state="rate_limited", message=e.message)
        except SearchProviderError as e:
            return ProviderHealth(ok=False, state="unavailable", message=e.message)
        except Exception as e:
            log.debug("brave health check failed: %s", e)
            return ProviderHealth(
                ok=False,
                state="unknown_error",
                message="Brave Search failed with an unexpected error.",
            )
        return ProviderHealth(ok=True, state="connected", message="Brave Search connected.")

    def _parse(self, raw: bytes) -> dict:
        """Decode the JSON body, raising MalformedResponseError when unusable."""
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise MalformedResponseError(
                "brave",
                "Brave Search returned an unreadable response.",
            ) from e
        if not isinstance(data, dict):
            raise MalformedResponseError(
                "brave",
                "Brave Search returned an unexpected payload.",
            )
        return data
