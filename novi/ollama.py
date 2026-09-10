"""Ollama process management — discovery, start, stop, and health."""

import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request


def find_ollama_executable() -> str | None:
    """Find Ollama without requiring a Windows PATH edit."""
    on_path = shutil.which("ollama")
    if on_path:
        return on_path
    if os.name != "nt":
        return None

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
    user_profile = os.environ.get("USERPROFILE", "")
    candidates = [
        Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe",
        Path(program_files) / "Ollama" / "ollama.exe",
        Path(program_files_x86) / "Ollama" / "ollama.exe",
        Path(user_profile) / "scoop" / "apps" / "ollama" / "current" / "ollama.exe",
        Path(r"C:\\ProgramData\\chocolatey\\bin\\ollama.exe"),
    ]
    packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        candidates.extend(packages.glob("Ollama.Ollama_*/*ollama.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def is_ollama_running(timeout: float = 2, ollama_url: str = "http://localhost:11434") -> bool:
    try:
        req = urllib.request.Request(f"{ollama_url.rstrip('/')}/api/tags")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def start_ollama(ollama_url: str = "http://localhost:11434") -> subprocess.Popen | None:
    if is_ollama_running(ollama_url=ollama_url):
        return None
    executable = find_ollama_executable()
    if executable is None:
        return None
    try:
        proc = subprocess.Popen([executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait_for_ollama(ollama_url):
            return proc
        return None
    except Exception:
        return None


def stop_ollama(proc: subprocess.Popen | None = None) -> bool:
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    executable = find_ollama_executable()
    if executable is None:
        return False
    try:
        subprocess.run([executable, "stop"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def wait_for_ollama(ollama_url: str = "http://localhost:11434", timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_ollama_running(ollama_url=ollama_url):
            return True
        time.sleep(1)
    return False
