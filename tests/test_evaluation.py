"""Phase 8 evaluation infrastructure tests.

Covers: benchmark dataset loading, metric computation, regression
detection, trace collection, the evaluation runner, and boundary rules.
"""

import ast
import json
from pathlib import Path

import pytest

from novi.evaluation import (
    AnalysisDriver,
    BenchmarkCase,
    BenchmarkDataset,
    EvaluationRunner,
    MetricCollector,
    MetricSet,
    RegressionDetector,
    TraceCollector,
)
from novi.evaluation.metrics import AnswerMetrics, CaseResult, RetrievalMetrics, ToolMetrics

CORPUS_PATH = Path(__file__).parent / "regression_corpus.json"


# ── Benchmark dataset ────────────────────────────────────────────────────


class TestBenchmarkDataset:
    def test_legacy_corpus_loads(self):
        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        assert len(ds) >= 50, "Phase 8 success criterion: 50+ benchmark cases"

    def test_covers_all_intents(self):
        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        intents = {c.expected_intent for c in ds}
        # Offline intent detection produces conversation/research/coding/vision;
        # planning intent is LLM-only, so planning is covered as a capability.
        assert {"conversation", "research", "coding", "vision"} <= intents
        planning_caps = any("planning" in c.expected_capabilities for c in ds)
        assert planning_caps

    def test_covers_retrieval_strategies(self):
        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        sources = {s for c in ds for s in c.expected_sources}
        assert "web" in sources
        assert "memory" in sources

    def test_legacy_field_mapping(self):
        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        by_id = {c.id: c for c in ds}
        # evidence_external → expected_grounding
        assert by_id["REG-007"].expected_grounding is True
        # evidence_memory does NOT imply web grounding
        assert by_id["REG-023"].expected_grounding is False
        # new-style memory cases carry explicit source ground truth
        assert by_id["REG-043"].expected_sources == ["memory"]
        assert by_id["REG-043"].expected_evidence_memory is True
        # vision case flag
        assert by_id["REG-054"].has_images is True
        assert by_id["REG-054"].expected_intent == "vision"
        # answer ground truth present on some cases
        assert by_id["REG-039"].expected_answer_contains == ["superposition"]

    def test_versioned_round_trip(self, tmp_path):
        ds = BenchmarkDataset.from_json(CORPUS_PATH, name="rt")
        out = tmp_path / "ds.json"
        ds.save(out)
        loaded = BenchmarkDataset.from_json(out)
        assert loaded.name == "rt"
        assert len(loaded) == len(ds)
        assert loaded.cases[0].to_dict() == ds.cases[0].to_dict()

    def test_unsupported_schema_rejected(self, tmp_path):
        p = tmp_path / "future.json"
        p.write_text(json.dumps({"_meta": {"schema": 99}, "cases": []}))
        with pytest.raises(ValueError):
            BenchmarkDataset.from_json(p)

    def test_by_tags(self):
        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        mem = ds.by_tags("memory")
        assert len(mem) >= 3
        assert all("memory" in c.tags for c in mem)


# ── Metric computation ───────────────────────────────────────────────────


def _result(case, **kw):
    defaults = dict(intent="conversation", strategy="respond")
    defaults.update(kw)
    return CaseResult(case=case, **defaults)


