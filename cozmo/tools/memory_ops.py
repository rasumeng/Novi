"""Long-term memory tools. Reads go through the Brain when wired (Rule #6)."""

from ..brain.brain import get_brain
from ..memory.manager import get_memory_manager
from . import register_tool


@register_tool()
def search_memory(query: str, k: int = 5) -> str:
    """Semantic search across long-term memory (preferences, facts, past interactions).

    Args:
        query: Natural language search query.
        k: Number of results to return (max 20).
    """
    backend = get_brain() or get_memory_manager()
    if backend is None:
        return "[error] Memory not initialized. Start a chat session first."

    from ..runtime.evidence import RetrievalQuality
    from ..runtime.retrieval_budget import ContextAllocation
    from ..runtime.sources import MemoryRetrievalSource

    # Delegate store access + translation to the shared RetrievalSource
    # adapter; when a Brain is wired the adapter reads through Brain.recall.
    # distance_threshold=1.0 and k=min(k,20) match the pre-unification call
    # exactly; formatting is preserved below.
    source = MemoryRetrievalSource(backend, distance_threshold=1.0)
    result = source.retrieve(query, ContextAllocation(max_results=min(k, 20)))
    if result.quality == RetrievalQuality.FAILED:
        raise RuntimeError(result.error or "memory retrieval failed")
    if not result.items:
        return "[info] No matching memories found."
    lines = []
    for item in result.items:
        meta = item.metadata
        mtype = meta.get("type", "memory")
        title = meta.get("title", "") or mtype
        text = item.text[:300].replace("\n", " ")
        lines.append(f"- **[{mtype}] {title}** (score={item.score:.2f}): {text}")
    return "\n".join(lines)
