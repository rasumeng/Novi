"""Task 3 — Memory beta experience: enable/disable, list, delete, clear-all.

- GET /api/memory/list returns status "disabled" when memory.enabled=False
- DELETE /api/memory clears all memories
"""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient


def _client_with_backend(backend, memory_enabled=True):
    import novi.webui_server as ws
    from novi.webui_server import create_app
    import novi.configuration.bootstrap as boot

    orig_backend = ws._shared_backend
    orig_cfg_fn = boot.get_configuration
    ws._shared_backend = backend
    cfg = MagicMock()

    def _get(key, default=None):
        if key == "memory.enabled":
            return memory_enabled
        return default

    cfg.get.side_effect = _get
    cfg.snapshot.return_value = {}
    boot.get_configuration = lambda: cfg
    app = create_app()
    client = TestClient(app)
    return client, ws, orig_backend, boot, orig_cfg_fn


def _restore(ws, orig_backend, boot, orig_cfg_fn):
    ws._shared_backend = orig_backend
    boot.get_configuration = orig_cfg_fn


def test_memory_disabled_returns_disabled():
    import novi.webui_server as ws
    import novi.configuration.bootstrap as boot

    mem = MagicMock()
    mem.list_all.return_value = [{"id": "m1", "text": "hello"}]
    backend = {"memory": mem}
    client, ws_mod, orig_backend, boot_mod, orig_cfg = _client_with_backend(
        backend, memory_enabled=False
    )
    try:
        r = client.get("/api/memory/list")
        assert r.status_code == 200
        assert r.json()["status"] == "disabled"
        r2 = client.get("/api/memory/search?q=hello")
        assert r2.json()["status"] == "disabled"
    finally:
        _restore(ws_mod, orig_backend, boot_mod, orig_cfg)


def test_memory_clear_all():
    import novi.webui_server as ws
    import novi.configuration.bootstrap as boot

    mem = MagicMock()
    mem.list_all.return_value = [{"id": "a", "text": "one"}, {"id": "b", "text": "two"}]
    mem.delete.side_effect = lambda i: True
    backend = {"memory": mem}
    client, ws_mod, orig_backend, boot_mod, orig_cfg = _client_with_backend(
        backend, memory_enabled=True
    )
    try:
        r = client.delete("/api/memory")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["deleted"] == 2
        mem.list_all.return_value = []
        r2 = client.get("/api/memory/list")
        assert r2.json()["data"] == []
    finally:
        _restore(ws_mod, orig_backend, boot_mod, orig_cfg)
