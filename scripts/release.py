#!/usr/bin/env python3
"""Single-source the Novi version across every manifest that declares one.

Six files carry the version: ``pyproject.toml``, ``tauri.conf.json``,
``src-tauri/Cargo.toml``, ``Cargo.lock``, ``package.json``, and
``package-lock.json``. They have already drifted once (``package-lock.json`` sat
at 0.1.0 while the rest read 0.2.0), so this script owns writing them and
``check`` is run in CI to catch anything that bypasses it.

``VERSION_FILES`` is the single registry of those files. Each entry carries its
label, path, reader, and writer together, so ``set`` and ``check`` cannot drift
apart: a manifest added to the registry is both written and verified, and one
added without a writer is not expressible.

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
from dataclasses import dataclass
from typing import Callable

ROOT = pathlib.Path(__file__).resolve().parent.parent

_TOML_VERSION = re.compile(r'^version = "([^"]+)"$', flags=re.MULTILINE)


# --- writers ---------------------------------------------------------------
# Each writer takes the new version and rewrites exactly one file.


def _patch_toml_version(path: pathlib.Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = _TOML_VERSION.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        raise RuntimeError(f"could not find a package version line in {path}")
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def _patch_json_version(path: pathlib.Path, version: str, occurrences: int) -> None:
    """Rewrite the first `occurrences` `"version": "..."` keys in place.

    A parse-and-redump would reformat hand-maintained layout (tauri.conf.json
    has blank lines and compact arrays), so a release would show a diff full of
    unrelated churn. Patching the literal keeps the diff to the version.
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
    if patched == text:
        return
    path.write_text(patched, encoding="utf-8")
    # Verify the write landed. Silently patching the wrong keys would leave skew
    # that only `check` would find, one release later.
    written = json.loads(path.read_text(encoding="utf-8"))
    verified = _json_versions(written)[:occurrences]
    if verified != [version] * occurrences:
        raise RuntimeError(
            f"could not write version {version!r} into {path}; "
            f"file now reads {verified}"
        )


def _write_pyproject(version: str) -> None:
    _patch_toml_version(ROOT / "pyproject.toml", version)


def _write_tauri_conf(version: str) -> None:
    _patch_json_version(ROOT / "novi/webui/src-tauri/tauri.conf.json", version, 1)


def _write_cargo(version: str) -> None:
    _patch_toml_version(ROOT / "novi/webui/src-tauri/Cargo.toml", version)


def _write_package_json(version: str) -> None:
    _patch_json_version(ROOT / "novi/webui/package.json", version, 1)


def _string_end(text: str, start: int) -> int:
    """Index just past the closing quote of the JSON string starting at `start`."""
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    raise RuntimeError("unterminated JSON string")


def _json_value_span(text: str, value_start: int) -> tuple[int, int]:
    """Span of the JSON value beginning at `value_start`."""
    char = text[value_start]
    if char == '"':
        return value_start, _string_end(text, value_start)
    depth = 0
    for index in range(value_start, len(text)):
        current = text[index]
        if current in "{[":
            depth += 1
        elif current in "}]":
            depth -= 1
            if depth == 0:
                return value_start, index + 1
    raise RuntimeError("unterminated JSON value")


def _key_value_span(text: str, key: str, depth: int) -> tuple[int, int] | None:
    """Span of the value of `key` inside the object nested `depth` deep.

    Depth 1 is the document's top-level object, 2 its children, and so on.
    Addressing by key name at a known depth is what lets a writer target one
    specific field: file order belongs to npm, so "the first `version` in the
    file" is not a location, it is a coincidence that happens to hold today.
    """
    index = 0
    length = len(text)
    current = 0
    while index < length:
        char = text[index]
        if char == '"':
            end = _string_end(text, index)
            probe = end
            while probe < length and text[probe] in " \t\r\n":
                probe += 1
            if current == depth and probe < length and text[probe] == ":":
                if json.loads(text[index:end]) == key:
                    value_start = probe + 1
                    while value_start < length and text[value_start] in " \t\r\n":
                        value_start += 1
                    return _json_value_span(text, value_start)
            index = end
            continue
        if char in "{[":
            current += 1
        elif char in "}]":
            current -= 1
        index += 1
    return None


