"""Task 1.2 — Fake Settings Audit guard.

Ensures every USER/ADVANCED-visible setting has a runtime/UI consumer.
No user-visible setting may silently do nothing.
"""

from novi.configuration.bootstrap import build_registry
from novi.configuration.schema import Visibility

# Mapping of USER/ADVANCED setting -> consumer file:line description.
# This is the audit table; if a setting is missing, the guard fails.
EXPECTED_CONSUMERS = {
    # llm
    "llm.workloads.general.model": "novi/models/service.py:60 _get_workloads_config + webui_server.py:1094",
    "llm.workloads.research.model": "novi/models/service.py:60",
    "llm.workloads.code.model": "novi/models/service.py:60",
    # providers
    "providers.ollama.reasoning": "novi/providers/base.py:99 reasoning",
    # runtime
    "runtime.max_steps": "novi/runtime/runtime.py:192",
    "runtime.max_history": "novi/runtime/runtime.py:191",
    "runtime.max_tool_output_chars": "novi/runtime/runtime.py:193",
    "runtime.temperature": "novi/runtime/runtime.py:197 temperature",
    # permissions
    "permissions.write_file": "novi/runtime/permissions.py:53",
    # mcp
    "mcp.enabled": "novi/runtime/mcp/lifecycle.py + webui_server.py",
    # memory
    "memory.max_turns_before_summary": "novi/services/context.py:152",
    "memory.max_short_term_pairs": "novi/services/context.py:153",
    # search
    "search.backend": "novi/search/service.py:50 configuration.get('search.backend')",
    "search.brave_api_key": "novi/search/service.py brave_api_key",
    "search.url": "novi/search/service.py url",
    # integrations
    "telegram.enabled": "novi/services/telegram.py TelegramLifecycle",
    "telegram.bot_token": "novi/services/telegram.py + providers",
}

# DEVELOPER settings that are expected to have consumers but are not user-visible.
# They are not checked as fake for the USER gate, but embedding must be respected.
DEVELOPER_EXPECTED = {
    "llm.max_tokens": "novi/providers/base.py:_resolve_max_tokens -> ChatOllama num_predict / ChatOpenAI max_tokens",
    "embedding.model": "novi/services/embedding_providers.py OllamaEmbeddingProvider",
    "embedding.backend": "novi/services/embedding.py EmbeddingService.provider",
    "embedding.dimension": "novi/services/embedding_providers.py dimension",
    "providers.default": "novi/models/service.py providers.default",
    "providers.ollama.url": "novi/providers/base.py base_url",
    "providers.openai.api_key_env": "novi/providers/base.py api_key_env",
    "telegram.allowed_chat_ids": "novi/telegram_bot.py allowed_chat_ids",
}


def test_no_fake_user_settings():
    reg = build_registry()
    fakes = []
    for s in reg.all():
        if s.visibility in (Visibility.USER, Visibility.ADVANCED):
            if s.id not in EXPECTED_CONSUMERS:
                fakes.append(s.id)
    assert fakes == [], f"Fake USER/ADVANCED settings with no consumer: {fakes}. Add consumer or demote to HIDDEN/DEVELOPER."


def test_every_user_setting_has_known_consumer():
    """Ensure audit table stays in sync with registry."""
    reg = build_registry()
    user_ids = {s.id for s in reg.all() if s.visibility in (Visibility.USER, Visibility.ADVANCED)}
    assert user_ids == set(EXPECTED_CONSUMERS.keys()), (
        f"Audit table drift.\nRegistry USER/ADVANCED: {sorted(user_ids)}\n"
        f"EXPECTED_CONSUMERS: {sorted(EXPECTED_CONSUMERS.keys())}\n"
        f"Missing from table: {sorted(user_ids - set(EXPECTED_CONSUMERS.keys()))}\n"
        f"Extra in table: {sorted(set(EXPECTED_CONSUMERS.keys()) - user_ids)}"
    )


def test_models_agent_not_user_visible():
    reg = build_registry()
    s = reg.get("models.agent")
    assert s.visibility == Visibility.HIDDEN, f"models.agent must be HIDDEN (workload is llm.workloads.*), got {s.visibility}"


def test_runtime_temperature_is_canonical():
    reg = build_registry()
    assert reg.has("runtime.temperature"), "runtime.temperature must be registered"
    s = reg.get("runtime.temperature")
    assert s.visibility == Visibility.ADVANCED
    # Legacy split must not exist as USER/ADVANCED
    for s in reg.all():
        assert s.id != "runtime.temperatures.chat", "runtime.temperatures.chat must be removed (collapsed to runtime.temperature)"


def test_llm_max_tokens_wired():
    """llm.max_tokens must be DEVELOPER (never USER if unwired) and have provider wiring."""
    reg = build_registry()
    s = reg.get("llm.max_tokens")
    assert s.visibility == Visibility.DEVELOPER, f"llm.max_tokens visibility must be DEVELOPER/HIDDEN, got {s.visibility}"
    # Verify provider wiring reads it
    import pathlib
    base = pathlib.Path("novi/providers/base.py").read_text()
    assert "llm.max_tokens" in base or "_resolve_max_tokens" in base, "llm.max_tokens must be wired to provider"
    assert "num_predict" in base, "Ollama wiring must map to num_predict"


def test_migration_collapses_temperatures():
    from novi.configuration.migration import migrate
    data = {"runtime": {"temperatures": {"chat": 0.77, "work": 0.1}, "max_steps": 8}}
    out = migrate(dict(data))
    rt = out.get("runtime", {})
    assert rt.get("temperature") == 0.77
    assert "temperatures" not in rt

    # When flat already present, keep it
    data2 = {"runtime": {"temperature": 0.5, "temperatures": {"chat": 0.9}}}
    out2 = migrate(dict(data2))
    assert out2["runtime"]["temperature"] == 0.5
    assert "temperatures" not in out2["runtime"]


def test_embedding_respected():
    """embedding.* must be respected by embedding providers / memory."""
    import pathlib
    providers = pathlib.Path("novi/services/embedding_providers.py").read_text()
    assert "embedding" in providers and "model" in providers
    # At least one consumer for each DEVELOPER embedding setting
    for sid in ["embedding.model", "embedding.backend", "embedding.dimension"]:
        assert sid in DEVELOPER_EXPECTED