class TestMetricCollector:
    def test_retrieval_precision_recall(self):
        collector = MetricCollector()
        cases = [
            # perfect web retrieval
            BenchmarkCase("a", "q", expected_grounding=True, expected_sources=["web"]),
            # retrieved web but expected memory → recall miss, precision miss
            BenchmarkCase("b", "q", expected_grounding=False, expected_sources=["memory"]),
            # expected nothing, retrieved nothing
            BenchmarkCase("c", "q", expected_grounding=False),
            # expected web but retrieved nothing → recall miss, no precision term
            BenchmarkCase("d", "q", expected_grounding=True, expected_sources=["web"]),
        ]
        results = [
            _result(cases[0], grounding_searched=True, retrieval_sources=["web"]),
            _result(cases[1], grounding_searched=False, retrieval_sources=["web"]),
            _result(cases[2], grounding_searched=False, retrieval_sources=[]),
            _result(cases[3], grounding_searched=False, retrieval_sources=[]),
        ]
        m = collector.retrieval_metrics(results)
        # precision: a=1/1, b=0/1 (retrieved web, expected memory) → 0.5
        assert m.precision == pytest.approx(0.5)
        # recall: a=1/1, b=0/1, d=0/1 → 1/3
        assert m.recall == pytest.approx(1 / 3)
        # grounding accuracy: a T=True ✓, b F=False ✓, c F=False ✓, d T=False ✗ → 3/4
        assert m.grounding_accuracy == pytest.approx(0.75)

    def test_answer_metrics(self):
        collector = MetricCollector()
        c1 = BenchmarkCase("a", "q", expected_answer_contains=["alpha", "beta"])
        c2 = BenchmarkCase("b", "q", expected_answer_contains=["gamma"])
        c3 = BenchmarkCase("c", "q")  # unscored
        results = [
            _result(c1, answer="ALPHA and beta here"),  # both present
            _result(c2, answer="nothing relevant"),      # 0/1
            _result(c3, answer="irrelevant"),
        ]
        m = collector.answer_metrics(results)
        assert m.correctness == pytest.approx(0.5)
        assert m.completeness == pytest.approx(0.5)  # (1.0 + 0.0)/2
        assert m.judged is False
        assert m.relevance == 0.0

    def test_tool_metrics(self):
        collector = MetricCollector()
        c1 = BenchmarkCase("a", "q")
        c2 = BenchmarkCase("b", "q")
        results = [
            _result(
                c1,
                max_steps=5,
                recovery_attempts=1,
                tool_calls=[
                    {"name": "read", "success": True, "latency_ms": 10},
                    {"name": "bash", "success": False, "latency_ms": 20},
                ],
            ),
            _result(c2, max_steps=5, tool_calls=[]),
        ]
        m = collector.tool_metrics(results)
        assert m.success_rate == pytest.approx(0.5)
        assert m.recovery_rate == pytest.approx(0.5)
        assert m.avg_tool_calls == pytest.approx(1.0)
        assert m.efficiency == pytest.approx((1 - 2 / 5 + 1 - 0) / 2)

    def test_metric_set_serialization(self):
        ms = MetricSet(
            retrieval=RetrievalMetrics(precision=0.9, recall=0.8,
                                        source_quality_distribution={"web": 3},
                                        grounding_accuracy=0.9),
            answer=AnswerMetrics(correctness=0.7, completeness=0.8),
            tools=ToolMetrics(success_rate=1.0, efficiency=0.9),
            latency=123.4,
            n=10,
        )
        restored = MetricSet.from_dict(ms.to_dict())
        assert restored == ms

    def test_empty_collect(self):
        collector = MetricCollector()
        ms = collector.collect([])
        assert ms.n == 0
        assert ms.latency == 0.0


# ── Regression detection ─────────────────────────────────────────────────


def _mk(retr=1.0, recall=1.0, acc=1.0, succ=1.0, eff=1.0, recov=0.0, latency=100.0):
    return MetricSet(
        retrieval=RetrievalMetrics(precision=retr, recall=recall, grounding_accuracy=acc),
        tools=ToolMetrics(success_rate=succ, efficiency=eff, recovery_rate=recov),
        latency=latency,
        n=10,
    )


class TestRegressionDetector:
    def test_flags_regression(self):
        detector = RegressionDetector()
        report = detector.compare(_mk(), _mk(acc=0.8), "base", "cand")
        assert report.passed is False
        assert any(
            f.metric == "retrieval.grounding_accuracy"
            and f.severity == "regression"
            for f in report.regressions
        )

    def test_flags_improvement(self):
        detector = RegressionDetector()
        report = detector.compare(_mk(recall=0.5), _mk(recall=0.9), "base", "cand")
        assert report.passed
        assert any(f.metric == "retrieval.recall" and f.severity == "improvement"
                   for f in report.improvements)

    def test_latency_regression_is_relative(self):
        detector = RegressionDetector()
        report = detector.compare(_mk(latency=100.0), _mk(latency=200.0), "base", "cand")
        assert any(f.metric == "latency" and f.severity == "regression"
                   for f in report.regressions)
        # 10% is within default threshold
        report2 = detector.compare(_mk(latency=100.0), _mk(latency=105.0), "b", "c")
        assert not any(f.metric == "latency" and f.severity == "regression"
                       for f in report2.regressions)

    def test_recovery_increase_is_regression(self):
        detector = RegressionDetector()
        report = detector.compare(_mk(recov=0.0), _mk(recov=0.4), "b", "c")
        assert any(f.metric == "tools.recovery_rate" and f.severity == "regression"
                   for f in report.regressions)

    def test_unchanged(self):
        detector = RegressionDetector()
        report = detector.compare(_mk(), _mk(), "b", "c")
        assert report.passed
        assert all(f.severity == "unchanged" for f in report.findings)

    def test_report_serialization(self):
        detector = RegressionDetector()
        report = detector.compare(_mk(), _mk(acc=0.5), "b", "c")
        d = report.to_dict()
        assert d["passed"] is False
        assert d["regressions"]
        assert d["baseline"] == "b"


