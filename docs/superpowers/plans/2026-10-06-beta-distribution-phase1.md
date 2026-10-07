# Beta Phase 1 — Shippable Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Novi from a repository into an installable, automatically-updating Windows binary released by pushing a version tag.

**Architecture:** A single `scripts/release.py` becomes the sole writer of the version across all four manifests. The Tauri updater is wired to a GitHub Releases `latest.json` and gated by a new tag-triggered CI workflow that builds and publishes the NSIS installer. Rust tests are added to the existing CI gate so the logic introduced in later phases is covered when it lands.

**Tech Stack:** Tauri 2.11.5, Rust (edition 2021), Python 3.11 (pytest), Node 22 (vitest), GitHub Actions, NSIS, Tauri minisign updater.

**Spec:** `docs/superpowers/specs/2026-10-06-beta-distribution-design.md`

## Global Constraints

- Platform is **Windows x64 only** for this beta.
- Installer is **unsigned**. No Authenticode certificate. SmartScreen's "Windows protected your PC" warning is accepted and must be documented for testers.
- Bundle target is **`nsis` only**. `msi` is removed from `bundle.targets`.
- Beta releases are published as **normal GitHub Releases**, never prereleases, because `releases/latest` excludes prereleases and the updater endpoint would 404.
- Release version string format is `MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]`, e.g. `0.3.0-beta.1`. Tauri requires `x.y.z` as the numeric prefix.
- Ollama is **NOT bundled**. The installer stays small; Ollama is provisioned at first run in a later phase.
- GitHub remote is `https://github.com/rasumeng/Novi.git` (public). The updater endpoint is derived from this.
- The public Tauri signing key is committed in `tauri.conf.json`. The private key is **never** committed; it lives only in GitHub Actions secrets.
- Every task ends with a commit. Do not batch commits across tasks.
- Run the full `pytest -q` suite before any commit that touches Python. It currently takes ~4.5 minutes.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/release.py` | **Creates.** Sole writer of the version across 4 manifests. `set` and `check` subcommands. |
| `tests/test_release_version.py` | **Creates.** Tests version sync + the invariant that no test hardcodes a version literal. |
| `tests/test_beta_hardening.py` | **Modifies.** Remove the `== "0.2.0"` pin (line 17); delegate to `scripts/release.py check`. |
| `novi/webui/src-tauri/tauri.conf.json` | **Modifies.** NSIS-only, `createUpdaterArtifacts`, updater plugin block. |
| `novi/webui/src-tauri/Cargo.toml` | **Modifies.** Add `tauri-plugin-updater`. |
| `novi/webui/src-tauri/capabilities/default.json` | **Modifies.** Add updater + process permissions. |
| `novi/webui/package.json` | **Modifies.** Add updater + process JS packages; bump version. |
| `.github/workflows/release.yml` | **Creates.** Tag-triggered build and publish. |
| `.github/workflows/beta-gate.yml` | **Modifies.** Add `cargo test`. |
| `docs/release/desktop-packaging.md` | **Modifies.** Document the tag-driven release procedure. |

**Why `scripts/release.py` and not a CI-only step:** version skew is the bug we are fixing. `package-lock.json` already drifted to `0.1.0` while everything else said `0.2.0`. A script both writes and *verifies*, and the verification runs in CI, closes it permanently.

---

## Task 1: Version becomes single-source

**Files:**
- Create: `scripts/release.py`
- Create: `tests/test_release_version.py`
- Modify: `tests/test_beta_hardening.py:7-17`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `scripts.release.set_version(version: str) -> None` — writes the version into all 4 manifests plus both lockfiles.
  - `scripts.release.check_version() -> list[str]` — returns a list of human-readable skew descriptions; empty means consistent. Must cover every file `set_version` writes.
  - `scripts.release.read_version() -> str` — reads the version from `pyproject.toml`, the reference manifest.
  - `scripts.release.numeric_prefix() -> str` — the `x.y.z` prefix Tauri uses for bundle comparison.
  - `scripts.release.VERSION_FILES` — the single registry of version-carrying files, each entry exposing **label, path, and writer together**. Label and writer must not be separable, or adding a manifest without a writer would pass CI.
  - CLI: `python scripts/release.py set 0.3.0-beta.1`, `python scripts/release.py check`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_release_version.py`:

