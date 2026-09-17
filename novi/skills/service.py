"""Explicit per-run skill activation with content-hash pinning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import SkillCatalog, SkillRecord, SkillValidationError, _safe_relative_path


@dataclass(frozen=True)
class ActivatedSkill:
    name: str
    description: str
    instructions: str
    content_hash: str
    support_files: tuple[str, ...]


class SkillService:
    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog
        self._run_catalogs: dict[str, dict[str, SkillRecord]] = {}
        self._active: dict[str, dict[str, ActivatedSkill]] = {}

    def begin_run(self, run_id: str) -> tuple[dict[str, str], ...]:
        if not run_id:
            raise ValueError("run_id is required")
        snapshot = {record.name: record for record in self._catalog.list()}
        self._run_catalogs[run_id] = snapshot
        self._active[run_id] = {}
        return tuple(record.catalog_entry() for record in snapshot.values())

    def activate(self, run_id: str, name: str) -> ActivatedSkill:
        if run_id not in self._run_catalogs:
            raise SkillValidationError("run skill catalog has not been initialized")
        active = self._active[run_id]
        if name in active:
            return active[name]
        record = self._run_catalogs[run_id].get(name)
        if record is None:
            raise SkillValidationError(f"skill was not available at run start: {name}")
        activated = ActivatedSkill(name=record.name, description=record.description,
            instructions=record.body, content_hash=record.content_hash,
            support_files=record.support_files)
        active[name] = activated
        return activated

    def read_support(self, run_id: str, name: str, relative_path: str) -> bytes:
        safe = _safe_relative_path(relative_path)
        active = self.activate(run_id, name)
        if safe not in active.support_files:
            raise SkillValidationError(f"support file is not indexed: {safe}")
        record = self._run_catalogs[run_id][name]
        target = (record.root / Path(*_path_parts(safe))).resolve()
        if record.root not in target.parents or not target.is_file() or target.is_symlink():
            raise SkillValidationError("support file escaped the skill root or is unavailable")
        return target.read_bytes()

    def active(self, run_id: str) -> tuple[ActivatedSkill, ...]:
        return tuple(self._active.get(run_id, {}).values())


def _path_parts(value: str) -> tuple[str, ...]:
    return tuple(value.split("/"))
