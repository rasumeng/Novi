"""Evidence grounding A/B evaluation (Phase 7 measurement tooling).

Compares two grounding forms for the same live model and query set:

  A_merged  — EvidenceBundle.merged_text (status quo)
  B_context — EvidenceContext.summary (structured, compressed)
  N_none    — no grounding (control / calibration)

Repeatable and version-controlled: cases come from a JSON corpus
(tests/evidence_corpus.json), results save to JSON and can be diffed across
runs via the ``evidence-compare`` command. Model resolution goes through
ModelService — providers stay a deployment detail.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from ..evidence import EvidenceProcessor
from ..runtime.evidence import EvidenceBundle, EvidenceCollector
from ..tools.search_pipeline import SearchResult

MODES = ("A_merged", "B_context", "N_none")

# Tolerance before a mode is flagged as regressed (recall/grounded delta).
REGRESSION_TOLERANCE = 0.05

ANSWER_PROMPT = """Answer the question using ONLY the grounding context below, if it is present. Do not use outside knowledge. If the context does not contain the answer, reply with exactly: I don't know.
<grounding>
{grounding}
</grounding>
Question: {query}
Answer:"""

JUDGE_PROMPT = """Evaluate whether the ANSWER is fully supported by the CONTEXT. If the answer contradicts the context, or introduces facts absent from it, it is NOT supported.

Question: {query}
Context:
{grounding}
Answer:
{answer}