# ── Trace collection ─────────────────────────────────────────────────────


class TestTraceCollector:
    def test_record_and_access(self):
        tc = TraceCollector()
        tc.record({"request_id": "1", "intent": "coding"})
        assert len(tc) == 1
        assert tc.last == {"request_id": "1", "intent": "coding"}

    def test_accepts_trace_object(self):
        from novi.runtime.trace import ExecutionTrace

        tc = TraceCollector()
        trace = ExecutionTrace(user_input="hello", intent="coding")
        tc.record(trace)
        assert tc.last["intent"] == "coding"

    def test_bounded(self):
        tc = TraceCollector(max_traces=3)
        for i in range(10):
            tc.record({"request_id": str(i)})
        assert len(tc) == 3
        assert [t["request_id"] for t in tc.traces] == ["7", "8", "9"]

    def test_clear(self):
        tc = TraceCollector()
        tc.record({"a": 1})
        tc.clear()
        assert len(tc) == 0
        assert tc.last is None

    def test_event_bus_attach(self):
        from novi.runtime.event_bus import EventBus, EventType

        bus = EventBus()
        tc = TraceCollector(event_bus=bus)
        bus.emit(EventType.TRACE_COMPLETED, trace={"request_id": "evt-1"})
        assert tc.last == {"request_id": "evt-1"}

    def test_ignores_invalid_records(self):
        tc = TraceCollector()
        tc.record(None)
        tc.record(42)
        assert len(tc) == 0


# ── Evaluation runner ────────────────────────────────────────────────────


class TestEvaluationRunner:
    def test_full_analysis_run(self):
        from novi.evaluation.drivers import AnalysisDriver

        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        runner = EvaluationRunner(driver=AnalysisDriver())
        result = runner.run(ds)
        assert result.metrics.n == len(ds)
        assert result.driver_name == "analysis"
        assert len(result.per_case) == len(ds)
        # corpus derived from actual orchestrator behavior → high accuracy
        assert result.metrics.retrieval.grounding_accuracy > 0.9
        assert result.metrics.retrieval.recall > 0.7

    def test_limit_and_tags(self):
        from novi.evaluation.drivers import AnalysisDriver

        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        runner = EvaluationRunner(driver=AnalysisDriver())
        r = runner.run(ds, limit=5)
        assert r.metrics.n == 5
        r2 = runner.run(ds, filter_tags=["memory"])
        assert r2.metrics.n == len(ds.by_tags("memory"))

    def test_compare_accepts_results_and_files(self, tmp_path):
        from novi.evaluation.drivers import AnalysisDriver

        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        runner = EvaluationRunner(driver=AnalysisDriver())
        base = runner.run(ds)
        cand = runner.run(ds)
        base.save(tmp_path / "base.json")
        cand.save(tmp_path / "cand.json")

        report = runner.compare(base, cand, "base", "cand")
        assert report.passed
        report2 = runner.compare(tmp_path / "base.json", tmp_path / "cand.json")
        assert report2.passed
        # identical metric sets → unchanged findings
        assert all(f.severity == "unchanged" for f in report2.findings)

    def test_analysis_driver_grounding_match(self):
        from novi.evaluation.drivers import AnalysisDriver

        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        runner = EvaluationRunner(driver=AnalysisDriver())
        result = runner.run(ds)
        # Phase 9 step 5: the memory source is now planned when evidence
        # signals it — retrieval_sources reflects memory (strategy stays NONE
        # for memory-only plans).
        mem = [r for r in result.per_case if "memory" in r.case.expected_sources]
        assert mem
        assert all("memory" in r.retrieval_sources for r in mem)


