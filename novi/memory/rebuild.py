"""Rebuild the local memory database for a new embedding backend.

The embedding change from HuggingFace SentenceTransformers (384-dim) to local
Ollama ``nomic-embed-text`` (768-dim) produces an incompatible vector space:
old LanceDB vectors cannot be meaningfully compared to new ones, so the vector
stores must be dropped and re-embedded.

Only vector stores (LanceDB ``lancedb/`` directories) are affected. The
Brain's non-vector persistence (conversations, scenarios, relationships — SQLite/
JSON) is embedding-independent and is preserved untouched.

Run once after switching embedding backends:

    python -m novi.memory.rebuild            # rebuild under ~/.novi
    python -m novi.memory.rebuild --home <dir>

Afterwards the knowledge index is re-embedded from the knowledge base on demand
(index_all is invoked so search works immediately).
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from ..configuration.bootstrap import get_configuration

log = logging.getLogger("novi.memory.rebuild")

# Roots under the profile home that are pure vector stores.
_VECTOR_ROOTS = ("memory", "brain", "knowledge_index", "vector_index")
_DB_DIR = "lancedb"


def _find_lancedb_dirs(home: Path) -> list[Path]:
    """Locate every ``lancedb`` vector-store directory under ``home``."""
    found: list[Path] = []

    def scan(path: Path):
        if path.name == _DB_DIR and path.is_dir():
            found.append(path)
            return
        if path.is_dir() and not path.name.startswith("."):
            try:
                for child in path.iterdir():
                    scan(child)
            except OSError:
                pass

    scan(home)
    found.sort(key=lambda p: str(p))
    return found


def rebuild(home: Path) -> dict:
    """Drop all vector stores under ``home`` and re-index the knowledge base.

    Non-vector persistence is left untouched. Returns a report dict.
    """
    home = Path(home).expanduser().resolve()
    db_dirs = _find_lancedb_dirs(home)
    removed = [str(p) for p in db_dirs]
    for p in db_dirs:
        log.warning("removing vector store: %s", p)
        shutil.rmtree(p, ignore_errors=True)

    # Re-embed the knowledge base so it is immediately searchable.
    knowledge_dir = home / "knowledge"
    persist_dir = home / "knowledge_index"
    reindexed = _reindex_knowledge(str(knowledge_dir), str(persist_dir))

    return {"removed": removed, "knowledge_index": str(persist_dir), "reindexed": reindexed}


def _reindex_knowledge(knowledge_dir: str, persist_dir: str) -> int:
    from ..services.embedding import EmbeddingService

    cfg = get_configuration().snapshot()
    cfg.setdefault("embedding", {}).setdefault("dimension", 768)
    from ..memory.knowledge_index import KnowledgeIndex

    index = KnowledgeIndex(
        knowledge_dir=knowledge_dir,
        persist_dir=persist_dir,
        embed_model=EmbeddingService(dict(cfg)),
    )
    index.index_all(force=True)
    return index.count()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="novi.memory.rebuild")
    from ..paths import home as app_home
    parser.add_argument(
        "--home",
        default=str(app_home()),
        help="profile directory to rebuild (default: ~/.novi)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    report = rebuild(args.home)
    print(f"removed {len(report['removed'])} vector store(s)")
    for r in report["removed"]:
        print(f"  - {r}")
    print(f"knowledge index rebuilt: {report['reindexed']} documents")


if __name__ == "__main__":
    main()