```python
"""The release script is the single writer of Novi's version.

These tests exist because version skew is a real, already-observed bug:
`novi/webui/package-lock.json` drifted to 0.1.0 while every other manifest
said 0.2.0, and `tests/test_beta_hardening.py` had begun pinning the literal
"0.2.0" instead of checking for skew.
"""

import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_checkout_has_no_version_skew():
    problems = release.check_version()
    assert problems == [], f"version skew detected:\n" + "\n".join(problems)


def test_version_files_cover_every_manifest_that_declares_a_version():
    labels = {label for label, _ in release.VERSION_FILES}
    # If a new manifest starts declaring a version, it must be added here or
    # the skew check silently ignores it.
    assert labels == {"pyproject.toml", "tauri.conf.json", "Cargo.toml", "package.json"}


def test_set_version_updates_every_manifest():
    # Capture the original BEFORE mutating. Restoring with read_version() after
    # the write would restore the value just written, leaving the repo at
    # 9.9.9-test.1 for every later test and the next commit.
    original = release.read_version()
    release.set_version("9.9.9-test.1")
    try:
        assert release.check_version() == []
        assert release.read_version() == "9.9.9-test.1"

        pyproject = _read(ROOT / "pyproject.toml")
        assert 'version = "9.9.9-test.1"' in pyproject

        tauri = json.loads(_read(ROOT / "novi/webui/src-tauri/tauri.conf.json"))
        assert tauri["version"] == "9.9.9-test.1"

        cargo = _read(ROOT / "novi/webui/src-tauri/Cargo.toml")
        assert 'version = "9.9.9-test.1"' in cargo

        pkg = json.loads(_read(ROOT / "novi/webui/package.json"))
        assert pkg["version"] == "9.9.9-test.1"
    finally:
        release.set_version(original)


def test_set_version_rejects_a_malformed_version():
    for bad in ["", "1.2", "v1.2.3", "1.2.3.4", "one.two.three", "1.2.3-"]:
        with pytest.raises(ValueError):
            release.set_version(bad)


def test_numeric_prefix_is_accepted_by_tauri_semver_rules():
    # Tauri requires x.y.z as the numeric prefix of the bundle version.
    original = release.read_version()
    release.set_version("0.3.0-beta.1")
    try:
        assert release.numeric_prefix() == "0.3.0"
    finally:
        release.set_version(original)


def test_no_test_file_hardcodes_a_version_literal():
    """A test asserting an exact version will fail on the next release.

    That failure is the intended behaviour for a *release gate*, but as a
    permanent unit test it is just breakage waiting to happen. Skew is
    asserted by `test_current_checkout_has_no_version_skew` instead.
    """
    offenders = []
    pattern = re.compile(r'==\s*"\d+\.\d+\.\d+')
    for path in (ROOT / "tests").rglob("test_*.py"):
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == [], (
        "these tests pin a literal version and will break on the next release:\n"
        + "\n".join(offenders)
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_release_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'release'`

- [ ] **Step 3: Write `scripts/release.py`**

Create `scripts/release.py`:

