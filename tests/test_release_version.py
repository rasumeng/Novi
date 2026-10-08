"""The release script is the single writer of Novi's version.

These tests exist because version skew is a real, already-observed bug:
`novi/webui/package-lock.json` drifted to 0.1.0 while every other manifest
said 0.2.0, and `tests/test_beta_hardening.py` had begun pinning the literal
"0.2.0" instead of checking for skew.
"""

import ast
import json
import pathlib
import re
import sys

import pytest

try:
    import tomllib
except ImportError:  # Python 3.10 has tomllib only from 3.11
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402

# This file legitimately asserts sentinels (9.9.9-test.1, 0.3.0-beta.1) inside
# asserts, so the guard below cannot scan itself.
GUARD_PATH = pathlib.Path(__file__).resolve()

# Any string that looks like a release version, however it is spelled.
SEMVER_SHAPED = release.VERSION_PATTERN


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_checkout_has_no_version_skew():
    problems = release.check_version()
    assert problems == [], f"version skew detected:\n" + "\n".join(problems)


def test_version_files_cover_every_manifest_that_declares_a_version():
    """Every registry entry is fully wired, and nothing is left out.

    Two separate properties, because the registry is the only coupling between
    `set` and `check`:

    - every declared entry has a callable reader and writer, so a manifest
      cannot be labelled without something that actually writes it;
    - the labels are exactly the set of manifests that declare a version, so a
      new version-carrying file cannot be added to the repo and silently ignored.
    """
    for entry in release.VERSION_FILES:
        assert callable(entry.write), f"{entry.label} has no callable writer"
        assert callable(entry.read), f"{entry.label} has no callable reader"
        assert entry.path.is_file(), f"{entry.label} does not exist at {entry.path}"

    labels = {entry.label for entry in release.VERSION_FILES}
    # If a new manifest starts declaring a version, it must be added here or
    # the skew check silently ignores it.
    assert labels == {
        "pyproject.toml",
        "tauri.conf.json",
        "Cargo.toml",
        "package.json",
        "package-lock.json",
        "Cargo.lock",
    }


def test_registry_paths_match_their_labels():
    """A label that no longer matches its path is how a writer goes stale."""
    expected = {
        "pyproject.toml": ROOT / "pyproject.toml",
        "tauri.conf.json": ROOT / "novi/webui/src-tauri/tauri.conf.json",
        "Cargo.toml": ROOT / "novi/webui/src-tauri/Cargo.toml",
        "package.json": ROOT / "novi/webui/package.json",
        "package-lock.json": ROOT / "novi/webui/package-lock.json",
        "Cargo.lock": ROOT / "novi/webui/src-tauri/Cargo.lock",
    }
    actual = {entry.label: entry.path for entry in release.VERSION_FILES}
    assert actual == expected


def test_every_version_carrying_file_in_the_repo_is_registered():
    """Discovery is independent of the registry, so the registry cannot lie.

    Scans the whole repo for files that declare a version the way each format
    declares one, and requires every hit to be registered. A new manifest that
    declares a version therefore fails here instead of being silently skipped by
    `check`.
    """
    # Manifest-shaped filenames, in the directories that hold manifests. Scoped
    # deliberately: a whole-tree scan also finds third-party JSON in `venv/`,
    # which is not Novi's version to manage.
    scan_patterns = {
        ".toml": re.compile(r'^\s*version\s*=\s*"[^"]+"', flags=re.MULTILINE),
        ".json": re.compile(r'"version"\s*:\s*"[^"]+"'),
        ".lock": re.compile(r'^\s*version\s*=\s*"[^"]+"', flags=re.MULTILINE),
    }
    roots = [ROOT, ROOT / "novi/webui", ROOT / "novi/webui/src-tauri"]
    manifest_names = {
        "pyproject.toml",
        "Cargo.toml",
        "tauri.conf.json",
        "package.json",
        "package-lock.json",
        "Cargo.lock",
    }

    registered = {entry.path.resolve() for entry in release.VERSION_FILES}
    unregistered = []
    for base in roots:
        for path in base.iterdir():
            if not path.is_file() or path.name not in manifest_names:
                continue
            if path.resolve() in registered:
                continue
            if scan_patterns[path.suffix].search(path.read_text(encoding="utf-8")):
                unregistered.append(str(path.relative_to(ROOT)))

    assert unregistered == [], (
        "these files declare a version but are not in release.VERSION_FILES, so "
        "release.py would neither write nor check them:\n" + "\n".join(sorted(unregistered))
    )


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

        lock_path = ROOT / "novi/webui/package-lock.json"
        if lock_path.exists():
            lock = json.loads(_read(lock_path))
            assert lock["version"] == "9.9.9-test.1"
            assert lock["packages"][""]["version"] == "9.9.9-test.1"

        # Cargo.lock is written too; assert it actually happened rather than
        # trusting the registry entry is wired.
        cargo_lock_path = ROOT / "novi/webui/src-tauri/Cargo.lock"
        if cargo_lock_path.exists():
            lock = tomllib.loads(_read(cargo_lock_path))
            entries = [p for p in lock.get("package", []) if p.get("name") == "novi-desktop"]
            assert entries, "Cargo.lock has no novi-desktop package entry"
            assert all(p["version"] == "9.9.9-test.1" for p in entries), (
                f"Cargo.lock novi-desktop version not synced: "
                f"{[p['version'] for p in entries]}"
            )
    finally:
        release.set_version(original)


