"""Phase 3 contract tests for the desktop tools.

* ``analyze_image``/``screenshot`` use ONLY the selected general workload model
  (``llm.workloads.general.model``) — never ``models.vision``, never a hardcoded
  name.
* Vision capability is derived from the selected model's catalog/discovery
  facts.
* General lacks vision → explicit capability error; General unset/not installed
  → model-unavailable error.
* ``screenshot``, ``analyze_image``, ``clipboard_read`` keep their
  capability/permission wiring (``desktop.enabled`` gate).
* The legacy ``task`` subagent tool is removed.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cozmo.tools import TOOL_REGISTRY


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, ok=True):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.ok = ok

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError
            raise HTTPError(f"HTTP {self.status_code}")


def _config(general_model=""):
    """A minimal config snapshot with the general workload model set/empty."""
    return {
        "llm": {"workloads": {"general": {"model": general_model}, "research": {"model": ""}, "code": {"model": ""}}},
        "ollama": {"url": "http://localhost:11434"},
        "desktop": {"enabled": True},
    }


def _patch_config(cfg):
    return patch("cozmo.config.load", return_value=cfg)


def _fake_caps(supports_vision):
    return SimpleNamespace(
        capabilities=frozenset({"vision"} if supports_vision else {"chat"}),
        supports_tools=True,
        supports_vision=supports_vision,
        supports_reasoning=False,
        supports_coding=False,
    )


# ── Selected-model contract ─────────────────────────────────────────────────


def test_analyze_image_uses_only_general_workload_model(tmp_path):
    """The HTTP call must carry the selected general model, verbatim."""
    from cozmo.tools import desktop

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    with _patch_config(_config(general_model="qwen2.5vl:7b")), \
         patch("cozmo.runtime.model_selector.model_capabilities",
               return_value=_fake_caps(True)) as mc, \
         patch.object(desktop, "requests") as fake_requests:
        fake_requests.post.return_value = FakeResponse(200, {"message": {"content": "a desk"}})

        result = desktop.analyze_image(str(img))

    assert result == "a desk"
    mc.assert_called_once_with("qwen2.5vl:7b")
    sent = fake_requests.post.call_args.kwargs["json"]
    assert sent["model"] == "qwen2.5vl:7b"
    assert any(m["type"] == "image_url" for m in sent["messages"][0]["content"])


def test_analyze_image_ignores_legacy_models_vision(tmp_path):
    """A legacy ``models.vision`` entry must never influence the model used."""
    from cozmo.tools import desktop

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    cfg = _config(general_model="qwen2.5vl:7b")
    cfg["models"] = {"vision": "llava:13b"}

    with _patch_config(cfg), \
         patch("cozmo.runtime.model_selector.model_capabilities",
               return_value=_fake_caps(True)), \
         patch.object(desktop, "requests") as fake_requests:
        fake_requests.post.return_value = FakeResponse(200, {"message": {"content": "ok"}})

        desktop.analyze_image(str(img))

    sent = fake_requests.post.call_args.kwargs["json"]
    assert sent["model"] == "qwen2.5vl:7b"


# ── Error contracts ─────────────────────────────────────────────────────────


def test_analyze_image_general_model_unset_raises_model_unavailable(tmp_path):
    """Unset general workload model → explicit model-unavailable error, no HTTP."""
    from cozmo.tools import desktop

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    with _patch_config(_config(general_model="")), \
         patch.object(desktop, "requests") as fake_requests:
        result = desktop.analyze_image(str(img))

    assert "Model unavailable" in result
    assert "general" in result
    fake_requests.post.assert_not_called()


def test_analyze_image_general_model_lacks_vision_returns_capability_error(tmp_path):
    """Selected model without vision capability → explicit capability error."""
    from cozmo.tools import desktop

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    with _patch_config(_config(general_model="qwen3:8b")), \
         patch("cozmo.runtime.model_selector.model_capabilities",
               return_value=_fake_caps(False)), \
         patch.object(desktop, "requests") as fake_requests:
        result = desktop.analyze_image(str(img))

    assert "does not support image input" in result
    assert "qwen3:8b" in result
    assert "general" in result
    fake_requests.post.assert_not_called()


def test_analyze_image_model_not_installed_surfaces_model_unavailable(tmp_path):
    """Ollama 404 (model not installed) → model-unavailable error."""
    from cozmo.tools import desktop

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    with _patch_config(_config(general_model="qwen2.5vl:7b")), \
         patch("cozmo.runtime.model_selector.model_capabilities",
               return_value=_fake_caps(True)), \
         patch.object(desktop, "requests") as fake_requests:
        fake_requests.post.return_value = FakeResponse(404, {"error": "model not found"})

        result = desktop.analyze_image(str(img))

    assert "Model unavailable" in result
    assert "qwen2.5vl:7b" in result


def test_analyze_image_missing_file_returns_error():
    from cozmo.tools import desktop

    result = desktop.analyze_image("/nonexistent/x.png")
    assert "file not found" in result


# ── Capability / permission wiring preserved ────────────────────────────────


def test_screenshot_requires_desktop_enabled():
    from cozmo.tools import desktop

    with _patch_config(_config(general_model="qwen2.5vl:7b")):
        cfg = _config(general_model="qwen2.5vl:7b")
        cfg["desktop"] = {"enabled": False}
        with _patch_config(cfg):
            result = desktop.screenshot()
    assert "desktop tools disabled" in result


def test_clipboard_read_requires_desktop_enabled():
    from cozmo.tools import desktop

    cfg = _config()
    cfg["desktop"] = {"enabled": False}
    with _patch_config(cfg):
        result = desktop.clipboard_read()
    assert "desktop tools disabled" in result


def test_clipboard_read_returns_clipboard_text():
    from cozmo.tools import desktop

    with _patch_config(_config()), patch.object(desktop, "pyperclip") as fake_pc:
        fake_pc.paste.return_value = "copied text"
        result = desktop.clipboard_read()
    assert result == "copied text"


# ── Registry contract ───────────────────────────────────────────────────────


def test_task_tool_removed_from_registry():
    assert "task" not in TOOL_REGISTRY


def test_desktop_tools_still_registered():
    assert {"screenshot", "analyze_image", "clipboard_read"} <= set(TOOL_REGISTRY)