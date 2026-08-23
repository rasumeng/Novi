"""Metric definitions — the stable measurement contract for Phase 8.

MetricSet is the cross-phase comparison unit. Definitions are intentionally
computed from the public ExecutionTrace.to_dict() contract and benchmark
ground truth — never from runtime internals or Phase 7 EvidenceContext
fields. This keeps metrics stable while evidence processing evolves.

Design rules:
- MetricSet fields are stable. Adding a metric is a non-breaking change;
  removing or renaming one is a versioned contract change.
- Answer metrics are model-free by default. An optional AnswerJudge
  (LLM-based, Phase 7+) can populate relevance / hallucination_rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .benchmark import BenchmarkCase

# Source-quality grades recorded by the retrieval pipeline (RetrievalQuality).
_QUALITY_GRADES = ("sufficient", "weak", "empty", "failed")


class AnswerJudge(Protocol):
    """Extension point for LLM-based answer scoring (used by MetricCollector)."""

    def score(self, answer: str, case: "BenchmarkCase") -> tuple[float, float]:
        """Return (relevance, hallucination_rate) for one answer in [0, 1]."""
        ...


@dataclass
class CaseResult:
    """One evaluated case — the intermediate record produced by a driver.

    Deliberately a small, schema-stable slice of runtime state. Computed from
    ExecutionTrace.to_dict() fields (or orchestrator analysis) so evaluation
    does not depend on internal runtime objects.
    """

    case: "BenchmarkCase" = field(repr=False)
    intent: str = ""
    strategy: str = ""
    capabilities: list[str] = field(default_factory=list)
    grounding_searched: bool = False
    grounding_quality: str = ""
    retrieval_sources: list[str] = field(default_factory=list)
    retrieval_strategy: str = ""
    answer: str = ""
    max_steps: int = 0
    recovery_attempts: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None
    trace: dict | None = None

    # ── Phase 8E: research evaluation (additive, optional) ──────────────
    citations: list[str] = field(default_factory=list)
    """Source ids cited in the answer ([S#])."""
    invalid_citations: list[str] = field(default_factory=list)
    """Cited ids absent from the manifest (unresolvable)."""
    manifest_size: int = 0
    searches: int = 0
    conflicts_surfaced: int = 0
    conflict_acknowledged: bool = False
    disclosed_insufficient: bool = False

    # ── Phase 8E: coding evaluation (additive, optional) ────────────────
    verification_passed: bool = False
    verifications: int = 0
    verification_failures: int = 0
    repair_attempts: int = 0
    edits: int = 0
    changed_files: int = 0
    first_attempt_passed: bool = False
    task_completed: bool = False

    # ── Phase 8 remediation (audit G): honest provenance of the result ──
    driver_mode: str = ""
    """"scripted" when a deterministic stand-in produced the behavior under
    test, "live" when a real bound model ran. Consumers can weigh offline
    metrics accordingly instead of mistaking them for end-to-end evidence."""
    staged_repair: bool = False
    """True when the DRIVER itself supplied the passing edit on a repair
    attempt (fixture-staged success). Such runs must never be read as proof
    that the agent repaired anything."""
    coverage_incomplete: bool = False
    """Research runs: True when the search budget left decomposed
    sub-questions unresearched (audit B)."""

    def to_dict(self) -> dict:
        return {
            "id": self.case.id,
            "input": self.case.input,
            "intent": self.intent,
            "strategy": self.strategy,
            "capabilities": list(self.capabilities),
            "grounding_searched": self.grounding_searched,
            "grounding_quality": self.grounding_quality,
            "retrieval_sources": list(self.retrieval_sources),
            "retrieval_strategy": self.retrieval_strategy,
            "answer": self.answer,
            "max_steps": self.max_steps,
            "recovery_attempts": self.recovery_attempts,
            "tool_calls": list(self.tool_calls),
            "latency_ms": self.latency_ms,
            "error": self.error,
            "citations": list(self.citations),
            "invalid_citations": list(self.invalid_citations),
            "manifest_size": self.manifest_size,
            "searches": self.searches,
            "conflicts_surfaced": self.conflicts_surfaced,
            "conflict_acknowledged": self.conflict_acknowledged,
            "disclosed_insufficient": self.disclosed_insufficient,
            "verification_passed": self.verification_passed,
            "verifications": self.verifications,
            "verification_failures": self.verification_failures,
            "repair_attempts": self.repair_attempts,
            "edits": self.edits,
            "changed_files": self.changed_files,
            "first_attempt_passed": self.first_attempt_passed,
            "task_completed": self.task_completed,
            "driver_mode": self.driver_mode,
            "staged_repair": self.staged_repair,
            "coverage_incomplete": self.coverage_incomplete,
        }


@dataclass
class RetrievalMetrics:
    """Retrieval quality: source selection precision/recall + grounding accuracy.

    Computed per case against expected_sources / expected_grounding.
    - precision: correct retrieved sources / retrieved sources
    - recall:    correct retrieved sources / expected sources
    - source_quality_distribution: counts of grounding_quality grades observed
    - grounding_accuracy: cases where grounding decision matched expectation
    """

    precision: float = 0.0
    recall: float = 0.0
    source_quality_distribution: dict = field(default_factory=dict)
    grounding_accuracy: float = 0.0

    def to_dict(self) -> dict:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "source_quality_distribution": dict(self.source_quality_distribution),
            "grounding_accuracy": round(self.grounding_accuracy, 4),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RetrievalMetrics":
        return cls(
            precision=float(d.get("precision", 0.0)),
            recall=float(d.get("recall", 0.0)),
            source_quality_distribution=dict(d.get("source_quality_distribution", {})),
            grounding_accuracy=float(d.get("grounding_accuracy", 0.0)),
        )


@dataclass
class AnswerMetrics:
    """Answer quality. Model-free by default via expected_answer_contains.

    - correctness:    fraction of scored cases with ALL expected phrases present
    - completeness:   mean fraction of expected phrases present across cases
    - relevance:      LLM-judged (0.0 when no AnswerJudge provided)
    - hallucination_rate: LLM-judged (0.0 when no AnswerJudge provided)
    - judged:         True when relevance/hallucination came from an AnswerJudge
    """

    correctness: float = 0.0
    completeness: float = 0.0
    relevance: float = 0.0
    hallucination_rate: float = 0.0
    judged: bool = False

    def to_dict(self) -> dict:
        return {
            "correctness": round(self.correctness, 4),
            "completeness": round(self.completeness, 4),
            "relevance": round(self.relevance, 4),
            "hallucination_rate": round(self.hallucination_rate, 4),
            "judged": self.judged,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AnswerMetrics":
        return cls(
            correctness=float(d.get("correctness", 0.0)),
            completeness=float(d.get("completeness", 0.0)),
            relevance=float(d.get("relevance", 0.0)),
            hallucination_rate=float(d.get("hallucination_rate", 0.0)),
            judged=bool(d.get("judged", False)),
        )


@dataclass
class ToolMetrics:
    """Tool execution efficiency.

    - success_rate:  fraction of recorded tool calls that succeeded
    - efficiency:    1 - mean(tool_calls / max(1, max_steps)); 1.0 = minimal calls
    - recovery_rate: fraction of runs that needed a recovery escalation
    - avg_tool_calls: mean tool calls per run
    """

    success_rate: float = 0.0
    efficiency: float = 0.0
    recovery_rate: float = 0.0
    avg_tool_calls: float = 0.0

    def to_dict(self) -> dict:
        return {
            "success_rate": round(self.success_rate, 4),
            "efficiency": round(self.efficiency, 4),
            "recovery_rate": round(self.recovery_rate, 4),
            "avg_tool_calls": round(self.avg_tool_calls, 4),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolMetrics":
        return cls(
            success_rate=float(d.get("success_rate", 0.0)),
            efficiency=float(d.get("efficiency", 0.0)),
            recovery_rate=float(d.get("recovery_rate", 0.0)),
            avg_tool_calls=float(d.get("avg_tool_calls", 0.0)),
        )


@dataclass
class ResearchMetrics:
    """Deterministic research answer quality (Phase 8E).

    All values are computed WITHOUT an LLM judge:
    - citation_resolvability: valid citations / all citations (0 when none)
    - citation_coverage:      evidence cases whose answer cites >=1 valid source
    - insufficiency_honesty:  weak-evidence cases that disclose insufficiency
    - conflict_acknowledgment: conflict-present cases acknowledging them
    - unnecessary_search_rate: cases exceeding expected_max_searches
    - avg_manifest_size / avg_searches: bounded-state sanity signals
    """

    citation_resolvability: float = 0.0
    citation_coverage: float = 0.0
    insufficiency_honesty: float = 0.0
    conflict_acknowledgment: float = 0.0
    unnecessary_search_rate: float = 0.0
    avg_manifest_size: float = 0.0
    avg_searches: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 4) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "ResearchMetrics":
        return cls(**{k: float(d.get(k, 0.0)) for k in cls.__dataclass_fields__})


@dataclass
class CodingMetrics:
    """Deterministic coding verification quality (Phase 8E).

    - task_completion:        runs finishing with passing verification
    - test_pass_rate:         passed verifications / total verifications
    - regression_rate:        previously-passing verifications later failing
                              within one run (bounded repair churn signal)
    - avg_repair_attempts:    implement attempts beyond the first
    - unnecessary_edit_rate:  runs editing files though verification never failed
    - tool_failure_rate:      failed tool calls / total calls
    - verification_failure_rate: runs with >=1 failing verification
    - staged_repair_rate:     Phase 8 remediation (audit G): fraction of runs
                              whose "repair" was supplied by the DRIVER
                              (fixture-staged), not observed agent behavior.
                              High values mean repair metrics are tautological
                              by construction, not evidence of agent skill.
    """

    task_completion: float = 0.0
    test_pass_rate: float = 0.0
    regression_rate: float = 0.0
    avg_repair_attempts: float = 0.0
    unnecessary_edit_rate: float = 0.0
    tool_failure_rate: float = 0.0
    verification_failure_rate: float = 0.0
    staged_repair_rate: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 4) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "CodingMetrics":
        return cls(**{k: float(d.get(k, 0.0)) for k in cls.__dataclass_fields__})


@dataclass
class MetricSet:
    """Complete evaluation result for one benchmark run.

    Serializable to/from JSON for baseline storage and comparison.
    """

    retrieval: RetrievalMetrics = field(default_factory=RetrievalMetrics)
    answer: AnswerMetrics = field(default_factory=AnswerMetrics)
    tools: ToolMetrics = field(default_factory=ToolMetrics)
    research: ResearchMetrics = field(default_factory=ResearchMetrics)
    coding: CodingMetrics = field(default_factory=CodingMetrics)
    latency: float = 0.0
    n: int = 0

    def to_dict(self) -> dict:
        return {
            "retrieval": self.retrieval.to_dict(),
            "answer": self.answer.to_dict(),
            "tools": self.tools.to_dict(),
            "research": self.research.to_dict(),
            "coding": self.coding.to_dict(),
            "latency": round(self.latency, 2),
            "n": self.n,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MetricSet":
        return cls(
            retrieval=RetrievalMetrics.from_dict(d.get("retrieval", {})),
            answer=AnswerMetrics.from_dict(d.get("answer", {})),
            tools=ToolMetrics.from_dict(d.get("tools", {})),
            research=ResearchMetrics.from_dict(d.get("research", {})),
            coding=CodingMetrics.from_dict(d.get("coding", {})),
            latency=float(d.get("latency", 0.0)),
            n=int(d.get("n", 0)),
        )


class MetricCollector:
    """Per-category metric computation from CaseResult records.

    Pluggable answer judging via ``answer_judge`` (an AnswerJudge) for the
    relevance / hallucination dimensions. Everything else is model-free.
    """

    def __init__(self, answer_judge: AnswerJudge | None = None):
        self.answer_judge = answer_judge

    def collect(self, results: list[CaseResult]) -> MetricSet:
        if not results:
            return MetricSet()
        return MetricSet(
            retrieval=self.retrieval_metrics(results),
            answer=self.answer_metrics(results),
            tools=self.tool_metrics(results),
            research=self.research_metrics(results),
            coding=self.coding_metrics(results),
            latency=self.latency_metrics(results),
            n=len(results),
        )

    # ── retrieval ───────────────────────────────────────────────────────

    def retrieval_metrics(self, results: list[CaseResult]) -> RetrievalMetrics:
        precision_sum = 0.0
        precision_n = 0
        recall_sum = 0.0
        recall_n = 0
        grounding_correct = 0

        quality_dist: dict[str, int] = {}
        for r in results:
            q = r.grounding_quality or ("none" if not r.grounding_searched else "unknown")
            quality_dist[q] = quality_dist.get(q, 0) + 1

            if r.grounding_searched == r.case.expected_grounding:
                grounding_correct += 1

            actual = set(r.retrieval_sources)
            expected = set(self._expected_sources(r.case))

            if actual and expected:
                inter = actual & expected
                precision_sum += len(inter) / len(actual)
                precision_n += 1
            if expected:
                inter = actual & expected
                recall_sum += len(inter) / len(expected)
                recall_n += 1

        return RetrievalMetrics(
            precision=precision_sum / precision_n if precision_n else 0.0,
            recall=recall_sum / recall_n if recall_n else 0.0,
            source_quality_distribution=quality_dist,
            grounding_accuracy=grounding_correct / len(results),
        )

    @staticmethod
    def _expected_sources(case: "BenchmarkCase") -> list[str]:
        if case.expected_sources:
            return case.expected_sources
        if case.expected_grounding:
            return ["web"]
        return []

    # ── answer ──────────────────────────────────────────────────────────

    def answer_metrics(self, results: list[CaseResult]) -> AnswerMetrics:
        scored = [r for r in results if r.case.expected_answer_contains]
        if not scored:
            return AnswerMetrics()

        all_present = 0
        completeness_sum = 0.0
        for r in scored:
            present = [p for p in r.case.expected_answer_contains
                       if p.lower() in (r.answer or "").lower()]
            if len(present) == len(r.case.expected_answer_contains):
                all_present += 1
            completeness_sum += len(present) / len(r.case.expected_answer_contains)

        relevance = 0.0
        hallucination = 0.0
        judged = self.answer_judge is not None
        if judged:
            scores = [self.answer_judge.score(r.answer, r.case) for r in results]
            relevance = sum(s[0] for s in scores) / len(scores) if scores else 0.0
            hallucination = sum(s[1] for s in scores) / len(scores) if scores else 0.0

        return AnswerMetrics(
            correctness=all_present / len(scored),
            completeness=completeness_sum / len(scored),
            relevance=relevance,
            hallucination_rate=hallucination,
            judged=judged,
        )

    # ── tools ───────────────────────────────────────────────────────────

    def tool_metrics(self, results: list[CaseResult]) -> ToolMetrics:
        total_calls = 0
        successful = 0
        efficiency_sum = 0.0
        recovered = 0
        for r in results:
            total_calls += len(r.tool_calls)
            successful += sum(1 for t in r.tool_calls if t.get("success"))
            budget = max(1, r.max_steps) if r.max_steps else 1
            efficiency_sum += max(0.0, 1.0 - len(r.tool_calls) / budget)
            if r.recovery_attempts > 0:
                recovered += 1

        return ToolMetrics(
            success_rate=successful / total_calls if total_calls else 1.0,
            efficiency=efficiency_sum / len(results),
            recovery_rate=recovered / len(results),
            avg_tool_calls=total_calls / len(results),
        )

    # ── research (Phase 8E — deterministic, judge-free) ──────────────────

    def research_metrics(self, results: list[CaseResult]) -> ResearchMetrics:
        citation_total = 0
        citation_valid = 0
        evidence_cases = 0
        cited_cases = 0
        weak_evidence = 0
        honest_weak = 0
        conflict_cases = 0
        acknowledged = 0
        unnecessary = 0
        searched_cases = 0
        manifest_sum = 0
        search_sum = 0

        for r in results:
            total = len(r.citations)
            valid = total - len(r.invalid_citations)
            citation_total += total
            citation_valid += max(0, valid)

            has_evidence = r.manifest_size > 0
            if has_evidence:
                evidence_cases += 1
                if any(c not in r.invalid_citations for c in r.citations):
                    cited_cases += 1

            # Weak evidence = no/failed retrieval yet an answer was produced.
            if not has_evidence and (r.answer or "").strip():
                weak_evidence += 1
                if r.disclosed_insufficient:
                    honest_weak += 1

            if r.conflicts_surfaced > 0:
                conflict_cases += 1
                if r.conflict_acknowledged:
                    acknowledged += 1

            limit = r.case.expected_max_searches
            if limit and r.searches > limit:
                unnecessary += 1
            if limit or r.searches:
                searched_cases += 1

            manifest_sum += r.manifest_size
            search_sum += r.searches

        n = len(results)
        return ResearchMetrics(
            citation_resolvability=(
                citation_valid / citation_total if citation_total else 0.0),
            citation_coverage=(
                cited_cases / evidence_cases if evidence_cases else 0.0),
            insufficiency_honesty=(
                honest_weak / weak_evidence if weak_evidence else 1.0),
            conflict_acknowledgment=(
                acknowledged / conflict_cases if conflict_cases else 1.0),
            unnecessary_search_rate=(
                unnecessary / searched_cases if searched_cases else 0.0),
            avg_manifest_size=manifest_sum / n,
            avg_searches=search_sum / n,
        )

    # ── coding (Phase 8E — deterministic, fixture-based) ─────────────────

    def coding_metrics(self, results: list[CaseResult]) -> CodingMetrics:
        completed = 0
        verifications = 0
        passed_verifications = 0
        regressions = 0
        repair_attempts = 0
        unnecessary_edits = 0
        tool_failures = 0
        tool_calls = 0
        failing_runs = 0
        staged_repairs = 0

        for r in results:
            if r.task_completed:
                completed += 1
            if r.staged_repair:
                staged_repairs += 1
            verifications += r.verifications
            passed_verifications += r.verifications - r.verification_failures
            repair_attempts += max(0, r.repair_attempts - 1)
            if r.edits > 0 and r.verification_failures == 0:
                unnecessary_edits += 1
            if r.verification_failures > 0:
                failing_runs += 1
            for t in r.tool_calls:
                tool_calls += 1
                if not t.get("success", False):
                    tool_failures += 1
            # Regression within a run: a passing verification followed later
            # by a failing one (repair churn), recorded via first_attempt flag.
            if (r.first_attempt_passed and r.verification_failures > 0):
                regressions += 1

        n = len(results)
        return CodingMetrics(
            task_completion=completed / n if n else 0.0,
            test_pass_rate=(
                passed_verifications / verifications if verifications else 0.0),
            regression_rate=regressions / n if n else 0.0,
            avg_repair_attempts=repair_attempts / n if n else 0.0,
            unnecessary_edit_rate=unnecessary_edits / n if n else 0.0,
            tool_failure_rate=(
                tool_failures / tool_calls if tool_calls else 0.0),
            verification_failure_rate=failing_runs / n if n else 0.0,
            staged_repair_rate=staged_repairs / n if n else 0.0,
        )

    # ── latency ─────────────────────────────────────────────────────────

    @staticmethod
    def latency_metrics(results: list[CaseResult]) -> float:
        return sum(r.latency_ms for r in results) / len(results)


def _default_quality_distribution() -> dict:
    return {g: 0 for g in _QUALITY_GRADES}
