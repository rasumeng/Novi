"""Phase 8 remediation — regression tests for audit findings A–G.

Each test pins a confirmed behavioral defect found by the post-implementation
audit and fixed in this pass. Nothing here weakens an existing contract:

A  gap→query refinement keeps entity/subject/timeframe anchors
B  budget-exhausted sub-questions are surfaced as incomplete coverage
C  insufficiency detection catches explicit uncertainty, never mere hedging
D  ConflictDetector flags same-period numeric contradictions
E  zero executed verification commands is NOT a pass
F  verification timeout is its own class and never blind-triggers repair
G  evaluation results disclose scripted/staged provenance
"""

import pytest

from novi.graphs import CodingGraph, ResearchGraph
from novi.graphs import coding_intel as ci
from novi.graphs import research_intel as ri
from novi.evidence.conflicts import ConflictDetector, MAJOR
from novi.evidence.context import Fact


# ── helpers ───────────────────────────────────────────────────────────────


class _JsonModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def invoke(self, msgs):
        self.calls += 1
        return type("R", (), {"content": self.answers.pop(0)
                              if self.answers else ""})()


def _bundle(quality=None, text="grounding evidence text for key",
            results=None, error=None, query="q"):
    from novi.runtime.evidence import EvidenceBundle, RetrievalQuality

    return EvidenceBundle(
        query=query,
        merged_text=text if not error else "",
        source_count=len(results or []),
        quality=quality or RetrievalQuality.SUFFICIENT,
        results=list(results or []),
        error=error,
    )


def _research_state(**kw):
    state = {
        "user_input": "what is the key",
        "analysis": None,
        "retrieval_plan": None,
        "grounding_text": "",
        "quality": "",
        "query": "what is the key",
        "search_attempts": 0,
        "max_search_attempts": 2,
        "system_prompt": "system",
        "plan_step_index": 0,
        "original_question": "what is the key",
    }
    state.update(kw)
    return state


# ── A: gap → refined query preserves entity/context ──────────────────────


def test_A_entity_and_missing_fact_stay_together():
    """Audit scenario: evidence covered CEO info, revenue missing.
    The refined query must keep the entity AND name the missing fact."""
    grounding = ("tesla ceo information biography leadership details "
                 "company profile 2024")
    refined = ri.refine_query("What was Tesla's 2024 revenue?",
                              ["revenue"], grounding)
    assert "tesla" in refined.lower(), "entity anchor lost"
    assert "revenue" in refined.lower(), "missing-fact target lost"
    assert len(refined.split()) <= ri.MAX_REFINED_TERMS


def test_A_two_gaps_never_drop_the_entity():
    """The pre-fix bug: two uncovered gaps produced 'revenue 2024'."""
    grounding = ("tesla ceo information biography leadership details "
                 "company profile overview")
    refined = ri.refine_query("What was Tesla's 2024 revenue?",
                              ["revenue", "2024"], grounding)
    assert "tesla" in refined.lower()
    assert "revenue" in refined.lower()
    assert len(refined.split()) <= ri.MAX_REFINED_TERMS


def test_A_multi_entity_question_keeps_subject():
    refined = ri.refine_query("Compare Acme and Globex annual profit",
                              ["profit"],
                              "acme globex comparison annual overview")
    assert "acme" in refined.lower()
    assert "profit" in refined.lower()


def test_A_timeframe_preserved_when_uncovered():
    refined = ri.refine_query("Microsoft cloud revenue growth 2023",
                              ["growth"],
                              "microsoft cloud revenue segment figures")
    assert "microsoft" in refined.lower()
    assert "2023" in refined


def test_A_generic_question_without_entity_stays_bounded():
    refined = ri.refine_query("explain quantum tunneling basics",
                              ["tunneling"], "quantum basics covered")
    assert refined
    assert len(refined.split()) <= ri.MAX_REFINED_TERMS


def test_A_fallbacks_unchanged():
    original = "python asyncio internals"
    # Full miss — no transform beats the question itself.
    assert ri.refine_query(original, ["asyncio", "event", "loop"], "") == \
        original
    # Covered gaps — duplicate gate must stay effective.
    assert ri.refine_query(original, ["asyncio", "loops"],
                           "python asyncio covers loops") == original
    assert ri.refine_query(original, [], "") == original


