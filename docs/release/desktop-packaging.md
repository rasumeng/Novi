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

## Release process

Releases are cut by pushing a tag. Merging to `main` does not publish
anything, so a broken build never reaches a tester.

```bash
# 1. Sync the version everywhere and confirm there is no skew.
python scripts/release.py set 0.3.0-beta.1
python scripts/release.py check

# 2. Update CHANGELOG.md.

# 3. Commit, then tag and push. The tag triggers the release workflow.
git commit -am "release: 0.3.0-beta.1"
git tag v0.3.0-beta.1
git push origin main --tags

# 4. Watch the run.
gh run watch --repo rasumeng/Novi
```

The workflow (`.github/workflows/release.yml`) triggers on `v*` tag pushes, and
also on `workflow_dispatch` with an explicit `version` input. It derives the
version by trimming the leading `v` from the tag (the manual input is used
verbatim), rejects anything that does not start with `MAJOR.MINOR.PATCH`, then
runs `release.py set` followed by `release.py check`. After installing the
Python and npm dependencies it builds the desktop backend sidecar, builds the
NSIS installer with `tauri-apps/tauri-action` (`projectPath: novi/webui`,
`--bundles nsis`), signs it with the minisign key, and publishes a GitHub
Release titled "Novi <version>" containing the installer, its `.sig`, and
`latest.json`.

Two checks exist specifically to fail the job rather than ship something that
breaks later:

- **The sidecar must be present before packaging.** `bundle.resources` bundles
  whatever happens to be in `novi/webui/src-tauri/resources/`, so a skipped
  backend build produces an installer that only fails at runtime, with "This
  Novi release is missing its bundled backend". The job checks that
  `novi-backend.exe` exists and is not implausibly small, and refuses to publish
  if not.
- **The published release must contain `latest.json`.** Because the release is
  deliberately not a prerelease, the updater fetches
  `releases/latest/download/latest.json`. If that asset were missing, every
  installed copy would start failing updates with a silent 404 and no visible
  error anywhere. The job lists the release's assets after publishing and fails
  if `latest.json` is not among them.

### Local release build

To produce the same installer without publishing:

```bash
pip install -e .[desktop-build]
python scripts/build_desktop_backend.py

cd novi/webui
npm run desktop:build
```

Run the Tauri build from `novi/webui`, not from the repository root. The Tauri
CLI looks for `tauri.conf.json` by walking up from the working directory, and
`novi/webui/src-tauri` is too deep to be found from the root, so a root-level
build aborts with "Couldn't recognize the current folder as a Tauri project".

Output lands in `novi/webui/src-tauri/target/release/bundle/nsis/`. The sidecar
is copied to `novi/webui/src-tauri/resources/` and ignored by Git, because it is
a platform-specific build artifact; it must be built before the Tauri package
build.

### Versions

Six files carry the version: `pyproject.toml`,
`novi/webui/src-tauri/tauri.conf.json`, `novi/webui/src-tauri/Cargo.toml`,
`novi/webui/package.json`, `novi/webui/package-lock.json`, and
`novi/webui/src-tauri/Cargo.lock`. `scripts/release.py` is the only supported
writer; `python scripts/release.py check` runs in CI and fails on skew.

### Beta releases are not GitHub prereleases

The in-app updater reads
`https://github.com/rasumeng/Novi/releases/latest/download/latest.json`.
GitHub's `/releases/latest` **excludes prereleases**, so tagging a beta as a
prerelease would make that URL return 404 and automatic updates would stop
working with no visible error. Beta builds are published as normal releases
titled "Novi 0.3.0-beta.1", with the beta marker carried in the version string
rather than in the release's prerelease flag.

### Signing

The updater signature is a Tauri minisign key. It proves an update genuinely
came from Novi. It is **not** an Authenticode certificate and does **not**
remove the Windows SmartScreen warning. The public key is committed in
`tauri.conf.json`; the private key lives only in the GitHub Actions secrets
`TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.

The private key must never be committed, and it exists nowhere else. Keep an
encrypted backup outside the repository. If it is lost, no future release can be
signed, which means no future release can publish `latest.json`, which means
every installed Novi silently stops updating.

### What testers will see

The installer is unsigned. On first run Windows SmartScreen shows "Windows
protected your PC". Testers must choose **More info → Run anyway**. This is
expected and is not a sign of a broken build.

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
