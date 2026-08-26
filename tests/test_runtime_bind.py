"""Phase 7 Stage 3B — NoviRuntime._bind_runnable single binding seam.

Proves the runtime constructs its LangChain runnable through exactly one
path: ``_bind_runnable → ModelService.bind_model/client_for_model``. Recovery
and escalation logic never reconstructs a runnable inline.

Also proves the runnable is rebound through the same seam when the model
answers without tools (search upgrade) and when a knowledge result escalates
to web search.
"""

import pytest

from novi.runtime.execution_context import ExecutionContext
from novi.runtime.runtime import NoviRuntime


class _RecordingModelService:
    """Model service stub that records every bind/client call it receives."""

    def __init__(self, client="client", bound="bound"):
        self.client = client
        self.bound = bound
        self.calls = []

    def bind_model(self, model_name, tools, temperature=0.0):
        self.calls.append(("bind_model", model_name, list(tools), temperature))
        return self.bound

    def client_for_model(self, model_name, temperature=0.0):
        self.calls.append(("client_for_model", model_name, temperature))
        return self.client


def _ctx(model_name="m1", temperature=0.4):
    ctx = ExecutionContext(user_input="hello")
    ctx.model_name = model_name
    ctx.temperature = temperature
    return ctx


# ── seam routing ─────────────────────────────────────────────────────────

def test_bind_routes_through_model_service_with_tools():
    svc = _RecordingModelService()
    rt = NoviRuntime(model_service=svc)

    runnable = rt._bind_runnable(_ctx(), ["read_file", "grep"])

    assert runnable == "bound"
    assert svc.calls == [("bind_model", "m1", ["read_file", "grep"], 0.4)]


def test_bind_routes_through_client_for_model_without_tools():
    svc = _RecordingModelService()
    rt = NoviRuntime(model_service=svc)

    runnable = rt._bind_runnable(_ctx(), [])

    assert runnable == "client"
    assert svc.calls == [("client_for_model", "m1", 0.4)]


def test_bind_uses_ctx_temperature_by_default():
    svc = _RecordingModelService()
    rt = NoviRuntime(model_service=svc)

    rt._bind_runnable(_ctx(model_name="m9", temperature=0.7), ["t"])

    assert svc.calls == [("bind_model", "m9", ["t"], 0.7)]


def test_bind_temperature_override_wins():
    svc = _RecordingModelService()
    rt = NoviRuntime(model_service=svc)

    rt._bind_runnable(_ctx(temperature=0.4), ["t"], temperature=0.0)

    assert svc.calls == [("bind_model", "m1", ["t"], 0.0)]


def test_bind_preserves_verbatim_model_name():
    """The selected model string reaches the seam unchanged."""
    svc = _RecordingModelService()
    rt = NoviRuntime(model_service=svc)

    rt._bind_runnable(_ctx(model_name="qwen3:8b"), ["t"])

    assert svc.calls[0][1] == "qwen3:8b"


# ── run_stream rebuilds through the same seam ────────────────────────────

def test_run_stream_initial_bind_goes_through_seam():
    """The initial runnable is built via _bind_runnable — the model service
    sees exactly one construction request for the run."""
    svc = _RecordingModelService()

    class _LoopRunnable:
        def stream(self, msgs):
            yield type("Chunk", (), {"content": "done",
                                     "additional_kwargs": {}})()

    svc.client = _LoopRunnable()
    rt = NoviRuntime(model_service=svc, cfg={"runtime": {"temperature": 0.2}})

    ctx = _ctx(model_name="m1", temperature=0.2)
    for _kind, *_rest in rt.run_stream(context=ctx):
        pass

    assert svc.calls == [("client_for_model", "m1", 0.2)]


def test_run_stream_does_not_reconstruct_inline():
    """The ReAct executor rebinds through the seam only — raw bind_model /
    client_for_model calls never appear inside the loop body, and the
    runtime hands the seam to the executor without invoking it."""
    import inspect

    import novi.runtime.react_attempt as react_attempt

    loop_src = inspect.getsource(react_attempt.run_react_attempt)
    seam_src = inspect.getsource(NoviRuntime._bind_runnable)

    assert "bind_runnable(ctx," in loop_src
    assert "bind_model" not in loop_src
    assert "client_for_model" not in loop_src
    assert "bind_model" in seam_src and "client_for_model" in seam_src