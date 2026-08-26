"""SearXNG auto-setup and management utilities."""

import os
import subprocess
import sys
import time
import urllib.request
import urllib.error


SEARXNG_CONTAINER = "novi-searxng"
SEARXNG_IMAGE = "searxng/searxng"
SEARXNG_PORT = 8080

DOCKER_DESKTOP_PATHS = [
    r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
    r"C:\Program Files\Docker\Docker\resources\Docker Desktop.exe",
    os.path.expanduser(r"~\AppData\Local\Docker\Docker Desktop\Docker Desktop.exe"),
]


def is_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_docker_daemon_reachable() -> bool:
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _launch_docker_desktop() -> bool:
    for path in DOCKER_DESKTOP_PATHS:
        if os.path.isfile(path):
            print("Docker Desktop not running. Launching...")
            try:
                subprocess.Popen([path], shell=True)
                return True
            except Exception:
                pass
    return False


def _wait_for_docker_daemon(timeout: int = 60) -> bool:
    for _ in range(timeout):
        if is_docker_daemon_reachable():
            return True
        time.sleep(1)
    return False


def ensure_docker_daemon() -> bool:
    if is_docker_daemon_reachable():
        return True
    if sys.platform == "win32" and _launch_docker_desktop():
        print("Waiting for Docker Desktop to start...")
        return _wait_for_docker_daemon(120)
    return False


def is_searxng_running(port: int = SEARXNG_PORT, timeout: float = 2) -> bool:
    """Check if SearXNG is running on the specified port."""
    try:
        req = urllib.request.Request(f"http://localhost:{port}/search?q=test&format=json")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def start_searxng(port: int = SEARXNG_PORT) -> bool:
    if not is_docker_available():
        print("Docker CLI not found. Install Docker to enable web search (SearXNG).")
        return False

    if not ensure_docker_daemon():
        print("Docker daemon not reachable. Start Docker Desktop manually.")
        return False

    if is_searxng_running(port):
        print(f"SearXNG already running on port {port}")
        return True

    print(f"Starting SearXNG on port {port}...")

    try:
        existing = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={SEARXNG_CONTAINER}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        container_exists = SEARXNG_CONTAINER in existing.stdout.splitlines()

        if container_exists:
            result = subprocess.run(
                ["docker", "start", SEARXNG_CONTAINER],
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            result = subprocess.run(
                [
                    "docker", "run", "-d",
                    "--name", SEARXNG_CONTAINER,
                    "-p", f"{port}:8080",
                    "-e", "SEARXNG_BASE_URL=http://localhost:8080/",
                    "--restart", "unless-stopped",
                    SEARXNG_IMAGE,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

        if result.returncode != 0:
            _print_docker_error(result.stderr)
            return False

        for _ in range(30):
            time.sleep(1)
            if is_searxng_running(port):
                print(f"SearXNG started successfully on port {port}")
                return True

        print(f"SearXNG did not become ready within 30s. Check with: docker logs {SEARXNG_CONTAINER}")
        return False
    except FileNotFoundError:
        print("Failed to start SearXNG: Docker not installed or not running.")
        return False
    except Exception as e:
        _print_docker_error(str(e))
        return False


def _print_docker_error(stderr: str):
    """Print an actionable error message based on Docker stderr output."""
    text = stderr or ""
    lowered = text.lower()
    if "is already in use" in lowered:
        print(f"Failed to start SearXNG: container name conflict. Try: docker rm {SEARXNG_CONTAINER}")
    elif "port is already allocated" in lowered or "bind" in lowered:
        print("Failed to start SearXNG: port conflict. Change the SearXNG port in config or free the port.")
    elif "cannot connect to the docker daemon" in lowered:
        print("Failed to start SearXNG: Docker not installed or not running.")
    else:
        print(f"Failed to start SearXNG: {text.strip() or 'unknown error'}")


def stop_searxng():
    """Stop SearXNG container."""
    try:
        subprocess.run(
            ["docker", "stop", SEARXNG_CONTAINER],
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            ["docker", "rm", SEARXNG_CONTAINER],
            capture_output=True,
            text=True,
            timeout=10,
        )
        print("SearXNG stopped and removed.")
    except Exception as e:
        print(f"Error stopping SearXNG: {e}")


def get_searxng_status() -> dict:
    """Get SearXNG status information."""
    docker_available = is_docker_available()
    running = is_searxng_running() if docker_available else False

    return {
        "docker_available": docker_available,
        "running": running,
        "url": f"http://localhost:{SEARXNG_PORT}" if running else None,
        "container": SEARXNG_CONTAINER,
    }


def ensure_searxng(port: int = SEARXNG_PORT) -> str:
    if is_searxng_running(port):
        return f"http://localhost:{port}"

    if is_docker_available():
        if ensure_docker_daemon() and start_searxng(port):
            return f"http://localhost:{port}"

    return ""
