"""Evaluation drivers — run benchmark cases and produce CaseResults.

Drivers are the replaceability seam of evaluation: any driver satisfying
EvaluationDriver can power EvaluationRunner. Two reference drivers:

- AnalysisDriver: offline orchestrator analysis. No model, no I/O. Fast.
- RuntimeDriver: full runtime execution through RuntimeInterface,
  consuming finalized traces via TraceCollector (event-based observation).

Neither driver controls runtime internals — both consume outputs only.
"""

from __future__ import annotations

import time
from typing import Protocol

from ..orchestrator import Orchestrator
from ..capabilities import CapabilityRegistry
from ..capabilities.builtin import register_builtin_capabilities
from .benchmark import BenchmarkCase
from .metrics import CaseResult
from .trace_collector import TraceCollector


class EvaluationDriver(Protocol):
    """Runs one benchmark case and returns a structured result."""

    name: str = ""

    def run(self, case: BenchmarkCase) -> CaseResult:
        ...


def _default_orchestrator() -> Orchestrator:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    return Orchestrator(capability_registry=registry)


class AnalysisDriver:
    """Offline orchestrator analysis — measures decision quality without a model."""

    name = "analysis"

    def __init__(self, orchestrator: Orchestrator | None = None):
        self._orchestrator = orchestrator or _default_orchestrator()

    def run(self, case: BenchmarkCase) -> CaseResult:
        t0 = time.perf_counter()
        plan = self._orchestrator.plan(
            case.input, has_images=case.has_images
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        analysis = plan.context.get("analysis")
        result = CaseResult(
            case=case,
            intent=plan.goal.intent.value,
            strategy=plan.strategy.value,
            capabilities=[c.id for c in plan.capabilities],
            max_steps=plan.max_steps,
            latency_ms=round(latency_ms, 2),
        )
        if analysis is not None:
            result.grounding_searched = bool(analysis.grounding.needs_grounding)
            result.retrieval_sources = [
                s.value for s in analysis.retrieval_plan.sources
            ]
            result.retrieval_strategy = analysis.retrieval_plan.strategy.value
        return result


class RuntimeDriver:
    """Full runtime evaluation. Requires a model and network for retrieval cases.

    Runs each case through RuntimeInterface.run() and consumes the finalized
    ExecutionTrace via an EventBus-attached TraceCollector. Timeout-guarded;
    a per-case failure is recorded as a CaseResult with an error, never thrown.
    """

    name = "runtime"

    def __init__(
        self,
        runtime=None,
        event_bus=None,
        trace_collector: TraceCollector | None = None,
        timeout_s: float = 120.0,
    ):
        from ..runtime.runtime import CozmoRuntime

        self._runtime = runtime if runtime is not None else CozmoRuntime()
        self._event_bus = event_bus
        self._collector = trace_collector
        self._owns_collector = trace_collector is None
        if trace_collector is None:
            runtime_bus = event_bus or getattr(self._runtime, "event_bus", None)
            if runtime_bus is not None:
                self._collector = TraceCollector(event_bus=runtime_bus, max_traces=10)
        self.timeout_s = timeout_s

    def run(self, case: BenchmarkCase) -> CaseResult:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._run_case, case)
            try:
                return future.result(timeout=self.timeout_s)
            except TimeoutError:
                return CaseResult(
                    case=case, error=f"timeout after {self.timeout_s}s"
                )
            except Exception as e:  # noqa: BLE001 — driver must not crash runner
                return CaseResult(case=case, error=f"error: {e}")

    def _run_case(self, case: BenchmarkCase) -> CaseResult:
        result = CaseResult(case=case, latency_ms=0.0)
        if self._collector is not None:
            self._collector.clear()
        t0 = time.perf_counter()
        answer = self._runtime.run(case.input)
        result.answer = answer or ""
        result.latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        trace = self._collector.last if self._collector else None
        if trace:
            self._apply_trace(result, trace)
        return result

    @staticmethod
    def _apply_trace(result: CaseResult, trace: dict) -> None:
        result.trace = trace
        result.intent = trace.get("intent", "")
        result.strategy = str(trace.get("strategy", ""))
        result.grounding_searched = bool(trace.get("grounding_searched", False))
        result.grounding_quality = trace.get("grounding_quality", "")
        sources = trace.get("retrieval_sources", "")
        result.retrieval_sources = (
            [s.strip() for s in sources.split(",") if s.strip()]
            if isinstance(sources, str)
            else list(sources or [])
        )
        result.retrieval_strategy = trace.get("retrieval_strategy", "")
        result.recovery_attempts = int(trace.get("recovery_attempts", 0))
        result.max_steps = int(trace.get("max_steps", 0)) if trace.get("max_steps") else 0
        result.capabilities = list(trace.get("tools_bound", []) or [])

        tool_calls: list[dict] = []
        for step in trace.get("steps", []) or []:
            for tc in step.get("tool_calls", []) or []:
                tool_calls.append(
                    {
                        "name": tc.get("name", ""),
                        "success": bool(tc.get("success", False)),
                        "latency_ms": float(tc.get("latency_ms", 0.0)),
                    }
                )
        result.tool_calls = tool_calls

    def close(self) -> None:
        if self._owns_collector and self._collector is not None:
            self._collector.detach()
