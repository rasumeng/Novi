"""Task 1.3 — Conversation persistence atomic writes + lock."""

import json
import threading
from pathlib import Path

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
    app = ws.create_app(cfg={})
    # create_app will mkdir CHATS_DIR; projects dir mkdir is inside projects section
    return app, chats, projects


def test_concurrent_conversation_writes_no_corruption(isolated_app):
    app, chats, _ = isolated_app
    conv_id = "conv-atomic"
    n = 20
    titles = [f"Title {i} - {i*7}" for i in range(n)]
    contents = [f"content body {i} unique-{i}" for i in range(n)]

    errors: list[Exception] = []

    def worker(i: int):
        try:
            with TestClient(app) as client:
                resp = client.put(
                    "/api/conversations",
                    json={
                        "id": conv_id,
                        "title": titles[i],
                        "messages": [{"role": "user", "content": contents[i]}],
                    },
                )
                assert resp.status_code == 200, resp.text
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"thread errors: {errors}"

    # index.json must be valid JSON
    idx_path = chats / "index.json"
    assert idx_path.exists()
    raw_text = idx_path.read_text("utf-8")
    raw = json.loads(raw_text)  # would raise if partial/corrupt
    assert "conversations" in raw
    matching = [c for c in raw["conversations"] if c["id"] == conv_id]
    assert len(matching) == 1, f"expected single entry, got {raw}"
    entry = matching[0]
    assert entry["title"] in titles

    # .md must be parseable and intact (last write wins)
    md_path = chats / f"{conv_id}.md"
    assert md_path.exists()
    md_text = md_path.read_text("utf-8")
    # first line is title
    assert md_text.startswith(f"# {entry['title']}")

    # No tmp files leaked
    assert not (chats / "index.json.tmp").exists()
    assert not (chats / f"{conv_id}.md.tmp").exists()

    # GET must return exactly one message with content from same write
    with TestClient(app) as client:
        resp = client.get("/api/conversations")
        assert resp.status_code == 200
        data = resp.json()
        conv = next(c for c in data if c["id"] == conv_id)
        assert len(conv["messages"]) == 1
        # content must be one of the written contents and title/content should correspond to same i?
        # At minimum content is valid and not interleaved/corrupted
        assert conv["messages"][0]["content"] in contents
        # title and md title line consistent
        assert conv["title"] == entry["title"]


def test_atomic_order_md_first_then_index(isolated_app):
    """Ensure PUT writes .md before index — no orphan index on crash simulation.

    We verify ordering indirectly: after a successful PUT both files exist
    and index entry references a valid md. If order were reversed, a crash
    between writes could leave index pointing to missing file — here we just
    confirm the happy path produces consistent pair.
    """
    app, chats, _ = isolated_app
    with TestClient(app) as client:
        resp = client.put(
            "/api/conversations",
            json={
                "id": "conv-order",
                "title": "Order Test",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert resp.status_code == 200

    assert (chats / "conv-order.md").exists()
    assert (chats / "index.json").exists()
    idx = json.loads((chats / "index.json").read_text("utf-8"))
    assert any(c["id"] == "conv-order" for c in idx["conversations"])
    # file content must be readable via GET
    with TestClient(app) as client:
        data = client.get("/api/conversations").json()
        conv = next(c for c in data if c["id"] == "conv-order")
        assert conv["messages"][0]["content"] == "hello"
