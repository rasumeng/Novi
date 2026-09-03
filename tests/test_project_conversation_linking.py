"""Task 1.4 — Canonical project↔conversation linking via conversation.projectId."""

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    import novi.webui_server as ws
    import novi.paths as paths

    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    chats = fake_home / "chats"
    projects = fake_home / "projects"
    monkeypatch.setattr(ws, "app_home", lambda: fake_home)
    monkeypatch.setattr(paths, "home", lambda: fake_home)
    monkeypatch.setattr(ws, "CHATS_DIR", chats)
    monkeypatch.setattr(ws, "ATTACHMENTS_DIR", fake_home / "attachments")
    monkeypatch.setattr(ws, "_shared_backend", None)
    # ensure clean lock state? locks are module-level RLocks, no need to reset
    app = ws.create_app(cfg={})
    return app, chats, projects, fake_home


def test_linking_single_source(isolated_app):
    app, chats, projects, fake_home = isolated_app
    with TestClient(app) as client:
        # create project
        resp = client.post("/api/projects", json={"name": "Proj A"})
        assert resp.status_code == 200, resp.text
        proj = resp.json()
        pid = proj["id"]
        # initially empty
        assert proj.get("conversationIds") == []

        # PUT conversation with projectId=X
        resp = client.put("/api/conversations", json={"id": "conv-1", "title": "Hello", "messages": [{"role": "user", "content": "hi"}], "projectId": pid})
        assert resp.status_code == 200, resp.text

        # read project -> should include it (derived)
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        proj_list = resp.json()
        p = next(x for x in proj_list if x["id"] == pid)
        assert "conv-1" in p["conversationIds"], f"derived missing: {p}"

        # verify GET /api/projects/{pid}/conversations also returns it
        resp = client.get(f"/api/projects/{pid}/conversations")
        assert resp.status_code == 200
        convs = resp.json()
        assert any(c["id"] == "conv-1" for c in convs)

        # update project via conversationIds mismatch -> conversation wins (should be ignored)
        resp = client.put(f"/api/projects/{pid}", json={"conversationIds": ["conv-999"], "name": "Proj A renamed"})
        assert resp.status_code == 200
        updated = resp.json()
        # derived should still be conv-1, not conv-999
        assert "conv-1" in updated["conversationIds"]
        assert "conv-999" not in updated["conversationIds"]

        # also GET projects still shows conv-1
        resp = client.get("/api/projects")
        p2 = next(x for x in resp.json() if x["id"] == pid)
        assert "conv-1" in p2["conversationIds"]
        assert "conv-999" not in p2["conversationIds"]

        # ensure persisted projects/index.json does NOT contain conversationIds
        raw = json.loads((projects / "index.json").read_text("utf-8"))
        for pr in raw["projects"]:
            assert "conversationIds" not in pr, f"persisted conversationIds should be stripped: {pr}"


def test_conversation_reassignment( isolated_app):
    app, chats, projects, fake_home = isolated_app
    with TestClient(app) as client:
        r1 = client.post("/api/projects", json={"name": "P1"}).json()
        r2 = client.post("/api/projects", json={"name": "P2"}).json()
        pid1, pid2 = r1["id"], r2["id"]

        client.put("/api/conversations", json={"id": "conv-r", "title": "R", "messages": [{"role": "user", "content": "hi"}], "projectId": pid1})
        # verify in P1
        p1_convs = client.get(f"/api/projects/{pid1}/conversations").json()
        assert any(c["id"] == "conv-r" for c in p1_convs)
        p2_convs = client.get(f"/api/projects/{pid2}/conversations").json()
        assert not any(c["id"] == "conv-r" for c in p2_convs)

        # reassign to P2 via PUT conversation
        client.put("/api/conversations", json={"id": "conv-r", "title": "R", "messages": [{"role": "user", "content": "hi"}], "projectId": pid2})
        p1_convs = client.get(f"/api/projects/{pid1}/conversations").json()
        assert not any(c["id"] == "conv-r" for c in p1_convs)
        p2_convs = client.get(f"/api/projects/{pid2}/conversations").json()
        assert any(c["id"] == "conv-r" for c in p2_convs)

        # clear assignment (projectId null)
        client.put("/api/conversations", json={"id": "conv-r", "title": "R", "messages": [{"role": "user", "content": "hi"}], "projectId": None})
        for pid in (pid1, pid2):
            convs = client.get(f"/api/projects/{pid}/conversations").json()
            assert not any(c["id"] == "conv-r" for c in convs)
        # also GET /api/projects derived should have none
        for p in client.get("/api/projects").json():
            assert "conv-r" not in p["conversationIds"]


def test_delete_pruning( isolated_app):
    app, chats, projects, fake_home = isolated_app
    with TestClient(app) as client:
        proj = client.post("/api/projects", json={"name": "P Del"}).json()
        pid = proj["id"]
        client.put("/api/conversations", json={"id": "conv-del", "title": "Del", "messages": [{"role": "user", "content": "hi"}], "projectId": pid})
        assert "conv-del" in next(p for p in client.get("/api/projects").json() if p["id"] == pid)["conversationIds"]
        # delete conversation
        resp = client.delete("/api/conversations/conv-del")
        assert resp.status_code == 200
        # derived should prune
        assert "conv-del" not in next(p for p in client.get("/api/projects").json() if p["id"] == pid)["conversationIds"]
        assert client.get(f"/api/projects/{pid}/conversations").json() == []


