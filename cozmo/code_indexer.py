import hashlib

from . import config as cozmo_config
from pathlib import Path
from .memory.lancedb_store import LanceStore
from .services import EmbeddingService

IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".cozmo", "target", "build", "dist"}


class ProjectIndex:
    """Project code index using LanceDB + Sentence Transformers.

    Indexes project files as chunked documents for semantic code search.
    """

    def __init__(self, project_root: str | Path, embed_model: str | EmbeddingService | None = None):
        self.root = Path(project_root).resolve()

        if isinstance(embed_model, EmbeddingService):
            embed_service = embed_model
        else:
            cfg = cozmo_config.load()
            model_name = embed_model or cfg.get("embedding", {}).get("model", "")
            embed_cfg = dict(cfg)
            embed_cfg.setdefault("embedding", {})["model"] = model_name
            embed_service = EmbeddingService(embed_cfg)

        def embed(text: str) -> list[float]:
            return embed_service.encode(text, normalize=True)

        project_key = hashlib.sha1(str(self.root).encode("utf-8")).hexdigest()[:12]
        self.store = LanceStore(
            uri=str(Path.home() / ".cozmo" / "project_index" / project_key),
            table_name="project_index",
            embed_func=embed,
            embed_dim=embed_service.dimension,
        )

    def index_all(self):
        docs = []
        for f in self.root.rglob("*"):
            if not f.is_file():
                continue
            if any(part in IGNORE_DIRS for part in f.parts):
                continue
            if f.name.startswith("."):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = str(f.relative_to(self.root))
            docs.append({"id": rel, "text": text, "metadata": {"path": rel}})
        if docs:
            texts = [d["text"] for d in docs]
            metas = [d["metadata"] for d in docs]
            self.store.add_texts(texts, metas)

    def query(self, text: str, k: int = 5) -> str:
        results = self.store.similarity_search(text, k=k)
        if not results:
            return ""
        return "\n".join(
            f"- {r['metadata'].get('path', '?')}: {r['text'][:500]}"
            for r in results
        )
