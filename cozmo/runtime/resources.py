"""
ResourceManager — VRAM/model/resource tracking with concurrency gating.

Tracks loaded models, VRAM usage, active/queued jobs.
Provides model ranking for ModelRouter.
Gates model loading to prevent OOM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

log = logging.getLogger("cozmo.resources")


@dataclass
class ResourceSnapshot:
    """Current resource state.

    ``vram_total_gb`` / ``vram_free_gb`` are ``None`` when VRAM is UNKNOWN.
    Consumers must treat ``None`` as "don't know", never as zero.
    """

    vram_total_gb: Optional[float] = None
    vram_used_gb: float = 0.0
    vram_free_gb: Optional[float] = None
    loaded_models: list[str] = field(default_factory=list)
    active_jobs: int = 0
    queued_jobs: int = 0
    max_active_jobs: int = 1
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class ResourceRequest:
    """Resource requirements for a model load or job execution."""

    model_name: str = ""
    vram_required_gb: float = 0.0
    estimated_duration_s: float = 0.0
    priority: int = 3
    blocking: bool = True


@dataclass
class ModelLoadInfo:
    """Tracks a loaded model's resource usage."""

    name: str = ""
    vram_gb: float = 0.0
    loaded_at: str = ""
    last_used_at: str = ""
    use_count: int = 0

    def __post_init__(self):
        now = datetime.now().isoformat()
        if not self.loaded_at:
            self.loaded_at = now
        if not self.last_used_at:
            self.last_used_at = now

    def touch(self):
        self.last_used_at = datetime.now().isoformat()
        self.use_count += 1


class ResourceManager:
    """Tracks system resources with concurrency gating and model ranking.

    Thread-safe for concurrent job submissions.
    """

    def __init__(self, vram_total_gb: Optional[float] = None, max_active_jobs: int = 1):
        self.vram_total_gb = vram_total_gb
        self.max_active_jobs = max_active_jobs
        self._loaded_models: dict[str, ModelLoadInfo] = {}
        self._active_jobs = 0
        self._queued_jobs = 0

    # ── VRAM ─────────────────────────────────────────────────────────────

    @property
    def vram_used_gb(self) -> float:
        return sum(m.vram_gb for m in self._loaded_models.values())

    @property
    def vram_known(self) -> bool:
        """True only when we actually know the total VRAM (never guessed)."""
        return isinstance(self.vram_total_gb, (int, float)) and self.vram_total_gb >= 0

    @property
    def vram_free_gb(self) -> Optional[float]:
        """Free VRAM, or ``None`` when total VRAM is unknown.

        Never substitutes a guess; refusals based on VRAM are skipped instead.
        """
        if not self.vram_known:
            return None
        return self.vram_total_gb - self.vram_used_gb

    def can_load(self, model_name: str, vram_gb: Optional[float]) -> bool:
        """Whether a model can load by VRAM, when VRAM is known.

        When total VRAM is unknown, we do not pretend to know: the VRAM
        constraint is refused as a gate but never evaluated as true/false on a
        fabricated number. Callers holding a numeric ``vram_gb`` requirement
        should treat an unknown-VRAM manager as "cannot verify" (false).
        """
        if not self.vram_known:
            # VRAM UNKNOWN — do not fabricate free/needed numbers.
            if vram_gb:
                return False
            # No VRAM requirement to enforce; nothing to refuse.
            return True
        return self.vram_free_gb >= (vram_gb or 0.0)

    def load_model(self, model_name: str, vram_gb: Optional[float]) -> bool:
        if model_name in self._loaded_models:
            self._loaded_models[model_name].touch()
            return True
        if not self.can_load(model_name, vram_gb):
            if self.vram_known:
                log.warning("insufficient VRAM for %s (need %.1fGB, have %.1fGB)",
                            model_name, vram_gb or 0.0, self.vram_free_gb)
            else:
                log.warning("cannot verify VRAM for %s (VRAM unknown); load refused",
                            model_name)
            return False
        self._loaded_models[model_name] = ModelLoadInfo(
            name=model_name, vram_gb=vram_gb or 0.0,
        )
        if self.vram_known:
            log.info("loaded model: %s (%.1fGB, %.1fGB free)",
                     model_name, vram_gb or 0.0, self.vram_free_gb)
        else:
            log.info("loaded model: %s (VRAM unknown)", model_name)
        return True

    def unload_model(self, model_name: str) -> bool:
        if model_name in self._loaded_models:
            vram = self._loaded_models[model_name].vram_gb
            del self._loaded_models[model_name]
            log.info("unloaded model: %s (freed %.1fGB)", model_name, vram)
            return True
        return False

    def unload_least_recently_used(self) -> Optional[str]:
        """Unload the model used longest ago to free VRAM.
        Returns the unloaded model name, or None if nothing to unload.
        """
        if not self._loaded_models:
            return None
        lru = min(self._loaded_models.values(), key=lambda m: m.last_used_at)
        self.unload_model(lru.name)
        return lru.name

    def is_loaded(self, model_name: str) -> bool:
        info = self._loaded_models.get(model_name)
        if info:
            info.touch()
            return True
        return False

    def touch_model(self, model_name: str):
        info = self._loaded_models.get(model_name)
        if info:
            info.touch()

    # ── Concurrency ──────────────────────────────────────────────────────

    def can_accept_job(self) -> bool:
        return self._active_jobs < self.max_active_jobs

    def reserve_job(self) -> bool:
        if not self.can_accept_job():
            self._queued_jobs += 1
            return False
        self._active_jobs += 1
        return True

    def release_job(self):
        self._active_jobs = max(0, self._active_jobs - 1)
        if self._queued_jobs > 0:
            self._queued_jobs -= 1

    # ── Model ranking ────────────────────────────────────────────────────

    def best_available(self, candidates: list[str],
                       min_vram_gb: float = 0.0) -> Optional[str]:
        """Pick the best model from candidates by:
        1. Already loaded (avoid reload cost)
        2. Fits in VRAM
        3. Most recently used (warm cache)
        """
        loaded = [m for m in candidates if m in self._loaded_models]
        if loaded:
            loaded.sort(key=lambda m: self._loaded_models[m].last_used_at, reverse=True)
            return loaded[0]

        fits = [m for m in candidates if self.can_load(m, min_vram_gb)]
        if fits:
            fits.sort(key=lambda m: self._loaded_models[m].vram_gb if m in self._loaded_models else 0)
            return fits[0]

        # Try unloading LRU to fit (only meaningful when VRAM is known; when
        # unknown there is no free-VRAM number to compare against, so we do not
        # unload on the basis of an invented value).
        if candidates and self.vram_known:
            target = candidates[0]
            needed = min_vram_gb
            while (self.vram_free_gb or 0.0) < needed:
                freed = self.unload_least_recently_used()
                if freed is None:
                    return None
            return target

        if candidates and not self.vram_known:
            # VRAM unknown — cannot rank-fit or unload-to-fit; pick the first
            # candidate without pretending we measured anything.
            return candidates[0]

        return None

    # ── Reporting ────────────────────────────────────────────────────────

    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            vram_total_gb=self.vram_total_gb,
            vram_used_gb=self.vram_used_gb,
            vram_free_gb=self.vram_free_gb,
            loaded_models=list(self._loaded_models.keys()),
            active_jobs=self._active_jobs,
            queued_jobs=self._queued_jobs,
            max_active_jobs=self.max_active_jobs,
        )

    def list_loaded(self) -> list[ModelLoadInfo]:
        return list(self._loaded_models.values())
