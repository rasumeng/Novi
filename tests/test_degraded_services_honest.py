"""Task 2.5 — Brain/Workspace Degraded vs Unavailable vs No Data.

Verifies:
- Each optional endpoint distinguishes empty (ok) vs unavailable (degraded)
- Brain endpoints include brainAvailable flag and status
- Workspace already returns honest 400 for not attached
- UI shows distinct Empty vs Error banners
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _client_with_backend(backend: dict | None):
    import novi.webui_server as ws
    from novi.webui_server import create_app

    # Ensure app uses our injected backend
    orig = ws._shared_backend
    ws._shared_backend = backend
    app = create_app()
    client = TestClient(app)
    return client, orig


def _restore(ws_mod, orig):
    ws_mod._shared_backend = orig


# ── Memory ────────────────────────────────────────────────────────────────

def test_memory_list_unavailable_when_no_memory():
    import novi.webui_server as ws
    backend = {"memory": None, "brain": None, "timeline_service": None}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/memory/list")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "unavailable"
        assert data["brainAvailable"] is False
        assert "Brain store unavailable" in data["error"]
        assert data["data"] == []
    finally:
        _restore(ws, orig)


def test_memory_list_ok_empty():
    import novi.webui_server as ws
    mem = MagicMock()
    mem.list_all.return_value = []
    backend = {"memory": mem, "brain": MagicMock(), "timeline_service": MagicMock()}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/memory/list")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["brainAvailable"] is True
        assert data["data"] == []
        assert "error" not in data or not data.get("error")
    finally:
        _restore(ws, orig)


def test_memory_list_ok_with_data():
    import novi.webui_server as ws
    mem = MagicMock()
    mem.list_all.return_value = [{"id": "m1", "text": "hello"}]
    backend = {"memory": mem}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/memory/list")
        data = r.json()
        assert data["status"] == "ok"
        assert data["brainAvailable"] is True
        assert len(data["data"]) == 1
    finally:
        _restore(ws, orig)


def test_memory_list_unavailable_on_exception():
    import novi.webui_server as ws
    mem = MagicMock()
    mem.list_all.side_effect = RuntimeError("db exploded")
    backend = {"memory": mem}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/memory/list")
        data = r.json()
        assert data["status"] == "unavailable"
        assert data["brainAvailable"] is False
        assert "Brain store unavailable" in data["error"]
        assert data["data"] == []
    finally:
        _restore(ws, orig)


def test_memory_search_unavailable_when_no_memory():
    import novi.webui_server as ws
    backend = {"memory": None}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/memory/search?q=hello")
        data = r.json()
        assert data["status"] == "unavailable"
        assert data["brainAvailable"] is False
        assert "Brain store unavailable" in data["error"]
        assert data["data"] == []
    finally:
        _restore(ws, orig)


def test_memory_search_empty_query_ok():
    import novi.webui_server as ws
    mem = MagicMock()
    backend = {"memory": mem}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/memory/search?q=")
        data = r.json()
        # empty query is not error, but still ok with empty data if memory exists
        assert data["status"] == "ok"
        assert data["brainAvailable"] is True
        assert data["data"] == []
    finally:
        _restore(ws, orig)


# ── Timeline ─────────────────────────────────────────────────────────────

def test_timeline_unavailable_when_no_service():
    import novi.webui_server as ws
    backend = {"timeline_service": None, "brain": None}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/timeline")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "unavailable"
        assert data["brainAvailable"] is False
        assert "Brain store unavailable" in data["error"]
        assert data["data"] == []
    finally:
        _restore(ws, orig)


def test_timeline_unavailable_on_exception():
    import novi.webui_server as ws
    svc = MagicMock()
    svc.recent.side_effect = RuntimeError("store broken")
    backend = {"timeline_service": svc}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/timeline")
        data = r.json()
        assert data["status"] == "unavailable"
        assert data["brainAvailable"] is False
        assert "Brain store unavailable" in data["error"]
    finally:
        _restore(ws, orig)


def test_timeline_ok_empty():
    import novi.webui_server as ws
    svc = MagicMock()
    svc.recent.return_value = []
    backend = {"timeline_service": svc}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/timeline")
        data = r.json()
        assert data["status"] == "ok"
        assert data["brainAvailable"] is True
        assert data["data"] == []
        assert "error" not in data or not data.get("error")
    finally:
        _restore(ws, orig)


def test_timeline_ok_with_entries():
    import novi.webui_server as ws
    svc = MagicMock()
    svc.recent.return_value = [{"id": "e1", "kind": "conversation.observed", "title": "Conversation logged", "detail": "hi", "timestamp": "2026-09-03T00:00:00"}]
    backend = {"timeline_service": svc}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/timeline?limit=10")
        data = r.json()
        assert data["status"] == "ok"
        assert len(data["data"]) == 1
        assert data["data"][0]["kind"] == "conversation.observed"
    finally:
        _restore(ws, orig)


# ── Knowledge ─────────────────────────────────────────────────────────────

def test_knowledge_unavailable_when_no_brain():
    import novi.webui_server as ws
    backend = {"brain": None}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/knowledge/overview")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "unavailable"
        assert data["brainAvailable"] is False
        assert "Brain store unavailable" in data["error"]
        assert data["categories"] == []
        assert data["total"] == 0
    finally:
        _restore(ws, orig)


def test_knowledge_ok_empty():
    import novi.webui_server as ws
    brain = MagicMock()
    brain.inspect_memory.return_value = {"categories": {}, "items": []}
    backend = {"brain": brain}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/knowledge/overview")
        data = r.json()
        assert data["status"] == "ok"
        assert data["brainAvailable"] is True
        assert data["categories"] == []
        assert data["total"] == 0
        assert "error" not in data or not data.get("error")
    finally:
        _restore(ws, orig)


def test_knowledge_ok_with_data():
    import novi.webui_server as ws
    brain = MagicMock()
    brain.inspect_memory.return_value = {
        "categories": {"preference": [{"content": "likes tea", "evidence": "verified"}]},
        "items": [{"id": "i1", "last_seen_at": "2026-09-03T00:00:00"}]
    }
    backend = {"brain": brain}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/knowledge/overview")
        data = r.json()
        assert data["status"] == "ok"
        assert data["brainAvailable"] is True
        assert data["total"] == 1
        assert data["categories"][0]["category"] == "preference"
    finally:
        _restore(ws, orig)


def test_knowledge_unavailable_on_exception():
    import novi.webui_server as ws
    brain = MagicMock()
    brain.inspect_memory.side_effect = RuntimeError("brain broken")
    backend = {"brain": brain}
    client, orig = _client_with_backend(backend)
    try:
        r = client.get("/api/knowledge/overview")
        data = r.json()
        assert data["status"] == "unavailable"
        assert data["brainAvailable"] is False
        assert "Brain store unavailable" in data["error"]
    finally:
        _restore(ws, orig)


# ── Workspace honest 400 ─────────────────────────────────────────────────

def test_workspace_not_attached_keeps_400(tmp_path, monkeypatch):
    import novi.webui_server as ws
    import json as _json

    # Use temp home via monkeypatching app_home
    from pathlib import Path as _P

    # backend needs projects index empty
    # create temp dirs for app_home
    import tempfile, os
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setattr(ws, "app_home", lambda: tmp_home)
    # also need to patch CHATS_DIR, PROJECTS_DIR within create_app? Use file creation directly
    backend = {}
    client, orig = _client_with_backend(backend)
    try:
        # create a project without workspace
        r = client.post("/api/projects", json={"name": "P1"})
        assert r.status_code == 200
        pid = r.json()["id"]
        r2 = client.post(f"/api/workspaces/{pid}/search", json={"query": "hello"})
        assert r2.status_code == 400
        assert "workspace not attached" in r2.json()["error"].lower()
        r3 = client.get(f"/api/workspaces/{pid}/read?path=README.md")
        assert r3.status_code == 400
        assert "workspace not attached" in r3.json()["error"].lower()
    finally:
        _restore(ws, orig)


# ── UI distinct empty vs error banners ───────────────────────────────────

def test_ui_has_distinct_empty_vs_error_banners():
    tl = Path("novi/webui/src/components/timeline/TimelinePage.tsx").read_text(encoding="utf-8")
    assert "Brain store unavailable — check logs" in tl
    assert "No knowledge yet — start a conversation" in tl or "No knowledge yet" in tl
    assert "Retry" in tl

    ko = Path("novi/webui/src/components/knowledge/KnowledgeOverview.tsx").read_text(encoding="utf-8")
    assert "Brain store unavailable — check logs" in ko
    assert "No knowledge yet — start a conversation" in ko

    ms = Path("novi/webui/src/components/settings/MemorySettings.tsx").read_text(encoding="utf-8")
    assert "Brain store unavailable — check logs" in ms

    # service helpers expose envelope
    svc = Path("novi/webui/src/services/novi.ts").read_text(encoding="utf-8")
    assert "brainAvailable" in svc
    assert "fetchTimelineEnvelope" in svc

    # hook distinguishes
    hook = Path("novi/webui/src/hooks/useNoviChat.ts").read_text(encoding="utf-8")
    assert "timelineError" in hook
    assert "timelineStatus" in hook


def test_brain_available_flag_additive():
    import novi.webui_server as ws

    # Knowledge ok adds brainAvailable true
    brain = MagicMock()
    brain.inspect_memory.return_value = {"categories": {}, "items": []}
    backend = {"brain": brain}
    client, orig = _client_with_backend(backend)
    try:
        data = client.get("/api/knowledge/overview").json()
        assert "brainAvailable" in data
        assert data["brainAvailable"] is True
        assert "status" in data
        assert data["status"] in ("ok", "unavailable", "disabled")
        # categories still present
        assert "categories" in data
    finally:
        _restore(ws, orig)

    # Timeline ok
    svc = MagicMock()
    svc.recent.return_value = []
    backend2 = {"timeline_service": svc}
    client2, orig2 = _client_with_backend(backend2)
    try:
        data2 = client2.get("/api/timeline").json()
        assert "brainAvailable" in data2
        assert data2["brainAvailable"] is True
        assert "status" in data2
        assert "data" in data2
    finally:
        _restore(ws, orig2)