```python
#!/usr/bin/env python3
"""Single-source the Novi version across every manifest that declares one.

Four files carry the version: ``pyproject.toml``, ``tauri.conf.json``,
``src-tauri/Cargo.toml``, and ``package.json``. They have already drifted once
(``package-lock.json`` sat at 0.1.0 while the rest read 0.2.0), so this script
owns writing them and ``check`` is run in CI to catch anything that bypasses it.

Usage:
    python scripts/release.py set 0.3.0-beta.1
    python scripts/release.py check
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Callable

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Each writer takes the new version and rewrites exactly one manifest.


def _write_pyproject(version: str) -> None:
    path = ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^version = "[^"]*"$',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(f"could not find a version line to update in {path}")
    path.write_text(updated, encoding="utf-8")


def _write_tauri_conf(version: str) -> None:
    path = ROOT / "novi/webui/src-tauri/tauri.conf.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_cargo(version: str) -> None:
    path = ROOT / "novi/webui/src-tauri/Cargo.toml"
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^version = "[^"]*"$',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(f"could not find a package version line in {path}")
    path.write_text(updated, encoding="utf-8")


def _write_package_json(version: str) -> None:
    path = ROOT / "novi/webui/package.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# (label, path relative to ROOT, writer)
VERSION_FILES: list[tuple[str, pathlib.Path, Callable[[str], None]]] = [
    ("pyproject.toml", ROOT / "pyproject.toml", _write_pyproject),
    ("tauri.conf.json", ROOT / "novi/webui/src-tauri/tauri.conf.json", _write_tauri_conf),
    ("Cargo.toml", ROOT / "novi/webui/src-tauri/Cargo.toml", _write_cargo),
    ("package.json", ROOT / "novi/webui/package.json", _write_package_json),
]

VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def validate_version(version: str) -> None:
    if not VERSION_PATTERN.match(version):
        raise ValueError(
            f"invalid version {version!r}; expected MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD], "
            "for example 0.3.0 or 0.3.0-beta.1"
        )


def read_version() -> str:
    """Read the version from pyproject.toml, the reference manifest."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("could not read version from pyproject.toml")
    return match.group(1)


def numeric_prefix(version: str) -> str:
    """Return the x.y.z prefix Tauri uses for bundle version comparison."""
    match = re.match(r"^(\d+\.\d+\.\d+)", version)
    if not match:
        raise ValueError(f"version {version!r} has no numeric x.y.z prefix")
    return match.group(1)


def set_version(version: str) -> None:
    validate_version(version)
    for _label, _path, writer in VERSION_FILES:
        writer(version)
    # Cargo.lock tracks the crate version and must agree with Cargo.toml.
    _sync_cargo_lock(version)


def _sync_cargo_lock(version: str) -> None:
    path = ROOT / "novi/webui/src-tauri/Cargo.lock"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(\nname = "novi-desktop"\nversion = ")[^"]+(")',
        rf'\g<1>{version}\g<2>',
        text,
    )
    if count:
        path.write_text(updated, encoding="utf-8")


def check_version() -> list[str]:
    """Return a list of skew descriptions. Empty list means consistent."""
    validate_version(read_version())
    reference = read_version()
    problems: list[str] = []

    tauri = json.loads((ROOT / "novi/webui/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    if tauri["version"] != reference:
        problems.append(f"tauri.conf.json: {tauri['version']} != {reference}")

    cargo_text = (ROOT / "novi/webui/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    cargo_match = re.search(r'^version = "([^"]+)"$', cargo_text, flags=re.MULTILINE)
    if not cargo_match:
        problems.append("Cargo.toml: no package version line found")
    elif cargo_match.group(1) != reference:
        problems.append(f"Cargo.toml: {cargo_match.group(1)} != {reference}")

    pkg = json.loads((ROOT / "novi/webui/package.json").read_text(encoding="utf-8"))
    if pkg["version"] != reference:
        problems.append(f"package.json: {pkg['version']} != {reference}")

    lock_path = ROOT / "novi/webui/package-lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for location, value in (("root", lock.get("version")), ("packages['']", lock.get("packages", {}).get("", {}).get("version"))):
            if value is not None and value != reference:
                problems.append(f"package-lock.json {location}: {value} != {reference} (run `npm install` in novi/webui)")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    setter = sub.add_parser("set", help="write the version into every manifest")
    setter.add_argument("version")

    sub.add_parser("check", help="fail if any manifest disagrees")

    args = parser.parse_args(argv)

    if args.command == "set":
        try:
            set_version(args.version)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(f"version set to {args.version} across {len(VERSION_FILES)} manifests")
        return 0

    problems = check_version()
    if problems:
        print("version skew detected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"version consistent: {read_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the release script by hand**

Run: `python scripts/release.py check`
Expected: exit 0, prints `version consistent: 0.2.0`

If it reports `package-lock.json` skew, run `npm install --prefix novi/webui` to regenerate the lockfile, then re-run the check.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_release_version.py -v`
Expected: PASS for 6 tests. `test_no_test_file_hardcodes_a_version_literal` will FAIL until Task 1 Step 6 removes the pin in `test_beta_hardening.py`. That is the expected ordering.

- [ ] **Step 6: Remove the hardcoded version pin**

In `tests/test_beta_hardening.py`, replace lines 7-17:

```python
def test_version_consistency():
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    py_ver = tomllib.load(open("pyproject.toml", "rb"))["project"]["version"]
    tauri_ver = json.loads(pathlib.Path("novi/webui/src-tauri/tauri.conf.json").read_text())["version"]
    pkg_ver = json.loads(pathlib.Path("novi/webui/package.json").read_text())["version"]
    assert py_ver == tauri_ver == pkg_ver, f"version skew: py={py_ver} tauri={tauri_ver} pkg={pkg_ver}"
    assert py_ver == "0.2.0"
```

with:

```python
def test_version_consistency():
    """Version skew, not a pinned literal.

    This used to assert `py_ver == "0.2.0"`, which guaranteed the suite would
    break on the first release and taught nothing about the other manifests.
    `scripts/release.py check` is the single-source verifier and also covers
    Cargo.toml and package-lock.json, which this test never looked at.
    """
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import release

    problems = release.check_version()
    assert problems == [], "version skew: " + "; ".join(problems)
```

Remove the now-unused `import json` at the top only if `json` is not used elsewhere in the file. It **is** used at line 33 and 74, so leave the import.

