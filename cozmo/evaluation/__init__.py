"""Evaluation — measurement infrastructure for Cozmo (Phase 8).

Consumes runtime outputs (ExecutionTrace, orchestrator analysis) to produce
metrics, benchmark results, and regression reports. Never controls runtime
execution. Replaced Evaluators remain pluggable through EvaluationDriver.
"""

from .benchmark import BenchmarkCase, BenchmarkDataset
from .drivers import (
    AnalysisDriver,
    CodingEvalDriver,
    EvaluationDriver,
    ResearchEvalDriver,
    RuntimeDriver,
)
from .evidence_ab import (
    EvidenceABCompare,
    EvidenceABReport,
    EvidenceCase,
    EvidenceDataset,
    compare_evidence_ab,
    run_evidence_ab,
)
from .metrics import (
    AnswerMetrics,
    CaseResult,
    CodingMetrics,
    MetricCollector,
    MetricSet,
    RetrievalMetrics,
    ResearchMetrics,
    ToolMetrics,
)
from .regression import (
    RegressionDetector,
    RegressionFinding,
    RegressionReport,
)
from .runner import EvaluationResult, EvaluationRunner
from .trace_collector import TraceCollector

__all__ = [
    "AnalysisDriver",
    "AnswerMetrics",
    "BenchmarkCase",
    "BenchmarkDataset",
    "CaseResult",
    "CodingEvalDriver",
    "CodingMetrics",
    "EvaluationDriver",
    "EvaluationResult",
    "EvaluationRunner",
    "EvidenceABCompare",
    "EvidenceABReport",
    "EvidenceCase",
    "EvidenceDataset",
    "MetricCollector",
    "MetricSet",
    "RegressionDetector",
    "RegressionFinding",
    "RegressionReport",
    "ResearchEvalDriver",
    "ResearchMetrics",
    "RetrievalMetrics",
    "RuntimeDriver",
    "ToolMetrics",
    "TraceCollector",
    "compare_evidence_ab",
    "run_evidence_ab",
]
