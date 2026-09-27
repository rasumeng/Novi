"""WorkspaceService — attach, sync, search, read with READ boundary."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from ..paths import home as app_home
from .capability import WorkspaceCapability
from .index import WorkspaceIndex, DEFAULT_EXCLUDES


def _source_hash(root: str) -> str:
    """Generate a short hash for a source root path."""
    return hashlib.sha1(root.encode()).hexdigest()[:12]


class WorkspaceService:
    """Manages per-project and per-conversation source grants and indexes."""

    def __init__(self):
        self._base = Path(app_home()) / "workspaces"
        self.MAX_SOURCES = 3

    def _project_base(self, project_id: str) -> Path:
        return self._base / f"proj_{project_id}"

    def _conversation_base(self, conversation_id: str) -> Path:
        return self._base / f"conv_{conversation_id}"

    def _sources_config_path(self, base: Path) -> Path:
        return base / "sources.json"

    def _source_index_path(self, base: Path, source_hash: str) -> Path:
        return base / source_hash / "index.sqlite"

    def _source_config_path(self, base: Path, source_hash: str) -> Path:
        return base / source_hash / "config.json"

    # Beta guardrail: prevent indexing monstrous trees that would freeze UI
    MAX_FILES_BETA = 10000
    MAX_BYTES_BETA = 500 * 1024 * 1024

    def _load_sources(self, base: Path) -> List[Dict]:
        """Load sources config from disk."""
        config_path = self._sources_config_path(base)
        if not config_path.exists():
            return []
        try:
            sources = json.loads(config_path.read_text("utf-8"))
            if not isinstance(sources, list):
                return []
            for source in sources:
                if isinstance(source, dict) and "indexedAt" not in source and "indexed_at" in source:
                    source["indexedAt"] = source.pop("indexed_at")
            return [source for source in sources if isinstance(source, dict)]
        except Exception:
            return []

    def _save_sources(self, base: Path, sources: List[Dict]) -> None:
        """Save sources config to disk."""
        base.mkdir(parents=True, exist_ok=True)
        config_path = self._sources_config_path(base)
        config_path.write_text(json.dumps(sources, indent=2), "utf-8")

    def _validate_source_limit(self, base: Path) -> None:
        """Check if we've hit the max sources limit."""
        sources = self._load_sources(base)
        if len(sources) >= self.MAX_SOURCES:
            raise ValueError(f"Maximum {self.MAX_SOURCES} source folders allowed. Remove one before adding another.")

    def _precheck_folder_size(self, root: Path) -> None:
        """Quick pre-check before full sync to avoid freezing on huge trees."""
        from .index import DEFAULT_EXCLUDES as _EX

        file_count = 0
        total_bytes = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name not in _EX]
            for name in files:
                p = Path(current) / name
                if not p.is_file():
                    continue
                file_count += 1
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    pass
                if file_count > self.MAX_FILES_BETA:
                    raise ValueError(f"Folder too large for beta — {file_count} files exceeds limit of {self.MAX_FILES_BETA}. Choose a smaller subfolder.")
                if total_bytes > self.MAX_BYTES_BETA:
                    raise ValueError(f"Folder too large for beta — ~{total_bytes // (1024*1024)} MB exceeds limit of {self.MAX_BYTES_BETA // (1024*1024)} MB. Choose a smaller subfolder.")

    # ===== Project Sources =====

    def attach_project_source(self, project_id: str, root: str | Path, capability: str = "READ") -> Dict:
        """Attach a source folder to a project."""
        cap = WorkspaceCapability.from_str(capability)
        if not WorkspaceCapability.enabled_for_beta(cap):
            raise ValueError(f"Capability {cap} not enabled for beta — only READ")
        root = Path(root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Source folder not found: {root}")

        base = self._project_base(project_id)
        sources = self._load_sources(base)
        source_hash = _source_hash(str(root))

        # Check if already attached
        for s in sources:
            if s["hash"] == source_hash:
                raise ValueError(f"Folder already attached: {root}")

        self._validate_source_limit(base)
        self._precheck_folder_size(root)

        # Create source config
        source_config = {
            "hash": source_hash,
            "root": str(root),
            "capability": cap.value,
            "excludes": sorted(DEFAULT_EXCLUDES),
            "attached_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }

        # Save source config
        source_dir = base / source_hash
        source_dir.mkdir(parents=True, exist_ok=True)
        self._source_config_path(base, source_hash).write_text(json.dumps(source_config, indent=2), "utf-8")

        # Sync index
        idx = WorkspaceIndex(self._source_index_path(base, source_hash))
        stats = idx.sync(root, set(source_config["excludes"]))

        # Update sources list
        source_info = {
            "hash": source_hash,
            "root": str(root),
            "capability": cap.value,
            "indexedAt": source_config["attached_at"],
            "stats": stats,
        }
        sources.append(source_info)
        self._save_sources(base, sources)

        return source_info

    def get_project_sources(self, project_id: str) -> List[Dict]:
        """Get all source folders for a project."""
        base = self._project_base(project_id)
        sources = self._load_sources(base)
        # Refresh stats
        for s in sources:
            try:
                idx = WorkspaceIndex(self._source_index_path(base, s["hash"]))
                files = idx.list_files()
                s["stats"] = {"total": len(files)}
            except Exception:
                pass
        return sources

    def remove_project_source(self, project_id: str, root: str) -> bool:
        """Remove a source folder from a project."""
        base = self._project_base(project_id)
        source_hash = _source_hash(root)
        sources = self._load_sources(base)
        sources = [s for s in sources if s["hash"] != source_hash]
        self._save_sources(base, sources)

        # Remove index directory
        import shutil
        source_dir = base / source_hash
        if source_dir.exists():
            shutil.rmtree(source_dir)
        return True

    # ===== Conversation Sources =====

    def attach_conversation_source(self, conversation_id: str, root: str | Path, capability: str = "READ") -> Dict:
        """Attach a source folder to a conversation."""
        cap = WorkspaceCapability.from_str(capability)
        if not WorkspaceCapability.enabled_for_beta(cap):
            raise ValueError(f"Capability {cap} not enabled for beta — only READ")
        root = Path(root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Source folder not found: {root}")

        base = self._conversation_base(conversation_id)
        sources = self._load_sources(base)
        source_hash = _source_hash(str(root))

        # Check if already attached
        for s in sources:
            if s["hash"] == source_hash:
                raise ValueError(f"Folder already attached: {root}")

        self._validate_source_limit(base)
        self._precheck_folder_size(root)

        # Create source config
        source_config = {
            "hash": source_hash,
            "root": str(root),
            "capability": cap.value,
            "excludes": sorted(DEFAULT_EXCLUDES),
            "attached_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }

        # Save source config
        source_dir = base / source_hash
        source_dir.mkdir(parents=True, exist_ok=True)
        self._source_config_path(base, source_hash).write_text(json.dumps(source_config, indent=2), "utf-8")

        # Sync index
        idx = WorkspaceIndex(self._source_index_path(base, source_hash))
        stats = idx.sync(root, set(source_config["excludes"]))

        # Update sources list
        source_info = {
            "hash": source_hash,
            "root": str(root),
            "capability": cap.value,
            "indexedAt": source_config["attached_at"],
            "stats": stats,
        }
        sources.append(source_info)
        self._save_sources(base, sources)

        return source_info

    def get_conversation_sources(self, conversation_id: str) -> List[Dict]:
        """Get all source folders for a conversation."""
        base = self._conversation_base(conversation_id)
        sources = self._load_sources(base)
        # Refresh stats
        for s in sources:
            try:
                idx = WorkspaceIndex(self._source_index_path(base, s["hash"]))
                files = idx.list_files()
                s["stats"] = {"total": len(files)}
            except Exception:
                pass
        return sources

    def remove_conversation_source(self, conversation_id: str, root: str) -> bool:
        """Remove a source folder from a conversation."""
        base = self._conversation_base(conversation_id)
        source_hash = _source_hash(root)
        sources = self._load_sources(base)
        sources = [s for s in sources if s["hash"] != source_hash]
        self._save_sources(base, sources)

        # Remove index directory
        import shutil
        source_dir = base / source_hash
        if source_dir.exists():
            shutil.rmtree(source_dir)
        return True

    def remove_project(self, project_id: str) -> None:
        """Remove every persisted source index owned by a deleted project."""
        import shutil

        shutil.rmtree(self._project_base(project_id), ignore_errors=True)

    def remove_conversation(self, conversation_id: str) -> None:
        """Remove every persisted source index owned by a deleted conversation."""
        import shutil

        shutil.rmtree(self._conversation_base(conversation_id), ignore_errors=True)

    # ===== Search & Read (works across all sources) =====

    def search_sources(self, base: Path, query: str, k: int = 10) -> List[Dict]:
        """Search across all sources in a base directory."""
        sources = self._load_sources(base)
        if not sources:
            return []

        all_hits: List[Dict] = []
        for s in sources:
            if s["capability"] != "READ":
                continue
            root = Path(s["root"])
            if not root.exists():
                continue
            idx = WorkspaceIndex(self._source_index_path(base, s["hash"]))
            idx.sync(root)
            path_hits = idx.search_path(query, k=k)
            content_hits = idx.search_content(root, query, k=k)

            # Merge hits from this source
            for h in path_hits:
                h["source_root"] = s["root"]
                h["source_hash"] = s["hash"]
                all_hits.append(h)
            for h in content_hits:
                h["source_root"] = s["root"]
                h["source_hash"] = s["hash"]
                all_hits.append(h)

        # Deduplicate and rank
        merged: Dict[str, Dict] = {}
        for h in all_hits:
            key = f"{h.get('source_root', '')}:{h['path']}"
            if key in merged:
                merged[key]["score"] += h["score"]
                if h.get("snippet"):
                    merged[key]["snippet"] = h["snippet"]
            else:
                merged[key] = h

        ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:k]

    def read_source_file(self, base: Path, rel_path: str, max_chars: int = 6000) -> Optional[str]:
        """Read a file from any source in the base directory."""
        sources = self._load_sources(base)
        for s in sources:
            if s["capability"] != "READ":
                continue
            root = Path(s["root"])
            if not root.exists():
                continue
            idx = WorkspaceIndex(self._source_index_path(base, s["hash"]))
            result = idx.read_file(root, rel_path, max_chars=max_chars)
            if result is not None:
                return result
        return None

    def list_conversation_files(self, conversation_id: str, limit: int = 200) -> List[Dict]:
        """List files for this conversation's READ-only sources.

        Listing includes non-text files such as PDFs even though only supported
        text files can be searched or read. This lets Novi answer structural
        questions about an attached folder honestly.
        """
        base = self._conversation_base(conversation_id)
        files: List[Dict] = []
        for source in self._load_sources(base):
            if source.get("capability") != "READ":
                continue
            root = Path(source["root"])
            if not root.exists():
                continue
            excludes = set(source.get("excludes") or DEFAULT_EXCLUDES)
            for current, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = sorted(name for name in dirs
                                  if name not in excludes and not name.startswith("."))
                for name in sorted(names):
                    path = Path(current) / name
                    try:
                        rel = str(path.relative_to(root))
                        size = path.stat().st_size
                    except (OSError, ValueError):
                        continue
                    files.append({
                        "path": rel,
                        "ext": path.suffix.lower(),
                        "parent": str(Path(rel).parent),
                        "size": size,
                        "source": source["hash"],
                        "folder": root.name or str(root),
                    })
                    if len(files) >= limit:
                        return files
        return files

    def search_conversation_sources(self, conversation_id: str, query: str,
                                    k: int = 10) -> List[Dict]:
        """Search only the READ-only sources owned by one conversation."""
        return self.search_sources(self._conversation_base(conversation_id), query, k)

    def read_conversation_file(self, conversation_id: str, source_hash: str,
                               rel_path: str, max_chars: int = 6000) -> Optional[str]:
        """Read one indexed file from an explicitly selected conversation source."""
        base = self._conversation_base(conversation_id)
        source = next((item for item in self._load_sources(base)
                       if item.get("hash") == source_hash and item.get("capability") == "READ"), None)
        if source is None:
            return None
        root = Path(source["root"])
        if not root.exists():
            return None
        index = WorkspaceIndex(self._source_index_path(base, source_hash))
        return index.read_file(root, rel_path, max_chars=max_chars)

    # ===== Legacy compatibility (single workspace) =====

    def attach(self, project_id: str, root: str | Path, capability: str = "READ") -> Dict:
        """Legacy: attach single workspace to project."""
        return self.attach_project_source(project_id, root, capability)

    def get_root(self, project_id: str) -> Path | None:
        """Legacy: get single workspace root."""
        sources = self.get_project_sources(project_id)
        return Path(sources[0]["root"]) if sources else None

    def get_capability(self, project_id: str) -> WorkspaceCapability:
        """Legacy: get capability."""
        sources = self.get_project_sources(project_id)
        if sources:
            return WorkspaceCapability.from_str(sources[0]["capability"])
        return WorkspaceCapability.READ

    def search(self, project_id: str, query: str, k: int = 10) -> List[Dict]:
        """Legacy: search single workspace."""
        base = self._project_base(project_id)
        return self.search_sources(base, query, k)

    def read(self, project_id: str, rel_path: str, max_chars: int = 6000) -> str | None:
        """Legacy: read from single workspace."""
        base = self._project_base(project_id)
        return self.read_source_file(base, rel_path, max_chars)

    def sync(self, project_id: str) -> Dict:
        """Legacy: sync single workspace."""
        sources = self.get_project_sources(project_id)
        total = {"added": 0, "changed": 0, "removed": 0, "total": 0}
        for s in sources:
            root = Path(s["root"])
            idx = WorkspaceIndex(self._source_index_path(self._project_base(project_id), s["hash"]))
            stats = idx.sync(root)
            total["added"] += stats["added"]
            total["changed"] += stats["changed"]
            total["removed"] += stats["removed"]
            total["total"] += stats["total"]
        return total
