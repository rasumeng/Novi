"""Real hardware detection (M2.1).

Replaces the previous RAM-only / invented-VRAM assumptions with an
UNKNOWN-capable detection layer that a future Automatic model resolver can
consume. The cardinal rule: **VRAM is never invented.** When VRAM cannot be
detected it stays an explicit ``UNKNOWN`` (``None``) — never a substituted
number (system RAM, GPU estimate, common default, arbitrary fallback).

Detection is command-based and extensible: an ``nvidia-smi`` backend (Windows
and Linux) plus a system RAM/CU/OS probe. Other GPUs (AMD/Intel/macOS) are
intentionally NOT resolved here; the detector returns the corresponding UNKNOWN
state so downstream code can distinguish "no GPU / cannot tell" from a measure.

The detector is a small seam only: it produces a ``HardwareProfile``. No model
qualification, ranking, or compatibility logic lives here.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DetectionConfidence(str, Enum):
    """How sure we are about the overall hardware picture.

    - ``HIGH``    — GPU known and its VRAM is known.
    - ``MEDIUM``  — GPU known but VRAM is NOT known.
    - ``LOW``     — GPU unknown, but useful RAM/CPU info exists.
    - ``UNKNOWN`` — essentially no useful hardware information.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class GpuConfidence(str, Enum):
    """Confidence for the GPU sub-probe alone."""

    KNOWN_VRAM = "known_vram"
    KNOWN_NO_VRAM = "known_no_vram"
    UNKNOWN = "unknown"


# Sentinel for an undetected / missing value. Used instead of inventing a
# number so ``UNKNOWN`` never silently becomes 0, 8, 16, or any guess.
UNKNOWN = None


@dataclass
class GpuInfo:
    """Detected GPU facts.

    ``vram_total_gb`` is ``None`` (``UNKNOWN``) when VRAM could not be
    measured — downstream code MUST treat ``None`` as "don't know", never as a
    numeric value.
    """

    vendor: str = ""
    name: str = ""
    vram_total_gb: Optional[float] = UNKNOWN
    confidence: GpuConfidence = GpuConfidence.UNKNOWN

    @property
    def present(self) -> bool:
        return bool(self.name) or self.confidence in (
            GpuConfidence.KNOWN_VRAM,
            GpuConfidence.KNOWN_NO_VRAM,
        )

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "name": self.name,
            "vram_total_gb": self.vram_total_gb,
            "detection_confidence": self.confidence.value,
        }


@dataclass
class CpuInfo:
    """Coarse CPU probe (secondary signal)."""

    name: str = ""
    cores: Optional[int] = UNKNOWN


@dataclass
class HardwareProfile:
    """Host hardware facts with explicit detection confidence.

    ``ram_gb`` / ``cpu`` are best-effort secondary signals and may be ``None``.
    ``gpu.vram_total_gb`` is ``None`` when unknown — never a guessed value.
    """

    gpu: GpuInfo = field(default_factory=GpuInfo)
    ram_gb: Optional[float] = UNKNOWN
    cpu: CpuInfo = field(default_factory=CpuInfo)
    platform: str = ""
    confidence: DetectionConfidence = DetectionConfidence.UNKNOWN

    def to_dict(self) -> dict:
        return {
            "gpu": self.gpu.to_dict(),
            "ram": self.ram_gb,
            "cpu": {"name": self.cpu.name, "cores": self.cpu.cores},
            "platform": self.platform,
            "confidence": self.confidence.value,
        }


# ── GPU backends ──────────────────────────────────────────────────────────


def _nvidia_smi() -> Optional[tuple[str, str, Optional[float]]]:
    """Return (vendor, gpu_name, vram_total_gb | None) from nvidia-smi.

    ``None`` means the command is unavailable/failed. When nvidia-smi runs but
    reports no memory, the GPU is known with ``vram=None``.
    """
    exe = shutil.which("nvidia-smi") or os.environ.get("NVIDIA_SMI")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0:
            return None
        first = (out.stdout or "").strip().splitlines()
        if not first:
            return None
        parts = [p.strip() for p in first[0].split(",") if p.strip()]
        name = parts[0] if parts else ""
        gpu = ("nvidia", name, None)
        if len(parts) >= 2 and parts[1].isdigit():
            vram_mib = int(parts[1])
            if vram_mib > 0:
                gpu = ("nvidia", name, round(vram_mib / 1024.0, 1))
        return gpu
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


_GPU_BACKENDS: list = [_nvidia_smi]


def detect_gpu() -> GpuInfo:
    """Detect the primary GPU using the available backends."""
    for backend in _GPU_BACKENDS:
        result = backend()
        if result is None:
            continue
        vendor, name, vram = result
        if vram is not None:
            return GpuInfo(
                vendor=vendor,
                name=name,
                vram_total_gb=vram,
                confidence=GpuConfidence.KNOWN_VRAM,
            )
        if name:
            return GpuInfo(
                vendor=vendor,
                name=name,
                vram_total_gb=UNKNOWN,
                confidence=GpuConfidence.KNOWN_NO_VRAM,
            )
        return GpuInfo(confidence=GpuConfidence.UNKNOWN)
    return GpuInfo(confidence=GpuConfidence.UNKNOWN)


# ── System (RAM / CPU / OS) ───────────────────────────────────────────────


def detect_ram_gb() -> Optional[float]:
    """Total physical RAM in GB, or None when undetectable."""
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass
    # Cross-platform-ish fallback via wmic (legacy Windows).
    try:
        out = subprocess.run(
            ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            if line.strip().isdigit():
                return round(int(line) / (1024**3), 1)
    except Exception:
        pass
    return UNKNOWN


def detect_cpu() -> CpuInfo:
    """Coarse CPU probe (name + logical cores), best effort."""
    try:
        os.cpu_count()
    except Exception:
        pass
    cores = None
    try:
        cores = os.cpu_count()
    except Exception:
        cores = None
    name = ""
    try:
        import platform as _p
        name = _p.processor() or ""
    except Exception:
        name = ""
    return CpuInfo(name=name or "", cores=cores)


def detect_platform() -> str:
    return platform.system().lower() or platform.platform()


def detect_hardware() -> HardwareProfile:
    """Produce a HardwareProfile with explicit confidence.

    Confidence derivation (per spec):
      - HIGH    : GPU known and VRAM known.
      - MEDIUM  : GPU known but VRAM unknown.
      - LOW     : GPU unknown, but useful RAM/CPU info exists.
      - UNKNOWN : essentially no useful hardware information.
    """
    gpu = detect_gpu()
    ram_gb = detect_ram_gb()
    cpu = detect_cpu()
    platform_name = detect_platform()

    if gpu.confidence == GpuConfidence.KNOWN_VRAM:
        confidence = DetectionConfidence.HIGH
    elif gpu.confidence == GpuConfidence.KNOWN_NO_VRAM:
        confidence = DetectionConfidence.MEDIUM
    elif ram_gb is not None or cpu.cores is not None:
        confidence = DetectionConfidence.LOW
    else:
        confidence = DetectionConfidence.UNKNOWN

    return HardwareProfile(
        gpu=gpu,
        ram_gb=ram_gb,
        cpu=cpu,
        platform=platform_name,
        confidence=confidence,
    )
