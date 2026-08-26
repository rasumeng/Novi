"""M2.1 — Hardware detection foundation tests.

Covers the UNKNOWN-capable hardware detector and ResourceManager UNKNOWN-VRAM
safety. External hardware commands (nvidia-smi, psutil, wmic) are mocked — the
tests never require a real GPU.

Key invariants verified:
    - GPU+VRAM detected  -> HIGH confidence, numeric VRAM
    - GPU detected, no VRAM -> MEDIUM confidence, VRAM is None (never a guess)
    - GPU unavailable    -> LOW/UNKNOWN, no fabricated GPU
    - nvidia-smi missing/fails -> treated as UNKNOWN, never app failure
    - system RAM detected as a double-check secondary signal
    - UNKNOWN VRAM never becomes a numeric fallback (0/8/16/...)
    - ResourceManager is safe (no arithmetic crash, no fabricated numbers)
"""

from types import SimpleNamespace

import pytest

import novi.configuration.hardware as hardware
from novi.configuration.hardware import (
    DetectionConfidence,
    GpuConfidence,
    HardwareProfile,
    detect_gpu,
    detect_hardware,
    detect_ram_gb,
)
from novi.runtime.resources import ResourceManager


# ── GPU detection ─────────────────────────────────────────────────────────


def _replace_backends(monkeypatch, backend):
    monkeypatch.setattr(hardware, "_GPU_BACKENDS", [backend])


def _smi_result(text: str, returncode: int = 0):
    return SimpleNamespace(stdout=text, returncode=returncode)


def test_gpu_and_vram_detected(monkeypatch):
    _replace_backends(monkeypatch, lambda: ("nvidia", "RTX 4090", 24.0))
    gpu = detect_gpu()
    assert gpu.name == "RTX 4090"
    assert gpu.vram_total_gb == 24.0
    assert gpu.confidence == GpuConfidence.KNOWN_VRAM


def test_gpu_known_vram_unavailable(monkeypatch):
    # nvidia-smi reports the GPU but no parseable memory -> VRAM must stay None
    _replace_backends(monkeypatch, lambda: ("nvidia", "GTX 1080", None))
    gpu = detect_gpu()
    assert gpu.name == "GTX 1080"
    assert gpu.vram_total_gb is None
    assert gpu.confidence == GpuConfidence.KNOWN_NO_VRAM


def test_gpu_unavailable(monkeypatch):
    _replace_backends(monkeypatch, lambda: None)
    gpu = detect_gpu()
    assert gpu.name == ""
    assert gpu.vram_total_gb is None
    assert gpu.confidence == GpuConfidence.UNKNOWN


def test_nvidia_smi_command_missing(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda _: None)
    monkeypatch.delenv("NVIDIA_SMI", raising=False)
    assert hardware._nvidia_smi() is None