Reply with a single digit: 1 if supported, 0 if not supported."""


class ModelClient(Protocol):
    """Minimal LangChain chat-model contract used by the A/B run."""

    def invoke(self, messages: list) -> Any: ...


# ── Dataset ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceCase:
    id: str
    query: str
    expected: tuple[str, ...] = ()
    sources: tuple[dict, ...] = ()


@dataclass
class EvidenceDataset:
    name: str
    cases: list[EvidenceCase]

    @classmethod
    def from_json(cls, path: str | Path) -> "EvidenceDataset":
        data = json.loads(Path(path).read_text("utf-8"))
        cases = [
            EvidenceCase(
                id=str(c.get("id", f"case-{i}")),
                query=str(c["query"]),
                expected=tuple(str(t) for t in c.get("expected", [])),
                sources=tuple(dict(s) for s in c.get("sources", [])),
            )
            for i, c in enumerate(data.get("cases", []))
        ]
        return cls(name=str(data.get("description", str(path)))[:60], cases=cases)

    def __iter__(self):
        return iter(self.cases)

    def __len__(self) -> int:
        return len(self.cases)


# ── Results ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CaseABResult:
    id: str
    query: str
    facts: int
    conflicts: int
    confidence: float
    fallback: bool
    chars_a: int
    chars_b: int
    modes: tuple  # (mode, dict) pairs

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceABReport:
    dataset: str
    model: str
    cases: tuple

    @classmethod
    def from_json(cls, path: str | Path) -> "EvidenceABReport":
        data = json.loads(Path(path).read_text("utf-8"))
        cases = tuple(
            CaseABResult(
                id=c["id"],
                query=c["query"],
                facts=int(c["facts"]),
                conflicts=int(c["conflicts"]),
                confidence=float(c["confidence"]),
                fallback=bool(c["fallback"]),
                chars_a=int(c["chars_a"]),
                chars_b=int(c["chars_b"]),
                modes=tuple(tuple(x) for x in c["modes"]),
            )
            for c in data.get("cases", [])
        )
        return cls(dataset=data.get("dataset", ""), model=data.get("model", ""), cases=cases)

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "model": self.model,
            "aggregate": self.aggregate(),
            "cases": [c.to_dict() for c in self.cases],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8"
        )

    # ── aggregates ──────────────────────────────────────────────────────

    def aggregate(self) -> dict:
        n = len(self.cases)
        agg = {"cases": n, "fallback_count": 0, "facts": 0, "avg_confidence": 0.0}
        if n == 0:
            return agg
        recall = {m: {"hits": 0, "total": 0} for m in MODES}
        grounded = {m: {"hits": 0, "n": n} for m in MODES}
        for c in self.cases:
            agg["fallback_count"] += int(c.fallback)
            agg["facts"] += c.facts
            agg["avg_confidence"] += c.confidence
            for mode, rec in c.modes:
                recall[mode]["hits"] += int(rec["hits"])
                recall[mode]["total"] += int(rec["total"])
                grounded[mode]["hits"] += int(rec["supported"])
        agg["avg_confidence"] = round(agg["avg_confidence"] / n, 3)
        chars_a = sum(c.chars_a for c in self.cases)
        chars_b = sum(c.chars_b for c in self.cases)
        agg["compression"] = round(
            1.0 - chars_b / max(1, chars_a), 4
        )
        agg["recall"] = {
            m: round(r["hits"] / max(1, r["total"]), 4)
            for m, r in recall.items()
        }
        agg["grounded"] = {
            m: round(g["hits"] / max(1, g["n"]), 4) for m, g in grounded.items()
        }
        return agg

    def verdict(self) -> str:
        """B must not regress more than tolerance vs A on recall and grounding."""
        agg = self.aggregate()
        r_a = agg["recall"]["A_merged"]
        r_b = agg["recall"]["B_context"]
        g_a = agg["grounded"]["A_merged"]
        g_b = agg["grounded"]["B_context"]
        if r_a > 0 and r_b < r_a - REGRESSION_TOLERANCE:
            return "FAIL"
        if g_a > 0 and g_b < g_a - REGRESSION_TOLERANCE:
            return "FAIL"
        return "PASS"


# ── Run ────────────────────────────────────────────────────────────────


def _build_bundle(case: EvidenceCase) -> EvidenceBundle:
    results = [
        SearchResult(
            title=str(s.get("title", "")),
            url=str(s.get("url", "")),
            snippet=str(s.get("snippet", "")),
            full_text=str(s.get("full_text", "")),
        )
        for s in case.sources
    ]
    merged = EvidenceCollector._merge(case.query, results)
    return EvidenceBundle(
        query=case.query,
        results=results,
        merged_text=merged.merged_text,
        source_count=len(results),
    )


def _call(client: ModelClient, prompt: str) -> str:
    from langchain_core.messages import HumanMessage

    try:
        resp = client.invoke([HumanMessage(content=prompt)])
    except Exception as e:  # noqa: BLE001 — a model failure marks the case, not the run
        return f"ERROR: {e}"
    content = getattr(resp, "content", resp)
    if isinstance(content, list):
        content = " ".join(
            str(c.get("text", "")) if isinstance(c, dict) else str(c)
            for c in content
        )
    return str(content or "")


def _score(answer: str, expected: tuple[str, ...]) -> tuple[int, int]:
    al = answer.lower()
    hits = sum(1 for t in expected if t.lower() in al)
    return hits, len(expected)


def _invoke_answer(
    client: ModelClient, query: str, grounding: str
) -> tuple[str, float]:
    t0 = time.perf_counter()
    answer = _call(
        client, ANSWER_PROMPT.format(grounding=grounding, query=query)
    )
    return answer, round((time.perf_counter() - t0) * 1000, 2)


def _judge(
    client: ModelClient, query: str, grounding: str, answer: str
) -> bool:
    out = _call(
        client,
        JUDGE_PROMPT.format(
            query=query, grounding=grounding[:4000], answer=answer[:1000]
        ),
    )
    return out.strip().startswith("1")


def run_evidence_ab(
    dataset: EvidenceDataset,
    client: ModelClient,
    judge_client: ModelClient | None = None,
    model_name: str = "",
) -> EvidenceABReport:
    """Run the A/B comparison over the dataset. Returns a frozen report."""
    judge = judge_client or client
    results: list[CaseABResult] = []
    for case in dataset.cases:
        bundle = _build_bundle(case)
        ctx = EvidenceProcessor().process(bundle)
        modes = {
            "A_merged": bundle.merged_text,
            "B_context": ctx.summary,
            "N_none": "",
        }
        mode_records = []
        for mode, grounding in modes.items():
            answer, latency_ms = _invoke_answer(client, case.query, grounding)
            hits, total = _score(answer, case.expected)
            mode_records.append(
                (
                    mode,
                    {
                        "answer": answer.strip()[:200],
                        "hits": hits,
                        "total": total,
                        "supported": _judge(judge, case.query, grounding, answer),
                        "latency_ms": latency_ms,
                    },
                )
            )
        results.append(
            CaseABResult(
                id=case.id,
                query=case.query,
                facts=len(ctx.facts),
                conflicts=len(ctx.conflicts),
                confidence=ctx.confidence,
                fallback=ctx.fallback,
                chars_a=len(bundle.merged_text),
                chars_b=len(ctx.summary),
                modes=tuple(mode_records),
            )
        )
    return EvidenceABReport(
        dataset=dataset.name, model=model_name, cases=tuple(results)
    )


# ── Cross-run comparison ───────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceABCompare:
    baseline: str
    candidate: str
    deltas: dict
    passed: bool

    def to_dict(self) -> dict:
        return asdict(self)


def compare_evidence_ab(
    baseline: EvidenceABReport | str | Path,
    candidate: EvidenceABReport | str | Path,
) -> EvidenceABCompare:
    """Diff two saved (or in-memory) reports per mode. Flags regressions."""
    if not isinstance(baseline, EvidenceABReport):
        b_name = Path(str(baseline)).stem
        baseline = EvidenceABReport.from_json(baseline)
    else:
        b_name = baseline.dataset
    if not isinstance(candidate, EvidenceABReport):
        c_name = Path(str(candidate)).stem
        candidate = EvidenceABReport.from_json(candidate)
    else:
        c_name = candidate.dataset
    b, c = baseline.aggregate(), candidate.aggregate()
    deltas = {}
    findings = []
    for mode in MODES:
        dr = round(c["recall"][mode] - b["recall"][mode], 4)
        dg = round(c["grounded"][mode] - b["grounded"][mode], 4)
        deltas[mode] = {
            "recall": {"base": b["recall"][mode], "cand": c["recall"][mode], "delta": dr},
            "grounded": {"base": b["grounded"][mode], "cand": c["grounded"][mode], "delta": dg},
        }
        if b["recall"][mode] > 0 and dr < -REGRESSION_TOLERANCE:
            findings.append(f"{mode}.recall regression ({dr:+.3f})")
        if b["grounded"][mode] > 0 and dg < -REGRESSION_TOLERANCE:
            findings.append(f"{mode}.grounded regression ({dg:+.3f})")
    dc = round(c["compression"] - b["compression"], 4)
    deltas["compression"] = {"base": b["compression"], "cand": c["compression"], "delta": dc}
    if dc < -REGRESSION_TOLERANCE:
        findings.append(f"compression regression ({dc:+.3f})")
    return EvidenceABCompare(
        baseline=b_name,
        candidate=c_name,
        deltas=deltas,
        passed=not findings,
    )