def test_set_version_rejects_a_malformed_version():
    for bad in ["", "1.2", "v1.2.3", "1.2.3.4", "one.two.three", "1.2.3-"]:
        with pytest.raises(ValueError):
            release.set_version(bad)


def test_validate_version_rejects_a_trailing_newline():
    """A trailing newline must not pass validation.

    In Python `$` matches immediately before a final `\n`, so a `$`-anchored
    pattern accepts "0.3.0\\n". That value is then written verbatim into the
    `version = "..."` basic string in pyproject.toml, where a raw newline is a
    syntax error -- the release fails with an unparseable manifest rather than a
    clear version error. Hence `\\Z`.
    """
    assert not SEMVER_SHAPED.match("0.3.0\n"), "`$` would accept a trailing newline"
    assert not SEMVER_SHAPED.match("0.3.0-beta.1\n")
    with pytest.raises(ValueError):
        release.set_version("0.3.0\n")


def test_package_lock_writer_targets_root_and_packages_root_only():
    """The lockfile writer addresses its targets by name, not by file position.

    package-lock.json carries the version twice: the root entry and the root
    package mirror under `packages[""]`. Every other `version` key in the file
    belongs to a dependency and must be left alone. A writer that patched "the
    first two `version` keys in file order" would rewrite a dependency's pin
    the moment npm reordered the root keys, and a verification reading the same
    two positions would pass on the damage.
    """
    lock_path = ROOT / "novi/webui/package-lock.json"
    if not lock_path.exists():
        pytest.skip("no package-lock.json in this checkout")

    def _dependency_versions() -> dict[str, str]:
        lock = json.loads(_read(lock_path))
        return {
            name: entry["version"]
            for name, entry in lock["packages"].items()
            if name and "version" in entry
        }

    original = release.read_version()
    before = _dependency_versions()
    assert before, "package-lock.json has no dependency entries to protect"

    release.set_version("9.9.9-lock.1")
    try:
        lock = json.loads(_read(lock_path))
        assert lock["version"] == "9.9.9-lock.1", "root version was not written"
        assert lock["packages"][""]["version"] == "9.9.9-lock.1", 'packages[""] was not written'
        # The proof that addressing is by name: nothing else moved.
        assert _dependency_versions() == before, (
            "the lockfile writer touched a dependency version: "
            f"{sorted(set(_dependency_versions().items()) ^ set(before.items()))}"
        )
    finally:
        release.set_version(original)

    restored = json.loads(_read(lock_path))
    assert restored["version"] == original
    assert restored["packages"][""]["version"] == original
    assert _dependency_versions() == before


# A lockfile whose keys are ordered the way npm is free to order them, and in
# which a dependency's `version` key comes *before* both of the writer's targets.
# Today's package-lock.json happens to put the root's two versions first, which is
# exactly why positional addressing looked correct: the test below is what gives
# the by-name targeting teeth.
_REORDERED_LOCK = """{
  "name": "novi-webui",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "node_modules/react": {
      "version": "18.3.1",
      "resolved": "https://registry.npmjs.org/react/-/react-18.3.1.tgz"
    },
    "": {
      "name": "novi-webui",
      "version": "0.2.0",
      "dependencies": {
        "react": "^18.3.1"
      }
    },
    "node_modules/clsx": {
      "version": "2.1.1",
      "resolved": "https://registry.npmjs.org/clsx/-/clsx-2.1.1.tgz"
    }
  },
  "version": "0.2.0"
}
"""


