"""WorkspaceIndex — incremental file metadata + content search.

Minimal viable for beta: metadata/path search + efficient content search
→ candidate files → targeted chunk extraction → Context Manager budget.

No vector required for beta. Extensible: add vector column / embeddings
later without changing the WorkspaceRetrievalSource interface.

Index schema (sqlite):
  files(path PK, hash, mtime, size, ext, lang, parent)
  chunks(file_path, chunk_id, text) — lazy, read on demand for now

Exclusions: .git, node_modules, .venv, venv, dist, build, target, __pycache__, .cache
plus binary/unsupported via mimetype.

Incremental: file unchanged (hash+mtime) → reuse; changed → re-index; new → add; deleted → remove.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import List, Dict

DEFAULT_EXCLUDES = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "target",
    "__pycache__", ".cache", ".mypy_cache", ".pytest_cache", ".next", ".turbo",
}

TEXT_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".sh", ".rs", ".go", ".java", ".c", ".cpp", ".h",
    ".css", ".html", ".sql", ".env.example", ".lock",
}

# also allow no-ext files like Dockerfile, Makefile
TEXT_BASENAMES = {"Dockerfile", "Makefile", "README", "LICENSE", "CHANGELOG"}


def _file_hash(path: Path) -> str:
    h = hashlib.sha1()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return ""


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTS:
        return True
    if path.name in TEXT_BASENAMES or path.name.startswith("README"):
        return True
    # fallback: try read small prefix
    try:
        with path.open("rb") as f:
            sample = f.read(1024)
            if b"\x00" in sample:
                return False
            sample.decode("utf-8")
            return True
    except Exception:
        return False


def _should_exclude(path: Path, root: Path, excludes: set[str]) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    for part in rel.parts:
        if part in excludes:
            return True
        if part.startswith(".") and part not in {".env.example"}:
            # hide dotfiles except allowed
            if part in {".git", ".hg", ".cache"}:
                return True
    return False


class WorkspaceIndex:
    """Per-project workspace index — sqlite at ~/.novi/workspaces/{projId}/index.sqlite"""

    def __init__(self, index_path: Path):
        self.index_path = Path(index_path)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        con = sqlite3.connect(self.index_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                hash TEXT, mtime REAL, size INTEGER,
                ext TEXT, lang TEXT, parent TEXT
            )
        """)
        con.commit()
        con.close()

    def sync(self, root: Path, excludes: set[str] | None = None) -> Dict[str, int]:
        """Incremental sync: walk root, upsert changed, prune deleted."""
        excludes = excludes or DEFAULT_EXCLUDES
        root = Path(root).resolve()
        if not root.exists():
            return {"added": 0, "changed": 0, "removed": 0, "total": 0}
        # current index
        con = sqlite3.connect(self.index_path)
        cur = con.cursor()
        existing = {row[0]: row for row in cur.execute("SELECT path, hash, mtime FROM files")}
        seen: set[str] = set()
        added = changed = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # prune excluded dirs in-place
            dirnames[:] = [d for d in dirnames if d not in excludes and not (d.startswith(".") and d in excludes)]
            for fn in filenames:
                fpath = Path(dirpath) / fn
                if _should_exclude(fpath, root, excludes):
                    continue
                if not _is_text_file(fpath):
                    continue
                try:
                    stat = fpath.stat()
                    rel = str(fpath.relative_to(root))
                    seen.add(rel)
                    h = _file_hash(fpath)
                    mtime = stat.st_mtime
                    size = stat.st_size
                    ext = fpath.suffix.lower()
                    parent = str(Path(rel).parent)
                    row = existing.get(rel)
                    if row is None:
                        cur.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)",
                                    (rel, h, mtime, size, ext, ext.lstrip("."), parent))
                        added += 1
                    elif row[1] != h or abs(row[2] - mtime) > 0.01:
                        cur.execute("UPDATE files SET hash=?, mtime=?, size=? WHERE path=?",
                                    (h, mtime, size, rel))
                        changed += 1
                except Exception:
                    continue
        # removed
        removed = 0
        for rel in list(existing.keys()):
            if rel not in seen:
                cur.execute("DELETE FROM files WHERE path=?", (rel,))
                removed += 1
        con.commit()
        total = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        con.close()
        return {"added": added, "changed": changed, "removed": removed, "total": total}

    def list_files(self) -> List[Dict]:
        con = sqlite3.connect(self.index_path)
        rows = con.execute("SELECT path, ext, parent, size FROM files ORDER BY path").fetchall()
        con.close()
        return [{"path": r[0], "ext": r[1], "parent": r[2], "size": r[3]} for r in rows]

    def search_path(self, query: str, k: int = 10) -> List[Dict]:
        """Filename/path substring search — cheap, no vector."""
        q = query.lower()
        terms = [t for t in q.split() if len(t) > 1]
        if not terms:
            return []
        con = sqlite3.connect(self.index_path)
        all_files = con.execute("SELECT path FROM files").fetchall()
        con.close()
        scored = []
        for (path,) in all_files:
            low = path.lower()
            score = sum(1 for t in terms if t in low)
            if score:
                # boost exact filename
                if Path(path).name.lower() in terms:
                    score += 1
                scored.append((score, path))
        scored.sort(reverse=True)
        return [{"path": p, "score": s} for s, p in scored[:k]]

    def search_content(self, root: Path, query: str, k: int = 10) -> List[Dict]:
        """Efficient content search — grep-like, targeted, not vector."""
        q = query.lower()
        terms = [t for t in q.split() if len(t) > 2]
        if not terms:
            return []
        root = Path(root).resolve()
        hits: List[Dict] = []
        con = sqlite3.connect(self.index_path)
        files = [r[0] for r in con.execute("SELECT path FROM files").fetchall()]
        con.close()
        for rel in files:
            fpath = root / rel
            try:
                text = fpath.read_text("utf-8", errors="ignore")[:20000]
                low = text.lower()
                score = sum(low.count(t) for t in terms)
                if score:
                    # find snippet
                    idx = low.find(terms[0])
                    snippet = text[max(0, idx-200): idx+800].strip().replace("\n", " ")[:600]
                    hits.append({"path": rel, "score": score, "snippet": snippet})
            except Exception:
                continue
        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits[:k]

    def read_file(self, root: Path, rel_path: str, max_chars: int = 6000) -> str | None:
        """READ capability — returns file text or None if not allowed / not found."""
        root = Path(root).resolve()
        target = (root / rel_path).resolve()
        # path traversal block: must be within root
        try:
            target.relative_to(root)
        except ValueError:
            return None
        if not target.exists() or not target.is_file():
            return None
        if not _is_text_file(target):
            return None
        try:
            text = target.read_text("utf-8", errors="ignore")
            if len(text) > max_chars:
                text = text[:max_chars] + "\n…[truncated]"
            return text
        except Exception:
            return None
