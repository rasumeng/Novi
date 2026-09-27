"""Single schema and filesystem authority for installed Novi skills."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")


class SkillValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    body: str
    root: Path
    content_hash: str
    support_files: tuple[str, ...]

    def catalog_entry(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description,
                "content_hash": self.content_hash}


class SkillCatalog:
    def __init__(self, root: str | Path, *, max_skill_bytes: int = 512_000,
                 max_support_file_bytes: int = 1_000_000) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_skill_bytes = max_skill_bytes
        self.max_support_file_bytes = max_support_file_bytes
        self._errors: dict[str, str] = {}

    def list(self) -> tuple[SkillRecord, ...]:
        records: list[SkillRecord] = []
        errors: dict[str, str] = {}
        for folder in sorted(self.root.iterdir(), key=lambda path: path.name.casefold()):
            if not folder.is_dir() or folder.is_symlink():
                continue
            try:
                records.append(self._load_folder(folder))
            except SkillValidationError as exc:
                errors[folder.name] = str(exc)
        duplicate_names = _duplicates(record.name for record in records)
        if duplicate_names:
            for name in duplicate_names:
                errors[name] = "duplicate skill identity"
            records = [record for record in records if record.name not in duplicate_names]
        self._errors = errors
        return tuple(records)

    def errors(self) -> dict[str, str]:
        self.list()
        return dict(self._errors)

    def get(self, name: str) -> SkillRecord:
        _validate_name(name)
        records = {record.name: record for record in self.list()}
        if name in records:
            return records[name]
        if name in self._errors:
            raise SkillValidationError(self._errors[name])
        raise SkillValidationError(f"skill not found: {name}")

    def create(self, name: str, description: str, body: str, *,
               support_files: dict[str, bytes] | None = None) -> SkillRecord:
        _validate_name(name)
        description = _validate_description(description)
        if not isinstance(body, str) or not body.strip():
            raise SkillValidationError("skill body is required")
        encoded_body = body.encode("utf-8")
        if len(encoded_body) > self.max_skill_bytes:
            raise SkillValidationError("skill body exceeds size limit")
        validated_files: dict[str, bytes] = {}
        for relative, content in (support_files or {}).items():
            safe = _safe_relative_path(relative)
            if safe == "SKILL.md":
                raise SkillValidationError("support files cannot replace SKILL.md")
            if not isinstance(content, bytes):
                raise SkillValidationError("support file content must be bytes")
            if len(content) > self.max_support_file_bytes:
                raise SkillValidationError(f"support file exceeds size limit: {safe}")
            validated_files[safe] = content
        destination = self.root / name
        if destination.exists():
            raise SkillValidationError(f"skill already exists: {name}")
        skill_text = f"---\nname: {name}\ndescription: {description}\n---\n{body.strip()}\n"
        temp = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=self.root))
        try:
            (temp / "SKILL.md").write_text(skill_text, encoding="utf-8")
            for relative, content in validated_files.items():
                target = temp / Path(*PurePosixPath(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            record = self._load_folder(temp, expected_name=name)
            os.replace(temp, destination)
            return SkillRecord(record.name, record.description, record.body,
                               destination, record.content_hash, record.support_files)
        except Exception:
            if temp.exists():
                shutil.rmtree(temp)
            raise

    def install_text(self, expected_name: str, text: str) -> SkillRecord:
        """Validate an uploaded SKILL.md completely, then install atomically."""
        _validate_name(expected_name)
        if not isinstance(text, str):
            raise SkillValidationError("SKILL.md must be text")
        name, description, body = _parse_skill(text)
        if name != expected_name:
            raise SkillValidationError(
                f"skill identity mismatch: filename '{expected_name}' != frontmatter '{name}'")
        return self.create(name, description, body)

    def delete(self, name: str) -> None:
        _validate_name(name)
        target = (self.root / name).resolve()
        if target.parent != self.root or not target.is_dir() or target.is_symlink():
            raise SkillValidationError(f"skill not found: {name}")
        shutil.rmtree(target)

    def _load_folder(self, folder: Path, *, expected_name: str | None = None) -> SkillRecord:
        skill_file = folder / "SKILL.md"
        if not skill_file.is_file() or skill_file.is_symlink():
            raise SkillValidationError("SKILL.md is required")
        raw = skill_file.read_bytes()
        if len(raw) > self.max_skill_bytes:
            raise SkillValidationError("SKILL.md exceeds size limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillValidationError("SKILL.md must be UTF-8") from exc
        name, description, body = _parse_skill(text)
        identity = expected_name or folder.name
        _validate_name(identity)
        if name != identity:
            raise SkillValidationError(
                f"skill identity mismatch: folder '{identity}' != frontmatter '{name}'")
        support: list[str] = []
        digest = hashlib.sha256(raw)
        for path in sorted(folder.rglob("*")):
            if path == skill_file or path.is_dir():
                continue
            if path.is_symlink():
                raise SkillValidationError("skill support files cannot be symlinks")
            relative = path.relative_to(folder).as_posix()
            _safe_relative_path(relative)
            content = path.read_bytes()
            if len(content) > self.max_support_file_bytes:
                raise SkillValidationError(f"support file exceeds size limit: {relative}")
            support.append(relative)
            digest.update(relative.encode("utf-8"))
            digest.update(hashlib.sha256(content).digest())
        return SkillRecord(name=name, description=description, body=body, root=folder.resolve(),
                           content_hash=digest.hexdigest(), support_files=tuple(support))


def _parse_skill(text: str) -> tuple[str, str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillValidationError("SKILL.md frontmatter is required")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise SkillValidationError("SKILL.md frontmatter is not closed") from exc
    fields: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        if not line.strip() or line[:1].isspace() or ":" not in line:
            index += 1
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if value in {"|", ">"}:
            continuation: list[str] = []
            index += 1
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                continuation.append(lines[index].strip())
                index += 1
            fields[key] = " ".join(part for part in continuation if part).strip()
            continue
        fields[key] = value.strip("\"'")
        index += 1
    name = fields.get("name", "")
    _validate_name(name)
    description = _validate_description(fields.get("description", ""))
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise SkillValidationError("skill body is required")
    return name, description, body


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise SkillValidationError("skill name must use 1-64 letters, numbers, hyphens, or underscores")


def _validate_description(description: str) -> str:
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError("skill description is required")
    value = " ".join(description.split())
    if len(value) > 1024:
        raise SkillValidationError("skill description exceeds size limit")
    return value


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SkillValidationError("support file must use a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillValidationError("support file must use a safe relative path")
    return path.as_posix()


def _duplicates(values) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates
