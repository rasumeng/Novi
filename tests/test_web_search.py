"""Phase 10A web-search architecture tests.

Covers: provider abstraction, Brave provider (success/auth/rate-limit/network/
malformed), SearXNG provider (success/unavailable/malformed),
WebSearchService selection + no-silent-fallback, and the web_search tool
registration. All external APIs are mocked — no live Brave access, no Docker,
no running SearXNG required.
"""

import asyncio
import json
from unittest.mock import patch

import pytest
from urllib.error import HTTPError, URLError

from novi.search import (
    AuthenticationError,
    BraveSearchProvider,
    MalformedResponseError,
    NotConfiguredError,
    ProviderHealth,
    RateLimitError,
    SearchResponse,
    SearchResult,
    SearXNGProvider,
    UnavailableError,
    WebSearchService,
)


class _FakeResponse:
    def __init__(self, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def _brave_payload(results=None):
    return json.dumps({
        "web": {"results": results if results is not None else [
            {"title": "Example", "url": "https://example.com/a", "description": "An example result"},
            {"title": "Second", "url": "https://example.com/b", "description": "Another", "page_age": "2 days ago"},
        ]}
    })


# ─── Provider abstraction ─────────────────────────────────────────────────────

class TestProviderAbstraction:
    def test_providers_instantiable(self):
        brave = BraveSearchProvider(api_key="test-key")
        searx = SearXNGProvider(url="http://localhost:8080")
        assert brave.name == "brave"
        assert searx.name == "searxng"

    def test_normalized_models(self):
        r = SearchResult(title="t", url="u", snippet="s", source="brave")
        resp = SearchResponse(query="q", results=[r], provider="brave", search_time_ms=1.0)
        assert resp.results[0].url == "u"
        assert resp.provider == "brave"


# ─── Brave provider ───────────────────────────────────────────────────────────

class TestBraveProvider:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_success_returns_normalized_results(self):
        brave = BraveSearchProvider(api_key="good-key")
        with patch("urllib.request.urlopen", return_value=_FakeResponse(_brave_payload())):
            resp = self._run(brave.search("test query", max_results=5))
        assert resp.provider == "brave"
        assert len(resp.results) == 2
        first = resp.results[0]
        assert isinstance(first, SearchResult)
        assert first.url == "https://example.com/a"
        assert first.source == "brave"
        assert resp.results[1].published_at == "2 days ago"

    def test_missing_api_key_raises_auth_error_before_network(self):
        brave = BraveSearchProvider(api_key="")
        with patch("urllib.request.urlopen") as mock_open:
            with pytest.raises(AuthenticationError):
                self._run(brave.search("query"))
        mock_open.assert_not_called()

    @pytest.mark.parametrize("code", [401, 403])
    def test_authentication_failure(self, code):
        brave = BraveSearchProvider(api_key="bad-key")
        err = HTTPError("url", code, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(AuthenticationError):
                self._run(brave.search("query"))

    def test_rate_limit(self):
        brave = BraveSearchProvider(api_key="key")
        err = HTTPError("url", 429, "Too Many Requests", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RateLimitError):
                self._run(brave.search("query"))

    def test_other_http_error_is_unavailable(self):
        brave = BraveSearchProvider(api_key="key")
        err = HTTPError("url", 500, "Internal Server Error", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(UnavailableError):
                self._run(brave.search("query"))

    def test_network_failure(self):
        brave = BraveSearchProvider(api_key="key")
        with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            with pytest.raises(UnavailableError):
                self._run(brave.search("query"))

    @pytest.mark.parametrize("body", [
        "not json at all",
        json.dumps({"unexpected": "shape"}),
        json.dumps({"web": "not-a-dict"}),
    ])
    def test_malformed_response(self, body):
        brave = BraveSearchProvider(api_key="key")
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            with pytest.raises(MalformedResponseError):
                self._run(brave.search("query"))

    def test_freshness_mapping(self):
        brave = BraveSearchProvider(api_key="key")
        captured = {}

        def _capture(req, timeout=None):
            captured["url"] = req.full_url
            return _FakeResponse(_brave_payload(results=[]))

        with patch("urllib.request.urlopen", side_effect=_capture):
            self._run(brave.search("news", time_range="week"))
        assert "freshness=pw" in captured["url"]

    def test_results_without_url_skipped_and_capped(self):
        payload = _brave_payload(results=[
            {"title": "no url", "url": "", "description": "x"},
            {"title": "ok", "url": "https://a.com", "description": "y"},
            {"title": "over", "url": "https://b.com", "description": "z"},
        ])
        brave = BraveSearchProvider(api_key="key")
        with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
            resp = self._run(brave.search("q", max_results=1))
        assert [r.url for r in resp.results] == ["https://a.com"]


# ─── SearXNG provider ─────────────────────────────────────────────────────────

class TestSearXNGProvider:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_success(self):
        searx = SearXNGProvider(url="http://localhost:8080")
        body = json.dumps({"results": [
            {"title": "Wiki", "url": "https://wiki.example/x", "content": "text",
             "publishedDate": "2026-01-01"},
        ]})
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            resp = self._run(searx.search("query"))
        assert resp.provider == "searxng"
        assert resp.results[0].published_at == "2026-01-01"

    def test_unavailable_endpoint(self):
        searx = SearXNGProvider(url="http://localhost:9999")
        with patch("urllib.request.urlopen", side_effect=URLError("refused")):
            with pytest.raises(UnavailableError):
                self._run(searx.search("query"))

    def test_http_error_is_unavailable(self):
        searx = SearXNGProvider(url="http://localhost:8080")
        err = HTTPError("url", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(UnavailableError):
                self._run(searx.search("query"))

    def test_malformed_response(self):
        searx = SearXNGProvider(url="http://localhost:8080")
        with patch("urllib.request.urlopen", return_value=_FakeResponse("<html>rate limited</html>")):
            with pytest.raises(MalformedResponseError):
                self._run(searx.search("query"))

    def test_empty_configured_url(self):
        searx = SearXNGProvider(url="")
        with pytest.raises(UnavailableError):
            self._run(searx.search("query"))


# ─── WebSearchService ─────────────────────────────────────────────────────────

def _config_get(backend="", **extra):
    values = {"search.backend": backend, "search.brave_api_key": "", "search.url": ""}
    values.update(extra)
    return lambda key, default=None: values.get(key, default)


class TestWebSearchService:
    def test_selects_brave_when_configured(self):
        svc = WebSearchService(config_get=_config_get(
            "brave", **{"search.brave_api_key": "k"}))
        provider = svc.create_provider()
        assert isinstance(provider, BraveSearchProvider)

    def test_selects_searxng_when_configured(self):
        svc = WebSearchService(config_get=_config_get(
            "searxng", **{"search.url": "http://localhost:8080"}))
        provider = svc.create_provider()
        assert isinstance(provider, SearXNGProvider)

    def test_no_provider_configured(self):
        svc = WebSearchService(config_get=_config_get(""))
        with pytest.raises(NotConfiguredError):
            svc.create_provider()
        assert not svc.is_configured()

    def test_search_sync_not_configured_raises(self):
        svc = WebSearchService(config_get=_config_get(""))
        with pytest.raises(NotConfiguredError):
            svc.search_sync("anything")

    def test_no_silent_fallback(self):
        """Brave selected + failing → error surfaces, SearXNG never tried."""
        searx_calls = []

        def _boom_searx(*a, **k):
            searx_calls.append(a)
            raise AssertionError("SearXNG must not be called")

        with patch.object(SearXNGProvider, "search", side_effect=_boom_searx):
            svc = WebSearchService(config_get=_config_get(
                "brave", **{"search.brave_api_key": "expired"}))
            err = HTTPError("url", 401, "Unauthorized", {}, None)
            with patch("urllib.request.urlopen", side_effect=err):
                with pytest.raises(AuthenticationError) as exc_info:
                    svc.search_sync("query")
        assert exc_info.value.provider == "brave"
        assert searx_calls == []

    def test_test_connection_not_configured(self):
        svc = WebSearchService(config_get=_config_get(""))
        result = asyncio.run(svc.test_connection())
        assert result["state"] == "not_configured"

    def test_test_connection_connected_searxng(self):
        svc = WebSearchService(config_get=_config_get(
            "searxng", **{"search.url": "http://localhost:8080"}))
        async def healthy(self):
            return ProviderHealth(ok=True, state="connected", message="SearXNG connected.")
        with patch.object(SearXNGProvider, "health_check", healthy):
            result = asyncio.run(svc.test_connection())
        assert result["state"] == "connected"

    def test_test_connection_auth_failed_brave(self):
        svc = WebSearchService(config_get=_config_get("brave", **{"search.brave_api_key": "bad"}))
        async def auth_fail(self):
            return ProviderHealth(ok=False, state="auth_failed", message="Invalid API key.")
        with patch.object(BraveSearchProvider, "health_check", auth_fail):
            result = asyncio.run(svc.test_connection())
        assert result["state"] == "auth_failed"
        assert "Invalid" in result["message"]


# ─── Capability / tool registration ───────────────────────────────────────────

class TestWebSearchCapability:
    def test_web_search_tool_registered(self):
        import novi.tools  # noqa: F401 — triggers registration
        from novi.tools import TOOL_REGISTRY
        assert "web_search" in TOOL_REGISTRY

    def test_no_provider_specific_tools_registered(self):
        import novi.tools  # noqa: F401
        from novi.tools import TOOL_REGISTRY
        assert "brave_search" not in TOOL_REGISTRY
        assert "searxng_search" not in TOOL_REGISTRY

    def test_tool_invokes_service_and_formats_sources(self, monkeypatch):
        import novi.tools as tools_mod
        import novi.tools.web_search as ws_mod

        class _FakeService:
            def __init__(self, *a, **k):
                pass

            def search_sync(self, query, *, max_results=5, time_range=None):
                return SearchResponse(query=query, results=[
                    SearchResult(title="Example Source — Article",
                                 url="https://example.com/article",
                                 snippet="Useful text", source="brave"),
                ], provider="brave")

        monkeypatch.setattr(ws_mod, "WebSearchService", _FakeService)
        out = tools_mod.TOOL_REGISTRY["web_search"]("current events")
        assert "https://example.com/article" in out
        assert "Example Source — Article" in out

    def test_tool_reports_provider_failure_verbatim(self, monkeypatch):
        import novi.tools as tools_mod
        import novi.tools.web_search as ws_mod

        class _Failing:
            def __init__(self, *a, **k):
                pass

            def search_sync(self, *a, **k):
                raise AuthenticationError("brave", "Brave Search authentication failed. Check your API key.")

        monkeypatch.setattr(ws_mod, "WebSearchService", _Failing)
        out = tools_mod.TOOL_REGISTRY["web_search"]("query")
        assert "Brave Search authentication failed" in out
