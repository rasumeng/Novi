"""Shared test fixtures and isolation for the full test suite."""

import pytest


@pytest.fixture(autouse=True)
def _clear_global_brain():
    """Reset the process-global Brain singleton after each test.

    ``cozmo.services.context`` registers the active Brain via ``set_brain``
    when a ``CozmoContext``/``WebUIBackend`` boot test runs. Without cleanup
    the singleton leaks into later tests that read ``get_brain()`` (e.g. tool
    retrieval) and bypass their fakes, causing order-dependent failures.
    """
    yield
    from cozmo.brain import set_brain

    set_brain(None)