from __future__ import annotations

import subprocess

from scripts import build_desktop_backend


def test_build_bundles_the_built_webui_into_the_backend(tmp_path, monkeypatch):
    webui_dist = tmp_path / "novi" / "webui" / "dist"
    webui_dist.mkdir(parents=True)
    (webui_dist / "index.html").write_text("<html></html>", encoding="utf-8")
    backend_dist = tmp_path / "build" / "desktop-backend"
    resources = tmp_path / "novi" / "webui" / "src-tauri" / "resources"
    captured = {}

    monkeypatch.setattr(build_desktop_backend, "ROOT", tmp_path)
    monkeypatch.setattr(build_desktop_backend, "WEBUI_DIST", webui_dist)
    monkeypatch.setattr(build_desktop_backend, "DIST", backend_dist)
    monkeypatch.setattr(build_desktop_backend, "TAURI_RESOURCES", resources)
    monkeypatch.setattr(build_desktop_backend.shutil, "which", lambda _: "pyinstaller")

    def fake_run(command, cwd):
        captured["command"] = command
        produced = backend_dist / "novi-backend.exe"
        produced.parent.mkdir(parents=True)
        produced.write_bytes(b"sidecar")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_desktop_backend.subprocess, "run", fake_run)

    assert build_desktop_backend.main() == 0
    command = captured["command"]
    data_arg = command[command.index("--add-data") + 1]
    assert data_arg == f"{webui_dist}{build_desktop_backend.os.pathsep}novi/webui/dist"
    assert (resources / "novi-backend.exe").read_bytes() == b"sidecar"


def test_build_refuses_to_make_a_backend_without_built_webui(tmp_path, monkeypatch):
    monkeypatch.setattr(build_desktop_backend, "WEBUI_DIST", tmp_path / "missing-dist")
    monkeypatch.setattr(build_desktop_backend.shutil, "which", lambda _: "pyinstaller")
    monkeypatch.setattr(
        build_desktop_backend.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not build")),
    )

    assert build_desktop_backend.main() == 2
