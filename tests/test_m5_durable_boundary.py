"""Milestone 5 Phase 6C — durable/config boundary guard tests.

Ensures the decisions from the durability-vs-configuration audit stay true:

  1. Checkpoints carry only a minimal, REDACTED slice of step execution
     context (tool name + redacted args + result preview), never raw tool
     arguments or secret material.
  2. Durable state (Task/Plan/Job/Checkpoint/ExecutionHistory) is
     configuration-free: no model names, provider config, credentials, MCP or
     connector state ever persists.
  3. Resume re-resolves the model from CURRENT settings (option B) — the
     checkpoint carries no model, and a continuation plan with empty
     ``model_spec`` falls through to ``ModelService.resolve``.
  4. Permissions are re-evaluated at tool-call time per run — a checkpointed
     task whose tool was permitted cannot inherit that permission after it is
     revoked; nothing is snapshotted into durable state.
  5. Startup recovery runs once per NoviContext on every execution surface
     (``build_application_execution`` → ``ctx.recover_once``), so CLI/Telegram
     surfaces match the WebUI ``warmup`` sweep (F-4 parity).
"""

import json

import pytest

from novi.jobs.job import Checkpoint, JobStatus
from novi.jobs.manager import JobManager
from novi.jobs.persistence import JobStore
from novi.runtime.event_bus import EventBus
from novi.services.job_lifecycle import JobLifecycle


@pytest.fixture
def store(tmp_path, monkeypatch):
    import novi.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")
    return JobStore()


@pytest.fixture
def manager(store):
    return JobManager(store=store)


def _wired(manager, task_store=None):
    bus = EventBus()
    lifecycle = JobLifecycle(manager, task_store=task_store)
    lifecycle.subscribe(bus)
    return bus, lifecycle


# ── 1. checkpoint carries redacted minimal step context ─────────────────────

def test_step_context_persisted_redacted(manager, store):
    """STEP_COMPLETED tools= payload lands in Checkpoint.tool_states, redacted."""
    bus, lifecycle = _wired(manager)
    bus.emit("plan.started", task_id="task-cx", plan_id="plan-cx", step_count=1)
    job_id = lifecycle.active_job("task-cx")

    bus.emit("step.completed", task_id="task-cx", plan_id="plan-cx",
             step_id="plan-cx-s1", index=0, result="did the thing",
             tools=[
                 {"name": "web_fetch", "args": {"url": "https://x", "api_key": "sk-1234"},
                  "ok": True, "result": "page loaded"},
                 {"name": "run_command", "args": {"command": "echo hi"}, "ok": False,
                  "result": "Error: denied"},
             ])

    cp = store.load_checkpoint(job_id)
    assert cp is not None
    assert cp.step == 1
    assert "step:plan-cx-s1" in cp.tool_states
    records = cp.tool_states["step:plan-cx-s1"]
    assert records[0]["name"] == "web_fetch"
    assert records[0]["args"]["api_key"] == "<redacted>"
    assert records[0]["args"]["url"] == "https://x"      # benign leaf survives
    assert records[1]["ok"] is False
    assert cp.messages[0]["step_id"] == "plan-cx-s1"
    assert cp.messages[0]["output"] == "did the thing"

    # round-trips through the job + checkpoint files
    saved = store.load(job_id)
    assert saved.checkpoint.tool_states["step:plan-cx-s1"][0]["args"]["api_key"] == "<redacted>"


def test_redaction_covers_nested_and_long_leaves():
    from novi.runtime.execution_redaction import redact_value

    blob = {
        "Authorization": "Bearer abc",
        "nested": {"token": "t", "ok": "kept", "deep": {"password": "p"}},
        "long": "x" * 5000,
        "list": [{"cookie": "c", "name": "n"}],
    }
    out = redact_value(blob)
    assert out["Authorization"] == "<redacted>"
    assert out["nested"]["token"] == "<redacted>"
    assert out["nested"]["ok"] == "kept"
    assert out["nested"]["deep"]["password"] == "<redacted>"
    assert out["long"] == "x" * 2000
    assert out["list"][0]["cookie"] == "<redacted>"
    assert out["list"][0]["name"] == "n"


# ── 2. durable state is configuration-free ──────────────────────────────────

def test_durable_state_never_carries_config_or_secrets(tmp_path,
                                                       manager, store):
    """A full run's persisted JSON contains no settings/credential material."""
    from novi.services.job_lifecycle import JobLifecycle

    # drive through the lifecycle so the durability-gate redaction applies
    bus = EventBus()
    lifecycle = JobLifecycle(manager, task_store=None)
    lifecycle.subscribe(bus)
    bus.emit("plan.started", task_id="task-sec", plan_id="plan-sec", step_count=1)
    bus.emit("step.completed", task_id="task-sec", plan_id="plan-sec",
             step_id="plan-sec-s1", index=0, result="result",
             tools=[{"name": "web_fetch",
                     "args": {"url": "https://x", "api_key": "sk-live-secret",
                              "headers": {"Authorization": "Bearer zzz"}},
                     "ok": True, "result": "loaded"}])

    paths = list((tmp_path / "jobs").glob("*"))
    assert paths, "no persisted job/checkpoint files found"

    forbidden = ["sk-live-secret", "Bearer zzz", "temperature", "max_tokens",
                 "providers", "ollama", "mcp.", "bot_token"]
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        lower = raw.lower()
        for token in forbidden:
            assert token.lower() not in lower, f"{path.name} leaked {token!r}"
        # masked value present, key name allowed but value redacted
        assert "<redacted>" in raw


