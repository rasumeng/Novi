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
        return f"Error: Web search failed ({e.provider}): {e.message}"
    except Exception as e:
        log.warning("web_search unexpected failure: %s", e, exc_info=True)
        return "Error: Web search failed with an unexpected error."

    if not response.results:
        return f"No results found for '{query}'."

    search_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Search performed: {search_date}"]
    for i, r in enumerate(response.results, 1):
        lines.append(f"{i}. **{r.title}**\n   {r.snippet}\n   Published: {r.published_at or 'unknown'}\n   {r.url}")
    return "\n\n".join(lines)


@register_tool()
def fetch_url(url: str, max_length: int = 2000) -> str:
    """Read a public URL as text."""
    return web_fetch(url, max_length)


@register_tool()
def web_fetch(url: str, max_length: int = 5000) -> str:
    """Read a public web page with bounded download size and deadline."""
    from ..search.reader import read_page
    try:
        return read_page(url, max_length=max_length)
    except Exception as exc:
        return f"Error: Failed to fetch URL: {exc}"


@register_tool()
def webfetch(url: str, format: str = "markdown", max_length: int = 8000) -> str:
    """Read a public URL as text, markdown, or HTML."""
    from ..search.reader import read_page
    try:
        if format not in ("markdown", "text", "html"):
            return "Error: Unsupported page format"
        return read_page(url, max_length=max_length, output_format=format)
    except Exception as exc:
        return f"Error: Failed to fetch URL: {exc}"
