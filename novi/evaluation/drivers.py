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
        from ..runtime.runtime import NoviRuntime

        self._runtime = runtime if runtime is not None else NoviRuntime()
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


# ── Phase 8E: research / coding evaluation drivers ────────────────────────


class _ScriptedResearchModel:
    """Deterministic stand-in for the synthesis model (OFFLINE mode).

    Cites the first manifest sources when evidence exists and echoes the
    question's own key terms so the answer stays topically anchored;
    honestly discloses insufficiency when it does not. Reproducible by
    construction — metrics from this driver never depend on model mood.
    Live evaluation injects a real bound runnable instead.
    """

    def __init__(self, disclose: bool = False):
        import re
        self._word = re.compile(r"[A-Za-z0-9]{3,}")
        self.disclose = disclose

    def invoke(self, msgs):
        content = str(getattr(msgs[0], "content", ""))
        if self.disclose or "No retrieved evidence" in content:
            return type("R", (), {"content": (
                "The retrieved evidence is insufficient to answer this "
                "question reliably.")})()
        question = str(getattr(msgs[-1], "content", "")) if len(msgs) > 1 else ""
        from ..runtime.retrieval import RetrievalExecutor
        anchors = RetrievalExecutor.extract_key_terms(question)[:6]
        topic = " ".join(dict.fromkeys(anchors))
        cites = []
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("[S") and "]" in line:
                cites.append(line[1:line.index("]")])
        cite_txt = " ".join(f"[{c}]" for c in cites[:2])
        return type("R", (), {"content": (
            f"About {topic}: based on the evidence {cite_txt}, the key "
            f"points around {topic} are covered and summarized here.")})()


def _offline_search(case):
    """Deterministic search stub: evidence presence follows the case's
    expected_grounding flag; URLs derive from the case id."""
    from ..runtime.evidence import EvidenceBundle, RetrievalQuality
    from ..tools.search_pipeline import SearchResult

    def search(query: str):
        if not case.expected_grounding:
            return EvidenceBundle(query=query, merged_text="",
                                  source_count=0,
                                  quality=RetrievalQuality.EMPTY)
        results = [
            SearchResult(
                title=f"{case.id} source {i}",
                url=f"https://eval.example/{case.id}/{i}",
                snippet=f"Deterministic evidence for {case.input}",
                full_text=f"Deterministic evidence for {case.input}. " * 3,
            )
            for i in range(1, 4)
        ]
        return EvidenceBundle(
            query=query, results=results,
            merged_text=f"Evidence summary for {query}",
            source_count=len(results), quality=RetrievalQuality.SUFFICIENT,
        )

    return search


class ResearchEvalDriver:
    """Offline-first research workflow evaluation (Phase 8E).

    Runs the ResearchGraph per case with a deterministic search stub and a
    scripted synthesis model — no network, no live model — and computes
    citation/conflict/search metrics from the graph's own state. Pass a real
    ``model`` and ``search_factory`` to evaluate live behavior with the same
    metric plumbing.
    """

    name = "research"

    def __init__(self, model=None, search_factory=None,
                 max_search_attempts: int = 3):
        from ..graphs.research_intel import (
            acknowledges_conflicts, build_manifest, cited_source_ids,
            validate_answer,
        )
        self._ri = (acknowledges_conflicts, build_manifest,
                    cited_source_ids, validate_answer)
        # Offline default: deterministic scripted synthesis so metrics never
        # depend on model mood. Live evaluation passes a bound runnable.
        self._model = model if model is not None else _ScriptedResearchModel()
        # Phase 8 remediation (audit G): results must disclose whether the
        # behavior under test came from a scripted stand-in or a live model.
        self.driver_mode = "live" if model is not None else "scripted"
        self._search_factory = search_factory or _offline_search
        self.max_search_attempts = max_search_attempts

    def run(self, case) -> CaseResult:
        from ..graphs import ResearchGraph

        t0 = time.perf_counter()
        graph = ResearchGraph(model=self._model,
                              search=self._search_factory(case),
                              max_search_attempts=self.max_search_attempts)
        result = graph.run({
            "user_input": case.input,
            "analysis": None,
            "retrieval_plan": None,
            "grounding_text": "",
            "quality": "",
            "query": case.input,
            "search_attempts": 0,
            "system_prompt": "evaluation",
            "plan_step_index": 0,
            "original_question": case.input,
        })
        latency = (time.perf_counter() - t0) * 1000

        acknowledges, build_manifest, cited_source_ids, validate_answer = \
            [fn for fn in self._ri]

        answer = result.get("answer") or ""
        manifest = result.get("citation_manifest")
        entries = list(getattr(manifest, "entries", []) or [])
        valid_ids = {e.source_id for e in entries}
        citations = cited_source_ids(answer)
        invalid = [c for c in citations if c not in valid_ids]
        conflicts = result.get("conflicts") or []

        r = CaseResult(case=case, intent="research", strategy="research",
                       grounding_searched=bool(result.get("search_attempts")),
                       answer=answer,
                       latency_ms=round(latency, 2))
        r.driver_mode = self.driver_mode
        # Audit B: surface honest decomposition coverage in the case record.
        r.coverage_incomplete = bool(result.get("coverage_incomplete"))
        r.citations = citations
        r.invalid_citations = invalid
        r.manifest_size = len(entries)
        r.searches = int(result.get("search_attempts") or 0)
        r.conflicts_surfaced = len(conflicts)
        r.conflict_acknowledged = acknowledges(answer, conflicts)
        detail = validate_answer(answer, manifest,
                                 has_evidence=bool(entries))
        r.disclosed_insufficient = bool(detail.get("disclosed_insufficient"))
        return r