def test_restart_consistency( isolated_app):
    app, chats, projects, fake_home = isolated_app
    with TestClient(app) as client:
        proj = client.post("/api/projects", json={"name": "Restart"}).json()
        pid = proj["id"]
        client.put("/api/conversations", json={"id": "conv-a", "title": "A", "messages": [{"role": "user", "content": "a"}], "projectId": pid})
        client.put("/api/conversations", json={"id": "conv-b", "title": "B", "messages": [{"role": "user", "content": "b"}], "projectId": pid})
        client.put("/api/conversations", json={"id": "conv-orphan", "title": "O", "messages": [{"role": "user", "content": "o"}]})

    # simulate restart: create new app with same fake_home
    import novi.webui_server as ws
    import novi.paths as paths
    # monkeypatch already set; just create new app instance
    app2 = ws.create_app(cfg={})
    with TestClient(app2) as client2:
        projs = client2.get("/api/projects").json()
        p = next(x for x in projs if x["id"] == pid)
        assert set(p["conversationIds"]) == {"conv-a", "conv-b"}
        # orphan not included
        assert "conv-orphan" not in p["conversationIds"]
        convs = client2.get(f"/api/projects/{pid}/conversations").json()
        assert {c["id"] for c in convs} == {"conv-a", "conv-b"}


def test_migration_strips_persisted_conversationIds_and_backfills( isolated_app):
    app, chats, projects, fake_home = isolated_app
    # Manually create legacy projects/index.json with conversationIds persisted, and conversations without projectId
    # First create a conversation without projectId via API, then manually inject legacy project
    with TestClient(app) as client:
        # create conv via API (no projectId)
        client.put("/api/conversations", json={"id": "conv-legacy", "title": "Legacy", "messages": [{"role": "user", "content": "hi"}]})
        # create project via API then manually inject legacy field
        proj = client.post("/api/projects", json={"name": "LegacyProj"}).json()
        pid = proj["id"]

    # Now directly write legacy format with conversationIds
    p_idx_path = projects / "index.json"
    raw = json.loads(p_idx_path.read_text("utf-8"))
    for pr in raw["projects"]:
        if pr["id"] == pid:
            pr["conversationIds"] = ["conv-legacy"]
    p_idx_path.write_text(json.dumps(raw, indent=2), "utf-8")

    # Also ensure conversation has no projectId yet (it doesn't)
    c_idx = json.loads((chats / "index.json").read_text("utf-8"))
    conv = next(c for c in c_idx["conversations"] if c["id"] == "conv-legacy")
    assert "projectId" not in conv or not conv.get("projectId")

    # Next GET should migrate
    import novi.webui_server as ws
    app2 = ws.create_app(cfg={})
    with TestClient(app2) as client2:
        projs = client2.get("/api/projects").json()
        p = next(x for x in projs if x["id"] == pid)
        # derived should include conv-legacy after migration
        assert "conv-legacy" in p["conversationIds"]
        # persisted should be stripped
        raw2 = json.loads(p_idx_path.read_text("utf-8"))
        for pr in raw2["projects"]:
            assert "conversationIds" not in pr
        # conversation should now have projectId backfilled
        c_idx2 = json.loads((chats / "index.json").read_text("utf-8"))
        conv2 = next(c for c in c_idx2["conversations"] if c["id"] == "conv-legacy")
        assert conv2.get("projectId") == pid


def test_migration_conversation_wins_over_legacy( isolated_app):
    app, chats, projects, fake_home = isolated_app
    with TestClient(app) as client:
        p1 = client.post("/api/projects", json={"name": "P1"}).json()
        p2 = client.post("/api/projects", json={"name": "P2"}).json()
        pid1, pid2 = p1["id"], p2["id"]
        # conv already assigned to p2 via canonical
        client.put("/api/conversations", json={"id": "conv-conflict", "title": "C", "messages": [{"role": "user", "content": "hi"}], "projectId": pid2})

    # Inject legacy: p1 claims conv-conflict
    p_idx_path = projects / "index.json"
    raw = json.loads(p_idx_path.read_text("utf-8"))
    for pr in raw["projects"]:
        if pr["id"] == pid1:
            pr["conversationIds"] = ["conv-conflict"]
    p_idx_path.write_text(json.dumps(raw, indent=2), "utf-8")

    import novi.webui_server as ws
    app2 = ws.create_app(cfg={})
    with TestClient(app2) as client2:
        projs = client2.get("/api/projects").json()
        p1_after = next(x for x in projs if x["id"] == pid1)
        p2_after = next(x for x in projs if x["id"] == pid2)
        # conversation was already p2, so p1 should NOT steal it (conversation wins)
        assert "conv-conflict" not in p1_after["conversationIds"]
        assert "conv-conflict" in p2_after["conversationIds"]
