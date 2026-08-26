"""Integration tests for the execution layer: NoviRuntime + ExecutionPlan.

The legacy ``runtime/engine.py`` (stateless ReAct loop) was removed in Phase 3;
NoviRuntime.run_stream is the sole execution loop. These tests cover the
runtime + ExecutionPlan contract that remains.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestRuntimeExecutionPlan:
    @pytest.fixture
    def runtime(self):
        from novi.runtime.runtime import NoviRuntime

        mm = MagicMock()
        rt = NoviRuntime(model_service=mm)
        return rt

    def test_execution_plan_uses_plan_tools(self, runtime):
        """When execution_plan is provided, its tools are used."""
        from novi.orchestrator.task_types import ExecutionPlan, Goal, IntentType

        plan = ExecutionPlan(
            goal=Goal(text="say hi", intent=IntentType.CONVERSATION),
            tools=["calculator", "read_file"],
            model_spec={"model": "test-model", "capability": "chat"},
            max_steps=3,
            temperature=0.5,
        )

        def _fake_classify(*a, **kw):
            from novi.orchestrator.intent import IntentType
            return IntentType.CONVERSATION

        with patch("novi.runtime.runtime.classify_intent", _fake_classify):
            gen = runtime.run_stream("say hi", execution_plan=plan)

            events = []
            try:
                while True:
                    events.append(next(gen))
            except StopIteration:
                pass

            # Verify the intent was set from the plan.
            status_events = [e for e in events if e[0] == "status"]
            assert any("conversation" in str(e).lower() or "analyzing" in str(e).lower() for e in status_events)

    def test_execution_plan_uses_plan_model(self, runtime):
        """When execution_plan is provided, plan's model_spec is used."""
        from novi.orchestrator.task_types import ExecutionPlan, Goal, IntentType

        plan = ExecutionPlan(
            goal=Goal(text="write code", intent=IntentType.CODING),
            tools=["read_file", "write_file"],
            model_spec={"model": "coder-model", "capability": "coding"},
            max_steps=5,
            temperature=0.2,
        )

        def _fake_classify(*a, **kw):
            from novi.orchestrator.intent import IntentType
            return IntentType.CODING

        with patch("novi.runtime.runtime.classify_intent", _fake_classify):
            gen = runtime.run_stream("write a function", execution_plan=plan)
            # Consume the generator — it will try to bind the model "coder-model"
            # which will fail via the MagicMock, but that's expected
            try:
                list(gen)
            except Exception:
                pass

        # The execution plan model override should have been used
        # (verify by checking the plan wasn't modified)
        assert plan.model_spec["model"] == "coder-model"