class CodingEvalDriver:
    """Offline fixture-repository coding evaluation (Phase 8E).

    Materializes each case's ``fixture`` into a temp workspace, runs a
    SCRIPTED editor loop (deterministic — writes the fixture solution), and
    verifies through the REAL pipeline: ToolExecutor.execute("run_command")
    → workspace-pinned shell runner → pytest. No network; requires a local
    pytest. Metrics measure the verification/repair machinery, not the LLM.

    Fixture schema (BenchmarkCase.fixture):
      files:     {path: content} written before the run
      test_file: pytest file body placed under tests/test_eval.py
      solution:  {path: content} written on repair attempts (attempt ≥ 2)
      expect_pass: whether verification should ultimately pass
    """

    name = "coding"

    def __init__(self, commands: list[str] | None = None):
        import sys

        exe = sys.executable.replace("\\", "/")
        if " " in exe:
            exe = f'"{exe}"'
        self.commands = commands or [f"{exe} -m pytest -q"]

    def run(self, case) -> CaseResult:
        return self._run_case(case)

    def _run_case(self, case) -> CaseResult:
        import shutil
        import tempfile
        from pathlib import Path

        from ..graphs import CodingGraph
        from ..graphs.coding_intel import report_from_tool_result
        from ..runtime.tool_executor import ToolExecutor
        from ..runtime.tool_registry import ToolRegistry
        from ..tools import TOOL_REGISTRY
        from ..tools.file_ops import set_allowed_root

        fixture = dict(case.fixture or {})
        t0 = time.perf_counter()

        class _Allow:
            def resolve(self, name, args, agent="novi"):
                return "allow"

        class _Lessons:
            def record(self, *a, **k):
                pass

        tool_calls: list[dict] = []

        tmp = Path(tempfile.mkdtemp(prefix="novi-eval-coding-"))
        try:
            files = dict(fixture.get("files") or {})
            test_body = fixture.get("test_file") or (
                "def test_eval():\n    assert True\n")
            files["tests/test_eval.py"] = test_body
            solution = dict(fixture.get("solution") or {})
            delay_repair = bool(fixture.get("delay_solution", True))

            for rel, content in files.items():
                p = tmp / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            set_allowed_root(tmp)
            try:
                reg = ToolRegistry()
                reg.register("run_command", TOOL_REGISTRY["run_command"])
                ex = ToolExecutor(registry=reg, perms=_Allow(),
                                  lesson_store=_Lessons(), lc_tools={},
                                  tool_fallbacks={}, max_tool_output=12000,
                                  perm_mode="bypass")

                def instrumented_verify(state):
                    reports = []
                    for command in self.commands:
                        tr = ex.execute("run_command", {"command": command})
                        tool_calls.append({"name": "run_command",
                                           "success": tr.success,
                                           "latency_ms": tr.latency_ms})
                        reports.append(report_from_tool_result(tr,
                                                               command=command))
                        if not reports[-1].passed:
                            break
                    return reports

                def scripted_editor(state):
                    attempt = int(state.get("attempt") or 0) + 1
                    events = [("tool_call", "write_file",
                               {"path": "solution.py"}, f"c{attempt}")]
                    # Deterministic repair semantics: solutions land on the
                    # second attempt when the fixture asks for staged repair.
                    if attempt >= 2 or not delay_repair:
                        for rel, content in solution.items():
                            p = tmp / rel
                            p.parent.mkdir(parents=True, exist_ok=True)
                            p.write_text(content, encoding="utf-8")
                        events.append(("tool_result", "write_file", "[ok]",
                                       f"c{attempt}",
                                       {"text": "+++ solution.py",
                                        "added": len(solution),
                                        "removed": 0}))
                    return (events, "scripted edit complete", "completed", True)

                graph = CodingGraph(run_loop=scripted_editor,
                                    verify=instrumented_verify,
                                    max_attempts=2)
                result_state = graph.run({
                    "user_input": case.input,
                    "analysis": None,
                    "retrieval_plan": None,
                    "system_prompt": "evaluation",
                    "plan_step_index": 0,
                })
            finally:
                import os
                set_allowed_root(os.getcwd())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        latency = (time.perf_counter() - t0) * 1000
        metrics = result_state.get("metrics") or {}
        passed = bool(result_state.get("verification_passed"))

        r = CaseResult(case=case, intent="coding", strategy="coding",
                       answer=result_state.get("answer") or "",
                       latency_ms=round(latency, 2))
        r.driver_mode = "scripted"  # editor loop is scripted by design
        # Audit G: when the DRIVER supplies the passing edit on repair
        # attempts, the run cannot count as observed agent repair skill.
        r.staged_repair = bool(solution) and delay_repair and \
            int(metrics.get("attempts") or 1) > 1
        r.tool_calls = tool_calls
        r.verification_passed = passed
        r.verifications = int(metrics.get("verifications") or 0)
        r.verification_failures = int(metrics.get("verification_failures") or 0)
        r.repair_attempts = int(metrics.get("attempts") or 1)
        r.edits = int(metrics.get("edits") or 0)
        r.changed_files = len(metrics.get("files") or [])
        r.first_attempt_passed = (
            r.verifications > 0 and r.verification_failures == 0
            and r.repair_attempts == 1)
        expect_pass = bool((case.fixture or {}).get("expect_pass", True))
        # Honest semantics: this records EXPECTATION MATCH, not success —
        # a fixture staged to fail whose verification failed also "matches".
        # Consumers needing true success must read verification_passed.
        r.task_completed = passed == expect_pass
        return r