- [ ] **Step 7: Run the full Python suite**

Run: `python -m pytest -q`
Expected: all pass (previously 2239 passed, 3 skipped; now 5 more tests from this task).

- [ ] **Step 8: Commit**

```bash
git add scripts/release.py tests/test_release_version.py tests/test_beta_hardening.py
git commit -m "build: make the release script the single source of version

Four manifests declared the version and one had already drifted, so
package-lock.json sat at 0.1.0 while the rest read 0.2.0.

test_beta_hardening pinned the literal \"0.2.0\", which guaranteed the
suite would fail on the first release while checking nothing about
Cargo.toml or the lockfile. It now asserts skew instead, and a new test
fails the build if any test file reintroduces a version literal."
```

---

## Task 2: Generate and commit the updater signing keypair

**Files:**
- Modify: `novi/webui/src-tauri/tauri.conf.json` (pubkey added in Task 3)
- No files committed from this task.

**Interfaces:**
- Consumes: nothing.
- Produces: a minisign **public** key string destined for `plugins.updater.pubkey`, and a **private** key stored only in GitHub Actions secrets.

- [ ] **Step 1: Generate the keypair locally**

Run: `npm --prefix novi/webui run tauri signer generate -- -w C:\Users\asume\.tauri\novi-updater.key`

If that path syntax fails on Windows, run `npx tauri signer generate -w C:/Users/asume/.tauri/novi-updater.key` from `novi/webui`.

Expected output includes:
- `Public key: <BASE64>` — record this, it goes in `tauri.conf.json`.
- `Private key: <BASE64>` — record this, it goes in GitHub secrets.
- A password you choose — record this, it goes in GitHub secrets.

Write all three to a local scratch file outside the repo, e.g. `C:\Users\asume\AppData\Local\Temp\opencode\novi-signer.txt`. **Never** write them into the repository.

- [ ] **Step 2: Add the secrets to the GitHub repository**

Run each, substituting the recorded values:

```bash
gh secret set TAURI_SIGNING_PRIVATE_KEY --repo rasumeng/Novi
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --repo rasumeng/Novi
```

`gh secret set` reads the value from stdin, so pipe the file contents:

```bash
Get-Content C:\Users\asume\AppData\Local\Temp\opencode\novi-signer.txt | gh secret set TAURI_SIGNING_PRIVATE_KEY --repo rasumeng/Novi
```

Verify without printing values:

```bash
gh secret list --repo rasumeng/Novi
```

Expected: both `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` are listed with an updated timestamp.

- [ ] **Step 3: Confirm the private key is not in the repository**

Run: `git status --short; git log --all --oneline -S "PRIVATE KEY" `

Expected: no new files staged, and the search returns nothing.

Do not commit anything in this task. The public key is committed in Task 3.

---

## Task 3: Wire the Tauri updater

**Files:**
- Modify: `novi/webui/src-tauri/Cargo.toml`
- Modify: `novi/webui/src-tauri/tauri.conf.json`
- Modify: `novi/webui/src-tauri/capabilities/default.json`
- Modify: `novi/webui/package.json`

**Interfaces:**
- Consumes: the public key from Task 2.
- Produces: `plugins.updater.pubkey` and `plugins.updater.endpoints` in `tauri.conf.json`; npm packages `@tauri-apps/plugin-updater` and `@tauri-apps/plugin-process` available to the webview; permissions `updater:default` and `process:allow-restart` granted to the `main` window.

- [ ] **Step 1: Add the Rust plugin dependency**

In `novi/webui/src-tauri/Cargo.toml`, add to `[dependencies]`:

```toml
tauri-plugin-updater = "2"
```

Resulting dependencies block:

```toml
[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
url = "2"
tauri-plugin-single-instance = "2"
tauri-plugin-global-shortcut = "2"
tauri-plugin-notification = "2"
tauri-plugin-os = "2.3.2"
tauri-plugin-updater = "2"
```

- [ ] **Step 2: Add the JS packages**

Run:

```bash
npm --prefix novi/webui install @tauri-apps/plugin-updater@^2 @tauri-apps/plugin-process@^2
```

Expected: both added to `dependencies` in `novi/webui/package.json`.

- [ ] **Step 3: Register the plugin in Rust**

In `novi/webui/src-tauri/src/main.rs`, inside the `tauri::Builder` chain, add `.plugin(tauri_plugin_updater::Builder::new().build())` directly after the existing `.plugin(tauri_plugin_os::init())` line (line 69):

```rust
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
```

- [ ] **Step 4: Update the Tauri config**

