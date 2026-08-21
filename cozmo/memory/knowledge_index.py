"""
KnowledgeIndex — indexes knowledge base files into LanceStore for semantic search.

Scans the knowledge/ directory (OKF markdown files with YAML frontmatter),
embeds them with Sentence Transformers, and makes them searchable.

Supports:
  - Overlapping chunking
  - Cross-encoder reranking
  - Hybrid retrieval (vector + keyword boost)
  - Metadata-preserving search
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..services import EmbeddingService, RerankerService
from ..configuration.bootstrap import get_configuration
from .lancedb_store import LanceStore
from .okf import parse_okf_file

log = logging.getLogger("cozmo.memory.knowledge")


_global_knowledge_index: "KnowledgeIndex | None" = None


def init_knowledge_index(
    knowledge_dir: str | Path,
    persist_dir: str | Path | None = None,
    reranker: RerankerService | str | None = None,
) -> "KnowledgeIndex":
    global _global_knowledge_index
    ki = KnowledgeIndex(knowledge_dir=knowledge_dir, persist_dir=persist_dir, rerank_model=reranker)
    ki.index_all()
    _global_knowledge_index = ki
    return ki


def get_knowledge_index() -> "KnowledgeIndex | None":
    return _global_knowledge_index


def _chunk_with_overlap(text: str, max_chars: int = 1000, overlap_chars: int = 150) -> list[str]:
    """Split text into overlapping chunks at paragraph boundaries.

    Oversized single paragraphs are force-split into max_chars windows with
    overlap so no chunk exceeds the limit.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text[:max_chars]] if text else []

    chunks = []
    current = ""
    for p in paragraphs:
        para_len = len(p)
        if para_len > max_chars:
            # Single paragraph exceeds max_chars — force-split with overlap
            if current:
                chunks.append(current)
                current = ""
            step = max(max_chars - overlap_chars, 1)
            for start in range(0, para_len, step):
                chunks.append(p[start:start + max_chars])
            continue

        candidate = (current + "\n\n" + p).strip() if current else p
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # Carry overlap from previous chunk
            if overlap_chars and overlap_chars < len(current):
                overlap_start = -overlap_chars
                nl = current.rfind("\n", overlap_start)
                overlap_text = current[nl:] if nl >= len(current) + overlap_start else current[overlap_start:]
            elif overlap_chars:
                overlap_text = current
            else:
                overlap_text = ""
            current = overlap_text + "\n\n" + p if overlap_text else p

    if current:
        chunks.append(current)

    return chunks


