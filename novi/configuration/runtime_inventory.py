"""Runtime inventory abstraction (Phase 5B/5C).

``RuntimeInventory`` is the interface discovery talks to. It answers two
questions per runtime:

* ``list_models()``  — what does the runtime currently have?
* ``show_model(name)`` — richer metadata for one model.

Backends:

* ``OllamaRuntimeInventory`` — talks to the local Ollama daemon. Uses
  ``/api/tags`` for listing and ``/api/show`` for per-model detail
  (parameter size, quantization, family, format, context length, license,
  capability tokens). Every field is parsed defensively: malformed or
  missing fields become ``None``, never a fabricated value.
* ``OpenAIRuntimeInventory`` — the OpenAI-compatible runtime is a hosted
  service; we cannot enumerate models from the daemon. It reports only the
  models explicitly configured in Novi config, each marked with the
  configured capability profile.

The HTTP primitives are plain functions so tests can patch them and so the
module-level ``query_ollama_tags`` seam used by existing tests keeps working.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence
from urllib.error import URLError
from urllib.request import urlopen, Request

from .model_records import ModelIdentity, ModelRecord, ModelStatus
from .model_seeds import ModelFact, SEED_MODEL_FACTS
from .name_inference import infer_capabilities_from_name
from .evidence import assemble_capability_evidence, capability_flags
from .qualification import Qualification

_DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Ollama /api/show capability tokens mapped 1:1 to Novi capabilities.
# A token is only mapped when its meaning is identical to ours; anything
# unrecognized stays in ``record.metadata["runtime_capabilities"]`` raw.
_RUNTIME_CAPABILITY_TOKENS: dict[str, str] = {
    "tools": "tools",
    "vision": "vision",
    "embedding": "embeddings",
    "reasoning": "reasoning",
}


# ── HTTP primitives (patchable seam) ────────────────────────────────────

def _http_get_json(url: str, timeout: float) -> Optional[dict]:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8", errors="replace")
    return json.loads(payload) if payload else None


def query_ollama_tags(url: str = _DEFAULT_OLLAMA_URL, timeout: float = 5.0) -> list[dict]:
    """Fetch ``/api/tags`` and return the raw ``models`` list.

    Returns ``[]`` on any failure (daemon down, bad response) — never raises.
    """
    try:
        payload = _http_get_json(f"{url.rstrip('/')}/api/tags", timeout)
    except Exception:
        return []
    if not payload or not isinstance(payload.get("models"), list):
        return []
    return payload["models"]


def query_ollama_show(url: str, name: str, timeout: float = 5.0) -> Optional[dict]:
    """Fetch ``/api/show`` payload for ``name``; ``None`` on any failure.

    Ollama returns HTTP 404 for unknown models — treated as ``None``, not an
    error. Model names are passed verbatim; never guessed or rewritten.
    """
    from urllib.error import HTTPError

    try:
        req = Request(
            f"{url.rstrip('/')}/api/show",
            data=json.dumps({"name": name}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
        return json.loads(payload) if payload else None
    except HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except Exception:
        return None


# ── Runtime inventories ─────────────────────────────────────────────────

class RuntimeInventory(ABC):
    """Interface for runtime model discovery."""

    @abstractmethod
    def list_models(self) -> list[ModelRecord]:
        """Current model set of this runtime as rich records."""

    @abstractmethod
    def show_model(self, name: str) -> Optional[ModelRecord]:
        """Rich record for one model, or ``None`` if unknown."""


def _capability_tokens_from_show(payload: dict) -> list[str]:
    """Map ``payload.capabilities`` tokens 1:1 to Novi capabilities.

    Unrecognized tokens are ignored here (they stay in raw metadata). Missing
    or malformed ``capabilities`` yields ``[]``.
    """
    tokens = payload.get("capabilities")
    if not isinstance(tokens, list):
        return []
    mapped = [_RUNTIME_CAPABILITY_TOKENS.get(t) for t in tokens]
    return [m for m in mapped if m]


def _context_length_from_show(payload: dict) -> Optional[int]:
    """Scan ``payload.model_info`` for a context-length key defensively.

    Returns the first parseable integer from known keys
    (``llama.context_length``, ``model.context_length``). ``None`` when
    absent or unparseable — never a fabricated guess.
    """
    model_info = payload.get("model_info")
    if not isinstance(model_info, dict):
        return None
    for key in ("llama.context_length", "model.context_length"):
        value = model_info.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _quantization_from_show(payload: dict) -> Optional[str]:
    details = payload.get("details")
    if isinstance(details, dict):
        value = details.get("quantization_level")
        if isinstance(value, str) and value:
            return value
    return None


class OllamaRuntimeInventory(RuntimeInventory):
    """Ollama daemon inventory: ``/api/tags`` + ``/api/show``."""

    def __init__(self, url: str = _DEFAULT_OLLAMA_URL, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def list_models(self) -> list[ModelRecord]:
        records: list[ModelRecord] = []
        for raw in query_ollama_tags(self.url, self.timeout):
            record = self._record_from_tags(raw)
            if record is not None:
                records.append(record)
        return records

    def show_model(self, name: str) -> Optional[ModelRecord]:
        payload = query_ollama_show(self.url, name, self.timeout)
        if payload is None:
            return None
        return self._record_from_show(payload, name)

    # -- defensive parsing -------------------------------------------------

    def _record_from_tags(self, raw: dict) -> Optional[ModelRecord]:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            return None
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        size = raw.get("size")
        size_bytes = size if isinstance(size, int) else None
        parameter_count = details.get("parameter_size") if isinstance(
            details.get("parameter_size"), str) else None
        family = details.get("family") if isinstance(details.get("family"), str) else None
        quantization = details.get("quantization_level") if isinstance(
            details.get("quantization_level"), str) else None
        format_ = details.get("format") if isinstance(details.get("format"), str) else None
        families = details.get("families") if isinstance(details.get("families"), list) else None

        fact: Optional[ModelFact] = SEED_MODEL_FACTS.get(name)
        inference_hints = infer_capabilities_from_name(name)
        evidence = assemble_capability_evidence(
            fact=fact, name=name, inference_hints=inference_hints)
        flags = capability_flags(evidence)

        identity = ModelIdentity(
            name=name,
            family=family or (fact.family if fact else None),
            size_tier=parameter_count,
            quantization=quantization,
        )
        families_list = [f for f in families if isinstance(f, str)] if families else []

        return ModelRecord(
            name=name,
            provider="ollama",
            runtime="ollama",
            status=ModelStatus.INSTALLED,
            identity=identity,
            source_kind="local-runtime",
            source_url=self.url,
            format=format_,
            size_bytes=size_bytes,
            parameter_count=parameter_count,
            capabilities=evidence,
            capability_flags=flags,
            qualification=(fact.qualification if fact else Qualification.EXPERIMENTAL),
            display_name=fact.display_name if fact else name,
            license=(fact.license if fact else None),
            approx_ram_gb=(fact.approx_ram_gb if fact else None),
            min_vram_gb=(fact.min_vram_gb if fact else None),
            caveats=list(fact.caveats) if fact else [],
            metadata={"families": families_list},
        )

    def _record_from_show(self, payload: dict, name: str) -> ModelRecord:
        tags_record = self._record_from_tags({
            "name": name,
            "details": payload.get("details") if isinstance(payload.get("details"), dict) else {},
            "size": payload.get("size"),
        })
        if tags_record is None:
            return None  # type: ignore[return-value]
        record = tags_record

        tokens = _capability_tokens_from_show(payload)
        context_length = _context_length_from_show(payload)
        license_ = payload.get("license")
        if not isinstance(license_, str) or not license_:
            license_ = None
        parameter_count = payload.get("parameters")
        if not isinstance(parameter_count, str):
            parameter_count = None

        fact = SEED_MODEL_FACTS.get(name)
        runtime_caps = list(tokens)
        # Add curated seed capabilities for known models, plus runtime tokens.
        evidence = assemble_capability_evidence(
            fact=fact,
            runtime_capabilities=runtime_caps,
            name=name,
            inference_hints=infer_capabilities_from_name(name),
        )
        flags = capability_flags(evidence)
        raw_caps = payload.get("capabilities")

        record.capabilities = evidence
        record.capability_flags = flags
        record.context_length = context_length
        record.license = license_ or (fact.license if fact else None)
        if parameter_count:
            record.parameter_count = parameter_count
        record.metadata["runtime_capabilities"] = [
            t for t in raw_caps if isinstance(t, str)] if isinstance(raw_caps, list) else []
        return record


class OpenAIRuntimeInventory(RuntimeInventory):
    """Hosted OpenAI-compatible runtime — reports only configured models.

    A hosted runtime cannot be enumerated from the daemon, so discovery
    surfaces the models explicitly configured for it, marked with their
    configured capability profile. This is not a model universe claim.
    """

    def __init__(self, configured_models: Optional[Sequence[dict]] = None):
        self.configured_models = list(configured_models or [])

    def list_models(self) -> list[ModelRecord]:
        records: list[ModelRecord] = []
        for cfg in self.configured_models:
            if not isinstance(cfg, dict):
                continue
            name = cfg.get("name") or cfg.get("model")
            if not isinstance(name, str) or not name:
                continue
            caps = cfg.get("capabilities")
            runtime_caps = [c for c in caps if isinstance(c, str)] if isinstance(caps, list) else []
            fact = SEED_MODEL_FACTS.get(name)
            evidence = assemble_capability_evidence(
                fact=fact, runtime_capabilities=runtime_caps, name=name)
            records.append(ModelRecord(
                name=name,
                provider="openai",
                runtime="openai",
                status=ModelStatus.INSTALLED,
                source_kind="config",
                capabilities=evidence,
                capability_flags=capability_flags(evidence),
                qualification=(fact.qualification if fact else Qualification.EXPERIMENTAL),
                display_name=(fact.display_name if fact else name),
            ))
        return records

    def show_model(self, name: str) -> Optional[ModelRecord]:
        for record in self.list_models():
            if record.name == name:
                return record
        return None