def test_A_graph_second_search_refines_from_original_question():
    queries = []

    def search(query):
        queries.append(query)
        if len(queries) == 1:
            return _bundle(None, text="tesla ceo leadership company details")
        return _bundle()

    model = _JsonModel(["final answer"])
    g = ResearchGraph(model=model, search=search)
    result = g.run(_research_state(
        user_input="What was Tesla's 2024 revenue?",
        query="What was Tesla's 2024 revenue?",
        original_question="What was Tesla's 2024 revenue?",
        quality="weak"))
    assert len(queries) == 2
    assert "tesla" in queries[1].lower(), (
        f"refined query lost the entity: {queries[1]!r}")


# ── B: decomposition vs search budget honesty ────────────────────────────


def test_B_budget_exhaustion_marks_coverage_incomplete():
    searches = []

    class _Dual:
        def invoke(self, msgs):
            content = ""
            if not getattr(_Dual, "called", False):
                _Dual.called = True
                content = ('{"sub_questions": ["part alpha", "part beta", '
                           '"part gamma"]}')
            else:
                content = "Summary of verified findings."
            return type("R", (), {"content": content})()

    g = ResearchGraph(model=_Dual(),
                      search=lambda q: (searches.append(q) or
                                        _bundle(results=[])),
                      max_search_attempts=2)
    result = g.run(_research_state(user_input="Compare alpha beta gamma",
                                   query="Compare alpha beta gamma"))

    assert len(searches) == 2, "bounded budget must hold"
    assert result.get("coverage_incomplete") is True
    assert result.get("unresearched_questions") == ["part gamma"]
    phases = [e.get("phase") for e in result.get("stream_events") or []]
    assert "coverage_incomplete" in phases
    assert result["validation_detail"].get("coverage_incomplete") is True


def test_B_synthesis_prompt_warns_about_unresearched_parts():
    seen = []
    calls = {"n": 0}

    class _Dual:
        def invoke(self, msgs):
            seen.append(str(getattr(msgs[0], "content", "")))
            calls["n"] += 1
            if calls["n"] == 1:
                content = ('{"sub_questions": ["alpha facts", "beta facts", '
                           '"gamma facts"]}')
            else:
                content = "Verified summary."
            return type("R", (), {"content": content})()

    g = ResearchGraph(model=_Dual(),
                      search=lambda q: _bundle(text="alpha beta material"),
                      max_search_attempts=2)
    result = g.run(_research_state(user_input="Compare alpha beta gamma",
                                   query="Compare alpha beta gamma"))
    synth_prompt = seen[-1]
    assert "COVERAGE WARNING" in synth_prompt
    assert "gamma facts" in synth_prompt
    assert "UNVERIFIED" in synth_prompt
    assert result["answer"] == "Verified summary."


def test_B_full_coverage_sets_no_incomplete_flag():
    searches = []
    g = ResearchGraph(model=_JsonModel([
        '{"sub_questions": ["only part one"]}',
        "done answer",
    ]), search=lambda q: (searches.append(q) or _bundle()),
        max_search_attempts=3)
    result = g.run(_research_state(
        user_input="Compare asyncio and threading versus multiprocessing",
        query="Compare asyncio and threading versus multiprocessing"))
    assert result.get("coverage_incomplete") is None or \
        result.get("coverage_incomplete") is False
    assert result["answer"] == "done answer"


# ── C: insufficiency detection ────────────────────────────────────────────


@pytest.mark.parametrize("answer", [
    "I don't have reliable information about that topic.",
    "I am not certain about the exact figure.",
    "I'm unsure how the merger proceeded.",
    "I dont know the current status.",          # apostrophe-normalized
    "The retrieved evidence is insufficient to confirm the revenue figure.",
    "This could not be verified against available sources.",
    "I was unable to verify the claim independently.",
])
def test_C_explicit_uncertainty_detected(answer):
    assert ri.discloses_insufficiency(answer) is True


