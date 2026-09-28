"""SearXNG auto-setup and management utilities."""

import os
import json
import secrets
import subprocess
import sys
import time
import urllib.request
import urllib.error
import shutil
from pathlib import Path, PureWindowsPath


SEARXNG_CONTAINER = "novi-searxng"
SEARXNG_IMAGE = "searxng/searxng"
SEARXNG_PORT = 8080
SEARXNG_CONFIG_DIR = Path.home() / ".novi" / "searxng"

SEARXNG_SETTINGS_TEMPLATE = """\
use_default_settings: true
search:
  formats:
    - html
    - json
server:
  secret_key: "{secret_key}"
"""

def _docker_cli_paths() -> list[str]:
    """Return common Docker CLI locations for GUI apps with a minimal PATH."""
    if sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        paths = [
            str(PureWindowsPath(program_files) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"),
        ]
        if local_app_data:
            paths.append(str(PureWindowsPath(local_app_data) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"))
        return paths
    if sys.platform == "darwin":
        return [
            "/Applications/Docker.app/Contents/Resources/bin/docker",
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
        ]
    return [
        "/usr/bin/docker",
        "/usr/local/bin/docker",
        "/snap/bin/docker",
        str(Path.home() / ".docker" / "bin" / "docker"),
    ]


def _docker_desktop_paths() -> list[str]:
    if sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        paths = [
            str(PureWindowsPath(program_files) / "Docker" / "Docker" / "Docker Desktop.exe"),
            str(PureWindowsPath(program_files) / "Docker" / "Docker" / "resources" / "Docker Desktop.exe"),
        ]
        if local_app_data:
            paths.extend([
                str(PureWindowsPath(local_app_data) / "Docker" / "Docker Desktop" / "Docker Desktop.exe"),
                str(PureWindowsPath(local_app_data) / "Programs" / "DockerDesktop" / "Docker Desktop.exe"),
            ])
        return paths
    if sys.platform == "darwin":
        return ["/Applications/Docker.app/Contents/MacOS/Docker"]
    return []


def _docker_executable() -> str | None:
    """Find Docker even when a per-user install is absent from PATH."""
    command = shutil.which("docker")
    if command:
        return command
    return next((path for path in _docker_cli_paths() if os.path.isfile(path)), None)


def is_docker_available() -> bool:
    docker = _docker_executable()
    if not docker:
        return False
    try:
        result = subprocess.run(
            [docker, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_docker_daemon_reachable() -> bool:
    docker = _docker_executable()
    if not docker:
        return False
    try:
        result = subprocess.run(
            [docker, "ps"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _launch_docker_desktop() -> bool:
    for path in _docker_desktop_paths():
        if os.path.isfile(path):
            print("Docker Desktop not running. Launching...")
            try:
                subprocess.Popen([path])
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
    if sys.platform in {"win32", "darwin"} and _launch_docker_desktop():
        print("Waiting for Docker Desktop to start...")
        return _wait_for_docker_daemon(120)
    return False


def is_searxng_running(port: int = SEARXNG_PORT, timeout: float = 2) -> bool:
    """Check if SearXNG is running on the specified port."""
    try:
        req = urllib.request.Request(f"http://localhost:{port}/search?q=test&format=json")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.load(response)
        return isinstance(payload, dict) and isinstance(payload.get("results"), list)
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
        docker = _docker_executable()
        if not docker:
            print("Docker CLI not found. Install Docker to enable web search (SearXNG).")
            return False
        SEARXNG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        settings_path = SEARXNG_CONFIG_DIR / "settings.yml"
        settings_path.write_text(
            SEARXNG_SETTINGS_TEMPLATE.format(secret_key=secrets.token_hex(32)),
            encoding="utf-8",
        )

        existing = subprocess.run(
            [docker, "ps", "-a", "--filter", f"name={SEARXNG_CONTAINER}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        container_exists = SEARXNG_CONTAINER in existing.stdout.splitlines()

        if container_exists:
            # Containers created by older Novi releases did not enable JSON,
            # so they answered our API requests with HTTP 403. Recreate the
            # managed container to apply the mounted settings below.
            removed = subprocess.run(
                [docker, "rm", "-f", SEARXNG_CONTAINER],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if removed.returncode != 0:
                _print_docker_error(removed.stderr)
                return False

        result = subprocess.run(
            [
                docker, "run", "-d",
                "--name", SEARXNG_CONTAINER,
                "-p", f"127.0.0.1:{port}:8080",
                "-v", f"{SEARXNG_CONFIG_DIR.resolve()}:/etc/searxng:rw",
                "-e", "SEARXNG_BASE_URL=http://localhost:8080/",
                "--restart", "unless-stopped",
                SEARXNG_IMAGE,
            ],
            capture_output=True,
            text=True,
            timeout=120,
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
        docker = _docker_executable()
        if not docker:
            print("Docker CLI not found.")
            return
        subprocess.run(
            [docker, "stop", SEARXNG_CONTAINER],
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            [docker, "rm", SEARXNG_CONTAINER],
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


def setup_searxng(port: int = SEARXNG_PORT) -> dict:
    """Start or reuse Novi's local SearXNG and return a UI-friendly result."""
    url = f"http://localhost:{port}"
    if is_searxng_running(port):
        return {
            "ok": True,
            "state": "connected",
            "url": url,
            "message": "SearXNG is ready and web search is enabled.",
        }
    if not is_docker_available():
        return {
            "ok": False,
            "state": "docker_missing",
            "message": "Install Docker, then try again.",
        }
    if not ensure_docker_daemon():
        return {
            "ok": False,
            "state": "docker_unavailable",
            "message": "Docker is installed but its daemon is unavailable. Start Docker, then try again.",
        }
    if not start_searxng(port):
        return {
            "ok": False,
            "state": "setup_failed",
            "message": "SearXNG could not be started. Check Docker and make sure port 8080 is free.",
        }
    return {
        "ok": True,
        "state": "connected",
        "url": url,
        "message": "SearXNG is ready and web search is enabled.",
    }
