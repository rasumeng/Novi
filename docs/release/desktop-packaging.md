# Desktop Packaging Decision Record

**Status:** Active for the desktop beta

Novi is currently a desktop-only product. A release must not depend on a user
having a clone of this repository, a virtual environment, or a system Python
installation.

## Decision

The packaged Tauri application launches a frozen `novi-backend` sidecar. The
sidecar owns only the local FastAPI/WebUI server and is bound to `127.0.0.1`.
Development builds may still use a local Python environment to preserve the
fast edit/run loop. Release builds never fall back to Python: if the bundled
backend is absent, they show a clear reinstall error instead.

## Build process

1. Install release tooling: `pip install -e .[desktop-build]`.
2. Build the sidecar: `python scripts/build_desktop_backend.py`.
3. Build the desktop package: `npm --prefix novi/webui run desktop:build`.

The sidecar is copied to `novi/webui/src-tauri/resources/` and ignored by Git,
because it is a platform-specific build artifact. Tauri includes that resource
in the installer. The release job must run the sidecar build before its Tauri
package build.

## Why this boundary exists

The browser UI, desktop shell, and backend remain separate layers. Tauri owns
window lifecycle and starts/stops the process; `novi.desktop_backend` owns
backend boot; existing FastAPI and agent systems remain unchanged. This keeps
the release packaging work from creating a second runtime implementation.

## Historical context

The earlier thin-shell design and its Python/venv launcher are retained in
`docs/desktop.md` as historical context. This record supersedes it for release
packaging decisions; it does not erase the rationale or evolution of the
desktop architecture.