@pytest.mark.parametrize("answer", [
    "Tesla grew revenue by 19% in 2024, led by energy storage.",
    "The merger may close later this year.",
    "It seems likely that prices will stabilize, possibly by Q3.",
    "Results appear consistent with guidance.",
])
def test_C_ordinary_hedging_not_flagged(answer):
    assert ri.discloses_insufficiency(answer) is False, answer


def test_C_validation_routes_disclosure_to_insufficient():
    detail = ri.validate_answer(
        "I am not certain; I don't have reliable information on this.",
        ri.CitationManifest(), has_evidence=True)
    assert detail["status"] == "insufficient"
    assert detail["disclosed_insufficient"] is True


# ── D: conflict detection over realistic contradictory evidence ──────────


def _fact(statement, conf=0.9):
    return Fact(statement=statement, confidence=conf, category="metric")


def test_D_same_period_numeric_contradiction_detected():
    a = _fact("Acme reported revenue of 96 billion dollars in 2024.")
    b = _fact("Acme reported revenue of 78 billion dollars in 2024.")
    conflicts = ConflictDetector().detect([a, b])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.severity == MAJOR
    assert "96" in c.statements[0] and "78" in c.statements[1]


def test_D_different_periods_are_not_conflicts():
    a = _fact("Acme reported revenue of 96 billion dollars in 2024.")
    b = _fact("Acme reported revenue of 88 billion dollars in 2023.")
    assert ConflictDetector().detect([a, b]) == ()


def test_D_identical_values_corroborate():
    a = _fact("Acme reported revenue of 96 billion dollars in 2024.")
    b = _fact("Acme reported revenue of 96 billion dollars in 2024.")
    assert ConflictDetector().detect([a, b]) == ()


def test_D_different_metrics_not_flagged():
    """Shared-subject guard: revenue vs profit share boilerplate but are not
    contradictions."""
    a = _fact("Company revenue was 96 billion dollars total.")
    b = _fact("Company profit was 78 billion dollars total.")
    assert ConflictDetector().detect([a, b]) == ()


def test_D_pipeline_surfaces_realistic_contradiction():
    """Full seam: retrieval bundles → EvidenceProcessor extraction →
    ConflictDetector → collect_conflicts output."""
    from novi.tools.search_pipeline import SearchResult

    body_a = ("Acme reported revenue of 96 billion dollars in 2024. The "
              "company expanded margins during the year.")
    body_b = ("Acme reported revenue of 78 billion dollars in 2024. The "
              "company faced headwinds during the year.")

    def bundle(url, body):
        from novi.runtime.evidence import EvidenceBundle, RetrievalQuality
        return EvidenceBundle(
            query="acme revenue 2024", merged_text=body, source_count=1,
            quality=RetrievalQuality.SUFFICIENT,
            results=[SearchResult(title=url, url=f"https://{url}",
                                  snippet="", full_text=body)])

    conflicts = ri.collect_conflicts([
        bundle("source-a.example/news", body_a),
        bundle("source-b.example/news", body_b),
    ])
    assert conflicts, "realistic contradiction pair must surface"
    joined = " ".join(" ".join(c["statements"]) for c in conflicts)
    assert "96" in joined and "78" in joined


# ── E: verification with zero commands ───────────────────────────────────


def _coding_state(**kw):
    state = {
        "user_input": "add a helper",
        "analysis": None,
        "retrieval_plan": None,
        "system_prompt": "system",
        "plan_step_index": 0,
        "answer": "",
        "stop_reason": "",
        "attempt": 0,
        "max_attempts": 2,
    }
    state.update(kw)
    return state


_EDIT_EVENTS = [("tool_call", "write_file", {"path": "a.py"}, "c1"),
                ("tool_result", "write_file", "[ok]", "c1",
                 {"text": "+++ a.py", "added": 1, "removed": 0})]


def test_E_zero_commands_is_never_a_pass():
    implements, verifies = [], []

    def run_loop(state):
        implements.append(1)
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        verifies.append(1)
        return []  # verifier present, but nothing configured/executed

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_coding_state())

    assert len(verifies) == 1
    assert result["verification_status"] == ci.VS_UNAVAILABLE
    assert result["verification_passed"] is False, (
        "zero executed commands must NOT read as successful verification")
    assert len(implements) == 1, (
        "no failure evidence exists — repair must not trigger")
    phases = [e.get("phase") for e in result.get("stream_events") or []]
    assert "verification_unavailable" in phases
    kinds = [e.kind for e in result.get("errors") or []]
    assert "internal" in kinds


