"""Benchmark dataset — typed cases with ground truth (Phase 8).

Extends the regression_corpus.json format. The loader accepts two shapes:

1. Legacy bare list (tests/regression_corpus.json):
   [{ "id": ..., "query": ..., "expected": { intent/strategy/capabilities/
      evidence_external/evidence_memory }, "tags": [...] }]

2. Versioned object with a schema marker (extensible without code changes):
   { "_meta": { "name": ..., "version": ..., "schema": 1 },
     "cases": [ ... ] }

New optional fields in either shape: expected.sources, expected.answer_contains,
expected.grounding, and a per-case "has_images" flag.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

# Legacy evidence_external maps to expected_grounding for the benchmark layer.
# evidence_memory is excluded: memory retrieval is a separate runtime path,
# not a grounding (web/knowledge) search.


def _coerce_grounding(expected: dict) -> bool:
    """Resolve expected_grounding: explicit 'grounding' wins, else legacy."""
    if "grounding" in expected:
        return bool(expected["grounding"])
    return bool(expected.get("evidence_external"))


@dataclass
class BenchmarkCase:
    """One benchmark case with ground truth for evaluation."""

    id: str
    input: str
    expected_intent: str = "conversation"
    expected_grounding: bool = False
    expected_sources: list[str] = field(default_factory=list)
    expected_answer_contains: list[str] = field(default_factory=list)
    expected_strategy: str = ""
    expected_capabilities: list[str] = field(default_factory=list)
    expected_evidence_memory: bool = False
    has_images: bool = False
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        expected: dict[str, Any] = {
            "intent": self.expected_intent,
            "strategy": self.expected_strategy,
            "capabilities": list(self.expected_capabilities),
            "grounding": self.expected_grounding,
            "sources": list(self.expected_sources),
            "answer_contains": list(self.expected_answer_contains),
            "evidence_memory": self.expected_evidence_memory,
        }
        d: dict[str, Any] = {"id": self.id, "input": self.input, "expected": expected}
        if self.has_images:
            d["has_images"] = True
        if self.tags:
            d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BenchmarkCase":
        expected = d.get("expected", {}) or {}
        grounding = _coerce_grounding(expected)
        return cls(
            id=str(d.get("id", "")),
            input=str(d.get("input", d.get("query", ""))),
            expected_intent=str(expected.get("intent", "conversation")),
            expected_grounding=grounding,
            expected_sources=list(expected.get("sources", []) or []),
            expected_answer_contains=list(expected.get("answer_contains", []) or []),
            expected_strategy=str(expected.get("strategy", "")),
            expected_capabilities=list(expected.get("capabilities", []) or []),
            expected_evidence_memory=bool(expected.get("evidence_memory", False)),
            has_images=bool(d.get("has_images", False)),
            tags=list(d.get("tags", []) or []),
        )


@dataclass
class BenchmarkDataset:
    """Ordered, typed collection of benchmark cases."""

    name: str = "benchmark"
    version: str = "1.0"
    cases: list[BenchmarkCase] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self) -> Iterator[BenchmarkCase]:
        return iter(self.cases)

    def __getitem__(self, index: int) -> BenchmarkCase:
        return self.cases[index]

    def add(self, case: BenchmarkCase) -> "BenchmarkDataset":
        self.cases.append(case)
        return self

    def extend(self, cases: list[BenchmarkCase]) -> "BenchmarkDataset":
        self.cases.extend(cases)
        return self

    def by_tags(self, *tags: str) -> list[BenchmarkCase]:
        wanted = set(tags)
        return [c for c in self.cases if wanted & set(c.tags)]

    def to_dict(self) -> dict:
        return {
            "_meta": {
                "name": self.name,
                "version": self.version,
                "schema": SCHEMA_VERSION,
            },
            "cases": [c.to_dict() for c in self.cases],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def from_json(cls, path: str | Path, name: str = "") -> "BenchmarkDataset":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return cls(
                name=name or Path(path).stem,
                cases=[BenchmarkCase.from_dict(d) for d in raw],
            )
        meta = raw.get("_meta", {}) or {}
        schema = int(meta.get("schema", SCHEMA_VERSION))
        if schema > SCHEMA_VERSION:
            raise ValueError(
                f"Dataset schema v{schema} exceeds supported schema v{SCHEMA_VERSION}"
            )
        return cls(
            name=name or meta.get("name", Path(path).stem),
            version=str(meta.get("version", "1.0")),
            cases=[BenchmarkCase.from_dict(d) for d in raw.get("cases", [])],
        )
