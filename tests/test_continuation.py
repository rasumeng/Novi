"""Milestone 5 Phase 5A/5B — continuation detection + resolution (read-only).

Covers:
  - 5A: IntentDetector classifies continuation requests as CONTINUATION.
  - 5B: ContinuationService resolves resumable work by joining TaskStore +
        JobStore, ranks candidates, never executes and never mutates.
"""

import pytest

from cozmo.orchestrator.intent import IntentDetector, classify_intent
from cozmo.orchestrator.task_types import IntentType, Task, TaskStatus
from cozmo.orchestrator.task_store import TaskStore
from cozmo.jobs.job import Checkpoint, Job, JobStatus
from cozmo.jobs.persistence import JobStore
from cozmo.services.continuation import ContinuationService
from cozmo.planner.models import Plan, PlanStep


def make_task(tid, *, status=TaskStatus.IN_PROGRESS, conversation_id="",
              raw_goal="build the widget", plan=None, updated_at="",
              created_at=""):
    return Task(
        id=tid,
        status=status,
        conversation_id=conversation_id,
        raw_goal=raw_goal,
        plan=plan,
        updated_at=updated_at,
        created_at=created_at,
    )


def make_job(job_id, task_id, *, status=JobStatus.INTERRUPTED,
             cp_step=1, completed_steps=None):
    return Job(
        id=job_id,
        task_id=task_id,
        status=status,
        started_at="2026-01-01T00:00:00",
        checkpoint=Checkpoint(
            job_id=job_id,
            task_id=task_id,
            plan_id="plan-1",
            step=cp_step,
            completed_steps=completed_steps or [],
        ),
    )


@pytest.fixture
def task_store(tmp_path):
    return TaskStore(persist_dir=str(tmp_path / "tasks"))


@pytest.fixture
def job_store(tmp_path, monkeypatch):
    import cozmo.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    return JobStore()


def _service(task_store, job_store):
    return ContinuationService(task_store=task_store, job_store=job_store)


def _seed(task_store, job_store, *pairs):
    """pairs: (task_kwargs, job_kwargs_or_None)."""
    for task_kw, job_kw in pairs:
        task_store.save(make_task(**task_kw))
        if job_kw is not None:
            job_store.save(make_job(**job_kw))


def _three_step_plan(task_id="task-1"):
    plan = Plan(id="plan-1", task_id=task_id)
    for i, desc in enumerate(["a", "b", "c"]):
        plan.add_step(PlanStep(id=f"plan-1-s{i+1}", plan_id="plan-1", description=desc))
    return plan


# ── 5A: continuation intent detection ─────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "continue the task",
    "resume what I was doing",
    "pick up where I left off",
    "what was I working on",
    "continue yesterday's work",
    "keep going with this",
    "carry on",
    "go on",
    "previous task",
])
def test_continuation_phrases_classify_as_continuation(phrase):
    assert classify_intent(phrase) is IntentType.CONTINUATION


@pytest.mark.parametrize("phrase", [
    "what is the weather",
    "refactor auth.py",
    "explain how DNS works",
    "design the architecture",
    "add a feature to the app",
])
def test_non_continuation_phrases_not_continuation(phrase):
    assert classify_intent(phrase) is not IntentType.CONTINUATION


def test_continuation_has_high_confidence():
    detector = IntentDetector()
    intent, confidence = detector.detect("continue what we started")
    assert intent is IntentType.CONTINUATION
    assert confidence >= 0.9


def test_continuation_beats_coding_keyword():
    # "continue" must not fall through to coding
    assert classify_intent("continue the code review") is IntentType.CONTINUATION


# ── 5B: same-conversation resolution ─────────────────────────────────────

def test_same_conversation_preferred_over_other(task_store, job_store):
    task_store.save(make_task("task-A", conversation_id="conv-1",
                              updated_at="2026-01-01T00:00:00"))
    task_store.save(make_task("task-B", conversation_id="conv-2",
                              updated_at="2026-01-02T00:00:00"))
    job_store.save(make_job("job-A", "task-A"))
    job_store.save(make_job("job-B", "task-B"))

    service = _service(task_store, job_store)
    targets = service.candidates(conversation_id="conv-1")
    assert targets[0].task_id == "task-A"
    assert targets[0].conversation_id == "conv-1"


# ── 5B: global resolution ────────────────────────────────────────────────

def test_global_resolution_finds_all_resumable(task_store, job_store):
    _seed(task_store, job_store,
          (dict(tid="task-1", conversation_id="conv-1"),
           dict(job_id="job-1", task_id="task-1")),
          (dict(tid="task-2", conversation_id="conv-2"),
           dict(job_id="job-2", task_id="task-2")))
    service = _service(task_store, job_store)
    targets = service.candidates()
    assert {t.task_id for t in targets} == {"task-1", "task-2"}


def test_resume_target_shape(task_store, job_store):
    task_store.save(make_task("task-1", conversation_id="conv-1",
                              plan=_three_step_plan()))
    job_store.save(make_job("job-1", "task-1", cp_step=2,
                            completed_steps=["plan-1-s1", "plan-1-s2"]))
    service = _service(task_store, job_store)
    targets = service.candidates()
    assert len(targets) == 1
    t = targets[0]
    assert t.task_id == "task-1"
    assert t.job_id == "job-1"
    assert t.plan_id == "plan-1"
    assert t.next_step == 2            # cp.step 2 = 2 completed → resume index 2
    assert t.checkpoint is not None
    assert t.completed_steps == ["plan-1-s1", "plan-1-s2"]


# ── ambiguity: return candidates, don't silently pick ────────────────────

def test_ambiguous_recommended_returns_none(task_store, job_store):
    _seed(task_store, job_store,
          (dict(tid="task-1", conversation_id="conv-1"),
           dict(job_id="job-1", task_id="task-1")),
          (dict(tid="task-2", conversation_id="conv-2"),
           dict(job_id="job-2", task_id="task-2")))
    service = _service(task_store, job_store)
    assert service.recommended() is None
    assert len(service.candidates()) == 2


def test_single_candidate_is_recommended(task_store, job_store):
    _seed(task_store, job_store,
          (dict(tid="task-1", conversation_id="conv-1"),
           dict(job_id="job-1", task_id="task-1")))
    service = _service(task_store, job_store)
    targets = service.candidates()
    assert len(targets) == 1
    rec = service.recommended(conversation_id="conv-1")
    assert rec is not None and rec.task_id == "task-1"


# ── terminal tasks excluded ──────────────────────────────────────────────

@pytest.mark.parametrize("terminal", [
    TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED, TaskStatus.ARCHIVED,
])
def test_terminal_tasks_excluded(task_store, job_store, terminal):
    _seed(task_store, job_store,
          (dict(tid="task-x", conversation_id="conv-1", status=terminal),
           dict(job_id="job-x", task_id="task-x")))
    service = _service(task_store, job_store)
    assert service.candidates() == []


# ── resolver is read-only ────────────────────────────────────────────────

def test_resolver_never_mutates_state(task_store, job_store):
    task_store.save(make_task("task-1", conversation_id="conv-1"))
    job_store.save(make_job("job-1", "task-1", status=JobStatus.INTERRUPTED))

    task_before = task_store.get("task-1")
    job_before = job_store.load("job-1")

    service = _service(task_store, job_store)
    _ = service.candidates(conversation_id="conv-1")
    _ = service.recommended(conversation_id="conv-1")

    task_after = task_store.get("task-1")
    job_after = job_store.load("job-1")
    assert task_after.status == task_before.status
    assert job_after.status == job_before.status
    assert job_after.checkpoint.step == job_before.checkpoint.step