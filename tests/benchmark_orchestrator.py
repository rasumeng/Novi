"""Benchmark the orchestrator analysis pipeline latency.

Measures time for: IntentDetector + EvidenceDetector + ComplexityEstimator
+ CapabilityResolver. This is the overhead before execution begins.
"""

import time

from novi.orchestrator import Orchestrator
from novi.capabilities import CapabilityRegistry
from novi.capabilities.builtin import register_builtin_capabilities

BENCHMARK_QUERIES = [
    "What is the best PVE loadout in Shindo Life?",
    "Explain binary search",
    "Fix the bug in FastAPI using latest docs",
    "Hello, how are you?",
    "What did we decide yesterday?",
    "Plan the architecture for a new feature",
    "Implement React hooks using current best practices",
    "Who won the Super Bowl?",
    "Is RTX 5090 worth buying?",
    "Refactor auth.py to add error handling",
]

WARMUP_ITERATIONS = 100
BENCHMARK_ITERATIONS = 500


def benchmark():
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    orchestrator = Orchestrator(capability_registry=registry)

    # Pre-warm
    for _ in range(WARMUP_ITERATIONS):
        orchestrator.analyze("Hello")

    # Benchmark plan() — full pipeline including capability resolution
    plan_times = []
    for _ in range(BENCHMARK_ITERATIONS):
        for q in BENCHMARK_QUERIES:
            t0 = time.perf_counter()
            plan = orchestrator.plan(q)
            plan_times.append(time.perf_counter() - t0)

    # Benchmark analyze() only — no capability resolution
    analyze_times = []
    for _ in range(BENCHMARK_ITERATIONS):
        for q in BENCHMARK_QUERIES:
            t0 = time.perf_counter()
            analysis = orchestrator.analyze(q)
            analyze_times.append(time.perf_counter() - t0)

    n_plan = len(plan_times)
    n_analyze = len(analyze_times)

    print(f"Benchmark: {WARMUP_ITERATIONS} warmup, {BENCHMARK_ITERATIONS} iterations x {len(BENCHMARK_QUERIES)} queries")
    print()
    print(f"Plan() — full pipeline (analyze + capability resolution + plan build):")
    print(f"  Total calls: {n_plan}")
    print(f"  Total time:  {sum(plan_times)*1000:.1f}ms")
    print(f"  Mean:        {sum(plan_times)/n_plan*1000:.3f}ms")
    print(f"  Min:         {min(plan_times)*1000:.3f}ms")
    print(f"  Max:         {max(plan_times)*1000:.3f}ms")
    print(f"  p50:         {sorted(plan_times)[n_plan//2]*1000:.3f}ms")
    print(f"  p95:         {sorted(plan_times)[int(n_plan*0.95)]*1000:.3f}ms")
    print(f"  p99:         {sorted(plan_times)[int(n_plan*0.99)]*1000:.3f}ms")
    print()
    print(f"Analyze() — analysis only (no plan):")
    print(f"  Total calls: {n_analyze}")
    print(f"  Total time:  {sum(analyze_times)*1000:.1f}ms")
    print(f"  Mean:        {sum(analyze_times)/n_analyze*1000:.3f}ms")
    print(f"  Min:         {min(analyze_times)*1000:.3f}ms")
    print(f"  Max:         {max(analyze_times)*1000:.3f}ms")
    print(f"  p50:         {sorted(analyze_times)[n_analyze//2]*1000:.3f}ms")
    print(f"  p95:         {sorted(analyze_times)[int(n_analyze*0.95)]*1000:.3f}ms")
    print(f"  p99:         {sorted(analyze_times)[int(n_analyze*0.99)]*1000:.3f}ms")


if __name__ == "__main__":
    benchmark()
