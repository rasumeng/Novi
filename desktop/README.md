# Cozmo Desktop (Tauri)

Desktop shell around the existing Cozmo WebUI. Thin wrapper only — the React
UI, FastAPI backend, and WebSocket protocol are untouched.

The app spawns `python -m cozmo webui` as a child process (same command used
in the browser workflow), waits for the backend to become ready, then opens a
native window pointed at it. WebSocket and API traffic stay identical.

## Layout

```
desktop/
  package.json              npm scripts (run from this directory)
  src-tauri/
    tauri.conf.json         Tauri config (dev URL, build hooks, bundling)
    src/main.rs             window + lifecycle wiring
    src/backend/launcher.rs BackendLauncher (child-process abstraction)
    capabilities/*          Tauri permissions (minimal, no IPC used)
    icons/                  generated icon set + generate-icons.ps1
```

`src-tauri/` lives two levels below the repo root, so the interpretation of
`tauri.conf.json` paths is:

- `desktop/` is the app root where you run `tauri dev` / `tauri build`.
- `../cozmo/webui` from `desktop/` is the frontend.
- `../../cozmo/webui/dist` from `src-tauri/` is the built frontend.

## Development (hot reload)

The CLI must be run from `desktop/` so relative paths resolve.

```bash
cd desktop
npm install            # installs @tauri-apps/cli
npm run dev            # = tauri dev
```

`tauri dev`:

1. Runs `npm --prefix ../cozmo/webui run dev` (Vite on :5173, HMR) via
   `beforeDevCommand`.
2. The Rust process spawns `python -m cozmo webui` (backend on :8765).
3. Waits for the backend health check (`GET /api/models`) to pass.
4. Opens the window at `http://localhost:5173`.

Edit `cozmo/webui/src/**` and HMR updates instantly. The WebSocket still hits
`127.0.0.1:8765/ws/chat`, exactly as in the browser dev workflow.

The launcher always spawns its own backend on `:8765`. Backend logs are
forwarded to the Tauri process's stderr as `[cozmo-backend]` lines. For a
plain browser workflow, keep using `cozmo webui` as before.

## Production build

```bash
cd desktop
npm run build          # tauri build
```

`tauri build`:

1. Runs `npm --prefix ../cozmo/webui run build` (`tsc && vite build` → dist).
2. Compiles the Rust release binary.
3. Bundles Windows installers (NSIS + MSI).

At runtime the release builds spawn the backend and load
`http://127.0.0.1:8765`, where FastAPI serves `webui/dist` and the WebSocket
same-origin — so no CORS and no source changes are required.

Output goes to `src-tauri/target/release/bundle/`.

## Configuration (env vars)

| Variable | Purpose |
| --- | --- |
| `COZMO_REPO_ROOT` | Repo location (default: derived from the binary path) |
| `COZMO_PYTHON` | Python interpreter to spawn instead of auto-detected venv |
| `COZMO_BACKEND_PORT` | Backend port (default `8765`) |
| `COZMO_BACKEND_BIN` | Path to a bundled sidecar executable (replaces venv mode) |

## Moving to a bundled sidecar later

The backend launcher is isolated in `src/backend/launcher.rs`. Setting
`COZMO_BACKEND_BIN=/path/to/cozmo-backend` switches it from
`python -m cozmo webui` to launching that executable with `--host`/`--port`.
No frontend or UI code needs to change.

## Troubleshooting

- **Window never opens** — the backend failed to become ready in 60s. Read the
  `[cozmo-backend]` log lines in the terminal; usually a port conflict or a
  missing venv.
- **Port 8765 already in use** — stop the browser-mode backend first.
- **Rust not installed** — install via
  [rustup](https://rustup.rs/), then run `tauri dev`.
- **Missing icons** — re-run `powershell -File src-tauri/icons/generate-icons.ps1`.