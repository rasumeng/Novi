"""Phase 8E — Evaluation Harness extension tests.

The existing evaluation framework (BenchmarkCase/Dataset/drivers/MetricSet/
RegressionDetector) gains research + coding measurement WITHOUT a second
framework:

Research (deterministic, judge-free)
  - citation resolvability/coverage from the graph's own manifest
  - insufficiency honesty on evidence-less cases
  - conflict acknowledgment
  - unnecessary-search rate against per-case budgets

Coding (offline fixture repos + REAL verification)
  - task completion / test pass rate / repair attempts / unnecessary edits
  - the coding driver runs actual pytest through ToolExecutor — offline,
    reproducible, no network.

MetricSet stays JSON-round-trip stable; RegressionDetector accepts the new
metric families additively.
"""

import json

import pytest

from cozmo.evaluation import (
    BenchmarkCase,
    BenchmarkDataset,
    CodingEvalDriver,
    MetricCollector,
    ResearchEvalDriver,
)
from cozmo.evaluation.metrics import CaseResult


# ── research metrics ──────────────────────────────────────────────────────


def _research_case(id="RES-T1", grounding=True, max_searches=2):
    return BenchmarkCase(
        id=id, input=f"research question {id}",
        expected_intent="research", expected_grounding=grounding,
        expected_max_searches=max_searches)


def test_research_driver_cites_and_resolves():
    driver = ResearchEvalDriver()
    case = _research_case(grounding=True)
    r = driver.run(case)

    assert r.manifest_size == 3, "manifest built from the stub's real results"
    assert r.citations, "scripted model must cite manifest sources"
    assert not r.invalid_citations
    assert r.searches >= 1


def test_research_driver_honest_insufficiency():
    driver = ResearchEvalDriver()
    r = driver.run(_research_case(id="RES-WEAK", grounding=False))
    assert r.disclosed_insufficient is True
    assert r.manifest_size == 0


def test_research_metrics_collected():
    driver = ResearchEvalDriver()
    results = [driver.run(_research_case("RES-A", grounding=True)),
               driver.run(_research_case("RES-B", grounding=False))]
    ms = MetricCollector().collect(results)

    assert ms.research.citation_resolvability == 1.0
    assert ms.research.insufficiency_honesty == 1.0
    assert ms.n == 2


def test_unnecessary_search_metric_flags_budget_overruns():
    collector = MetricCollector()

    def _case_result(searches, budget):
        c = _research_case(max_searches=budget)
        r = CaseResult(case=c, intent="research")
        r.searches = searches
        return r

    ms = collector.collect([_case_result(4, 2), _case_result(1, 2)])
    assert ms.research.unnecessary_search_rate == pytest.approx(0.5)


def test_conflict_acknowledgment_defaults_true_without_conflicts():
    collector = MetricCollector()
    c = _research_case()
    r = CaseResult(case=c, intent="research")
    r.conflicts_surfaced = 0
    ms = collector.collect([r])
    assert ms.research.conflict_acknowledgment == 1.0


# ── metricset round-trip stability ────────────────────────────────────────


def test_metricset_json_round_trip_includes_new_families():
    from cozmo.evaluation.metrics import MetricSet

    ms = MetricSet()
    d = ms.to_dict()
    assert "research" in d and "coding" in d
    ms2 = MetricSet.from_dict(json.loads(json.dumps(d)))
    assert ms2.to_dict() == d


def test_regression_detector_accepts_new_families_additively():
    from cozmo.evaluation import RegressionDetector
    from cozmo.evaluation.metrics import (
        CodingMetrics, MetricSet, ResearchMetrics, RetrievalMetrics,
    )

    base = MetricSet(retrieval=RetrievalMetrics(precision=0.8),
                     research=ResearchMetrics(citation_resolvability=1.0),
                     coding=CodingMetrics(task_completion=0.9), latency=500.0)
    cand = MetricSet(retrieval=RetrievalMetrics(precision=0.6),
                     research=ResearchMetrics(citation_resolvability=1.0),
                     coding=CodingMetrics(task_completion=0.9), latency=510.0)
    report = RegressionDetector().compare(base, cand)
    assert not report.passed
    regressed = {f.metric for f in report.regressions}
    assert "retrieval.precision" in regressed
    # New families default to no thresholds → never spuriously flagged.
    assert not any(m.startswith(("research.", "coding.")) for m in regressed)


