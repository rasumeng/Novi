"""Model auto-install — Ollama pull with progress.

No terminal interaction required. The UI calls ``install(name)`` and receives
progress events over the config/ws bridge.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections.abc import Callable

log = logging.getLogger("cozmo.config.install")

ProgressFn = Callable[[dict], None]
"""progress: {name, status: started|progress|done|error, downloaded, total, pct}"""


class ModelInstaller:
    """Streams Ollama model pulls, emitting progress callbacks."""

    def __init__(self, ollama_url: str = "http://localhost:11434", on_progress: ProgressFn | None = None):
        self.ollama_url = ollama_url.rstrip("/")
        self.on_progress = on_progress

    def pull(self, name: str, on_progress: ProgressFn | None = None):
        cb = on_progress or self.on_progress
        self._emit(cb, {"name": name, "status": "started"})
        body = json.dumps({"model": name, "stream": True}).encode()
        req = urllib.request.Request(
            f"{self.ollama_url}/api/pull",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._emit_chunk(cb, name, chunk)
            self._emit(cb, {"name": name, "status": "done"})
        except Exception as e:
            self._emit(cb, {"name": name, "status": "error", "error": str(e)})
            raise

    def _emit_chunk(self, cb, name: str, chunk: dict):
        status = chunk.get("status", "")
        if status == "success":
            self._emit(cb, {"name": name, "status": "done"})
            return
        # Progress payloads look like {"status":"downloading","total":N,"completed":N}
        downloaded = chunk.get("completed")
        total = chunk.get("total")
        pct = None
        if total and downloaded:
            pct = round(100.0 * downloaded / total, 1)
        self._emit(cb, {
            "name": name,
            "status": "progress",
            "phase": status,
            "downloaded": downloaded,
            "total": total,
            "pct": pct,
        })

    @staticmethod
    def _emit(cb, payload: dict):
        if cb:
            try:
                cb(payload)
            except Exception as e:
                log.warning("install progress handler failed: %s", e)


def delete_model(name: str, ollama_url: str = "http://localhost:11434") -> bool:
    """Delete a model from the local Ollama daemon (disk operation).

    Returns ``True`` only when Ollama confirms removal (HTTP 2xx). This never
    touches configuration or workload selections — removing a model from disk
    and changing which model a workload uses are unrelated actions.
    """
    name = (name or "").strip()
    if not name:
        return False
    body = json.dumps({"model": name}).encode()
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/delete",
        data=body,
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log.warning("model delete failed for '%s': %s", name, e)
        return False


def install_model_background(
    name: str,
    ollama_url: str = "http://localhost:11434",
    on_progress: ProgressFn | None = None,
) -> threading.Thread:
    """Kick off a model install on a daemon thread."""
    installer = ModelInstaller(ollama_url, on_progress)
    t = threading.Thread(target=lambda: installer.pull(name), daemon=True)
    t.start()
    return t