from pathlib import Path
from types import SimpleNamespace

from novi.runtime.run_contracts import ModelSnapshot, RunImage, RunRequest, RunStatus
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


class InlineImageModel:
    def stream(self, messages):
        user = next(message for message in messages if message.type == "human")
        text = user.content[0]["text"]
        assert "What does this say?" in text
        assert "image.png" in text
        assert "Answer directly" in text
        assert user.content[1]["type"] == "image_url"
        assert user.content[1]["image_url"]["url"].startswith(
            "data:image/png;base64,")
        yield SimpleNamespace(content="It says hello.", tool_calls=[],
                              usage_metadata=None, response_metadata={})


class SystemPromptSpy:
    def __init__(self):
        self.system = ""

    def stream(self, messages):
        system = next((m for m in messages if m.type == "system"), None)
        self.system = system.content if system else ""
        yield SimpleNamespace(content="ok", tool_calls=[],
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


def test_attached_image_is_inline_and_legacy_image_tool_is_not_exposed(tmp_path):
    image_path = tmp_path / "att-1.png"
    image_path.write_bytes(b"image")
    ctx = Context(InlineImageModel())
    service = build_run_service(
        ctx, persist_dir=tmp_path / "runs", skill_root=tmp_path / "skills",
        registry={"analyze_image": lambda file_path, prompt="": "legacy"},
    )
    try:
        run_id = service.start(RunRequest(
            conversation_id="image-chat", user_message_id="u1",
            user_text="What does this say?", images=(RunImage(
                id="att-1", name="image.png", media_type="image/png",
                path=str(image_path),
            ),),
        ))
        assert service.snapshot(run_id).status is RunStatus.COMPLETED
        assert "analyze_image" not in {
            tool.name for tool in ctx.model_service.bound[1]
        }
    finally:
        service.close()


def test_system_prompt_forbids_tool_use_when_context_already_answers(tmp_path):
    # Small models over-apply the tool-forward instructions and invent
    # searches for content that is already in the conversation.
    spy = SystemPromptSpy()
    ctx = Context(spy)
    service = build_run_service(ctx, persist_dir=tmp_path,
        skill_root=tmp_path / "skills", registry={})
    try:
        run_id = service.start(RunRequest(conversation_id="direct",
                                          user_message_id="u1",
                                          user_text="what does this say?"))
        assert service.snapshot(run_id).status is RunStatus.COMPLETED
    finally:
        service.close()
    prompt = spy.system.lower()
    assert "already have" in prompt
    assert "directly" in prompt
    assert "instead of" in prompt


def test_memory_tools_are_hidden_when_memory_is_disabled(tmp_path):
    ctx = Context(Model())
    ctx.config["memory"] = {"enabled": False}
    service = build_run_service(
        ctx, persist_dir=tmp_path, skill_root=tmp_path / "skills",
        registry={"echo": lambda value: value,
                  "search_knowledge": lambda query, k=5: "notes",
                  "search_memory": lambda query, k=5: "memories"})
    try:
        service.start(RunRequest(conversation_id="c", user_message_id="u1",
                                 user_text="go"))
        names = {tool.name for tool in ctx.model_service.bound[1]}
        assert "echo" in names
        assert "search_knowledge" not in names
        assert "search_memory" not in names
    finally:
        service.close()


def test_memory_tools_are_exposed_when_memory_is_enabled(tmp_path):
    ctx = Context(Model())
    ctx.config["memory"] = {"enabled": True}
    service = build_run_service(
        ctx, persist_dir=tmp_path, skill_root=tmp_path / "skills",
        registry={"echo": lambda value: value,
                  "search_knowledge": lambda query, k=5: "notes",
                  "search_memory": lambda query, k=5: "memories"})
    try:
        service.start(RunRequest(conversation_id="c", user_message_id="u1",
                                 user_text="go"))
        names = {tool.name for tool in ctx.model_service.bound[1]}
        assert {"search_knowledge", "search_memory"} <= names
    finally:
        service.close()


def test_system_prompt_states_principles_not_search_recipes(tmp_path):
    spy = SystemPromptSpy()
    ctx = Context(spy)
    service = build_run_service(ctx, persist_dir=tmp_path,
        skill_root=tmp_path / "skills", registry={})
    try:
        service.start(RunRequest(conversation_id="p", user_message_id="u1",
                                 user_text="hi"))
    finally:
        service.close()
    prompt = spy.system
    # The old recipe ordered a local-search-then-web-search sequence, which
    # small models follow literally on every question.
    assert "If local searches do not contain" not in prompt
    assert "Do not repeatedly rephrase the same local search" not in prompt
    assert "search_knowledge" not in prompt
    assert "search_memory" not in prompt


def test_system_prompt_keeps_the_native_tool_call_constraint(tmp_path):
    # The dispatcher only accepts native tool_calls; this must survive.
    spy = SystemPromptSpy()
    ctx = Context(spy)
    service = build_run_service(ctx, persist_dir=tmp_path,
        skill_root=tmp_path / "skills", registry={})
    try:
        service.start(RunRequest(conversation_id="p", user_message_id="u1",
                                 user_text="hi"))
    finally:
        service.close()
    assert "native tool call" in spy.system


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
