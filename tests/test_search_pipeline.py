"""Tests for search pipeline — time_range mapping, error propagation, HTTP 400 handling."""

from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import pytest

from novi.tools.search_pipeline import (
    _SEARXNG_TIME_MAP,
    _search_searxng,
    _search_multi,
    SearchConfig,
    SearchResult,
)


class _FakeResponse:
    def __init__(self, body: bytes = b'{"results": []}'):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


@pytest.fixture
def searxng_stub(monkeypatch):
    """Stub SearXNG bootstrap so tests never touch the live server or Docker."""
    monkeypatch.setattr("novi.tools.search_pipeline._ensure_searxng", lambda: "http://localhost:8080")


class TestTimeRangeMapping:
    """_SEARXNG_TIME_MAP maps short codes to SearXNG values."""

    def test_known_short_codes(self):
        assert _SEARXNG_TIME_MAP["d"] == "day"
        assert _SEARXNG_TIME_MAP["w"] == "week"
        assert _SEARXNG_TIME_MAP["m"] == "month"
        assert _SEARXNG_TIME_MAP["y"] == "year"

    def test_unknown_code_passthrough(self, searxng_stub):
        """Invalid code passes through to SearXNG, which rejects it with HTTP 400."""
        config = SearchConfig(timelimit="invalid")
        with patch("urllib.request.urlopen", side_effect=HTTPError("url", 400, "Bad Request", {}, None)):
            results, err = _search_searxng("test", config)
        assert err is not None
        assert "HTTP 400" in err
        assert results == []

    def test_none_timelimit_no_time_range(self, searxng_stub):
        config = SearchConfig(timelimit=None)
        with patch("urllib.request.urlopen", return_value=_FakeResponse()):
            results, err = _search_searxng("test", config)
        assert err is None
        assert isinstance(results, list)


class TestSearchErrorPropagation:
    """_search_searxng and _search_multi return (results, error) tuples."""

    def test_returns_tuple(self, searxng_stub):
        config = SearchConfig()
        with patch("urllib.request.urlopen", return_value=_FakeResponse()):
            results, err = _search_searxng("test", config)
        assert isinstance(results, list)
        assert err is None or isinstance(err, str)

    def test_search_multi_returns_tuple(self, searxng_stub):
        config = SearchConfig()
        with patch("urllib.request.urlopen", return_value=_FakeResponse()):
            results, err = _search_multi("test", config)
        assert isinstance(results, list)
        assert err is None or isinstance(err, str)

    def test_empty_query_no_error(self):
        config = SearchConfig()
        results, err = _search_searxng("", config)
        assert results == []
        assert err is None

        results, err = _search_multi("", config)
        assert results == []
        assert err is None


class TestEvidenceBundleError:
    """EvidenceBundle carries error field through collect()."""

    def test_error_on_search_failure(self):
        with patch("novi.tools.search_pipeline._search_searxng") as mock_search:
            mock_search.return_value = ([], "HTTP 400: Bad Request")
            config = SearchConfig()
            results, err = _search_multi("query", config)
            assert results == []
            assert err == "HTTP 400: Bad Request"

    def test_no_error_on_success(self):
        with patch("novi.tools.search_pipeline._search_searxng") as mock_search:
            mock_search.return_value = ([SearchResult(title="A", url="http://a", snippet="test")], None)
            config = SearchConfig()
            results, err = _search_multi("query", config)
            assert len(results) == 1
            assert err is None