def test_E_status_vocabulary_on_normal_paths():
    def verify_pass(state):
        return [ci.VerificationReport(kind="test", exit_code=0,
                                      stdout_tail="ok", stderr_tail="",
                                      duration_ms=1.0, passed=True)]

    g = CodingGraph(run_loop=lambda s: (list(_EDIT_EVENTS), "e", "completed",
                                        True),
                    verify=verify_pass)
    r = g.run(_coding_state())
    assert r["verification_status"] == ci.VS_VERIFIED
    assert r["verification_passed"] is True

    def verify_fail(state):
        return [_report_fail()]

    def _report_fail():
        return ci.VerificationReport(kind="test", exit_code=1,
                                     stdout_tail="1 failed", stderr_tail="",
                                     duration_ms=1.0, passed=False)

    g2 = CodingGraph(run_loop=lambda s: (list(_EDIT_EVENTS), "e", "completed",
                                         True),
                     verify=verify_fail, max_attempts=1)
    r2 = g2.run(_coding_state())
    assert r2["verification_status"] == ci.VS_FAILED
    assert r2["completion_reason"] == "verification_failed"


def test_E_skipped_verification_reports_skipped_status():
    g = CodingGraph(run_loop=lambda s: ([("token", "explanation")],
                                        "explanation", "completed", True),
                    verify=lambda s: [])
    r = g.run(_coding_state())
    assert r.get("verification_skipped") == "no_edits"
    assert r["verification_status"] == ci.VS_SKIPPED


# ── F: timeout classification ─────────────────────────────────────────────


class _FakeToolResult:
    def __init__(self, structured=None, output=""):
        self.output = output
        self.success = True
        self.error = None
        self.latency_ms = 5.0
        self.structured = structured or {}


def test_F_structured_timeout_classified_as_timeout():
    tr = _FakeToolResult(structured={
        "exit_code": None, "stdout_tail": "", "stderr_tail": "",
        "duration_ms": 120000.0, "timed_out": True, "blocked": False,
    })
    r = ci.report_from_tool_result(tr, command="pytest -q")
    assert r.classification == "timeout"
    assert r.passed is False
    assert r.exit_code is None


def test_F_legacy_timeout_text_classified_as_timeout():
    tr = _FakeToolResult(output="Error: command timed out after 120s")
    r = ci.report_from_tool_result(tr, command="pytest -q")
    assert r.classification == "timeout"
    assert r.passed is False


def test_F_overall_priority_permission_over_timeout():
    t = ci.VerificationReport(kind="test", exit_code=None, stdout_tail="",
                              stderr_tail="", duration_ms=1.0, passed=False,
                              classification="timeout")
    p = ci.VerificationReport(kind="test", exit_code=None, stdout_tail="",
                              stderr_tail="", duration_ms=0.0, passed=False,
                              classification="permission_denied")
    passed, cls = ci.overall([t, p])
    assert passed is False and cls == "permission_denied"
    passed, cls = ci.overall([t])
    assert passed is False and cls == "timeout"


def test_F_timeout_does_not_trigger_repair():
    implements, verifies = [], []

    def run_loop(state):
        implements.append(1)
        return (list(_EDIT_EVENTS), "edited", "completed", True)

    def verify(state):
        verifies.append(1)
        return [ci.VerificationReport(
            kind="test", exit_code=None, stdout_tail="", stderr_tail="",
            duration_ms=120000.0, passed=False, command="pytest -q",
            classification="timeout")]

    g = CodingGraph(run_loop=run_loop, verify=verify)
    result = g.run(_coding_state())

    assert len(implements) == 1, "timeout must not blind-trigger code repair"
    assert len(verifies) == 1
    assert result["completion_reason"] == "verification_timeout"
    assert result["stop_reason"] == "verification_timeout"
    assert result["verification_classification"] == "timeout"
    kinds = [e.kind for e in result.get("errors") or []]
    assert "timeout" in kinds


# ── G: evaluation provenance honesty ─────────────────────────────────────


