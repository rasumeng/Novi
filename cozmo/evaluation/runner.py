"""EvaluationRunner — orchestrates benchmark execution, metric collection,
and comparison.

Flow: dataset → driver.run(case) per case → MetricCollector → EvaluationResult
Comparison: baseline vs candidate MetricSet → RegressionDetector → RegressionReport
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .benchmark import BenchmarkDataset
from .drivers import AnalysisDriver, EvaluationDriver
from .metrics import AnswerJudge, CaseResult, MetricCollector, MetricSet
from .regression import RegressionDetector, RegressionReport


@dataclass
class EvaluationResult:
    """Full output of one evaluation run."""

    dataset_name: str
    metrics: MetricSet
    driver_name: str = ""
    per_case: list[CaseResult] = field(default_factory=list)
    ran_at: str = ""
    duration_s: float = 0.0

    def __post_init__(self):
        if not self.ran_at:
            self.ran_at = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset_name,
            "driver": self.driver_name,
            "ran_at": self.ran_at,
            "duration_s": round(self.duration_s, 2),
            "metrics": self.metrics.to_dict(),
            "per_case": [c.to_dict() for c in self.per_case],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "EvaluationResult":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            dataset_name=raw.get("dataset", ""),
            metrics=MetricSet.from_dict(raw.get("metrics", {})),
            driver_name=raw.get("driver", ""),
            ran_at=raw.get("ran_at", ""),
            duration_s=float(raw.get("duration_s", 0.0)),
            per_case=[
                _case_result_from_dict(c) for c in raw.get("per_case", [])
            ],
        )


class EvaluationRunner:
    """Runs benchmark datasets through a driver and reports metrics."""

    def __init__(
        self,
        driver: EvaluationDriver | None = None,
        collector: MetricCollector | None = None,
        detector: RegressionDetector | None = None,
    ):
        self.driver = driver or AnalysisDriver()
        self.collector = collector or MetricCollector()
        self.detector = detector or RegressionDetector()

    def run(
        self,
        dataset: BenchmarkDataset,
        limit: int | None = None,
        filter_tags: list[str] | None = None,
        answer_judge: AnswerJudge | None = None,
    ) -> EvaluationResult:
        cases = dataset.cases
        if filter_tags:
            wanted = set(filter_tags)
            cases = [c for c in cases if wanted & set(c.tags)]
        if limit is not None:
            cases = cases[: int(limit)]

        started = datetime.now()
        if answer_judge is not None and self.collector.answer_judge is None:
            self.collector = MetricCollector(answer_judge=answer_judge)
        results: list[CaseResult] = []
        for case in cases:
            result = self.driver.run(case)
            results.append(result)

        duration = (datetime.now() - started).total_seconds()
        return EvaluationResult(
            dataset_name=dataset.name,
            metrics=self.collector.collect(results),
            driver_name=getattr(self.driver, "name", ""),
            per_case=results,
            duration_s=duration,
        )

    def compare(
        self,
        baseline: MetricSet | EvaluationResult | dict | str | Path,
        candidate: MetricSet | EvaluationResult | dict | str | Path,
        baseline_name: str = "baseline",
        candidate_name: str = "candidate",
    ) -> RegressionReport:
        """Compare two metric sets; accepts MetricSet, EvaluationResult,
        serialized dict, or a JSON file path."""
        b = self._as_metric_set(baseline)
        c = self._as_metric_set(candidate)
        return self.detector.compare(
            b, c, baseline_name=baseline_name, candidate_name=candidate_name
        )

    @staticmethod
    def _as_metric_set(
        value: MetricSet | EvaluationResult | dict | str | Path,
    ) -> MetricSet:
        if isinstance(value, MetricSet):
            return value
        if isinstance(value, EvaluationResult):
            return value.metrics
        if isinstance(value, dict):
            return MetricSet.from_dict(value)
        raw: dict[str, Any] = json.loads(Path(value).read_text(encoding="utf-8"))
        if "metrics" in raw:  # EvaluationResult serialization
            raw = raw["metrics"]
        return MetricSet.from_dict(raw)


def _case_result_from_dict(d: dict) -> CaseResult:
    from .benchmark import BenchmarkCase

    case = BenchmarkCase(
        id=d.get("id", ""),
        input=d.get("input", ""),
    )
    return CaseResult(
        case=case,
        intent=d.get("intent", ""),
        strategy=d.get("strategy", ""),
        capabilities=list(d.get("capabilities", []) or []),
        grounding_searched=bool(d.get("grounding_searched", False)),
        grounding_quality=d.get("grounding_quality", ""),
        retrieval_sources=list(d.get("retrieval_sources", []) or []),
        retrieval_strategy=d.get("retrieval_strategy", ""),
        answer=d.get("answer", ""),
        max_steps=int(d.get("max_steps", 0)),
        recovery_attempts=int(d.get("recovery_attempts", 0)),
        tool_calls=list(d.get("tool_calls", []) or []),
        latency_ms=float(d.get("latency_ms", 0.0)),
        error=d.get("error"),
    )
