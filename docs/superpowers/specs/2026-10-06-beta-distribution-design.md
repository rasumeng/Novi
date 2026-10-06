# Novi Beta Distribution & First-Run — Design

**Date:** 2026-10-06
**Status:** Approved
**Scope:** Packaging, release automation, in-app updates, first-run onboarding

---

## 1. Problem

Novi's application code is healthy — `pytest -q` passes 2239 tests and
`npm run build` compiles clean. What does not exist is the layer that turns a
repository into a product someone else can download, install, and update.

Concretely, as of this design:

| Capability | State |
|---|---|
| Auto-updater | Absent. No `tauri-plugin-updater`, no `plugins.updater` block |
| Release automation | Absent. `beta-gate.yml` runs tests only; nothing builds or publishes |
| Versioning | Duplicated in 4 files; `package-lock.json` already drifted to `0.1.0` |
| First-run onboarding | Absent. Zero matches for onboarding/welcome/firstRun in source |
| Chat-model provisioning | Absent. A fresh install never selects or installs a model |
| Crash recovery | Absent. The supervisor thread exits after startup |

There is also a **silent download**: `ensure_embedding_model` runs at backend
boot (`novi/desktop_backend.py:35` *and* again at `novi/webui_server.py:2970`),
pulling ~274 MB with no user consent and no visible progress.

The "Unsloth principle" that shapes this design: **present required downloads
before starting, show progress, verify each component actually runs, preserve
progress across interruption, and mark setup complete only after the app can
successfully execute its core function.**

---

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Platform | Windows x64 only | Beta scope |
| Signing | Unsigned installer; Tauri-signed updates | No certificate budget. See §8 |
| Installer | NSIS only | One artifact, one update path |
| Ollama delivery | Download at first run, not bundled | See §8 |
| Model downloads | Consent-gated with real size shown | Large downloads need explicit opt-in |
| Release trigger | Push a version tag | Nothing ships by accident |
| GitHub labeling | Normal release, titled "Beta" | `/releases/latest` ignores prereleases |
| Update delivery | Prompt in-app, install with progress | User always chooses |
| Crash handling | Detect + one automatic restart | Removes worst support ticket |
| Audience | Small invited group | Can tolerate one Windows warning |

### 2.1 Why Ollama is downloaded rather than bundled

The full `ollama-windows-amd64.zip` is **1.47 GB** (verified against the Ollama
v0.40.0 release manifest). Bundling it would make the installer ~1.6 GB.

Tauri's Windows updater has **no differential download** — the update artifact
*is* the NSIS `setup.exe`. A bundled Ollama therefore means **every Novi update
re-downloads 1.6 GB**. During a weekly-release beta, testers will skip updates,
which defeats the purpose of building an updater at all.

Shipping a ~150 MB installer and provisioning Ollama on first run is the
resolution: one large download, once, instead of one large download per update.

---

## 3. Architecture

### 3.1 The load-bearing constraint

The web UI is served **by** the Python backend: `novi/webui_server.py:2957`
mounts `StaticFiles`, and `novi/webui/src-tauri/src/main.rs:92` points the
window at `http://127.0.0.1:8765`.

**The React app cannot exist until the Python sidecar is running.** Combined
with the blocking `ensure_embedding_model` call at
`novi/desktop_backend.py:35`, today's first launch pulls ~274 MB before a
single pixel renders.

This forces the execution order: **Rust executes provisioning steps; React
observes.** The dependency direction is also the correct one regardless — the
runtime dependency lives outside the app, so the app shell must own it.

### 3.2 Sequence

```
NSIS install (~150 MB)
   ↓
Rust starts novi-backend.exe  ← backend no longer touches Ollama at boot
   ↓
Backend serves http://127.0.0.1:8765
   ↓
React loads → BootScreen → Onboarding
   ↓
Rust::provisioning drives steps 1–4, emitting progress events
   ↓
Backend endpoint drives steps 5–7
   ↓
provisioning.json marked complete → normal app
```

### 3.3 Steps

