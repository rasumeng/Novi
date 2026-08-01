"""Long-term memory tools. Direct access to the persistent MemoryManager store."""

from ..memory.manager import get_memory_manager
from . import register_tool


@register_tool()
def search_memory(query: str, k: int = 5) -> str:
    """Semantic search across long-term memory (preferences, facts, past interactions).

    Args:
        query: Natural language search query.
        k: Number of results to return (max 20).
    """
    mem = get_memory_manager()
    if mem is None:
        return "[error] Memory not initialized. Start a chat session first."
    results = mem.query(query, k=min(k, 20), distance_threshold=1.0)
    if not results:
        return "[info] No matching memories found."
    lines = []
    for r in results:
        meta = r.get("metadata", {})
        mtype = meta.get("type", "memory")
        title = meta.get("title", "") or mtype
        score = r.get("score", 0.0)
        text = r.get("text", "")[:300].replace("\n", " ")
        lines.append(f"- **[{mtype}] {title}** (score={score:.2f}): {text}")
    return "\n".join(lines)
