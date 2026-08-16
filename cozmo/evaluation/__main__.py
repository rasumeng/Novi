"""Phase 8 evaluation CLI.

Usage:
    python -m cozmo.evaluation analyze  [--dataset PATH] [--limit N] [--save PATH]
    python -m cozmo.evaluation runtime   [--dataset PATH] [--limit N] [--timeout S] [--save PATH]
    python -m cozmo.evaluation compare BASELINE.json CANDIDATE.json
    python -m cozmo.evaluation evidence     [--dataset PATH] [--limit N] [--model NAME] [--judge-model NAME] [--workload WORKLOAD] [--save PATH]
    python -m cozmo.evaluation evidence-compare BASELINE.json CANDIDATE.json

analyze  — offline orchestrator decision evaluation (no model required)
runtime  — full runtime evaluation (requires a configured model)
compare  — before/after regression report between two saved results
evidence — Phase 7 grounding A/B: merged_text vs EvidenceContext.summary vs none
evidence-compare — diff two saved evidence reports per grounding mode
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _print_summary(result) -> None:
    m = result.metrics
    print("=" * 60)
    print(f"Evaluation: {result.dataset_name}  driver={result.driver_name}")
    print(f"  cases:     {m.n}")
    print(f"  duration:  {result.duration_s:.2f}s")
    print("-" * 60)
    print(f"Retrieval:  precision={m.retrieval.precision:.3f}  "
          f"recall={m.retrieval.recall:.3f}  "
          f"grounding_acc={m.retrieval.grounding_accuracy:.3f}")
    print(f"            quality={dict(m.retrieval.source_quality_distribution)}")
    print(f"Answer:     correctness={m.answer.correctness:.3f}  "
          f"completeness={m.answer.completeness:.3f}"
          + (f"  (judged: rel={m.answer.relevance:.3f} hall={m.answer.hallucination_rate:.3f})"
             if m.answer.judged else "  (not LLM-judged)"))
    print(f"Tools:      success={m.tools.success_rate:.3f}  "
          f"efficiency={m.tools.efficiency:.3f}  "
          f"recovery={m.tools.recovery_rate:.3f}  "
          f"avg_calls={m.tools.avg_tool_calls:.2f}")
    print(f"Latency:    {m.latency:.0f}ms avg")
    print("=" * 60)


def _print_report(report) -> None:
    print(f"Regression report: {report.baseline_name} -> {report.candidate_name}")
    print(f"  verdict: {'PASS' if report.passed else 'FAIL'}")
    for f in report.findings:
        arrow = "▼" if f.severity == "regression" else "▲" if f.severity == "improvement" else "·"
        print(f"  {arrow} {f.metric:28s} {f.baseline:7.3f} -> {f.candidate:7.3f}  ({f.severity})")


def _cmd_analyze(args) -> int:
    from .benchmark import BenchmarkDataset
    from .drivers import AnalysisDriver
    from .runner import EvaluationRunner

    dataset = BenchmarkDataset.from_json(args.dataset)
    runner = EvaluationRunner(driver=AnalysisDriver())
    result = runner.run(dataset, limit=args.limit)
    _print_summary(result)
    if args.save:
        result.save(args.save)
        print(f"Saved: {args.save}")
    return 0


def _cmd_runtime(args) -> int:
    from .benchmark import BenchmarkDataset
    from .drivers import RuntimeDriver
    from .runner import EvaluationRunner

    dataset = BenchmarkDataset.from_json(args.dataset)
    driver = RuntimeDriver(timeout_s=args.timeout)
    try:
        runner = EvaluationRunner(driver=driver)
        result = runner.run(dataset, limit=args.limit)
        _print_summary(result)
        if args.save:
            result.save(args.save)
            print(f"Saved: {args.save}")
        return 0
    finally:
        driver.close()


def _cmd_compare(args) -> int:
    from .runner import EvaluationRunner

    runner = EvaluationRunner()
    report = runner.compare(
        args.baseline,
        args.candidate,
        baseline_name=Path(args.baseline).stem,
        candidate_name=Path(args.candidate).stem,
    )
    _print_report(report)
    return 0 if report.passed else 1


def _cmd_evidence(args) -> int:
    from .evidence_ab import MODES, EvidenceDataset, run_evidence_ab
    from ..services.context import CozmoContext

    dataset = EvidenceDataset.from_json(args.dataset)
    if args.limit:
        dataset.cases = dataset.cases[: args.limit]

    svc = CozmoContext().model_service
    client = (
        svc.client_for_model(args.model, 0.0)
        if args.model
        else svc.client(args.workload, 0.0)
    )
    judge = svc.client_for_model(args.judge_model, 0.0) if args.judge_model else None
    model_name = args.model or svc.resolve(args.workload)[1]

    report = run_evidence_ab(dataset, client, judge_client=judge, model_name=model_name)
    _print_evidence(report)
    if args.save:
        report.save(args.save)
        print(f"Saved: {args.save}")
    return 0


def _print_evidence(report) -> None:
    from .evidence_ab import MODES

    agg = report.aggregate()
    print("=" * 60)
    print(f"Evidence A/B: {report.dataset}  model={report.model}")
    print(f"  cases: {agg['cases']}  compression={agg['compression']:.1%}  "
          f"fallback={agg['fallback_count']}  avg_conf={agg['avg_confidence']}")
    print("-" * 60)
    for m in MODES:
        print(f"  {m:10s} recall={agg['recall'][m]:.3f}  "
              f"grounded={agg['grounded'][m]:.3f}")
    print(f"  verdict: {report.verdict()}")
    print("=" * 60)


def _cmd_evidence_compare(args) -> int:
    from .evidence_ab import MODES, compare_evidence_ab

    comp = compare_evidence_ab(args.baseline, args.candidate)
    print(f"Evidence compare: {comp.baseline} -> {comp.candidate}")
    print(f"  verdict: {'PASS' if comp.passed else 'FAIL'}")
    for mode in MODES:
        d = comp.deltas[mode]
        print(f"  {mode:10s} recall {d['recall']['base']:.3f} -> {d['recall']['cand']:.3f} "
              f"({d['recall']['delta']:+.3f})  "
              f"grounded {d['grounded']['base']:.3f} -> {d['grounded']['cand']:.3f}")
    dc = comp.deltas["compression"]
    print(f"  compression {dc['base']:.1%} -> {dc['cand']:.1%} ({dc['delta']:+.1%})")
    return 0 if comp.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cozmo.evaluation", description="Cozmo Phase 8 evaluation tooling"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="offline orchestrator evaluation")
    p_analyze.add_argument("--dataset", default="tests/regression_corpus.json")
    p_analyze.add_argument("--limit", type=int, default=None)
    p_analyze.add_argument("--save")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_runtime = sub.add_parser("runtime", help="full runtime evaluation")
    p_runtime.add_argument("--dataset", default="tests/regression_corpus.json")
    p_runtime.add_argument("--limit", type=int, default=None)
    p_runtime.add_argument("--timeout", type=float, default=120.0)
    p_runtime.add_argument("--save")
    p_runtime.set_defaults(func=_cmd_runtime)

    p_compare = sub.add_parser("compare", help="before/after regression report")
    p_compare.add_argument("baseline")
    p_compare.add_argument("candidate")
    p_compare.set_defaults(func=_cmd_compare)

    p_ev = sub.add_parser(
        "evidence",
        help="grounding A/B: merged_text vs EvidenceContext.summary vs none",
    )
    p_ev.add_argument("--dataset", default="tests/evidence_corpus.json")
    p_ev.add_argument("--limit", type=int, default=None)
    p_ev.add_argument("--model", help="model name override (default: --workload)")
    p_ev.add_argument("--judge-model", help="model used for groundedness judging")
    p_ev.add_argument("--workload", default="general",
                      help="workload to resolve when no --model (general/research/code)")
    p_ev.add_argument("--save")
    p_ev.set_defaults(func=_cmd_evidence)

    p_evc = sub.add_parser(
        "evidence-compare", help="diff two saved evidence A/B reports"
    )
    p_evc.add_argument("baseline")
    p_evc.add_argument("candidate")
    p_evc.set_defaults(func=_cmd_evidence_compare)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
