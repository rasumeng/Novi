"""Ollama process management — start, stop, check health."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request


def is_ollama_running(timeout: float = 2) -> bool:
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def start_ollama(ollama_url: str = "http://localhost:11434") -> subprocess.Popen | None:
    if is_ollama_running():
        return None
    try:
        proc = subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    try:
        subprocess.run(["ollama", "stop"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def wait_for_ollama(ollama_url: str = "http://localhost:11434", timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_ollama_running():
            return True
        time.sleep(1)
    return False
