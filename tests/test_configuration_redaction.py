"""Config redaction tests (M5.2).

Proves secret values are masked on every read surface using only schema
classification — never endpoint-scattered checks — and that masked read-backs
written by a client never round-trip placeholders into storage.

Covered surfaces:
* exact SECRET settings (``telegram.bot_token``) at leaf paths
* namespace ``secret_segments`` (``mcp.servers.<name>.env``) while keeping the
  tree shape (env *names* visible, values masked)
* non-secret paths pass through untouched
* a namespace write that echoes a masked read-back preserves the stored value
  instead of persisting the mask
"""

import pytest

from cozmo.configuration.bootstrap import build_registry
from cozmo.configuration.redaction import ConfigRedactor


@pytest.fixture
def redactor():
    return ConfigRedactor(build_registry())


@pytest.fixture
def _registry():
    return build_registry()


# ── exact SECRET leaves ────────────────────────────────────────────────


def test_exact_secret_leaf_masked(redactor):
    value = redactor.redact("telegram.bot_token", "12345:ABCDEF")
    assert value == {"configured": True, "masked": True}


def test_exact_secret_unguarded_when_empty(redactor):
    value = redactor.redact("telegram.bot_token", "")
    assert value == {"configured": False, "masked": True}


# ── namespace secret_segments ──────────────────────────────────────────


def test_namespace_env_leaf_masked(redactor):
    path = "mcp.servers.github.env.GITHUB_TOKEN"
    value = redactor.redact(path, "ghp_supersecret")
    assert value == {"configured": True, "masked": True}


def test_namespace_env_values_masked_keys_kept(redactor):
    tree = {
        "servers": {
            "github": {
                "command": "npx",
                "env": {"GITHUB_TOKEN": "ghp_supersecret",
                        "ORG": "acme"},
            },
            "filesystem": {"root": "/tmp"},
        },
    }
    out = redactor.redact("mcp.servers", tree)
    github = out["servers"]["github"]
    assert github["command"] == "npx"
    assert github["env"] == {
        "GITHUB_TOKEN": {"configured": True, "masked": True},
        "ORG": {"configured": True, "masked": True},
    }
    assert out["servers"]["filesystem"]["root"] == "/tmp"


def test_namespace_whole_subtree_redacted(redactor):
    # redacting at the namespace root masks every env leaf recursively
    out = redactor.redact("mcp.servers", {"github": {"env": {"TOKEN": "x"}}})
    assert out == {"github": {"env": {"TOKEN": {"configured": True, "masked": True}}}}


# ── non-secret passthrough ─────────────────────────────────────────────


def test_non_secret_passthrough(redactor):
    assert redactor.redact("runtime.max_steps", 42) == 42
    assert redactor.redact("embedding.model", "nomic-embed-text") == "nomic-embed-text"
    assert redactor.redact("runtime", {"max_steps": 42}) == {"max_steps": 42}


def test_secret_segments_exposed_in_schema(_registry):
    setting = _registry.resolve("mcp.servers")
    assert setting.namespace is True
    assert "env" in setting.secret_segments


# ── masked read-back never round-trips into storage ────────────────────


def test_masked_readback_preserves_stored_value(tmp_path, monkeypatch):
    """A client echo of a masked read-back must not persist the placeholder.

    Full-stack through the WebUI write path, which owns the
    masked-placeholder → live-value reconciliation for namespace writes.
    """
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HOMEDRIVE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", "")
    import cozmo.configuration.bootstrap as boot
    monkeypatch.setattr(boot, "CONFIG_PATH", tmp_path / ".cozmo" / "config.toml")
    monkeypatch.setattr(boot, "_configuration", None)

    from fastapi.testclient import TestClient
    from cozmo.webui_server import create_app

    client = TestClient(create_app(cfg={}))
    client.patch("/api/configuration", json={
        "mcp.servers": {"github": {"env": {"GITHUB_TOKEN": "ghp_real"}}},
    })
    stored = client.get("/api/configuration").json()["mcp"]["servers"]
    # a client that echoes the masked read-back alongside a real new key must
    # preserve the stored secret instead of persisting the placeholder
    stored["github"]["env"]["OTHER"] = "visible"
    client.patch("/api/configuration", json={"mcp.servers": stored})

    final = client.get("/api/configuration").json()["mcp"]["servers"]
    assert final["github"]["env"]["GITHUB_TOKEN"] == {
        "configured": True, "masked": True}
    assert final["github"]["env"]["OTHER"] == {"configured": True, "masked": True}


def test_redactor_is_masked_detects_placeholders(redactor, _registry):
    placeholder = redactor.redact("telegram.bot_token", "x")
    assert redactor.is_masked(placeholder)
    assert not redactor.is_masked("x")
    assert not redactor.is_masked({"configured": True})