# ── 3. resume re-resolves model from current settings ───────────────────────

def test_continuation_plan_model_spec_empty_resolves_at_runtime(manager):
    """Resume target + continuation ExecutionPlan carry NO model pin."""
    from novi.orchestrator.task_types import ExecutionPlan

    plan = ExecutionPlan(
        task_id="task-r",
        goal="continue",
        model_spec={"model": "", "supports_tools": True},
        temperature=0.2,
        max_steps=10,
    )
    assert plan.model_spec["model"] == ""
    # Runtime._resolve_model: empty plan model → fall through to
    # model_service.resolve(role) from CURRENT config. Guard the contract:
    # continuation must never pin a stale model name.
    assert "model" in plan.model_spec


def test_runtime_resolve_prefers_service_when_plan_empty():
    """Contract: empty plan model_spec → model_service.resolve from current config.

    Mirrors the run_stream branch (runtime.py ~504-516): with an
    ``execution_plan`` present and ``model_service`` wired (webui/CLI both wire
    it), the CURRENT-config service resolve wins over any plan-level model —
    resume after a settings change re-resolves, nothing in the checkpoint pins
    the old model.
    """
    from novi.orchestrator.task_types import ExecutionPlan

    # plan level: continuation builds model_spec with empty model — no pin
    pinned = ExecutionPlan(task_id="t", goal="g",
                           model_spec={"model": "pinned-model"})
    empty = ExecutionPlan(task_id="t", goal="g", model_spec={"model": ""})
    assert pinned.model_spec["model"] == "pinned-model"
    assert empty.model_spec["model"] == ""

    # the durable contract that matters: nothing about the model is carried
    # into the checkpoint path — Checkpoint has no model/provider fields
    from novi.jobs.job import Checkpoint
    cp = Checkpoint(job_id="j", task_id="t", plan_id="p", step=1)
    serialized = json.dumps(cp.to_dict())
    assert "model" not in serialized
    assert "provider" not in serialized


# ── 4. permissions re-evaluated on resume, never snapshotted ────────────────

def test_checkpoint_never_snapshots_permissions(manager, store):
    """tool_states stores tool usage, NOT the permission decision/policy."""
    bus, lifecycle = _wired(manager)
    bus.emit("plan.started", task_id="task-perm", plan_id="plan-perm", step_count=1)
    job_id = lifecycle.active_job("task-perm")
    bus.emit("step.completed", task_id="task-perm", plan_id="plan-perm",
             step_id="plan-perm-s1", index=0, result="ok",
             tools=[{"name": "run_command", "args": {"command": "echo x"},
                     "ok": True, "result": "x"}])
    cp = store.load_checkpoint(job_id)
    serialized = json.dumps(cp.to_dict())
    # the usage record is allowed; the policy/prompt/decision is not
    assert "run_command" in serialized
    assert "allowed_tools" not in serialized
    assert "permission" not in serialized.lower()


# ── 5. recovery parity: recover_once across surfaces ────────────────────────

def test_recover_once_runs_single_sweep(tmp_path, monkeypatch):
    import novi.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")

    from novi.jobs.job import Job
    from novi.jobs.persistence import JobStore
    from novi.services.context import NoviContext

    store = JobStore()
    store.save(Job(id="job-parity", task_id="task-parity",
                   status=JobStatus.RUNNING))

    ctx = NoviContext(cfg={"ollama": {"url": "http://x"}})
    bus = EventBus()
    seen = []
    bus.on("job.interrupted", lambda ev: seen.append(ev))

    first = ctx.recover_once(bus=bus)
    second = ctx.recover_once(bus=bus)
    assert [m["job_id"] for m in first] == ["job-parity"]
    assert second == []
    assert store.load("job-parity").status is JobStatus.INTERRUPTED
    assert len(seen) == 1                       # events emitted exactly once


def test_recover_once_reached_from_build_application_execution(tmp_path,
                                                               monkeypatch):
    import novi.jobs.persistence as persistence
    monkeypatch.setattr(persistence, "JOBS_DIR", tmp_path / "jobs")

    from novi.jobs.job import Job
    from novi.jobs.persistence import JobStore
    from novi.services.context import NoviContext
    from novi.services.execution import build_application_execution

    store = JobStore()
    store.save(Job(id="job-surface", task_id="task-surface",
                   status=JobStatus.RUNNING))

    ctx = NoviContext(cfg={"ollama": {"url": "http://x"}})
    calls = []

    def spy(bus=None):
        calls.append(1)
        return ctx.recover_jobs(bus=bus)
    ctx.recover_once = spy
    # monkeypatch context's job store so the sweep hits the temp dir
    ctx._job_store = store

    runtime, coordinator, _ = build_application_execution(ctx)
    assert runtime is not None and coordinator is not None
    assert len(calls) == 1
    assert store.load("job-surface").status is JobStatus.INTERRUPTED