class KnowledgeIndex:
    """Indexes knowledge base files for semantic search with RAG pipeline.

    Args:
        knowledge_dir: Path to the knowledge base directory
        persist_dir: LanceDB storage directory
        embed_model: Sentence Transformer model name
        rerank_model: Cross-encoder model name (or None to disable reranking)
    """

    def __init__(
        self,
        knowledge_dir: str | Path | None = None,
        persist_dir: Optional[str | Path] = None,
        embed_model: str | EmbeddingService | None = None,
        rerank_model: Optional[str | RerankerService] = None,
    ):
        if knowledge_dir is None:
            knowledge_dir = get_configuration().get(
                "workspace.knowledge", "~/.cozmo/knowledge")
        self.knowledge_dir = Path(knowledge_dir).expanduser().resolve()

        if isinstance(embed_model, EmbeddingService):
            embed_service = embed_model
        else:
            cfg = get_configuration().snapshot()
            model_name = embed_model or cfg.get("embedding", {}).get("model", "")
            embed_cfg = dict(cfg)
            embed_cfg.setdefault("embedding", {})["model"] = model_name
            embed_service = EmbeddingService(embed_cfg)
        self._embedder = embed_service

        def embed(text: str) -> list[float]:
            return embed_service.encode(text, normalize=True)

        embed_dim = embed_service.dimension

        index_dir = Path(persist_dir) if persist_dir else Path.home() / ".cozmo" / "knowledge_index"
        self.store = LanceStore(
            uri=str(index_dir / "lancedb"),
            table_name="knowledge_index",
            embed_func=embed,
            embed_dim=embed_dim,
            embed_model=embed_service.model_name,
            vector_index=get_configuration().get("vector_index.enabled", True),
        )
        self._indexed_files: dict[str, float] = {}
        if isinstance(rerank_model, RerankerService):
            self._reranker = rerank_model
        elif isinstance(rerank_model, str):
            self._reranker = RerankerService({"reranker": {"model": rerank_model}})
        else:
            self._reranker = None

    def index_all(self, force: bool = False):
        """Index all knowledge files. Skips files with unchanged mtime."""
        if not self.knowledge_dir.is_dir():
            self.knowledge_dir.mkdir(parents=True, exist_ok=True)
            log.info("created knowledge directory at %s", self.knowledge_dir)
            return

        count = 0
        for f in sorted(self.knowledge_dir.rglob("*.md")):
            try:
                rel = str(f.relative_to(self.knowledge_dir))
                mtime = f.stat().st_mtime
                if not force and rel in self._indexed_files and self._indexed_files[rel] == mtime:
                    continue
                self.index_file(f, rel)
                self._indexed_files[rel] = mtime
                count += 1
            except Exception as e:
                log.warning("failed to index %s: %s", f, e)

        if count:
            log.info("indexed %d knowledge file(s)", count)

    def index_file(self, path: Path, rel: Optional[str] = None):
        """Index a single knowledge file.

        Chunks get deterministic ids of the form ``<rel>::<chunk>`` so
        re-indexing the same file replaces, rather than duplicates, its rows.
        """
        if rel is None:
            rel = path.relative_to(self.knowledge_dir).as_posix()
        else:
            rel = Path(rel).as_posix()
        meta, body = parse_okf_file(path)
        title = meta.get("title", path.stem)
        tags = meta.get("tags", [])

        # Remove existing chunks for this file (matches both deterministic rows
        # and any legacy uuid rows) before re-adding.
        existing = self.store.query_sql(f"metadata LIKE '%\"path\": \"{rel}\"%'")
        for e in existing:
            self.store.delete(e.get("id", ""))

        chunks = _chunk_with_overlap(body)
        ids = [f"{rel}::{i}" for i in range(len(chunks))]
        for i, chunk in enumerate(chunks):
            metadata = {
                "path": rel,
                "title": title,
                # Durable Brain identity of the mirrored item (M4). Lets
                # retrieval deduplicate semantic chunks against graph-expanded
                # neighbors by durable id, never by filename/title. None for
                # user-authored notes that were never Brain-synced.
                "item_id": meta.get("id"),
                "tags": tags,
                "type": "knowledge",
                "chunk": i,
                "total_chunks": len(chunks),
                "timestamp": meta.get("timestamp", datetime.now().isoformat()),
                "embed_model": self._embedder.model_name,
            }
            self.store.add_texts([chunk], [metadata], ids=[ids[i]])

    def search(self, query: str, k: int = 5, rerank: bool = True) -> list[dict]:
        """Search knowledge base. Returns ranked results with metadata.

        Pipeline: vector search → cross-encoder rerank (if enabled).
        """
        # Fetch more candidates for reranking
        fetch_k = k * 3 if (rerank and self._reranker) else k
        results = self.store.search_with_importance(query, k=fetch_k)
        if not results:
            return []

        if rerank and self._reranker:
            results = self._reranker.rerank(query, results, k=k)

        # Normalize scores to 0-1 for consistent output
        if results and "rerank_score" in results[0]:
            scores = [r.get("rerank_score", 0) for r in results]
            max_s = max(scores) if scores else 0
            if max_s > 0:
                for r in results:
                    r["score"] = round(max(0, r.get("rerank_score", 0) / max_s), 4)

        return results[:k]

    def search_by_tag(self, tag: str, k: int = 20) -> list[dict]:
        """Search knowledge base by tag."""
        return self.store.query_sql(
            f"metadata LIKE '%\"tags\": [%\"{tag}\"%]%' OR metadata LIKE '%\"tags\": [\"{tag}\"%'"
        )[:k]

    def count(self) -> int:
        return self.store.count()

    def get_paths(self) -> set[str]:
        all_items = self.store.list_all(limit=5000)
        return {item["metadata"].get("path", "") for item in all_items if "metadata" in item}
