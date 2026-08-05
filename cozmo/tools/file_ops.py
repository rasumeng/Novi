from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..memory.knowledge_index import get_knowledge_index

from . import register_tool

# Restrict reads to project root or subdirs
ALLOWED_ROOT = Path.cwd()
KNOWLEDGE = Path("./knowledge").resolve()


def set_allowed_root(root: str | Path):
    """Update the allowed root directory for file operations at runtime."""
    global ALLOWED_ROOT
    ALLOWED_ROOT = Path(root).resolve()


def _safe_path(path: str) -> Path | None:
    resolved = (ALLOWED_ROOT / path).resolve()
    if ALLOWED_ROOT in resolved.parents or resolved == ALLOWED_ROOT:
        return resolved
    return None


@register_tool()
def read_file(path: str) -> str:
    """Read contents of a text file (capped at 5000 chars)."""
    safe = _safe_path(path)
    if safe is None:
        return "Error: path outside allowed directory"
    if not safe.exists():
        return "Error: file not found"
    if not safe.is_file():
        return "Error: not a file"
    try:
        text = safe.read_text(encoding="utf-8")
        if len(text) > 5000:
            return text[:5000] + f"\n... [truncated, {len(text)} total chars]"
        return text
    except Exception as e:
        return f"Error reading file: {e}"


@register_tool()
def list_directory(path: str = ".") -> str:
    """List files and directories at the given path."""
    safe = _safe_path(path)
    if safe is None:
        return "Error: path outside allowed directory"
    if not safe.exists():
        return "Error: path not found"
    if not safe.is_dir():
        return "Error: not a directory"
    try:
        entries = [str(e.name) + ("/" if e.is_dir() else "") for e in safe.iterdir()]
        return "\n".join(sorted(entries))
    except Exception as e:
        return f"Error listing directory: {e}"


@register_tool()
def glob_search(pattern: str, path: str = ".") -> str:
    """Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts').

    Args:
        pattern: Glob pattern to match (supports **, *, ?).
        path: Directory to search in (default: current directory).
    """
    import fnmatch
    safe = _safe_path(path)
    if safe is None:
        return "Error: path outside allowed directory"
    if not safe.exists():
        return "Error: path not found"
    matches = []
    for f in safe.rglob("*"):
        if not f.is_file():
            continue
        if ".git" in f.parts or "__pycache__" in f.parts or "node_modules" in f.parts:
            continue
        rel = str(f.relative_to(safe))
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f.name, pattern):
            matches.append(rel)
    if not matches:
        return "No matches found."
    matches.sort()
    if len(matches) > 200:
        return "\n".join(matches[:200]) + f"\n... [{len(matches) - 200} more]"
    return "\n".join(matches)


@register_tool()
def read(path: str, offset: int = 0, limit: int = 0) -> str:
    """Read file contents with optional line range.

    Args:
        path: File path (relative to project root).
        offset: Line number to start from (1-indexed). 0 = from beginning.
        limit: Max number of lines to return. 0 = no limit.
    """
    safe = _safe_path(path)
    if safe is None:
        return "Error: path outside allowed directory"
    if not safe.exists():
        return "Error: file not found"
    if not safe.is_file():
        return "Error: not a file"
    try:
        lines = safe.read_text(encoding="utf-8").splitlines(keepends=True)
        total = len(lines)
        if offset > 0:
            start = max(0, offset - 1)
        else:
            start = 0
        if limit > 0:
            end = min(total, start + limit)
        else:
            end = total
        selected = lines[start:end]
        numbered = [f"{i + 1}: {line.rstrip()}" for i, line in enumerate(selected, start=start)]
        result = "\n".join(numbered)
        if end < total:
            result += f"\n... [{total - end} more lines]"
        return result
    except Exception as e:
        return f"Error reading file: {e}"


@register_tool()
def read_knowledge(path: str) -> str:
    """Read a file from the knowledge base.

    Args:
        path: Relative path inside knowledge base (e.g. 'learnings/python-patterns.md').
    """
    try:
        target = (KNOWLEDGE / path).resolve()
        try:
            target.relative_to(KNOWLEDGE)
        except ValueError:
            return "[error] Path traversal not allowed"
        if not target.exists():
            return f"[error] File not found: {path}"
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"[error] {e}"


@register_tool()
def search_knowledge(query: str, k: int = 5) -> str:
    """Semantic search across the knowledge base. Returns ranked results with titles and excerpts.

    Args:
        query: Natural language search query.
        k: Number of results to return (max 20).
    """
    ki = get_knowledge_index()
    if ki is None:
        return "[error] Knowledge index not initialized. Start a chat session first."

    from ..runtime.evidence import RetrievalQuality
    from ..runtime.retrieval_budget import ContextAllocation
    from ..runtime.sources import KnowledgeRetrievalSource

    # Phase 9 step 6: delegate store access + translation to the shared
    # RetrievalSource adapter. k=min(k,20) matches the pre-unification call
    # exactly (rerank=True is the index default); formatting is preserved below.
    source = KnowledgeRetrievalSource(ki)
    result = source.retrieve(query, ContextAllocation(max_results=min(k, 20)))
    if result.quality == RetrievalQuality.FAILED:
        raise RuntimeError(result.error or "knowledge retrieval failed")
    if not result.items:
        return "[info] No matching knowledge found."
    lines = []
    for item in result.items:
        meta = item.metadata
        path = meta.get("path", "?")
        title = meta.get("title", path)
        text = item.text[:300].replace("\n", " ")
        lines.append(f"- **{title}** ({path}, score={item.score:.2f}): {text}")
    return "\n".join(lines)


@register_tool()
def write_knowledge(path: str, content: str, type: str = "Reference", title: str = "", tags: list[str] | None = None) -> str:
    """Write a file to the knowledge base with OKF frontmatter.

    Args:
        path: Relative path inside knowledge base (e.g. 'learnings/new-thing.md').
        content: The markdown body content.
        type: Concept type (Conversation, Learning, Project, Reference).
        title: Human-readable title.
        tags: List of tags for categorization.
    """
    try:
        target = (KNOWLEDGE / path).resolve()
        try:
            target.relative_to(KNOWLEDGE)
        except ValueError:
            return "[error] Path traversal not allowed"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = title or path.replace(".md", "").replace("/", " - ").replace("-", " ").title()
        tags = tags or []

        frontmatter = {
            "type": type,
            "title": title,
            "tags": tags,
            "timestamp": now,
        }

        with open(target, "w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(frontmatter, f, default_flow_style=False, allow_unicode=True)
            f.write("---\n\n")
            f.write(content)

        from ..memory.knowledge_index import get_knowledge_index
        ki = get_knowledge_index()
        if ki is not None:
            ki.index_file(target)

        # Architecture Rule #6: knowledge writes go through the Brain. The
        # file knowledge base + index remain for backward compatibility with
        # read_knowledge/search_knowledge; the Brain is additionally the
        # canonical writer so the claim is immediately discoverable via recall.
        from ..brain.brain import get_brain
        brain = get_brain()
        if brain is not None:
            try:
                brain.learn(content, source="knowledge")
            except Exception:
                pass
        return f"[ok] Written to knowledge/{path}"
    except Exception as e:
        return f"[error] {e}"