def test_lockfile_writer_survives_key_reordering(tmp_path):
    """Targets are found by name, so npm's key order cannot redirect the writer.

    Positional addressing means "the first N `version` keys in the file". On this
    layout the first such key belongs to `node_modules/react`, so a positional
    writer would stamp the release version onto a dependency's pin, leave the
    root at the old version, and -- because it verified the same two positions it
    wrote -- report success.
    """
    lock_path = tmp_path / "package-lock.json"
    lock_path.write_text(_REORDERED_LOCK, encoding="utf-8")

    release._patch_package_lock_versions(lock_path, "9.9.9-reorder.1")

    written = json.loads(lock_path.read_text(encoding="utf-8"))
    assert written["version"] == "9.9.9-reorder.1", "root version was not written"
    assert written["packages"][""]["version"] == "9.9.9-reorder.1", (
        'packages[""] version was not written'
    )
    # Both dependencies, one before and one after the root entry, are untouched.
    assert written["packages"]["node_modules/react"]["version"] == "18.3.1", (
        "a dependency's pinned version was overwritten"
    )
    assert written["packages"]["node_modules/clsx"]["version"] == "2.1.1", (
        "a dependency's pinned version was overwritten"
    )
    # The file is still valid JSON with npm's layout intact.
    assert written["name"] == "novi-webui"
    assert written["packages"][""]["dependencies"] == {"react": "^18.3.1"}


def test_check_version_detects_skew_in_every_registered_file():
    """check() must catch drift in every file set() writes, lockfiles included.

    Breaks one registered file at a time and requires check to name it. This is
    the property that matters for a release gate: `set` touching a file that
    `check` ignores is silent corruption.
    """
    original = release.read_version()
    sentinel = "9.9.9-check.1"
    reference = release.VERSION_FILES[0]
    assert reference.label == "pyproject.toml", "pyproject.toml is the reference manifest"

    release.set_version(sentinel)
    try:
        for entry in release.VERSION_FILES:
            if entry.label == reference.label:
                # Reverting the reference re-bases every comparison, so the
                # skew surfaces against every other file instead. Still a
                # detection; there is nothing to name for the reference itself.
                entry.write(original)
                assert release.check_version(), "check() did not notice a reverted reference"
                entry.write(sentinel)
                continue
            entry.write(original)
            try:
                problems = release.check_version()
                assert problems, f"check() did not notice drift in {entry.label}"
                assert any(entry.label in problem for problem in problems), (
                    f"check() did not name {entry.label}: {problems}"
                )
            finally:
                entry.write(sentinel)
    finally:
        release.set_version(original)


def test_no_test_file_hardcodes_a_version_literal():
    """No test may pin a release version literal inside an assert.

    A test asserting an exact version breaks on the next release. That failure
    is the intended behaviour for a *release gate*, but as a permanent unit test
    it is just breakage waiting to happen. Skew is asserted by
    `test_current_checkout_has_no_version_skew` instead.

    Scope of this guard, stated precisely because it is not total coverage:

    - It walks the AST of every `tests/**/test_*.py` file except this one
      (whose sentinels are legitimate), and flags any semver-shaped string
      constant anywhere inside an `assert`. "Anywhere inside" is deliberate:
      a bare `assert v == "0.2.0"` is not the only shape, and
      `assert d == {"version": "0.2.0"}` or `assert v in ["0.2.0"]` pin a
      version just as hard.
    - Docstrings and comments are structurally invisible to it, which is the
      point: prose may quote a version while explaining why the pin was removed.
    - It does NOT follow constant indirection. A module-level
      `EXPECTED = "0.2.0"` asserted by name is not detected. That is a known,
      accepted gap.
    """
    offenders = []
    for path in (ROOT / "tests").rglob("test_*.py"):
        if path.resolve() == GUARD_PATH:
            continue
        # Parse bytes, not text: some test files carry a UTF-8 BOM that ast.parse
        # rejects as U+FEFF when handed an already-decoded str.
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (SyntaxError, ValueError) as error:
            # An unparseable file is a finding, not a suite-breaking exception:
            # this guard must never be the reason the tests fail to run.
            offenders.append(f"{path.relative_to(ROOT)}: could not parse ({error})")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            for constant in ast.walk(node.test):
                if not isinstance(constant, ast.Constant):
                    continue
                value = constant.value
                if isinstance(value, str) and SEMVER_SHAPED.match(value):
                    offender = f"{path.relative_to(ROOT)}:{constant.lineno}: pins {value}"
                    if offender not in offenders:
                        offenders.append(offender)
    assert offenders == [], (
        "these tests pin a version literal inside an assert and will break on the "
        "next release:\n" + "\n".join(offenders)
    )
