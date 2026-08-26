"""Diagnostics and code intelligence tools.

- diagnostics(path): LSP error retrieval stub
- sourcegraph(query): Search public repos via Sourcegraph API
"""
import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path

from . import register_tool

log = logging.getLogger("novi.tools.diagnostics")


@register_tool()
def diagnostics(path: str = ".") -> str:
    """Get diagnostics (errors, warnings) for a file or directory.

    Currently a stub that checks for common issues. Can be enhanced
    with LSP integration later.

    Args:
        path: File or directory path to check. Default: current directory.
    """
    target = Path(path).resolve()
    if not target.exists():
        return f"Error: path not found: {path}"

    results = []

    if target.is_file():
        results.extend(_check_file(target))
    elif target.is_dir():
        # Check common config files and Python files
        for f in target.rglob("*.py"):
            if "__pycache__" in f.parts or "node_modules" in f.parts:
                continue
            file_results = _check_file(f)
            if file_results:
                results.extend(file_results)
        # Check for common issues
        results.extend(_check_project(target))

    if not results:
        return "No diagnostics found."

    return "\n".join(results)


def _check_file(path: Path) -> list[str]:
    """Basic file-level checks."""
    issues = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        # Python syntax check
        if path.suffix == ".py":
            try:
                compile(content, str(path), "exec")
            except SyntaxError as e:
                issues.append(f"{path}:{e.lineno}: error: {e.msg}")

        # Check for common Python issues
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Bare except
            if stripped == "except:":
                issues.append(f"{path}:{i}: warning: bare except (use specific exception)")
            # TODO/FIXME
            if stripped.startswith("# TODO") or stripped.startswith("# FIXME"):
                issues.append(f"{path}:{i}: info: {stripped[:60]}")

    except Exception as e:
        issues.append(f"{path}: error: {e}")

    return issues


def _check_project(path: Path) -> list[str]:
    """Project-level checks."""
    issues = []

    # Check for requirements.txt without pyproject.toml
    has_requirements = (path / "requirements.txt").exists()
    has_pyproject = (path / "pyproject.toml").exists()
    if has_requirements and not has_pyproject:
        issues.append(f"{path}: info: has requirements.txt but no pyproject.toml")

    # Check for .gitignore
    if not (path / ".gitignore").exists():
        issues.append(f"{path}: info: no .gitignore found")

    # Check for common missing config
    if not (path / ".env.example").exists() and (path / ".env").exists():
        issues.append(f"{path}: info: has .env but no .env.example")

    return issues


@register_tool()
def sourcegraph(query: str, max_results: int = 10) -> str:
    """Search public code on Sourcegraph. Requires SOURCEGRAPH_URL and optional SOURCEGRAPH_TOKEN env vars.

    Args:
        query: Search query (supports Sourcegraph search syntax).
        max_results: Maximum results to return (default 10).
    """
    import os
    sg_url = os.environ.get("SOURCEGRAPH_URL", "https://sourcegraph.com")
    sg_token = os.environ.get("SOURCEGRAPH_TOKEN", "")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Novi/1.0",
    }
    if sg_token:
        headers["Authorization"] = f"token {sg_token}"

    # Use the search API
    params = urllib.parse.urlencode({
        "q": query,
        "first": str(max_results),
    })
    url = f"{sg_url}/api/search/stream?{params}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            # Sourcegraph streaming API returns NDJSON
            data = resp.read().decode("utf-8", errors="replace")

        results = []
        for line in data.strip().splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "match":
                    repo = event.get("data", {}).get("repository", {})
                    repo_name = repo.get("name", "unknown")
                    file_path = event.get("data", {}).get("path", "")
                    line_num = event.get("data", {}).get("line", {}).get("lineNumber", "")
                    preview = event.get("data", {}).get("line", {}).get("preview", "")
                    results.append(f"- **{repo_name}** `{file_path}:{line_num}`\n  {preview[:200]}")
            except json.JSONDecodeError:
                continue

        if not results:
            # Try the simpler search API
            url2 = f"{sg_url}/api/repos/search?{params}"
            req2 = urllib.request.Request(url2, headers=headers)
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                data2 = json.loads(resp2.read().decode("utf-8"))
            repos = data2.get("repositories", [])
            for r in repos[:max_results]:
                name = r.get("name", "")
                desc = r.get("description", "")
                stars = r.get("stars", 0)
                results.append(f"- **{name}** ({stars}★) {desc}")

        if not results:
            return "No results found."

        header = f"Sourcegraph search: `{query}` ({len(results)} results)"
        return header + "\n\n" + "\n".join(results)

    except urllib.error.HTTPError as e:
        return f"Sourcegraph API error: {e.code} {e.reason}"
    except Exception as e:
        return f"Error searching Sourcegraph: {e}"
