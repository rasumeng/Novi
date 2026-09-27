"""Integration tests for the v2 pipeline: Orchestrator → JobManager → Runtime."""
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def orchestrator():
    import json
    from novi.orchestrator.orchestrator import Orchestrator
    from novi.orchestrator.router import WorkloadRouter
    from novi.capabilities import CapabilityRegistry
    from novi.capabilities.builtin import register_builtin_capabilities
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    mapping = {
        "write a python function": {"workload": "code", "confidence": 0.9, "relation": "new", "state": {"topic": "code", "workload": "code", "status": "in_progress"}, "reasoning": ""},
        "latest news about ai": {"workload": "research", "confidence": 0.9, "relation": "new", "state": {"topic": "", "workload": "research", "status": "in_progress"}, "reasoning": ""},
        "implement a python function": {"workload": "code", "confidence": 0.9, "relation": "new", "state": {"topic": "code", "workload": "code", "status": "in_progress"}, "reasoning": ""},
    }
    class _M:
        def invoke(self, prompt: str) -> str:
            pl = prompt.lower()
            for k, v in mapping.items():
                if k in pl:
                    return json.dumps(v)
            return json.dumps({"workload": "general", "confidence": 0.85, "relation": "new", "state": {}, "reasoning": ""})
    router = WorkloadRouter(llm=_M())
    return Orchestrator(capability_registry=registry, router=router)


@pytest.fixture
def job_manager():
    from novi.jobs.manager import JobManager
    return JobManager()


# ── Test: Orchestrator produces correct plans ──────────────────────────────


class TestOrchestrator:
    def test_conversation_intent(self, orchestrator):
        plan = orchestrator.plan("Hello, how are you?")
        assert plan.goal is not None
        assert plan.goal.intent.value == "conversation"
        assert any(c.id == "conversation" for c in plan.capabilities)
        assert plan.strategy.value == "respond"
        assert plan.max_steps <= 5

    def test_coding_intent(self, orchestrator):
        plan = orchestrator.plan("Write a Python function to sort a list")
        assert plan.goal.intent.value == "coding"
        assert "coding" in plan.capabilities or "coding" in str(plan.capabilities)
        assert plan.strategy.value == "execute"
        assert plan.tools is not None

    def test_research_intent(self, orchestrator):
        plan = orchestrator.plan("What is the latest news about AI?")
        assert plan.goal.intent.value == "research"
        assert plan.strategy.value == "research"

    def test_force_capability_override(self, orchestrator):
        plan = orchestrator.plan("Hello", force_capability="coding")
        assert "coding" in plan.capabilities or "coding" in str(plan.capabilities)

    def test_plan_has_tools(self, orchestrator):
        plan = orchestrator.plan("Refactor this Python code")
        assert len(plan.tools) > 0


# ── Test: JobManager lifecycle ─────────────────────────────────────────────


class TestJobManager:
    def test_submit_and_start(self, job_manager):
        job = job_manager.submit(task_id="task-1", strategy="execute")
        assert job.id.startswith("job-")
        assert job.status.value == "pending"
        assert job_manager.start(job.id) is True
        assert job.status.value == "running"

    def test_submit_pause_resume(self, job_manager):
        job = job_manager.submit(task_id="task-2")
        job_manager.start(job.id)
        from novi.jobs.job import Checkpoint
        cp = Checkpoint(job_id=job.id, step=3)
        assert job_manager.pause(job.id, cp) is True
        assert job.status.value == "paused"
        new_job = job_manager.resume(job.id)
        assert new_job is not None
        assert new_job.status.value == "queued"
        assert new_job.metadata.get("resumed_from") == job.id

    def test_submit_cancel(self, job_manager):
        job = job_manager.submit(task_id="task-3")
        job_manager.start(job.id)
        assert job_manager.cancel(job.id) is True
        assert job.status.value == "cancelled"

    def test_submit_complete(self, job_manager):
        job = job_manager.submit(task_id="task-4")
        job_manager.start(job.id)
        assert job_manager.complete(job.id, result="done") is True
        assert job.status.value == "done"

    def test_list_by_task(self, job_manager):
        job_manager.submit(task_id="task-group")
        job_manager.submit(task_id="task-group")
        job_manager.submit(task_id="task-other")
        group_jobs = job_manager.list_by_task("task-group")
        assert len(group_jobs) == 2

    def test_retry(self, job_manager):
        job = job_manager.submit(task_id="task-retry", max_retries=3)
        job_manager.start(job.id)
        job_manager.complete(job.id, error="oops")
        retried = job_manager.retry(job.id)
        assert retried is not None
        assert retried.retry_count == 1

    def test_max_retries_exceeded(self, job_manager):
        job = job_manager.submit(task_id="task-maxretry", max_retries=0)
        assert job_manager.retry(job.id) is None



# ── Test: End-to-end pipeline integration ───────────────────────────────────


class TestPipelineIntegration:
    def test_orchestrator_to_jobmanager(self, orchestrator, job_manager):
        """Orchestrator produces plan → JobManager creates job."""
        plan = orchestrator.plan("Write a Python script to count files")
        job = job_manager.submit(
            task_id=plan.task_id,
            strategy=plan.strategy.value,
            metadata={"intent": plan.goal.intent.value},
        )
        assert job is not None
        assert job.status.value == "pending"
        assert job_manager.start(job.id) is True

    def test_orchestrator_plan_has_tools_for_coding(self, orchestrator):
        """Coding intent should resolve to coding tools."""
        plan = orchestrator.plan("implement a Python function to sort a list")
        assert len(plan.tools) > 0
        has_workspace_tool = any(
            t in ("read_file", "glob", "write_file", "edit_file", "bash", "grep")
            for t in plan.tools
        )
        assert has_workspace_tool, f"No workspace tools found in {plan.tools}"

    def test_orchestrator_plan_has_tools_for_research(self, orchestrator):
        """Research intent should resolve to web tools."""
        plan = orchestrator.plan("Search the web for AI news")
        has_web_tool = any("web" in t or "search" in t for t in plan.tools)
        assert has_web_tool, f"No web tools found in {plan.tools}"