Replace the `bundle` block in `novi/webui/src-tauri/tauri.conf.json` (lines 20-31) and append a `plugins` block:

```json
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "createUpdaterArtifacts": true,
    "resources": ["resources"],
    "windows": {
      "nsis": {
        "installMode": "currentUser"
      }
    },
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.ico",
      "icons/icon.png"
    ]
  },
  "plugins": {
    "updater": {
      "pubkey": "PASTE_PUBLIC_KEY_FROM_TASK_2_HERE",
      "endpoints": [
        "https://github.com/rasumeng/Novi/releases/latest/download/latest.json"
      ],
      "windows": {
        "installMode": "passive"
      }
    }
  }
}
```

Three deliberate choices here:
- `targets: ["nsis"]` drops `msi`, so there is one installer and one update path.
- `createUpdaterArtifacts: true` makes the bundler emit `setup.exe.sig`. Without it the updater has nothing to verify and updates silently fail.
- The endpoint is the plain `releases/latest` URL. This is why beta releases must not be marked as prereleases.
- `installMode: "passive"` shows the user a progress dialog during the update install rather than installing invisibly.
- `installMode: "currentUser"` keeps the NSIS install per-user, so no administrator rights are needed.

- [ ] **Step 5: Add the capability permissions**

Replace `novi/webui/src-tauri/capabilities/default.json` with:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "main-capability",
  "description": "Capability for the main window",
  "windows": ["main"],
  "permissions": [
    "core:window:default",
    "core:window:allow-close",
    "core:window:allow-minimize",
    "core:window:allow-toggle-maximize",
    "core:window:allow-start-dragging",
    "updater:default",
    "process:allow-restart"
  ]
}
```

Without `updater:default` the webview cannot call the updater at all, and `process:allow-restart` is required to relaunch after an update installs. This is the single most commonly missed step in Tauri updater setup.

- [ ] **Step 6: Verify the Rust crate compiles**

Run: `cargo check --manifest-path novi/webui/src-tauri/Cargo.toml`
Expected: succeeds, finishing with `Finished dev [unoptimized + debuginfo] target(s)`.

A failure mentioning a missing `plugins.updater` schema means `tauri-build` has not regenerated; re-run after `cargo clean -p novi-desktop`.

- [ ] **Step 7: Verify the frontend still typechecks and builds**

Run: `npm --prefix novi/webui run build`
Expected: succeeds. `tsc` will not complain about the new packages until code imports them, which happens in a later phase — that is expected and fine.

- [ ] **Step 8: Commit**

```bash
git add novi/webui/src-tauri/Cargo.toml novi/webui/src-tauri/tauri.conf.json novi/webui/src-tauri/capabilities/default.json novi/webui/src-tauri/src/main.rs novi/webui/package.json novi/webui/package-lock.json
git commit -m "feat(desktop): wire the Tauri updater and reduce the bundle to NSIS

Drops the msi target so there is one installer and one update path, and
enables createUpdaterArtifacts so the bundler emits setup.exe.sig — without
it the updater has nothing to verify.

The endpoint is the plain releases/latest URL, which is why beta releases
must never be marked as GitHub prereleases: that endpoint excludes them
and updates would 404 silently.

adds updater:default and process:allow-restart to the main capability;
without them the webview cannot call the updater at all."
```

---

## Task 4: Add `cargo test` to the CI gate

**Files:**
- Modify: `.github/workflows/beta-gate.yml:39-40`

**Interfaces:**
- Consumes: nothing.
- Produces: the beta gate runs Rust unit tests, so phases 2-3's new logic is covered by CI when it lands.

- [ ] **Step 1: Write the failing verification**

The gate currently runs only `cargo check`. Confirm no Rust tests execute in CI:

Run: `gh workflow view "Beta gate" --repo rasumeng/Novi --yaml | Select-String "cargo"`
Expected: only `cargo check` appears.

- [ ] **Step 2: Add the test step**

In `.github/workflows/beta-gate.yml`, replace lines 39-40:

```yaml
      - name: Check desktop shell
        run: cargo check --manifest-path novi/webui/src-tauri/Cargo.toml
```

with:

```yaml
      - name: Check desktop shell
        run: cargo check --manifest-path novi/webui/src-tauri/Cargo.toml
      - name: Test desktop shell
        # Phases 2 and 3 of the beta distribution plan put most of their new
        # logic in Rust. Without this step that logic ships untested, which
        # contradicts the position taken in docs/RISK_AUDIT.md that the test
        # suite is the part of this codebase that can be trusted.
        run: cargo test --manifest-path novi/webui/src-tauri/Cargo.toml
