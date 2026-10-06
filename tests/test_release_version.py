"""The release script is the single writer of Novi's version.

These tests exist because version skew is a real, already-observed bug:
`novi/webui/package-lock.json` drifted to 0.1.0 while every other manifest
said 0.2.0, and `tests/test_beta_hardening.py` had begun pinning the literal
"0.2.0" instead of checking for skew.
"""

import ast
import json
import pathlib
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

        # set_version() also syncs Cargo.lock via _sync_cargo_lock(); assert it
        # actually happened rather than trusting the call is wired up.
        lock_path = ROOT / "novi/webui/src-tauri/Cargo.lock"
        if lock_path.exists():
            import tomllib

            lock = tomllib.loads(_read(lock_path))
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
    current = release.read_version()
    # Walk real comparison assertions instead of grepping lines, so prose
    # (docstrings, comments) that merely mentions a version is not flagged, and
    # so sentinel versions this file asserts against are not mistaken for
    # release pins. Only a literal equal to the live version breaks a release.
    for path in (ROOT / "tests").rglob("test_*.py"):
        # Parse bytes, not text: some test files carry a UTF-8 BOM that ast.parse
        # rejects as U+FEFF when handed an already-decoded str.
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            for comparison in ast.walk(node.test):
                if not isinstance(comparison, ast.Compare):
                    continue
                for operand in list(comparison.comparators) + [comparison.left]:
                    if isinstance(operand, ast.Constant) and operand.value == current:
                        offender = f"{path.relative_to(ROOT)}:{operand.lineno}: pins {current}"
                        if offender not in offenders:
                            offenders.append(offender)
    assert offenders == [], (
        f"these tests pin the release version {current} and will break on the next "
        "release:\n" + "\n".join(offenders)
    )