def _patch_package_lock_versions(path: pathlib.Path, version: str) -> None:
    """Write `version` into package-lock.json's root and its `packages[""]`.

    Both targets are addressed by key name at a known nesting depth, never by
    position in the file. Positional addressing is a latent bug: if npm ever
    reordered the root keys, the writer would patch a dependency's pin instead
    of the root's, and a read-back that inspected the first two keys in file
    order would agree with the damage and pass.
    """
    original = path.read_text(encoding="utf-8")
    text = original
    targets: list[tuple[int, int]] = []

    root_span = _key_value_span(text, "version", 1)
    if root_span is None:
        raise RuntimeError(f"no root version key in {path}")
    targets.append(root_span)

    # `packages[""]` is the root package's own mirror; a lockfile without it
    # (older npm layout) simply has one fewer place to write. The nested lookup
    # runs *inside* that object's span, not at a fixed depth: a dependency entry
    # sits at the same depth as `""`, so depth alone would not distinguish them.
    packages = _key_value_span(text, "packages", 1)
    empty_entry = _key_value_span(text, "", 2) if packages else None
    if packages is not None and empty_entry is not None:
        nested = _key_value_span(text[empty_entry[0]:empty_entry[1]], "version", 1)
        if nested is None:
            raise RuntimeError(f'no version key under packages[""] in {path}')
        targets.append((empty_entry[0] + nested[0], empty_entry[0] + nested[1]))

    for start, end in sorted(targets, reverse=True):
        text = text[:start] + f'"{version}"' + text[end:]
    if text == original:
        return
    path.write_text(text, encoding="utf-8")

    # Verify the write landed, reading the very same keys back by name.
    written = json.loads(path.read_text(encoding="utf-8"))
    root_after = written.get("version")
    nested_after = written.get("packages", {}).get("", {}).get("version")
    if root_after != version:
        raise RuntimeError(
            f"could not write version {version!r} into {path}; "
            f"root version now reads {root_after!r}"
        )
    if len(targets) > 1 and nested_after != version:
        raise RuntimeError(
            f"could not write version {version!r} into {path}; "
            f'packages[""] version now reads {nested_after!r}'
        )


def _write_package_lock(version: str) -> None:
    """Sync package-lock.json, which mirrors package.json's own version.

    npm rewrites this file on every install, so leaving it stale is exactly how
    it drifted to 0.1.0 while every other manifest read 0.2.0. It carries the
    version twice -- the root entry and `packages[""]` -- and both are written.
    """
    path = ROOT / "novi/webui/package-lock.json"
    if not path.exists():
        return
    if _read_package_lock() == {"root": version, "packages['']": version}:
        return
    _patch_package_lock_versions(path, version)


def _write_cargo_lock(version: str) -> None:
    """Sync the novi-desktop entry in Cargo.lock, which tracks Cargo.toml."""
    path = ROOT / "novi/webui/src-tauri/Cargo.lock"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(\nname = "novi-desktop"\nversion = ")[^"]+(")',
        rf'\g<1>{version}\g<2>',
        text,
    )
    if count == 0:
        raise RuntimeError(f"could not find a novi-desktop entry in {path}")
    if updated != text:
        path.write_text(updated, encoding="utf-8")
    written = _read_cargo_lock()
    if written.get("novi-desktop") != version:
        raise RuntimeError(
            f"could not write version {version!r} into {path}; "
            f"file now reads {written.get('novi-desktop')!r}"
        )


# --- readers ---------------------------------------------------------------
# Each reader returns {location: version} for one file, so `check` and `set`
# agree on what a file declares. An empty dict means "declares no version".


def _json_versions(data: object) -> list[str]:
    """Every `"version"` string value in a JSON document, in file order."""
    text = json.dumps(data)
    return re.findall(r'"version"\s*:\s*"([^"]*)"', text)


