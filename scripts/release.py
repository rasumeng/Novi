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


def _patch_json_version(path: pathlib.Path, version: str, occurrences: int) -> None:
    """Rewrite the first `occurrences` `"version": "..."` keys in place.

    A parse-and-redump would reformat hand-maintained layout (tauri.conf.json
    has blank lines and compact arrays), so a release would show a diff full
    of unrelated churn. Patching the literal keeps the diff to the version.
    """
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r'("version":\s*)"[^"]*"')

    def _replace(match: re.Match[str]) -> str:
        return f'{match.group(1)}"{version}"'

    patched, count = pattern.subn(_replace, text, count=occurrences)
    found = len(pattern.findall(text))
    if found < occurrences:
        raise RuntimeError(
            f"expected at least {occurrences} version keys in {path}, found {found}"
        )
    if patched != text:
        path.write_text(patched, encoding="utf-8")


def _write_tauri_conf(version: str) -> None:
    _patch_json_version(ROOT / "novi/webui/src-tauri/tauri.conf.json", version, 1)


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
    _patch_json_version(ROOT / "novi/webui/package.json", version, 1)


def _write_package_lock(version: str) -> None:
    """Sync package-lock.json, which mirrors package.json's own version.

    npm rewrites this file on every install, so leaving it stale is exactly how
    it drifted to 0.1.0 while every other manifest read 0.2.0. The first two
    `version` keys are the root entry and `packages[""]`, in that order.
    """
    path = ROOT / "novi/webui/package-lock.json"
    if not path.exists():
        return
    lock = json.loads(path.read_text(encoding="utf-8"))
    root = lock.get("packages", {}).get("")
    if lock.get("version") == version and (root is None or root.get("version") == version):
        return
    _patch_json_version(path, version, 2)
    # Fail loudly rather than leave the lockfile half-synced.
    patched = json.loads(path.read_text(encoding="utf-8"))
    patched_root = patched.get("packages", {}).get("")
    if patched.get("version") != version or (
        patched_root is not None and patched_root.get("version") != version
    ):
        raise RuntimeError(f"could not sync package-lock.json versions in {path}")


# Every manifest that declares the Novi version, as (label, path).
VERSION_FILES: list[tuple[str, pathlib.Path]] = [
    ("pyproject.toml", ROOT / "pyproject.toml"),
    ("tauri.conf.json", ROOT / "novi/webui/src-tauri/tauri.conf.json"),
    ("Cargo.toml", ROOT / "novi/webui/src-tauri/Cargo.toml"),
    ("package.json", ROOT / "novi/webui/package.json"),
]

VERSION_WRITERS: list[Callable[[str], None]] = [
    _write_pyproject,
    _write_tauri_conf,
    _write_cargo,
    _write_package_json,
    _write_package_lock,
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


def numeric_prefix(version: str | None = None) -> str:
    """Return the x.y.z prefix Tauri uses for bundle version comparison."""
    if version is None:
        version = read_version()
    match = re.match(r"^(\d+\.\d+\.\d+)", version)
    if not match:
        raise ValueError(f"version {version!r} has no numeric x.y.z prefix")
    return match.group(1)


def set_version(version: str) -> None:
    validate_version(version)
    for writer in VERSION_WRITERS:
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