```

- [ ] **Step 3: Verify the Rust tests pass locally**

Run: `cargo test --manifest-path novi/webui/src-tauri/Cargo.toml`
Expected: 2 tests pass. Both are `#[cfg(all(test, target_os = "windows"))]` in `launcher.rs` (lines 418-455) and test `is_novi_backend_process`.

If they report 0 tests, the platform gate did not match; check that you are on Windows.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/beta-gate.yml
git commit -m "ci: run cargo test in the beta gate

The gate only ran cargo check, so the two existing Rust tests never
executed. Phases 2 and 3 of the beta distribution work put roughly a
thousand lines of new logic in Rust, and docs/RISK_AUDIT.md is explicit
that the tests are the trustworthy part of this codebase."
```

---

## Task 5: The tag-triggered release workflow

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: the secrets from Task 2; the Tauri config from Task 3; `scripts/release.py` from Task 1.
- Produces: a GitHub Release containing `Novi_0.3.0-beta.1_x64-setup.exe`, its `.sig`, and `latest.json`. That `latest.json` is the artifact the in-app updater fetches.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

# Releases are cut by pushing a tag, never by merging. Every merge to main
# publishing an installer would ship to testers, including the merges made
# while trying to fix something.
on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      version:
        description: "Version to release, e.g. 0.3.0-beta.1"
        required: true

permissions:
  contents: write

jobs:
  release:
    name: Build and publish installer
    runs-on: windows-latest
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: novi/webui/package-lock.json

      - uses: dtolnay/rust-toolchain@stable

      - name: Derive version
        id: version
        shell: pwsh
        run: |
          if ("${{ github.event.inputs.version }}" -ne "") {
            $version = "${{ github.event.inputs.version }}"
          } else {
            $version = "${{ github.ref_name }}".TrimStart('v')
          }
          if ($version -notmatch '^\d+\.\d+\.\d+') {
            throw "Version '$version' must start with MAJOR.MINOR.PATCH, e.g. 0.3.0-beta.1"
          }
          "version=$version" >> $env:GITHUB_OUTPUT
          Write-Host "Releasing version $version"

      - name: Sync version across manifests
        run: python scripts/release.py set "${{ steps.version.outputs.version }}"

      - name: Verify no version skew
        run: python scripts/release.py check

      - name: Install Python dependencies
        run: python -m pip install --upgrade pip; python -m pip install -e .[desktop-build]

      - name: Install frontend dependencies
        run: npm --prefix novi/webui ci

      - name: Build the desktop backend sidecar
        # Must run before tauri build: bundle.resources picks up whatever is in
        # src-tauri/resources, and a build without this produces an installer
        # that dies at runtime with "This Novi release is missing its bundled
        # backend".
        run: python scripts/build_desktop_backend.py

      - name: Confirm the sidecar is present
        shell: pwsh
        run: |
          $sidecar = "novi/webui/src-tauri/resources/novi-backend.exe"
          if (-not (Test-Path $sidecar)) {
            throw "Backend sidecar was not built - refusing to publish a broken installer"
          }
          $size = (Get-Item $sidecar).Length
          Write-Host "sidecar present: $([math]::Round($size / 1MB, 1)) MB"
          if ($size -lt 1MB) {
            throw "Backend sidecar is implausibly small ($size bytes) - PyInstaller likely failed"
          }

      - name: Build and publish the installer
        uses: tauri-apps/tauri-action@v0
        env:
          # The minisign private key proves an update genuinely came from Novi.
          # It is NOT an Authenticode certificate and does not remove the
          # SmartScreen warning - those are separate trust systems.
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
        with:
          tagName: v${{ steps.version.outputs.version }}
          releaseName: "Novi ${{ steps.version.outputs.version }}"
          releaseBody: |
            Windows x64 beta installer.

            Novi is unsigned. Windows SmartScreen will show "Windows protected
            your PC" on first run. Choose **More info → Run anyway**.

            Ollama is not bundled. On first launch Novi will ask before
            downloading it (~1.5 GB).
          releaseDraft: false
          # Deliberately NOT a GitHub prerelease. The updater endpoint
          # (releases/latest/download/latest.json) excludes prereleases, so
          # marking this as a prerelease would break automatic updates.
          prerelease: false
          args: --bundles nsis
```

- [ ] **Step 2: Validate the workflow syntax**

Run:

```bash
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('YAML OK')"
```

Expected: `YAML OK`

If PyYAML is unavailable, use `node -e "require('yaml')..."` or simply push and read the Actions log.

- [ ] **Step 3: Confirm the workflow is recognised**

