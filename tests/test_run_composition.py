from pathlib import Path
from types import SimpleNamespace

from novi.runtime.run_contracts import RunRequest, RunStatus
from novi.services.run_composition import build_run_service


class Model:
    def __init__(self):
        self.calls = 0

    def stream(self, messages):
        self.calls += 1
        if self.calls == 1:
            yield SimpleNamespace(content="", tool_calls=[{
                "id": "c1", "name": "echo", "args": {"value": "hello"}}],
                usage_metadata=None, response_metadata={})
        else:
            yield SimpleNamespace(content="", tool_calls=[{
                "id": "f1", "name": "finish_task",
                "args": {"summary": "done", "outcome": "completed"}}],
                usage_metadata=None, response_metadata={})


class Models:
    def __init__(self, model):
        self.model = model
        self.bound = None

    def resolve_primary(self):
        return "fake", "model"

    def bind_model(self, model_name, tools, temperature=0.0):
        self.bound = (model_name, tools)
        return self.model


class Context:
    def __init__(self, model):
        self.model_service = Models(model)
        self.config = {"permissions": {"echo": "allow"}}


def test_real_composition_binds_native_tools_and_executes_registered_function(tmp_path):
    seen = []
    ctx = Context(Model())
    service = build_run_service(ctx, persist_dir=tmp_path,
        skill_root=tmp_path / "skills",
        registry={"echo": lambda value: seen.append(value) or value})

    run_id = service.start(RunRequest(
        conversation_id="cli:test", user_message_id="u1", user_text="go"))

    assert seen == ["hello"]
    assert service.snapshot(run_id).status is RunStatus.COMPLETED
    assert {tool.name for tool in ctx.model_service.bound[1]} >= {
        "echo", "report_progress", "activate_skill", "finish_task"}
    service.close()


def test_headless_approval_is_recorded_as_blocked_work(tmp_path):
    model = Model()
    ctx = Context(model)
    ctx.config = {"permissions": {"echo": "ask"}}
    service = build_run_service(ctx, persist_dir=tmp_path, skill_root=tmp_path / "skills",
        headless=True,
        registry={"echo": lambda value: value})

    run_id = service.start(RunRequest(
        conversation_id="background:test", user_message_id="u1", user_text="go"))

    state = service.snapshot(run_id)
    assert state.status is RunStatus.BLOCKED
    assert "headless_approval_required" in (state.terminal_reason or "")
    service.close()