| # | Step | Owner | Consent |
|---|---|---|---|
| 1 | Environment check (disk space, OS, arch) | Rust | no |
| 2 | Download Ollama zip, verify SHA-256 | Rust | **yes** |
| 3 | Extract; launch on `127.0.0.1:11435`; verify `/api/version` | Rust | no |
| 4 | Start backend against `:11435`; poll `/api/health` | Rust | no |
| 5 | Pull embedding model | Backend | **yes** |
| 6 | Recommend chat model; user picks or skips | Backend | **yes** |
| 7 | Execute real inference to verify; then mark complete | Backend | no |

Step 7 is the Unsloth principle made literal: onboarding is not complete until
Novi has **actually produced a generation** — not until a file exists.

Each step is independently skippable on success and retriable on failure.

### 3.4 Single channel

React talks to **only** the Rust state machine, for every step. It never
orchestrates provisioning itself. For model steps, Rust calls the backend's
HTTP API and polls the status endpoint added in §5.2.

This closes a real hole: model install progress lives in a Python in-memory dict
(`_installing_models`, `webui_server.py:1081`), so a page reload mid-download
loses it and there is no way to ask whether a download is still running.

### 3.5 Changes that must be made elsewhere

Two deletions are prerequisites, not optional cleanup:

| Delete | Location | Why |
|---|---|---|
| Ollama auto-start | `desktop_backend.py:29-33` | Rust owns the runtime. Two owners of a process is the bug. |
| `ensure_embedding_model` | `desktop_backend.py:35` **and** `webui_server.py:2970` | Both blocking and silent. Becomes consented step 5, and removes double-provisioning. |

Also fix: `desktop_backend.py:27` reads `ctx.config.get("ollama", {})` (raw dict)
while `webui_server.py:2969` reads `configuration.get("ollama.url")` — two key
spaces for one value.

### 3.6 Runtime isolation

Rust launches Ollama on **`127.0.0.1:11435`**, not `11434`. A developer machine
may already run Ollama on 11434; a distinct port makes collision impossible.
If a system Ollama is detected on 11434, show a one-line note rather than
silently contending for GPU and VRAM.

Model storage is overridden, not defaulted: `OLLAMA_MODELS` points at
`%LOCALAPPDATA%\Novi\models`, so Novi never co-mingles blobs with a
system-installed Ollama and uninstall can remove its own models.

