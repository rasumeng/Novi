"""The Brain must be constructible-off for the beta.

The ``brain`` property builds a ``VectorStore``, which embeds the whole corpus
through Ollama during startup. That single call was ~183s of embedding requests
on a 39-document corpus and made the first chat connection (and the test suite)
unusably slow.

With ``brain.enabled = false`` the property returns ``None`` and nothing is
constructed. Every existing caller is already ``None``-safe
(``run_composition``, ``write_knowledge``, ``search_memory``), so no caller
changes are required.
"""

from novi.services.context import _memory_enabled


def _cfg(**extra):
    base = {"ollama": {"url": "http://localhost:11434"}, "embedding": {
        "backend": "ollama", "model": "nomic-embed-text:v1.5", "dimension": 768}}
    base.update(extra)
    return base


def test_brain_flag_defaults_to_disabled_for_a_bare_beta():
    from novi.services.context import _brain_enabled

    # No explicit brain section at all -> minimal posture.
    assert _brain_enabled(_cfg()) is False


def test_brain_flag_honours_an_explicit_enable():
    from novi.services.context import _brain_enabled

    assert _brain_enabled(_cfg(brain={"enabled": True})) is True


def test_brain_flag_tolerates_malformed_config():
    from novi.services.context import _brain_enabled

    assert _brain_enabled(_cfg(brain=None)) is False
    assert _brain_enabled({"brain": "nonsense"}) is False


def test_memory_flag_still_defaults_to_enabled_when_unset():
    # Guards the existing memory gate: absent means enabled, so an existing
    # install that predates the flag keeps its behaviour.
    assert _memory_enabled(_cfg()) is True
    assert _memory_enabled(_cfg(memory={"enabled": False})) is False
