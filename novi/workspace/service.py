"""WorkspaceService — attach, sync, search, read with READ boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..paths import home as app_home
from .capability import WorkspaceCapability
from .index import WorkspaceIndex, DEFAULT_EXCLUDES


class WorkspaceService:
    """Manages per-project workspace grants and indexes."""

    def __init__(self):
        self._base = Path(app_home()) / "workspaces"

    def _config_path(self, project_id: str) -> Path:
        return self._base / project_id / "config.json"

    def _index_path(self, project_id: str) -> Path:
        return self._base / project_id / "index.sqlite"

    def attach(self, project_id: str, root: str | Path, capability: str = "READ") -> Dict:
        """Attach explicit local folder to project — validates and syncs."""
        cap = WorkspaceCapability.from_str(capability)
        if not WorkspaceCapability.enabled_for_beta(cap):
            raise ValueError(f"Capability {cap} not enabled for beta — only READ")
        root = Path(root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Workspace path not found: {root}")
        # store config
        import json
        cfg_path = self._config_path(project_id)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = {
            "project_id": project_id,
            "root": str(root),
            "capability": cap.value,
            "excludes": sorted(DEFAULT_EXCLUDES),
        }
        cfg_path.write_text(json.dumps(cfg, indent=2), "utf-8")
        idx = WorkspaceIndex(self._index_path(project_id))
        stats = idx.sync(root, set(cfg["excludes"]))
        return {"root": str(root), "capability": cap.value, "stats": stats, "config": cfg}

    def get_root(self, project_id: str) -> Path | None:
        import json
        cfg_path = self._config_path(project_id)
        if not cfg_path.exists():
            return None
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
            return Path(cfg["root"])
        except Exception:
            return None

    def get_capability(self, project_id: str) -> WorkspaceCapability:
        import json
        cfg_path = self._config_path(project_id)
        if not cfg_path.exists():
            return WorkspaceCapability.READ
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
            return WorkspaceCapability.from_str(cfg.get("capability", "READ"))
        except Exception:
            return WorkspaceCapability.READ

    def search(self, project_id: str, query: str, k: int = 10) -> List[Dict]:
        root = self.get_root(project_id)
        if not root:
            return []
        cap = self.get_capability(project_id)
        if cap != WorkspaceCapability.READ:
            return []
        idx = WorkspaceIndex(self._index_path(project_id))
        # incremental check: sync if mtimes changed (light)
        idx.sync(root)
        path_hits = idx.search_path(query, k=k)
        content_hits = idx.search_content(root, query, k=k)
        # merge by path, path hits boost
        merged: Dict[str, Dict] = {}
        for h in path_hits:
            merged[h["path"]] = {"path": h["path"], "score": h["score"] * 2, "snippet": ""}
        for h in content_hits:
            if h["path"] in merged:
                merged[h["path"]]["score"] += h["score"]
                merged[h["path"]]["snippet"] = h["snippet"]
            else:
                merged[h["path"]] = h
        ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:k]

    def read(self, project_id: str, rel_path: str, max_chars: int = 6000) -> str | None:
        root = self.get_root(project_id)
        if not root:
            return None
        cap = self.get_capability(project_id)
        if cap != WorkspaceCapability.READ:
            return None
        idx = WorkspaceIndex(self._index_path(project_id))
        return idx.read_file(root, rel_path, max_chars=max_chars)

    def sync(self, project_id: str) -> Dict:
        root = self.get_root(project_id)
        if not root:
            return {"added": 0, "changed": 0, "removed": 0, "total": 0}
        idx = WorkspaceIndex(self._index_path(project_id))
        return idx.sync(root)
