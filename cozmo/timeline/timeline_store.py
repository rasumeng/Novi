"""
TimelineStore — bounded JSONL persistence for assistant timeline entries.

Stores a single append-only JSONL file under ~/.cozmo/timeline/timeline.jsonl.
New entries are appended at the tail (newest last); reads return newest-first.
Bounded: once the entry count exceeds ``max_entries``, the oldest entries are
trimmed on next write. Thread-safe via an internal lock so it can be driven
from Brain event dispatch (which may run on arbitrary worker threads).

Only user-facing TimelineEntry payloads are persisted — never internal ids,
scores, embeddings, or storage paths.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("cozmo.timeline.store")

TIMELINE_DIR = Path.home() / ".cozmo" / "timeline"
DEFAULT_MAX_ENTRIES = 500


class TimelineStore:
    """Append-only, bounded JSONL store of timeline entries."""

    def __init__(self, persist_dir: Path | None = None, max_entries: int = DEFAULT_MAX_ENTRIES):
        self._dir = Path(persist_dir) if persist_dir else TIMELINE_DIR
        self._file = self._dir / "timeline.jsonl"
        self._max_entries = max(1, max_entries)
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if not self._file.exists():
            self._file.touch(exist_ok=True)

    def append(self, entry: dict) -> dict:
        """Persist a timeline entry, assign an instance id + timestamp if missing, trim the tail."""
        data = dict(entry)
        data.setdefault("id", uuid.uuid4().hex)
        data.setdefault("timestamp", datetime.now().isoformat())
        line = json.dumps(data, default=str, ensure_ascii=False)
        with self._lock:
            self._ensure()
            with self._file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._trim()
        return data

    def _trim(self) -> None:
        lines = self._read_lines()
        if len(lines) > self._max_entries:
            kept = lines[-self._max_entries:]
            with self._file.open("w", encoding="utf-8") as f:
                f.write("".join(ln + "\n" for ln in kept))

    def _read_lines(self) -> list[str]:
        try:
            return self._file.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []

    def list(self, limit: int = 200) -> list[dict]:
        """Return the most recent entries, newest first."""
        with self._lock:
            lines = self._read_lines()
        rows = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows[::-1][: max(1, limit)]

    def clear(self) -> None:
        with self._lock:
            self._ensure()
            self._file.write_text("", encoding="utf-8")