The backend learns this URL from Rust after step 3. Until then `/api/health`
honestly reports `ollama.ready: false` — which is what that endpoint was built
for (`webui_server.py:1086`: *"honest readiness report for **first-run** and
Settings"*).

### 3.7 Backend-never-starts fallback

If the backend never starts, the window points at a dead URL and shows nothing —
today's failure mode is a blank window and an `eprintln!` nobody sees. Because
the web UI structurally cannot report its own absence, provisioning adds a
**native Rust fallback window** for exactly this case.

---

## 4. Release pipeline

### 4.1 Version becomes single-source

`0.2.0` is duplicated in `tauri.conf.json`, `Cargo.toml`, `package.json`, and
`pyproject.toml`. A new `scripts/release.py` writes all of them from one input.

`tests/test_beta_hardening.py:17` asserts `py_ver == "0.2.0"`. That hardcoded
pin **fails on the first bump** and must be replaced with a *skew* check —
which is what the test was evidently trying to express.

### 4.2 Tag-driven release

```
git tag v0.3.0-beta.1 && git push origin v0.3.0-beta.1
   ↓
release.yml: verify gate → PyInstaller sidecar → tauri build --bundles nsis
   ↓  signs with TAURI_SIGNING_PRIVATE_KEY from secrets
GitHub Release + latest.json + setup.exe + setup.exe.sig
   ↓
Novi checks on launch (≤ once per 24h) → "0.3.0 Beta 1 available — Install and restart"
```

New file `.github/workflows/release.yml`, separate from the existing
`beta-gate.yml`. The gate is unchanged; release depends on it.

### 4.3 Why not a GitHub prerelease

Tauri's simple updater endpoint is
`https://github.com/OWNER/REPO/releases/latest/download/latest.json`, but
GitHub's `/releases/latest` **excludes prereleases**. Tagging the beta as a
prerelease makes that URL 404 and updates silently never arrive. Beta builds
are therefore published as **normal releases titled "Beta"**, with the beta
marker carried in the version string.

### 4.4 Tauri configuration changes

- Add `tauri-plugin-updater` (Rust) and `@tauri-apps/plugin-updater` +
  `@tauri-apps/plugin-process` (npm).
- `bundle.targets`: `["nsis"]` — drop `msi`.
- `bundle.createUpdaterArtifacts: true` — without it no `.sig` is produced and
  the updater has nothing to verify.
- `plugins.updater.pubkey`: public key, safe to commit.
- `plugins.updater.endpoints`: the `releases/latest/download/latest.json` URL.
- `capabilities/default.json`: add `updater:default` and
  `process:allow-restart`. Without these the updater cannot be called from the
  webview at all.

### 4.5 Secrets

Both free, both GitHub Actions secrets: `TAURI_SIGNING_PRIVATE_KEY`,
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. The public key lives in
`tauri.conf.json`.

---

## 5. Onboarding implementation

### 5.1 Rust provisioning module

New `novi/webui/src-tauri/src/provisioning/`, exposing Tauri commands:
`provisioning_status`, `provisioning_start`, `provisioning_retry`,
`provisioning_cancel`.

State persists to `%LOCALAPPDATA%\Novi\provisioning.json`, so an interrupted
setup resumes at the first incomplete step rather than restarting.

The Ollama version and its expected SHA-256 are **compile-time constants in
the Rust module**, not fetched at runtime: `NOVI_OLLAMA_VERSION`,
`NOVI_OLLAMA_URL`, `NOVI_OLLAMA_SHA256`. Verification must not depend on a value
the same download could influence. At the time of writing the current pin is
`v0.40.0`, asset `ollama-windows-amd64.zip`. Bumping Ollama is a deliberate
code change that updates all three constants together.

Requirements:
- Download to a `.part` file; verify SHA-256 against the pinned digest before
  extraction; only then move into place.
- Honour HTTP range resume where the server supports it.
- Check free disk space before downloading (needs ~4 GB: 1.5 GB zip, extracted
  copy, plus models).
- Never write outside `%LOCALAPPDATA%\Novi`.

### 5.2 Backend changes

| Change | Purpose |
|---|---|
| Accept Ollama URL from Rust | Runtime is Rust-owned |
| `GET /api/models/install/status` | Survives reload; lets Rust poll |
| Remove both `ensure_embedding_model` calls | Consent + no double-pull |
| Emit `boot_error` | `useBoot.ts:81` handles it and
  `useBoot.test.tsx:94` tests it, but **no backend path sends it** — boot
  failure surfaces only as a WebSocket close |

### 5.3 Frontend

New `novi/webui/src/components/onboarding/` plus a `useOnboarding` hook, gated
in `App.tsx` after the existing boot gate (`App.tsx:202` is currently the only
branch).

`BootScreen.tsx:69-71` also renders a progress bar with **no `aria-valuenow`
binding** — `useBoot`'s real percent is computed and then discarded. Wiring it
is a small honest fix included here.

### 5.4 Honest recommendations

Two known defects must be fixed before onboarding can show a truthful choice:

1. **No download size.** `catalog.py:388` hardcodes `"size": None`, so the UI
   cannot show "4.7 GB" for an uninstalled model. Consent requires a real
   number. `ModelRecord.size_bytes` exists but is only populated from `/api/show`
   for *installed* models.
2. **Wrong model recommended on non-NVIDIA hardware.** `hardware.py:158-186`
   resolves GPU via `nvidia-smi` only, so AMD/Intel returns `UNKNOWN`. With
   VRAM unknown, `catalog.py:301` selects the smallest tier — which means every
   non-NVIDIA tester is recommended `qwen3.5:0.8b`, a 1 GB model. Pinned by
   `tests/test_setup_consent.py:129-134`.

`_seed_advisory_records` also returns exactly **one** candidate. Onboarding
should offer a small ranked set, not a single option.

---

## 6. Crash recovery & observability

Today the supervisor thread **exits** once the backend is ready
(`main.rs:115-121`). Nothing watches the child again. Backend output is
`eprintln!`'d into a 200-line ring buffer, and `windows_subsystem = "windows"`
(`main.rs:1`) means a release build has **no console** — those lines go
nowhere. A tester reporting "it crashed" currently provides nothing actionable.

| Change | Detail |
|---|---|
| Session-long supervisor | Poll `is_running()`; on unexpected exit write the log tail and restart **once**. A second failure surfaces a UI notice rather than looping. |
| Persistent log file | `%LOCALAPPDATA%\Novi\logs\backend.log`, size-capped |
| Capture Ollama output | `ollama.py:55` sends stdout/stderr to `DEVNULL`; a failed model load is currently invisible |

---

## 7. Test strategy

Existing suites to extend: `pytest` (176 files), `vitest` (23 files), and Rust
tests in `launcher.rs` (currently 2, Windows-only).

| Area | Approach |
|---|---|
| Version skew | Extend `test_beta_hardening.py`; assert *equality*, never a literal |
| Provisioning state machine | Pure Rust unit tests over step transitions — no network |
| SHA-256 verification | Corrupt-bytes fixture must fail verification |
| Resumable download | Range-request handling against a local stub server |
| Install status endpoint | pytest, mirroring `test_setup_consent.py` |
| Onboarding UI | vitest; consent, skip, retry, resume, failure |
| Recommendation honesty | Non-NVIDIA hardware must not recommend the smallest model |

`docs/BETA_CHECKLIST.md`'s manual matrix gains three rows: clean-machine
install, first-run onboarding, and update-install.

---

## 8. Accepted risks

**SmartScreen.** Unsigned and unreputable means "Windows protected your PC".
Testers must click *More info → Run anyway*. Per Microsoft's documentation,
self-signed certificates produce the identical warning, and EV certificates no
longer confer a SmartScreen benefit. Acceptable for an invited group; hostile to
the general public. Microsoft Store submission remains the later path to a
warning-free install without a paid certificate.

**PyInstaller false positives.** The backend is built with `--onefile`
(`scripts/build_desktop_backend.py:33`), a pattern associated with antivirus
false positives, and `--onedir` would be less likely to trigger. Shipped
unsigned, some corporate Defender policies may block it. Worth noting for
enterprise testers; not blocking for the beta.

**Ollama upgrade cadence.** A pinned Ollama version updates only when Novi
updates. Acceptable for beta.

**Model catalog is hardcoded.** `SEED_MODEL_FACTS` is explicitly
non-authoritative and there is no remote registry discovery. The "Unsloth
revelation" about missing registry discovery remains an open gap, out of scope
here.

**Tauri's Rust side is young.** `launcher.rs` is 455 lines with 2 tests;
`beta-gate.yml` only runs `cargo check`, not `cargo test`. Rust tests will not
run in CI until that is added.

---

## 9. Sequencing

| Phase | Contents | Outcome |
|---|---|---|
| 1 | Version single-source; updater wiring; `release.yml` | Downloadable, auto-updating binary |
| 2 | Rust provisioning; backend endpoints; onboarding UI; honest recommendations | Usable first-run |
| 3 | Supervisor + logging; update prompt; checklist | Supportable beta |

Phase 1 alone is shippable: testers get a real installer with working automatic
updates, and onboarding arrives in the next tag. Phase 1 is cut as
`v0.3.0-beta.1`. Phases 2 and 3 add the first-run experience and supportability
on top of it.

Rust tests do not currently run in CI — `beta-gate.yml` executes only
`cargo check`. Adding `cargo test` to the gate is part of phase 1, since
phases 2 and 3 introduce the majority of the new Rust logic.