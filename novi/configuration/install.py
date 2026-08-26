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

log = logging.getLogger("novi.config.install")

ProgressFn = Callable[[dict], None]
"""progress: {name, status: started|progress|done|error, downloaded, total, pct}"""

DEFAULT_EMBEDDING_MODEL = "nomic-embed-text:v1.5"
"""The application's single canonical embedding model (768 dims via Ollama).

Pinned to an exact upstream tag so embeddings stay reproducible across
machines and time. One source of truth: startup provisioning pulls it when
missing, and an unset ``embedding.model`` resolves to it at runtime.
Changing this constant invalidates every persisted vector store
(dimension/space change) — bump it together with a migration plan.
"""


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


# ── startup provisioning ──────────────────────────────────────────────

def _installed_model_names(ollama_url: str) -> set[str]:
    """Names (with tags) of models present in the local Ollama daemon."""
    with urllib.request.urlopen(f"{ollama_url.rstrip('/')}/api/tags", timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {m.get("name", "") for m in data.get("models", [])}


def _name_matches(installed: set[str], name: str) -> bool:
    """Exact tag match, or same base model when the installed side is untagged.

    ``nomic-embed-text`` in Ollama lists as ``nomic-embed-text:latest``, so a
    pinned request like ``nomic-embed-text:v1.5`` must NOT treat it as
    present — but a bare configured name should match its tagged listing.
    """
    if name in installed:
        return True
    base = name.split(":")[0]
    if ":" not in name:
        return any(i.split(":")[0] == base for i in installed)
    return False


def ensure_embedding_model(
    ollama_url: str = "http://localhost:11434",
    *,
    configuration=None,
    pull=None,
    echo=print,
):
    """Guarantee the canonical default embedding model exists locally.

    Startup provisioning, called from the webui launch path after Ollama is
    confirmed up. Behavior:

    * backend != ollama → no-op.
    * ``embedding.model`` unset → resolves to ``DEFAULT_EMBEDDING_MODEL`` and
      persists that selection so Settings and dimension stay consistent.
    * target missing from the daemon → blocking pull with progress output
      (blocking because the first knowledge indexing pass immediately
      follows; a background thread would race it).
    * Ollama unreachable → warn and return; startup never fails here.

    Returns the resolved model name, or None when nothing was ensured.
    """
    url = ollama_url.rstrip("/")
    try:
        if configuration is None:
            from .bootstrap import get_configuration

            configuration = get_configuration()
        if (configuration.get("embedding.backend", "ollama") or "ollama") != "ollama":
            return None
        configured = (configuration.get("embedding.model", "") or "").strip()
        # Legacy installs carry the untagged base name ("nomic-embed-text");
        # upgrade those selections to the pinned default so every machine
        # embeds with identical weights.
        default_base = DEFAULT_EMBEDDING_MODEL.split(":")[0]
        adopt_default = not configured or configured == default_base
        if adopt_default:
            target = DEFAULT_EMBEDDING_MODEL
        else:
            target = configured

        try:
            installed = _installed_model_names(url)
        except Exception as e:
            echo(f"Warning: could not reach Ollama to check embedding model: {e}")
            return None

        if _name_matches(installed, target):
            if adopt_default:
                configuration.set("embedding.model", target, by="startup")
            return target

        echo(f"Installing default embedding model '{target}' ...")
        progress = {"pct": None}

        def _show(p: dict):
            pct = p.get("pct")
            if p.get("status") == "done":
                echo(f"  {target} done")
            elif pct is not None and pct != progress["pct"]:
                progress["pct"] = pct
                echo(f"  {target} {pct}%")

        (pull or ModelInstaller(url).pull)(target, _show)

        if adopt_default:
            configuration.set("embedding.model", target, by="startup")
        return target
    except Exception as e:
        log.warning("embedding model provisioning failed: %s", e)
        echo(f"Warning: could not ensure embedding model: {e}")
        return None