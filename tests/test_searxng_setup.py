"""Hermetic coverage for the opt-in local SearXNG setup endpoint."""

import pytest
from io import BytesIO


@pytest.fixture(autouse=True)
def _isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    import novi.configuration.bootstrap as boot

    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".novi" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)


def _client():
    from fastapi.testclient import TestClient
    from novi.webui_server import create_app

    return TestClient(create_app(cfg={}))


def test_health_probe_requires_a_json_search_response(monkeypatch):
    import novi.searxng_util as searxng_util

    monkeypatch.setattr(
        searxng_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: BytesIO(b"<html>SearXNG</html>"),
    )

    assert searxng_util.is_searxng_running() is False


def test_health_probe_accepts_json_search_response(monkeypatch):
    import novi.searxng_util as searxng_util

    monkeypatch.setattr(
        searxng_util.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: BytesIO(b'{"query":"test","results":[]}'),
    )

    assert searxng_util.is_searxng_running() is True


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", r"C:\Users\tester\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"),
        ("darwin", "/Applications/Docker.app/Contents/Resources/bin/docker"),
        ("linux", "/usr/bin/docker"),
    ],
)
def test_docker_cli_is_discovered_from_platform_install(platform, expected, monkeypatch):
    import novi.searxng_util as searxng_util

    monkeypatch.setattr(searxng_util.shutil, "which", lambda _name: None)
    monkeypatch.setattr(searxng_util.sys, "platform", platform)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    monkeypatch.setattr(searxng_util.os.path, "isfile", lambda path: path == expected)

    assert searxng_util._docker_executable() == expected


def test_setup_searxng_enables_search_after_success(monkeypatch):
    import novi.searxng_util as searxng_util
    from novi.configuration.bootstrap import get_configuration

    monkeypatch.setattr(
        searxng_util,
        "setup_searxng",
        lambda: {
            "ok": True,
            "state": "connected",
            "url": "http://localhost:8080",
            "message": "SearXNG is ready.",
        },
    )

    response = _client().post("/api/search/setup/searxng")

    assert response.status_code == 200
    assert response.json()["state"] == "connected"
    assert get_configuration().get("search.backend") == "searxng"
    assert get_configuration().get("search.url") == "http://localhost:8080"


def test_setup_searxng_preserves_search_config_after_failure(monkeypatch):
    import novi.searxng_util as searxng_util
    from novi.configuration.bootstrap import get_configuration

    config = get_configuration()
    config.set("search.backend", "brave", by="test")
    config.set("search.brave_api_key", "existing-key", by="test")
    monkeypatch.setattr(
        searxng_util,
        "setup_searxng",
        lambda: {
            "ok": False,
            "state": "docker_missing",
            "message": "Install Docker Desktop, then try again.",
        },
    )

    response = _client().post("/api/search/setup/searxng")

    assert response.status_code == 200
    assert response.json()["state"] == "docker_missing"
    assert config.get("search.backend") == "brave"
    assert config.get("search.brave_api_key") == "existing-key"


def test_docker_container_enables_json_and_is_bound_to_loopback(tmp_path, monkeypatch):
    import novi.searxng_util as searxng_util

    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(searxng_util, "is_docker_available", lambda: True)
    monkeypatch.setattr(searxng_util, "_docker_executable", lambda: "docker")
    monkeypatch.setattr(searxng_util, "ensure_docker_daemon", lambda: True)
    monkeypatch.setattr(searxng_util, "is_searxng_running", lambda port=8080: len(calls) >= 2)
    monkeypatch.setattr(searxng_util, "SEARXNG_CONFIG_DIR", tmp_path / "searxng")
    monkeypatch.setattr(searxng_util.subprocess, "run", fake_run)
    monkeypatch.setattr(searxng_util.time, "sleep", lambda _seconds: None)

    assert searxng_util.start_searxng() is True
    docker_run = next(command for command in calls if command[:3] == ["docker", "run", "-d"])
    assert "127.0.0.1:8080:8080" in docker_run
    assert "-v" in docker_run
    assert any(value.endswith(":/etc/searxng:rw") for value in docker_run)
    settings = (tmp_path / "searxng" / "settings.yml").read_text(encoding="utf-8")
    assert "use_default_settings: true" in settings
    assert "- json" in settings
    assert "secret_key:" in settings
    assert "ultrasecretkey" not in settings


def test_unhealthy_managed_container_is_recreated(tmp_path, monkeypatch):
    import novi.searxng_util as searxng_util

    calls = []

    class Result:
        returncode = 0
        stdout = "novi-searxng\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(searxng_util, "is_docker_available", lambda: True)
    monkeypatch.setattr(searxng_util, "_docker_executable", lambda: "docker")
    monkeypatch.setattr(searxng_util, "ensure_docker_daemon", lambda: True)
    monkeypatch.setattr(searxng_util, "is_searxng_running", lambda port=8080: len(calls) >= 3)
    monkeypatch.setattr(searxng_util, "SEARXNG_CONFIG_DIR", tmp_path / "searxng")
    monkeypatch.setattr(searxng_util.subprocess, "run", fake_run)
    monkeypatch.setattr(searxng_util.time, "sleep", lambda _seconds: None)

    assert searxng_util.start_searxng() is True
    assert ["docker", "rm", "-f", "novi-searxng"] in calls
    assert any(command[:3] == ["docker", "run", "-d"] for command in calls)
