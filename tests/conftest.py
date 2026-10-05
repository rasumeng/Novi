"""Shared test fixtures and isolation for the full test suite."""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_profile(tmp_path_factory):
    """Point the Novi profile at a temp directory for the whole test session.

    ``novi.paths.HOME`` is a module-level constant with no environment
    override, so any test that calls ``Configuration.set()`` writes straight
    into the developer's real ``~/.novi/config.toml``. That has silently
    rewritten ``llm.primary_model``, flipped the search backend, and injected
    fake credentials into the live profile during ordinary test runs.

    Module-level profile constants are already bound at import time, so the
    ones that matter are re-pointed here too.
    """
    from novi import paths
    from novi.configuration import bootstrap

    root = tmp_path_factory.mktemp("novi-profile")
    paths.HOME = root
    bootstrap._configuration = None

    try:
        import novi.webui_server as webui_server
    except Exception:  # pragma: no cover - server import is optional here
        webui_server = None
    if webui_server is not None:
        for name, sub in (("CHATS_DIR", "chats"), ("ATTACHMENTS_DIR", "attachments"),
                          ("SKILLS_DIR", "skills")):
            if hasattr(webui_server, name):
                setattr(webui_server, name, root / sub)

    yield root

    bootstrap._configuration = None


@pytest.fixture(autouse=True)
def _clear_global_brain():
    """Reset the process-global Brain singleton after each test.

    ``novi.services.context`` registers the active Brain via ``set_brain``
    when a ``NoviContext``/``WebUIBackend`` boot test runs. Without cleanup
    the singleton leaks into later tests that read ``get_brain()`` (e.g. tool
    retrieval) and bypass their fakes, causing order-dependent failures.
    """
    yield
    from novi.brain import set_brain

    set_brain(None)


@pytest.fixture(autouse=True)
def _memory_enabled_for_tests(monkeypatch):
    """Pin the canonical ``memory.enabled`` flag on for the whole suite.

    Several gates read it and would otherwise follow the developer's personal
    ``~/.novi/config.toml``: ``retrieval._memory_enabled``, the
    ``/api/memory/*`` endpoints, and the model-facing tool filter. Without this
    pin, tests asserting memory plumbing pass or fail depending on a local
    toggle.

    Intercepts only reads of that one key rather than replacing the accessor:
    swapping ``get_configuration`` wholesale also breaks tests that drive
    configuration on purpose (model selection, setup/consent). Monkeypatch --
    not ``Configuration.set`` -- so nothing is written to the user's config
    even if the run is killed before teardown.
    """
    from novi.configuration.manager import Configuration
    from novi.runtime import retrieval

    real_get = Configuration.get

    def get_with_memory_pinned(self, key, default=None):
        if key == "memory.enabled":
            return True
        return real_get(self, key, default)

    monkeypatch.setattr(Configuration, "get", get_with_memory_pinned)
    monkeypatch.setattr(retrieval, "_memory_enabled", lambda: True)