# ── Runtime driver (fake runtime through RuntimeInterface) ───────────────


class _FakeRuntime:
    """Minimal RuntimeInterface-compatible runtime that emits traces."""

    def __init__(self, event_bus, answer="", fail=False, delay=0.0):
        from novi.runtime.event_bus import EventType

        self.event_bus = event_bus
        self._answer = answer
        self._fail = fail
        self._delay = delay
        self._event_type = EventType

    def run(self, user_input):
        import time

        time.sleep(self._delay)
        if self._fail:
            raise RuntimeError("model unavailable")
        self.event_bus.emit(
            self._event_type.TRACE_COMPLETED,
            trace={
                "intent": "conversation",
                "grounding_searched": False,
                "grounding_quality": "",
                "retrieval_sources": "web",
                "retrieval_strategy": "web_only",
                "recovery_attempts": 0,
                "max_steps": 5,
                "tools_bound": ["web_search"],
                "steps": [
                    {
                        "step": 0,
                        "tool_calls": [
                            {"name": "web_search", "success": True, "latency_ms": 5.0}
                        ],
                    }
                ],
            },
        )
        return self._answer


class TestRuntimeDriver:
    def test_consumes_trace_and_answer(self):
        from novi.evaluation.drivers import RuntimeDriver
        from novi.runtime.event_bus import EventBus

        bus = EventBus()
        fake = _FakeRuntime(bus, answer="a useful answer")
        driver = RuntimeDriver(runtime=fake, event_bus=bus, timeout_s=10)
        case = BenchmarkCase("r1", "query", expected_grounding=True,
                             expected_sources=["web"])
        result = driver.run(case)
        assert result.answer == "a useful answer"
        assert result.trace is not None
        assert result.intent == "conversation"
        assert result.retrieval_sources == ["web"]
        assert result.tool_calls and result.tool_calls[0]["success"] is True
        assert result.error is None
        assert result.latency_ms > 0

    def test_records_failure_as_error(self):
        from novi.evaluation.drivers import RuntimeDriver
        from novi.runtime.event_bus import EventBus

        bus = EventBus()
        fake = _FakeRuntime(bus, fail=True)
        driver = RuntimeDriver(runtime=fake, event_bus=bus, timeout_s=10)
        case = BenchmarkCase("r2", "query")
        result = driver.run(case)
        assert result.error is not None
        assert "error" in result.error

    def test_timeout_guard(self):
        from novi.evaluation.drivers import RuntimeDriver
        from novi.runtime.event_bus import EventBus

        bus = EventBus()
        fake = _FakeRuntime(bus, delay=2.0)
        driver = RuntimeDriver(runtime=fake, event_bus=bus, timeout_s=0.2)
        case = BenchmarkCase("r3", "query")
        result = driver.run(case)
        assert result.error is not None
        assert "timeout" in result.error


# ── Boundary rules ───────────────────────────────────────────────────────


class TestBoundaries:
    def test_evaluation_does_not_import_runtime_internals(self):
        """Evaluation must observe runtime outputs, never reach internals.

        drivers.py is the documented consumer harness (may construct a
        runtime through RuntimeInterface). EventBus (event_bus) is the
        observation layer and is allowed.
        """
        forbidden = [
            "runtime.runtime",
            "runtime.retrieval",
            "runtime.engine",
            "runtime.trace",
            "runtime.tracer",
            "runtime.execution_context",
            "runtime.retrieval_coordinator",
        ]
        ev_dir = Path(__file__).parent.parent / "novi" / "evaluation"
        for py in ev_dir.rglob("*.py"):
            if py.name == "drivers.py":
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
                elif isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
            joined = "\n".join(imports)
            for f in forbidden:
                assert f not in joined, (
                    f"{py.name} imports runtime internals ({f}) — "
                    f"evaluation must not control runtime execution"
                )

    def test_corpus_ids_unique(self):
        ds = BenchmarkDataset.from_json(CORPUS_PATH)
        ids = [c.id for c in ds]
        assert len(ids) == len(set(ids)), "corpus ids must be unique"
