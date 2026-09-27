from pathlib import Path
from types import SimpleNamespace

from novi.runtime.run_contracts import ModelSnapshot, RunRequest, RunStatus
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
            yield SimpleNamespace(content="done", tool_calls=[],
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


class SourceModel:
    def __init__(self):
        self.calls = 0

    def stream(self, messages):
        self.calls += 1
        if self.calls == 1:
            yield SimpleNamespace(content="", tool_calls=[{
                "id": "source-list", "name": "list_source_files", "args": {}}],
                usage_metadata=None, response_metadata={})
        else:
            yield SimpleNamespace(content="notes.txt", tool_calls=[],
                usage_metadata=None, response_metadata={})


class Sources:
    def __init__(self):
        self.listed_for = None

    def get_conversation_sources(self, conversation_id):
        return [{"root": "C:/notes", "hash": "abc", "capability": "READ"}]

    def list_conversation_files(self, conversation_id):
        self.listed_for = conversation_id
        return [{"source": "abc", "folder": "notes", "path": "notes.txt"}]


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
        "echo", "report_progress", "activate_skill"}
    service.close()


def test_conversation_sources_are_exposed_as_scoped_read_tools(tmp_path):
    ctx = Context(SourceModel())
    ctx.workspace_service = Sources()
    service = build_run_service(ctx, persist_dir=tmp_path,
        skill_root=tmp_path / "skills", registry={})
    try:
        run_id = service.start(RunRequest(
            conversation_id="source-chat", user_message_id="u1",
            user_text="What is in this folder?"))
        assert service.snapshot(run_id).status is RunStatus.COMPLETED
        assert ctx.workspace_service.listed_for == "source-chat"
        assert {tool.name for tool in ctx.model_service.bound[1]} >= {
            "list_source_files", "search_source_files", "read_source_file"}
    finally:
        service.close()


def test_explicit_model_does_not_resolve_or_replace_primary(tmp_path):
    ctx = Context(Model())
    def unavailable():
        raise RuntimeError("primary is not configured")
    ctx.model_service.resolve_primary = unavailable
    service = build_run_service(ctx, persist_dir=tmp_path,
        skill_root=tmp_path / "skills", registry={"echo": lambda value: value})
    try:
        run_id = service.start(RunRequest(conversation_id="selected", user_message_id="u",
            user_text="go", model=ModelSnapshot(provider="fake", model="chosen-model")))
        assert service.snapshot(run_id).status is RunStatus.COMPLETED
        assert ctx.model_service.bound[0] == "chosen-model"
        assert service.snapshot(run_id).request.model.model == "chosen-model"
    finally:
        service.close()


def test_failed_model_binding_does_not_leave_queued_orphan(tmp_path):
    import pytest
    ctx = Context(Model())
    original = ctx.model_service.bind_model
    def unavailable(*args, **kwargs):
        raise RuntimeError("model unavailable")
    ctx.model_service.bind_model = unavailable
    service = build_run_service(ctx, persist_dir=tmp_path,
        skill_root=tmp_path / "skills", registry={"echo": lambda value: value})
    request = RunRequest(conversation_id="retry", user_message_id="u", user_text="go")
    try:
        with pytest.raises(RuntimeError, match="model unavailable"):
            service.start(request)
        assert service._store.runs_for_conversation("retry") == ()
        ctx.model_service.bind_model = original
        assert service.snapshot(service.start(request)).status is RunStatus.COMPLETED
    finally:
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


def test_nested_permission_settings_do_not_break_composition(tmp_path):
    """Only per-tool scalar rules belong in the runtime authorization map."""
    ctx = Context(Model())
    ctx.config = {
        "permissions": {
            "echo": "allow",
            "commands": {"default": "ask"},
            "paths": [str(tmp_path)],
        }
    }

    service = build_run_service(
        ctx,
        persist_dir=tmp_path,
        skill_root=tmp_path / "skills",
        registry={"echo": lambda value: value},
    )

    run_id = service.start(RunRequest(
        conversation_id="cli:nested-permissions",
        user_message_id="u1",
        user_text="go",
    ))
    assert service.snapshot(run_id).status is RunStatus.COMPLETED
    service.close()