def test_nvidia_smi_command_fails(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(hardware.subprocess, "run",
                        lambda *a, **k: _smi_result("", returncode=1))
    assert hardware._nvidia_smi() is None


def test_nvidia_smi_vram_parse(monkeypatch):
    # memory.total reported in MiB -> converted to GB (8192 MiB = 8.0 GB)
    monkeypatch.setattr(hardware.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(
        hardware.subprocess, "run",
        lambda *a, **k: _smi_result("NVIDIA GeForce RTX 4060, 8192\n"))
    assert hardware._nvidia_smi() == ("nvidia", "NVIDIA GeForce RTX 4060", 8.0)


# ── Confidence + overall profile ──────────────────────────────────────────


def test_hardware_high_when_gpu_and_vram_known(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu",
                        lambda: hardware.GpuInfo("nvidia", "RTX 4060", 8.0,
                                                 GpuConfidence.KNOWN_VRAM))
    monkeypatch.setattr(hardware, "detect_ram_gb", lambda: 32.0)
    monkeypatch.setattr(hardware, "detect_cpu", lambda: hardware.CpuInfo("cpu", 8))
    hp = detect_hardware()
    assert hp.confidence == DetectionConfidence.HIGH
    assert hp.gpu.vram_total_gb == 8.0


def test_hardware_medium_when_gpu_known_no_vram(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu",
                        lambda: hardware.GpuInfo("nvidia", "GTX 1080", None,
                                                 GpuConfidence.KNOWN_NO_VRAM))
    monkeypatch.setattr(hardware, "detect_ram_gb", lambda: 32.0)
    hp = detect_hardware()
    assert hp.confidence == DetectionConfidence.MEDIUM
    assert hp.gpu.vram_total_gb is None


def test_hardware_low_when_gpu_unknown_but_ram_known(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu",
                        lambda: hardware.GpuInfo(confidence=GpuConfidence.UNKNOWN))
    monkeypatch.setattr(hardware, "detect_ram_gb", lambda: 16.0)
    hp = detect_hardware()
    assert hp.confidence == DetectionConfidence.LOW
    assert hp.gpu.name == ""


def test_hardware_unknown_when_nothing_useful(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu",
                        lambda: hardware.GpuInfo(confidence=GpuConfidence.UNKNOWN))
    monkeypatch.setattr(hardware, "detect_ram_gb", lambda: None)
    monkeypatch.setattr(hardware, "detect_cpu", lambda: hardware.CpuInfo("", None))
    hp = detect_hardware()
    assert hp.confidence == DetectionConfidence.UNKNOWN


# ── RAM detection (secondary signal) ──────────────────────────────────────


def test_ram_detected(monkeypatch):
    fake = SimpleNamespace(virtual_memory=lambda: SimpleNamespace(
        total=16 * 1024**3))
    monkeypatch.setitem(hardware.sys.modules, "psutil", fake)
    assert detect_ram_gb() == 16.0


def test_ram_unknown_when_unavailable(monkeypatch):
    monkeypatch.setitem(hardware.sys.modules, "psutil", None)
    monkeypatch.setattr(hardware.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert detect_ram_gb() is None


# ── UNKNOWN VRAM propagation in ResourceManager ──────────────────────────


def test_unknown_vram_never_becomes_numeric_fallback():
    rm = ResourceManager()  # no invented 8.0 default
    assert rm.vram_total_gb is None
    assert rm.vram_free_gb is None
    assert rm.vram_known is False


def test_unknown_vram_refuses_vram_constraint():
    rm = ResourceManager()
    # cannot claim a model fits when VRAM is unknown
    assert rm.can_load("alpha", 4.0) is False
    # no VRAM requirement -> nothing to refuse
    assert rm.can_load("alpha", 0.0) is True


def test_unknown_vram_load_refused_for_vram_required():
    rm = ResourceManager()
    assert rm.load_model("alpha", 4.0) is False
    assert not rm.is_loaded("alpha")
    # zero requirement loads fine even without VRAM knowledge
    assert rm.load_model("beta", 0.0) is True


def test_unknown_vram_best_available_does_not_guess():
    rm = ResourceManager()
    # does not crash, does not unload-to-fit on unknown VRAM
    assert rm.best_available(["a", "b"], min_vram_gb=6.0) == "a"


def test_known_vram_still_enforces_capacity():
    rm = ResourceManager(vram_total_gb=8.0)
    assert rm.vram_known is True
    assert rm.can_load("x", 4.0) is True
    assert rm.can_load("x", 10.0) is False
    assert rm.load_model("x", 10.0) is False


def test_known_vram_tracks_usage_via_snapshot():
    rm = ResourceManager(vram_total_gb=8.0)
    rm.load_model("m", 3.0)
    snap = rm.snapshot()
    assert snap.vram_total_gb == 8.0
    assert snap.vram_used_gb == 3.0
    assert snap.vram_free_gb == 5.0


def test_hardware_profile_dict_none_vram():
    hp = HardwareProfile()
    d = hp.to_dict()
    assert d["gpu"]["vram_total_gb"] is None
    assert d["ram"] is None