Run: `git add .github/workflows/release.yml; git status --short`
Expected: `.github/workflows/release.yml` staged as new.

- [ ] **Step 4: Commit**

```bash
git commit -m "ci: publish the Windows installer from a pushed tag

Builds the PyInstaller sidecar, verifies it exists and is plausibly sized,
then tauri-action builds and publishes a signed NSIS installer with
latest.json.

Refuses to publish if the sidecar is missing, because bundle.resources
silently bundles whatever is present and a build without desktop:backend
produces an installer that only fails at runtime.

Published as a normal release rather than a prerelease: the updater's
releases/latest endpoint excludes prereleases, so a prerelease would make
automatic updates 404 with no visible error."
```

---

## Task 6: Document the release procedure

**Files:**
- Modify: `docs/release/desktop-packaging.md`

**Interfaces:**
- Consumes: everything above.
- Produces: a procedure a human can follow at 2am.

- [ ] **Step 1: Replace the build process section**

In `docs/release/desktop-packaging.md`, replace the `## Build process` section (lines 17-26) with:

```markdown
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

The workflow (`.github/workflows/release.yml`) then builds the sidecar,
verifies it, builds the NSIS installer, signs it, and publishes a GitHub
Release containing the installer, its signature, and `latest.json`.

### Local release build

To produce the same installer without publishing:

```bash
pip install -e .[desktop-build]
python scripts/build_desktop_backend.py
npm --prefix novi/webui run desktop:build
```

Output lands in `novi/webui/src-tauri/target/release/bundle/nsis/`.

### Versions

`pyproject.toml`, `tauri.conf.json`, `src-tauri/Cargo.toml`, and
`package.json` all declare the version. `scripts/release.py` is the only
supported writer; `python scripts/release.py check` runs in CI and fails on
skew.

### Beta releases are not GitHub prereleases

The in-app updater reads
`https://github.com/rasumeng/Novi/releases/latest/download/latest.json`.
GitHub's `/releases/latest` **excludes prereleases**, so tagging a beta as a
prerelease would make that URL return 404 and automatic updates would stop
working with no visible error. Beta builds are published as normal releases
titled "Beta", with the beta marker carried in the version string
(`0.3.0-beta.1`).

### Signing

The updater signature is a Tauri minisign key. It proves an update genuinely
came from Novi. It is **not** an Authenticode certificate and does **not**
remove the Windows SmartScreen warning. The public key is committed in
`tauri.conf.json`; the private key lives only in the GitHub Actions secrets
`TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.

### What testers will see

The installer is unsigned. On first run Windows SmartScreen shows "Windows
protected your PC". Testers must choose **More info → Run anyway**. This is
expected and is not a sign of a broken build.
```

- [ ] **Step 2: Update the CHANGELOG**

In `CHANGELOG.md`, the current head is `## v0.3.0 (unreleased)`. Leave it as-is; Task 7 will confirm it is consistent with the version being shipped.

- [ ] **Step 3: Commit**

```bash
git add docs/release/desktop-packaging.md
git commit -m "docs: document the tag-driven release procedure

Records why beta releases are not GitHub prereleases, what the Tauri
signature does and does not prove, and that testers should expect a
SmartScreen warning."
```

---

## Task 7: Cut v0.3.0-beta.1 and verify on your own machine

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a published release and a verified local install.

This is the acceptance gate for the whole phase. Nothing after it should be
attempted until these checks pass.

- [ ] **Step 1: Verify the gate is green before tagging**

Run, all four:

```bash
python scripts/release.py check
python -m pytest -q
npm --prefix novi/webui run build
cargo test --manifest-path novi/webui/src-tauri/Cargo.toml
```

Expected: all pass. Any failure here means do not tag.

- [ ] **Step 2: Set the version and update the changelog**

```bash
python scripts/release.py set 0.3.0-beta.1
python scripts/release.py check
```

Then edit `CHANGELOG.md`, replacing `## v0.3.0 (unreleased)` with:

```markdown
## v0.3.0-beta.1

First downloadable beta.

- Windows x64 NSIS installer, published from a pushed tag.
- In-app automatic updates, signed with a Tauri updater key.
- Ollama is not bundled; it is provisioned on first run in a future release.
  This build still expects Ollama to already be installed.
- Known issue: the backend still pulls the embedding model silently at first
  launch, and no chat model is provisioned automatically.
- Known issue: unsigned. Windows SmartScreen will warn on first run.

> The installer is unsigned. Testers must choose
> **More info -> Run anyway**.
```

- [ ] **Step 3: Commit and tag**

