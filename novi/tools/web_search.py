import re
import logging
import urllib.request
from datetime import datetime, timezone

from ..search import SearchProviderError, WebSearchService
from . import register_tool

log = logging.getLogger("novi.search")


@register_tool()
def web_search(query: str, max_results: int = 5, timelimit: str = None) -> str:
    """Search the web for current information. Returns date-stamped results with title + snippet + URL.

    Routes through Novi's configured search provider (Settings → Connectors → Web Search).

    Args:
        query: Search query
        max_results: Number of results (default 5)
        timelimit: Time filter - 'd' (day), 'w' (week), 'm' (month), 'y' (year). Default: None (all time)
    """
    if not query or not query.strip():
        return "No search query provided."

    try:
        response = WebSearchService().search_sync(
            query,
            max_results=max_results,
            time_range=timelimit,
        )
    except SearchProviderError as e:
        # Typed provider failures surface verbatim — no silent fallback.
        return f"Web search failed ({e.provider}): {e.message}"
    except Exception as e:
        log.warning("web_search unexpected failure: %s", e, exc_info=True)
        return "Web search failed with an unexpected error."

    if not response.results:
        return f"No results found for '{query}'."

    search_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Search performed: {search_date}"]
    for i, r in enumerate(response.results, 1):
        lines.append(f"{i}. **{r.title}**\n   {r.snippet}\n   {r.url}")
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
