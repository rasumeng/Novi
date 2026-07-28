import re
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from . import register_tool

log = logging.getLogger("cozmo.search")


_SEARXNG_TIME_MAP = {
    "d": "day",
    "w": "week",
    "m": "month",
    "y": "year",
}


def _search_searxng(query: str, max_results: int = 5, timelimit: str = None) -> list[dict]:
    if not query or not query.strip():
        return []
    try:
        from ..searxng_util import ensure_searxng
        searxng_url = ensure_searxng()
    except Exception:
        return []

    if not searxng_url:
        return []

    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "language": "en",
    })
    if timelimit:
        time_val = _SEARXNG_TIME_MAP.get(timelimit, timelimit)
        params += f"&time_range={time_val}"

    url = f"{searxng_url}/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Cozmo/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            })
        return results
    except Exception as e:
        log.warning("SearXNG search failed: %s", e)
        return []


@register_tool()
def web_search(query: str, max_results: int = 5, timelimit: str = None) -> str:
    """Search the web for current information. Returns date-stamped results with title + snippet + URL.

    Uses SearXNG (self-hosted) for all searches.

    Args:
        query: Search query
        max_results: Number of results (default 5)
        timelimit: Time filter - 'd' (day), 'w' (week), 'm' (month), 'y' (year). Default: None (all time)
    """
    search_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    results = _search_searxng(query, max_results, timelimit)
    if not results:
        return "Web search unavailable: SearXNG returned no results (is the SearXNG container running?)"
    lines = [f"Search performed: {search_date}"]
    for i, r in enumerate(results, 1):
        title = r.get("title", r.get("title", ""))
        snippet = r.get("snippet", r.get("body", ""))
        href = r.get("url", r.get("href", ""))
        lines.append(f"{i}. **{title}**\n   {snippet}\n   {href}")
    return "\n\n".join(lines)


@register_tool()
def fetch_url(url: str, max_length: int = 2000) -> str:
    """Fetch a URL and return clean text content.

    Args:
        url: The URL to fetch.
        max_length: Maximum characters to return (default 2000).
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_length:
            text = text[:max_length] + "\n[truncated]"

        return text
    except Exception as e:
        return f"[error] Failed to fetch URL: {e}"


@register_tool()
def web_fetch(url: str, max_length: int = 5000) -> str:
    """Fetch and read content from a URL. Returns cleaned article text using trafilatura."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                favor_precision=False,
                favor_recall=True,
            )
            if text and len(text) > 100:
                if len(text) > max_length:
                    text = text[:max_length] + "..."
                return text

        import urllib.request
        import re
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_length:
            text = text[:max_length] + "..."

        return text or "No readable content found."
    except Exception as e:
        return f"Error fetching URL: {e}"


@register_tool()
def webfetch(url: str, format: str = "markdown", max_length: int = 8000) -> str:
    """Fetch content from a URL and return in specified format.

    Args:
        url: The URL to fetch.
        format: Output format - 'markdown' (default), 'text', or 'html'.
        max_length: Maximum characters to return (default 8000).
    """
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded and format == "markdown":
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                favor_precision=False,
                favor_recall=True,
                output_format="txt",
            )
            if text and len(text) > 100:
                if len(text) > max_length:
                    text = text[:max_length] + "\n[truncated]"
                return text
        elif downloaded and format == "html":
            if len(downloaded) > max_length:
                return downloaded[:max_length] + "\n[truncated]"
            return downloaded

        # Fallback: raw fetch
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        if format == "html":
            if len(raw) > max_length:
                return raw[:max_length] + "\n[truncated]"
            return raw

        # text or markdown fallback: strip HTML
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_length:
            text = text[:max_length] + "\n[truncated]"

        return text or "No readable content found."
    except Exception as e:
        return f"Error fetching URL: {e}"