def _read_pyproject() -> dict[str, str]:
    match = _TOML_VERSION.search((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError("no version line found")
    return {"version": match.group(1)}


def _read_tauri_conf() -> dict[str, str]:
    data = json.loads((ROOT / "novi/webui/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    return {"version": data["version"]}


def _read_cargo() -> dict[str, str]:
    match = _TOML_VERSION.search(
        (ROOT / "novi/webui/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    )
    if not match:
        raise RuntimeError("no package version line found")
    return {"version": match.group(1)}


def _read_package_json() -> dict[str, str]:
    data = json.loads((ROOT / "novi/webui/package.json").read_text(encoding="utf-8"))
    return {"version": data["version"]}


def _read_package_lock() -> dict[str, str]:
    path = ROOT / "novi/webui/package-lock.json"
    if not path.exists():
        return {}
    lock = json.loads(path.read_text(encoding="utf-8"))
    root = lock.get("packages", {}).get("") or {}
    found = {}
    if lock.get("version") is not None:
        found["root"] = lock["version"]
    if root.get("version") is not None:
        found["packages['']"] = root["version"]
    if not found:
        raise RuntimeError("no version keys found")
    return found


def _read_cargo_lock() -> dict[str, str]:
    path = ROOT / "novi/webui/src-tauri/Cargo.lock"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    found = {
        name: value
        for name, value in re.findall(r'\nname = "([^"]+)"\nversion = "([^"]+)"', text)
        if name == "novi-desktop"
    }
    if not found:
        raise RuntimeError("no novi-desktop entry found")
    return found


@dataclass(frozen=True)
class VersionFile:
    """One version-carrying file. Label, path, reader, and writer travel together.

    Keeping them in one entry is what makes the registry trustworthy: there is
    no way to register a file for `check` without also registering its writer for
    `set`, or to label a file whose writer is missing.
    """

    label: str
    path: pathlib.Path
    read: Callable[[], dict[str, str]]
    write: Callable[[str], None]


# Every file that declares the Novi version. This is the single source of truth:
# `set_version` writes each entry, `check_version` verifies each entry.
VERSION_FILES: list[VersionFile] = [
    VersionFile("pyproject.toml", ROOT / "pyproject.toml", _read_pyproject, _write_pyproject),
    VersionFile(
        "tauri.conf.json",
        ROOT / "novi/webui/src-tauri/tauri.conf.json",
        _read_tauri_conf,
        _write_tauri_conf,
    ),
    VersionFile(
        "Cargo.toml",
        ROOT / "novi/webui/src-tauri/Cargo.toml",
        _read_cargo,
        _write_cargo,
    ),
    VersionFile("package.json", ROOT / "novi/webui/package.json", _read_package_json, _write_package_json),
    VersionFile(
        "package-lock.json",
        ROOT / "novi/webui/package-lock.json",
        _read_package_lock,
        _write_package_lock,
    ),
    VersionFile(
        "Cargo.lock",
        ROOT / "novi/webui/src-tauri/Cargo.lock",
        _read_cargo_lock,
        _write_cargo_lock,
    ),
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
    match = _TOML_VERSION.search(text)
    if not match:
        raise RuntimeError("could not read version from pyproject.toml")
    return match.group(1)


def numeric_prefix(version: str | None = None) -> str:
    """Return the x.y.z prefix Tauri uses for bundle version comparison."""
    if version is None:
        version = read_version()
    # Validate on both paths, not just the defaulted one: an explicit
    # `1.2.3.4` would otherwise match the prefix regex below and silently
    # yield a wrong bundle version.
    validate_version(version)
    match = re.match(r"^(\d+\.\d+\.\d+)", version)
    if not match:
        raise ValueError(f"version {version!r} has no numeric x.y.z prefix")
    return match.group(1)


def set_version(version: str) -> None:
    validate_version(version)
    for entry in VERSION_FILES:
        entry.write(version)


def check_version() -> list[str]:
    """Return a list of skew descriptions. Empty list means consistent.

    Every file `set_version` writes is checked here; a writer and a checker can
    no longer be out of step because both iterate `VERSION_FILES`.
    """
    reference = read_version()
    validate_version(reference)
    problems: list[str] = []

    for entry in VERSION_FILES:
        try:
            declared = entry.read()
        except Exception as error:  # noqa: BLE001 - a broken file is a finding
            problems.append(f"{entry.label}: could not read version ({error})")
            continue
        if not declared:
            continue
        for location, value in declared.items():
            if value != reference:
                problems.append(f"{entry.label} {location}: {value} != {reference}")

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
        print(f"version set to {args.version} across {len(VERSION_FILES)} files")
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
