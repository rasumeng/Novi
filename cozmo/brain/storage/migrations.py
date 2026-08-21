"""Phase D migration — flat knowledge table → typed vector store (one-time, offline).

Reads the legacy flat ``cozmo_knowledge`` LanceDB table written by Phase C's
KnowledgeStore and re-embeds every row into the typed ``knowledge_items``
VectorStore, then drops the flat table.

Run once per profile:
    python -m cozmo.brain.storage.migrations --persist-dir ~/.cozmo/brain
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

import lancedb

from ...configuration.bootstrap import get_configuration
from ...memory.lancedb_store import LanceStore
from ...services.embedding import EmbeddingService
from ..types import KnowledgeForm, KnowledgeItem, KnowledgeStatus
from .vector_store import VectorStore

log = logging.getLogger("cozmo.brain.storage.migrations")

FLAT_TABLE = "cozmo_knowledge"
TYPED_TABLE = "knowledge_items"


def _flat_row_to_item(row: dict) -> KnowledgeItem:
    meta = row.get("metadata", {})
    created = meta.get("created_at")
    try:
        form = KnowledgeForm(meta.get("form", KnowledgeForm.ATOMIC.value))
    except ValueError:
        form = KnowledgeForm.ATOMIC
    try:
        status = KnowledgeStatus(meta.get("status", KnowledgeStatus.CANDIDATE.value))
    except ValueError:
        status = KnowledgeStatus.CANDIDATE
    return KnowledgeItem(
        id=row.get("id", ""),
        form=form,
        content=row.get("text", ""),
        confidence=float(meta.get("confidence", 0.0)),
        status=status,
        tags=tuple(meta.get("tags", ())),
        sources=tuple(meta.get("sources", ())),
        scenario_id=meta.get("scenario_id"),
        created_at=datetime.fromisoformat(created) if created else None,
    )


def migrate(persist_dir: str | Path, embed_model: str | EmbeddingService | None = None) -> dict:
    """Re-embed flat cozmo_knowledge rows into typed knowledge_items.

    Returns {"migrated": n, "dropped_flat": bool}.
    """
    persist_dir = Path(persist_dir)
    embed_cfg = dict(get_configuration().snapshot())
    embed_cfg.setdefault("embedding", {})["model"] = embed_model or get_configuration().get("embedding.model", "")
    embed_service = (
        embed_model
        if isinstance(embed_model, EmbeddingService)
        else EmbeddingService(embed_cfg)
    )

    def embed(text: str) -> list[float]:
        return embed_service.encode(text, normalize=True)

    db = lancedb.connect(str(persist_dir / "lancedb"))

    try:
        listing = db.list_tables()
    except Exception:
        listing = None
    table_names = getattr(listing, "tables", listing) or []
    if FLAT_TABLE not in table_names:
        return {"migrated": 0, "dropped_flat": False}

    flat = LanceStore(
        uri=persist_dir / "lancedb",
        table_name=FLAT_TABLE,
        embed_func=embed,
        embed_dim=embed_service.dimension,
        embed_model=embed_service.model_name,
        vector_index=False,
    )
    rows = flat.list_all(limit=10_000)
    items = [_flat_row_to_item(r) for r in rows if r.get("text")]

    typed = VectorStore(
        persist_dir=persist_dir, embed_model=embed_service, table_name=TYPED_TABLE
    )
    ids = typed.add_many(items, source_kind="migration")

    try:
        db.drop_table(FLAT_TABLE)
        dropped = True
    except Exception:
        dropped = False
        log.warning("could not drop flat table %s; remove it manually", FLAT_TABLE)

    log.info(
        "migrated %d flat rows into %s (dropped flat: %s)",
        len(ids), TYPED_TABLE, dropped,
    )
    return {"migrated": len(ids), "dropped_flat": dropped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--persist-dir",
        type=str,
        default=str(Path.home() / ".cozmo" / "brain"),
        help="Brain persist directory (default ~/.cozmo/brain)",
    )
    parser.add_argument(
        "--embed-model", type=str, default=None, help="Embedding model override"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    result = migrate(args.persist_dir, embed_model=args.embed_model)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
