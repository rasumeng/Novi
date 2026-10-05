"""Provider requests must time out instead of hanging forever."""

import httpx

from novi.providers.base import OllamaProvider


def test_ollama_client_gets_a_default_request_timeout():
    provider = OllamaProvider("qwen3.5:2b", {"url": "http://localhost:11434"})

    model = provider.get_chat_model()

    assert model.client_kwargs["timeout"] > 0


def test_request_timeout_is_configurable():
    provider = OllamaProvider("qwen3.5:2b",
                              {"url": "http://localhost:11434",
                               "request_timeout": 42})

    model = provider.get_chat_model()

    assert model.client_kwargs["timeout"] == 42


def test_invalid_request_timeout_falls_back_to_the_default():
    provider = OllamaProvider("qwen3.5:2b",
                              {"url": "http://localhost:11434",
                               "request_timeout": "not-a-number"})

    model = provider.get_chat_model()

    assert isinstance(model.client_kwargs["timeout"], (int, float))
    assert model.client_kwargs["timeout"] > 0


def test_timeout_error_is_classified_as_a_timeout():
    from novi.runtime.agent_loop import _is_timeout

    assert _is_timeout(httpx.ReadTimeout("timed out"))
    assert _is_timeout(RuntimeError("wrapped: timed out"))


def test_unrelated_error_is_not_a_timeout():
    from novi.runtime.agent_loop import _is_timeout

    assert not _is_timeout(ValueError("bad tool arguments"))
    assert not _is_timeout(RuntimeError("connection refused"))