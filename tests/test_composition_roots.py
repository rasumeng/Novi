"""Post-Phase-9C composition-root wiring guards.

These tests pin the dependency-injection invariants the audit found broken:

1. ``NoviContext.orchestrator`` must resolve ExecutionPlan.tools through a
   capability registry that actually contains the builtin capabilities. An
   empty registry silently plans zero-tool runs on every non-WebUI surface
   (CLI / Telegram / background / scheduler all execute via
   ``build_application_execution`` → ``ctx.create_runtime``).

2. The WebUI backend must not construct a second Orchestrator/TaskStore next
   to the composition root's. ``JobLifecycle`` binds
   ``ctx.orchestrator.task_store``; a forked store silently drops
   ExecutionHistory and task result write-backs for WebUI runs.
"""

from pathlib import Path

import pytest


@pytest.fixture
def stub_model_service(monkeypatch):
    """A ModelService stand-in so no network/provider discovery ever runs."""
    class _StubService:
        def resolve(self, workload):
            return "ollama", "stub-model"

        def client(self, workload, temperature=0.0):
            raise AssertionError("stub: no chat model expected in these tests")

    from novi.services.context import NoviContext

    def _apply(ctx):
        monkeypatch.setattr(ctx, "_model_service", _StubService(), raising=False)
        return ctx

    return _apply


def test_context_orchestrator_resolves_builtin_capabilities(stub_model_service):
    """Coding plans built through the composition root carry real tools."""
    from novi.services.context import NoviContext

    ctx = stub_model_service(NoviContext(cfg={"ollama": {"url": "http://x"}}))
    orchestrator = ctx.orchestrator
    plan = orchestrator.plan("fix the bug in main.py by editing the function")

    assert plan.capabilities, "capabilities must resolve through the registry"
    assert plan.tools, (
        "ExecutionPlan.tools must be populated — an empty registry here "
        "plans zero-tool runs on every surface wired through NoviContext"
    )


def test_context_orchestrator_registry_has_builtins(stub_model_service):
    from novi.services.context import NoviContext

    ctx = stub_model_service(NoviContext(cfg={"ollama": {"url": "http://x"}}))
    registered = {c.id for c in ctx.orchestrator.capabilities.list()}
    for cap_id in ("conversation", "search", "research", "coding",
                   "filesystem", "terminal", "planning", "vision", "memory"):
        assert cap_id in registered


def test_webui_job_lifecycle_shares_context_task_store():
    """First JobLifecycle access must not fork a second TaskStore.

    Regression: build_backend used to construct its own Orchestrator, leaving
    ctx._orchestrator unset; the first ``ctx.job_lifecycle`` access then
    materialized a competing Orchestrator + empty TaskStore, orphaning all
    WebUI ExecutionHistory writes.
    """
    from novi.services.context import NoviContext

    ctx = NoviContext(cfg={"ollama": {"url": "http://x"}})
    job_lifecycle = ctx.job_lifecycle

    assert job_lifecycle._task_store is ctx.orchestrator.task_store


def test_webui_backend_composes_context_orchestrator_source():
    """webui.py must derive its orchestrator/store from the composition root.

    Source-level guard (same style as the architecture tests): constructing
    ``Orchestrator(...)`` or a bare ``TaskStore()`` inside webui.py forks the
    composition root's identity.
    """
    src = (Path(__file__).resolve().parents[1] / "novi" / "webui.py").read_text(
        encoding="utf-8")
    assert "Orchestrator(" not in src
    assert "TaskStore()" not in src
    assert "ctx.orchestrator" in src


def test_context_tracks_live_config_even_when_seeded(tmp_path, monkeypatch):
    """A seeded NoviContext must still subscribe to configuration changes.

    Regression (live model switching): the WebUI composition path constructs
    ``NoviContext(cfg=snapshot)`` with an already-materialized framework
    snapshot (``get_backend`` -> ``WebUIBackend(cfg)``). The ``on_any``
    subscription used to live inside ``if self._cfg is None``, so it never ran,
    ModelService kept its startup snapshot, and workload/model changes only
    took effect after an application restart.
    """
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    import novi.configuration.bootstrap as boot

    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".novi" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)

    from novi.configuration.bootstrap import get_configuration
    from novi.services.context import NoviContext

    framework = get_configuration()
    framework.set("llm.workloads.general.model", "model-a", by="test")

    # Seeded exactly like webui_server.get_backend seeds it: snapshot first.
    ctx = NoviContext(cfg=framework.snapshot())

    updates = []

    class _FakeService:
        def update_configuration(self, config):
            updates.append(config)

    ctx._model_service = _FakeService()

    _ = ctx.config  # first access must subscribe even when _cfg is pre-seeded

    framework.set("llm.workloads.general.model", "model-b", by="test")

    assert ctx.config["llm"]["workloads"]["general"]["model"] == "model-b"
    assert updates and updates[-1]["llm"]["workloads"]["general"]["model"] == "model-b"

    # Subscription is idempotent: repeated property access never stacks handlers.
    accesses_before = len(updates)
    _ = ctx.config
    _ = ctx.config
    framework.set("llm.workloads.general.model", "model-c", by="test")
    assert len(updates) == accesses_before + 1
