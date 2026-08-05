"""Memory inspection + correction tools (Phase F Step 7 trust surface).

Routes exclusively through the Brain facade (Architecture Rule #1): no global
bypass store, no direct storage access. Reads are ``inspect_memory()`` /
``project_context()``; writes are ``correct_memory()`` (append-only demote/
supersede/archive through ``update_status`` + edges).
"""

from ..brain.brain import get_brain
from . import register_tool


@register_tool()
def inspect_memory() -> str:
    """List what Cozmo remembers, grouped by category.

    Read-only. Returns each item's status, confidence, importance, last seen,
    and any supersede/conflict edges, plus a grouped personal-context view.
    """
    brain = get_brain()
    if brain is None:
        return "[error] Brain not initialized. Start a chat session first."
    view = brain.inspect_memory()
    if not view:
        return "[info] No knowledge stored."

    lines = ["**Personal context:**"]
    categories = view.get("categories", {})
    if not categories:
        lines.append("- (none)")
    for category, entries in sorted(categories.items()):
        for e in entries:
            lines.append(
                f"- [{category}] **{e['content']}** "
                f"(status={e['status']}, conf={e['confidence']:.2f}, "
                f"importance={e['importance']:.2f})"
            )

    lines.append("\n**All items:**")
    items = view.get("items", [])
    if not items:
        lines.append("- (none)")
    for i in items:
        edges = f", edges=[{', '.join(i['edges'])}]" if i["edges"] else ""
        lines.append(
            f"- `{i['id']}` [{i['status']}] {i['content']} "
            f"(conf={i['confidence']:.2f}, last={i['last_seen_at']}){edges}"
        )
    return "\n".join(lines)


@register_tool()
def correct_memory(
    item_id: str,
    action: str = "superseded",
    statement: str = "",
    tags: str = "",
) -> str:
    """Correct what Cozmo remembers (append-only, never deletes).

    Args:
        item_id: The id of the knowledge item to correct (from inspect_memory).
        action: One of "superseded" (mark obsolete + optionally record a
            replacement), "demote" (lower to corroborated), or "archive"
            (demote to candidate — out of default retrieval, still queryable).
        statement: Replacement fact for action="superseded"; recorded as a new
            verified item linked by a supersedes edge.
        tags: Comma-separated tags for the replacement statement.
    """
    brain = get_brain()
    if brain is None:
        return "[error] Brain not initialized. Start a chat session first."
    tag_tuple = tuple(t.strip() for t in tags.split(",") if t.strip())
    result = brain.correct_memory(
        item_id, statement=statement or None, action=action, tags=tag_tuple
    )
    if not result.get("ok"):
        return f"[error] {result.get('error', 'correction failed')}"
    parts = []
    if "superseded" in result:
        parts.append(f"superseded {result['superseded']}")
    if "demoted" in result:
        parts.append(f"demoted {result['demoted']}")
    if "archived" in result:
        parts.append(f"archived {result['archived']}")
    if result.get("recorded"):
        parts.append(f"recorded new item {result['recorded']}")
    return f"[ok] {', '.join(parts)}."