```bash
git add -A
git commit -m "release: 0.3.0-beta.1"
git tag v0.3.0-beta.1
git push origin main --tags
```

- [ ] **Step 4: Watch the release workflow**

Run: `gh run watch --repo rasumeng/Novi`

Expected: the `Release / Build and publish installer` job succeeds.

If it fails at "Confirm the sidecar is present", PyInstaller did not produce the binary. Inspect the log and run `python scripts/build_desktop_backend.py` locally to see the error.

- [ ] **Step 5: Verify the published artifacts**

Run:

```bash
gh release view v0.3.0-beta.1 --repo rasumeng/Novi --json assets --jq '.assets[].name'
```

Expected names, in addition to source archives:

```
Novi_0.3.0-beta.1_x64-setup.exe
Novi_0.3.0-beta.1_x64-setup.exe.sig
latest.json
```

If `latest.json` is missing, `createUpdaterArtifacts` did not take effect — re-check `tauri.conf.json`.

- [ ] **Step 6: Verify the updater endpoint is live**

Run:

```bash
curl -sL https://github.com/rasumeng/Novi/releases/latest/download/latest.json
```

Expected: JSON containing `"version": "0.3.0-beta.1"` and a `signature` field. This is the exact URL the in-app updater fetches; if it 404s, no client will ever receive an update.

- [ ] **Step 7: Install and smoke-test locally**

Download and run `Novi_0.3.0-beta.1_x64-setup.exe` on this machine.

Confirm each:
1. SmartScreen warns, and **More info → Run anyway** installs it.
2. No administrator prompt appears (`installMode: currentUser`).
3. Novi launches and reaches the chat UI.
4. The app is installed under `%LOCALAPPDATA%`, not `Program Files`.
5. Uninstalling from Windows Settings removes the app.

Step 3 will fail today if Ollama is not already installed — that is the known, documented Phase 2 gap, not a packaging defect.

**Step 7a: verify the upgraded Tauri runtime (REQUIRED before tagging).**

Task 3 pulled `tauri` 2.11.5 → 2.12.1 and `wry` 0.55.1 → 0.57.0, transitively forced by
`tauri-plugin-process` 2.4.0 requiring `tauri ^2.12`. The repo has exactly two Rust tests,
both process-name matching, so **nothing in CI or the test suite exercises the window,
tray, global shortcut, single-instance handling, or the WebView2 load of
`http://127.0.0.1:8765`** under the new versions. `cargo check` only proves it compiles.

Do not push the tag until the built shell has been launched once and all of these are
confirmed on a real desktop session:

1. The window appears at 1280×860 with the dark background (no white WebView2 flash).
2. The window loads `http://127.0.0.1:8765` — not a blank frame, not an error page.
3. The custom titlebar renders, and minimize/close/toggle-maximize all work.
4. `CmdOrCtrl+Shift+Space` toggles window visibility.
5. The tray icon appears and its menu works.
6. Launching Novi a second time focuses the existing window instead of spawning a second
   backend on the port.

A quick way to sanity-check without an installer, from `novi/webui`:

```bash
cargo tauri dev
```

If any of the six fails, the remedy is pinning `tauri-plugin-process` to `=2.3.1` plus
`cargo update -p tauri --precise 2.11.5`, which keeps `wry` off 0.57.

- [ ] **Step 8: Verify the updater cannot be bypassed**

Confirm the app refuses a tampered update. Build a local test by editing a
copy of `latest.json` to point at a different `url`, serve it, and confirm the
updater rejects it with a signature error. If this is not feasible now, record
it as an open verification item in `docs/BETA_CHECKLIST.md` rather than
skipping it silently.

---

## Out of Scope for This Plan

Deferred to Phase 2 and Phase 3 plans, per the spec:

| Deferred | Spec section |
|---|---|
| Rust provisioning module (Ollama download, SHA-256 verify, extract, launch on :11435) | §3.3, §5.1 |
| Native fallback window when the backend never starts | §3.7 |
| Backend accepting the Ollama URL from Rust | §3.6 |
| `GET /api/models/install/status` | §3.4, §5.2 |
| Removing the silent `ensure_embedding_model` calls | §3.5 |
| Emitting `boot_error` | §5.2 |
| Onboarding UI and `useOnboarding` hook | §5.3 |
| Real download sizes for uninstalled models (`"size": None`) | §5.4 |
| AMD/Intel GPU detection | §5.4 |
| Session-long supervisor and persistent log file | §6 |
| Capturing Ollama's discarded stdout/stderr | §6 |
| Update-check hook and prompt banner UI | §4.2 |
| `BETA_CHECKLIST.md` manual matrix additions | §7 |