def test_G_research_driver_discloses_scripted_mode():
    from novi.evaluation import BenchmarkCase
    from novi.evaluation.drivers import CodingEvalDriver, ResearchEvalDriver

    driver = ResearchEvalDriver()  # offline default
    case = BenchmarkCase(id="REM-R1", input="question one",
                         expected_intent="research",
                         expected_grounding=True)
    r = driver.run(case)
    assert r.driver_mode == "scripted"

    live = ResearchEvalDriver(model=_JsonModel(["live answer"]))
    r2 = live.run(case)
    assert r2.driver_mode == "live"


def test_G_coding_driver_flags_staged_repair():
    from novi.evaluation import BenchmarkCase
    from novi.evaluation.drivers import CodingEvalDriver

    staged = {
        "files": {"app.py": "def add(a, b):\n    raise NotImplementedError\n"},
        "test_file": "from app import add\n\ndef test_add():\n"
                     "    assert add(1, 2) == 3\n",
        "solution": {"app.py": "def add(a, b):\n    return a + b\n"},
        "delay_solution": True,
        "expect_pass": True,
    }
    r = CodingEvalDriver().run(BenchmarkCase(
        id="REM-C1", input="implement add", expected_intent="coding",
        fixture=dict(staged)))
    assert r.staged_repair is True, (
        "driver-supplied repair edit must be disclosed as staged")
    assert r.driver_mode == "scripted"

    unstaged = dict(staged)
    unstaged["delay_solution"] = False
    r2 = CodingEvalDriver().run(BenchmarkCase(
        id="REM-C2", input="implement add", expected_intent="coding",
        fixture=unstaged))
    assert r2.staged_repair is False


def test_G_metrics_surface_staged_repair_rate():
    from novi.evaluation.metrics import CaseResult
    from novi.evaluation import BenchmarkCase
    from novi.evaluation import MetricCollector

    case = BenchmarkCase(id="X", input="i", expected_intent="coding")
    r1 = CaseResult(case=case, intent="coding")
    r1.staged_repair = True
    r2 = CaseResult(case=BenchmarkCase(id="Y", input="j",
                                       expected_intent="coding"),
                    intent="coding")
    ms = MetricCollector().collect([r1, r2])
    assert ms.coding.staged_repair_rate == pytest.approx(0.5)


def test_G_case_result_round_trip_carries_provenance():
    from novi.evaluation.metrics import CaseResult
    from novi.evaluation import BenchmarkCase

    r = CaseResult(case=BenchmarkCase(id="Z", input="in"),
                   intent="coding", driver_mode="scripted",
                   staged_repair=True, coverage_incomplete=True)
    d = r.to_dict()
    assert d["driver_mode"] == "scripted"
    assert d["staged_repair"] is True
    assert d["coverage_incomplete"] is True


def test_G_compare_flags_coding_regression():
    from novi.evaluation import RegressionDetector
    from novi.evaluation.metrics import CodingMetrics, MetricSet

    base = MetricSet(coding=CodingMetrics(task_completion=1.0))
    cand = MetricSet(coding=CodingMetrics(task_completion=0.8))
    report = RegressionDetector().compare(base, cand)
    regressed = {f.metric for f in report.regressions}
    assert "coding.task_completion" in regressed


def test_G_research_result_records_incomplete_coverage():
    from novi.evaluation import BenchmarkCase
    from novi.evaluation.drivers import ResearchEvalDriver

    searches = []

    driver = ResearchEvalDriver(max_search_attempts=1)
    case = BenchmarkCase(id="REM-R2", input="Compare alpha beta gamma parts",
                         expected_intent="research",
                         expected_grounding=True)
    # Decomposition needs a JSON-capable model; inject stub producing three.
    driver._model = _JsonModel([
        '{"sub_questions": ["alpha facts", "beta facts", "gamma facts"]}',
        "Partial verified summary.",
    ])
    orig_factory = driver._search_factory

    def factory(case_):
        base_search = orig_factory(case_)
        def search(query):
            searches.append(query)
            return base_search(query)
        return search

    driver._search_factory = factory
    r = driver.run(case)
    assert len(searches) < 3
    assert r.coverage_incomplete is True
