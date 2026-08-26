"""Regression detection — compare metric sets and flag degradations.

Compares a candidate MetricSet against a baseline and produces a
RegressionReport. Thresholds are per-metric absolute deltas; latency uses
a relative degradation. Higher is better for all metrics except
recovery_rate and latency, where an increase is a regression.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import MetricSet

# Default thresholds (absolute delta; latency = relative delta).
# Phase 8 remediation (audit G): research/coding metrics are part of the
# comparison contract so `compare` can flag degradations there too.
DEFAULT_THRESHOLDS = {
    "retrieval.grounding_accuracy": 0.05,
    "retrieval.precision": 0.05,
    "retrieval.recall": 0.05,
    "answer.correctness": 0.05,
    "answer.completeness": 0.05,
    "tools.success_rate": 0.05,
    "tools.efficiency": 0.05,
    "tools.recovery_rate": 0.02,
    "research.citation_resolvability": 0.05,
    "research.citation_coverage": 0.05,
    "research.insufficiency_honesty": 0.05,
    "research.conflict_acknowledgment": 0.05,
    "coding.task_completion": 0.05,
    "coding.test_pass_rate": 0.05,
    "coding.regression_rate": 0.02,
    "coding.verification_failure_rate": 0.02,
    "latency": 0.10,
}

# Metrics where a LOWER value is better.
_LOWER_IS_BETTER = {
    "tools.recovery_rate", "latency",
    "coding.regression_rate", "coding.verification_failure_rate",
}

# Latency regressions require an absolute increase above this floor (ms) in
# addition to the relative threshold — otherwise sub-ms analysis-mode noise
# between identical runs would falsely flag.
LATENCY_ABS_FLOOR_MS = 50.0


@dataclass
class RegressionFinding:
    metric: str
    baseline: float
    candidate: float
    delta: float
    severity: str = "unchanged"  # "regression" | "improvement" | "unchanged"

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "baseline": round(self.baseline, 4),
            "candidate": round(self.candidate, 4),
            "delta": round(self.delta, 4),
            "severity": self.severity,
        }


@dataclass
class RegressionReport:
    baseline_name: str = "baseline"
    candidate_name: str = "candidate"
    findings: list[RegressionFinding] = field(default_factory=list)

    @property
    def regressions(self) -> list[RegressionFinding]:
        return [f for f in self.findings if f.severity == "regression"]

    @property
    def improvements(self) -> list[RegressionFinding]:
        return [f for f in self.findings if f.severity == "improvement"]

    @property
    def passed(self) -> bool:
        return not self.regressions

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline_name,
            "candidate": self.candidate_name,
            "passed": self.passed,
            "regressions": [f.to_dict() for f in self.regressions],
            "improvements": [f.to_dict() for f in self.improvements],
            "findings": [f.to_dict() for f in self.findings],
        }


class RegressionDetector:
    """Compares MetricSets and flags per-metric regressions."""

    def __init__(self, thresholds: dict | None = None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def compare(
        self,
        baseline: MetricSet,
        candidate: MetricSet,
        baseline_name: str = "baseline",
        candidate_name: str = "candidate",
    ) -> RegressionReport:
        findings = [
            self._compare_one(metric, baseline, candidate)
            for metric in self.thresholds
        ]
        findings = [f for f in findings if f is not None]
        return RegressionReport(
            baseline_name=baseline_name,
            candidate_name=candidate_name,
            findings=findings,
        )

    def _compare_one(
        self, metric: str, baseline: MetricSet, candidate: MetricSet
    ) -> RegressionFinding | None:
        b = _metric_value(baseline, metric)
        c = _metric_value(candidate, metric)
        if b is None or c is None:
            return None

        higher_better = metric not in _LOWER_IS_BETTER
        threshold = self.thresholds[metric]

        # Latency degrades on relative increase AND absolute increase beyond a
        # noise floor; other metrics are absolute deltas.
        if metric == "latency":
            change = (c - b) / b if b else 0.0
            abs_change = c - b
            if abs_change <= LATENCY_ABS_FLOOR_MS:
                return RegressionFinding(
                    metric=metric,
                    baseline=b,
                    candidate=c,
                    delta=change,
                    severity="unchanged",
                )
        else:
            change = c - b

        if change < -threshold:
            severity = "regression" if higher_better else "improvement"
        elif change > threshold:
            severity = "improvement" if higher_better else "regression"
        else:
            severity = "unchanged"

        return RegressionFinding(
            metric=metric,
            baseline=b,
            candidate=c,
            delta=change,
            severity=severity,
        )


def _metric_value(ms: MetricSet, metric: str) -> float | None:
    mapping = {
        "retrieval.grounding_accuracy": ms.retrieval.grounding_accuracy,
        "retrieval.precision": ms.retrieval.precision,
        "retrieval.recall": ms.retrieval.recall,
        "answer.correctness": ms.answer.correctness,
        "answer.completeness": ms.answer.completeness,
        "tools.success_rate": ms.tools.success_rate,
        "tools.efficiency": ms.tools.efficiency,
        "tools.recovery_rate": ms.tools.recovery_rate,
        "research.citation_resolvability": ms.research.citation_resolvability,
        "research.citation_coverage": ms.research.citation_coverage,
        "research.insufficiency_honesty": ms.research.insufficiency_honesty,
        "research.conflict_acknowledgment": ms.research.conflict_acknowledgment,
        "coding.task_completion": ms.coding.task_completion,
        "coding.test_pass_rate": ms.coding.test_pass_rate,
        "coding.regression_rate": ms.coding.regression_rate,
        "coding.verification_failure_rate": ms.coding.verification_failure_rate,
        "latency": ms.latency,
    }
    return mapping.get(metric)