# ── coding driver: REAL verification through fixture repos ────────────────


def test_coding_driver_staged_repair_completes(tmp_path):
    driver = CodingEvalDriver()
    case = BenchmarkCase(
        id="COD-T1", input="implement add",
        expected_intent="coding",
        fixture={
            "files": {"app.py": "def add(a, b):\n    raise NotImplementedError\n"},
            "test_file": "from app import add\n\ndef test_add():\n"
                         "    assert add(1, 2) == 3\n",
            "solution": {"app.py": "def add(a, b):\n    return a + b\n"},
            "delay_solution": True,
            "expect_pass": True,
        })
    r = driver.run(case)

    assert r.verification_failures >= 1, "first verify must fail pre-solution"
    assert r.repair_attempts == 2
    assert r.task_completed is True
    assert r.verifications == 2


def test_coding_driver_first_attempt_pass_no_repairs(tmp_path):
    driver = CodingEvalDriver()
    case = BenchmarkCase(
        id="COD-T2", input="already correct",
        expected_intent="coding",
        fixture={
            "files": {"app.py": "def mul(a, b):\n    return a * b\n"},
            "test_file": "from app import mul\n\ndef test_mul():\n"
                         "    assert mul(2, 3) == 6\n",
            "delay_solution": False,
            "expect_pass": True,
        })
    r = driver.run(case)
    assert r.first_attempt_passed is True
    assert r.repair_attempts == 1
    assert r.task_completed is True


def test_coding_driver_retry_limit_and_honest_failure():
    driver = CodingEvalDriver()
    case = BenchmarkCase(
        id="COD-T3", input="impossible fixture",
        expected_intent="coding",
        fixture={
            "files": {"app.py": "value = 1\n"},
            "test_file": "from app import value\n\ndef test_value():\n"
                         "    assert value == 999999\n",
            "expect_pass": False,
        })
    r = driver.run(case)
    assert r.task_completed is True  # honest outcome MATCHES expectation
    assert r.repair_attempts <= 2, "retry budget enforced"


def test_coding_driver_is_offline_reproducible():
    """Same case twice → identical structural metrics (latency aside)."""
    driver = CodingEvalDriver()
    case = BenchmarkCase(
        id="COD-T4", input="deterministic run",
        expected_intent="coding",
        fixture={
            "files": {},
            "test_file": "def test_ok():\n    assert True\n",
            "delay_solution": False,
            "expect_pass": True,
        })
    a, b = driver.run(case), driver.run(case)
    for attr in ("verifications", "verification_failures", "repair_attempts",
                 "edits", "changed_files", "task_completed"):
        assert getattr(a, attr) == getattr(b, attr), attr


# ── CLI wiring ────────────────────────────────────────────────────────────


def test_cli_registers_research_and_coding_commands():
    from cozmo.evaluation.__main__ import main
    import argparse

    parser = argparse.ArgumentParser()
    # Smoke: both commands parse and target their corpora by default.
    with pytest.raises(SystemExit) as exc:
        main(["research", "--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        main(["coding", "--help"])
    assert exc.value.code == 0


def test_corpus_files_load_through_benchmark_dataset():
    from pathlib import Path

    research = BenchmarkDataset.from_json(
        str(Path(__file__).parent / "research_corpus.json"))
    coding = BenchmarkDataset.from_json(
        str(Path(__file__).parent / "coding_corpus.json"))
    assert len(research) >= 3
    assert all(c.expected_intent == "research" for c in research)
    assert len(coding) >= 2
    assert all(c.fixture for c in coding)
