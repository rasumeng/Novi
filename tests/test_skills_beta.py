"""Task 6 — Skills beta: creation validation + permission gate.

- POST /api/skills 400s on invalid name / missing description / missing content
- Valid skill appears in GET /api/skills
- Skill-invoked tools route through ToolExecutor._check_permission (deny blocks)
- Invalid on-disk skills (no frontmatter description) never listed/active
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import novi.webui_server as ws

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(ws, "SKILLS_DIR", skills_dir)
    app = ws.create_app()
    return TestClient(app)


def test_create_skill_valid(client):
    r = client.post(
        "/api/skills",
        json={"name": "my-skill", "description": "does things", "content": "# hi"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "my-skill"
    r2 = client.get("/api/skills")
    assert r2.status_code == 200
    names = [s["name"] for s in r2.json()]
    assert "my-skill" in names


@pytest.mark.parametrize("bad", ["", "../evil", "..\\evil", "Bad Name!", "a", "x" * 70])
def test_create_skill_invalid_name_rejected(client, bad):
    r = client.post(
        "/api/skills",
        json={"name": bad, "description": "desc", "content": "# hi"},
    )
    assert r.status_code == 400, (bad, r.text)
    assert "error" in r.json()


def test_create_skill_invalid_content_rejected(client):
    # missing description
    r = client.post(
        "/api/skills", json={"name": "nodesc", "description": "", "content": "# hi"}
    )
    assert r.status_code == 400, r.text
    assert "error" in r.json()
    # missing content
    r2 = client.post(
        "/api/skills",
        json={"name": "nocontent", "description": "has desc", "content": "   "},
    )
    assert r2.status_code == 400, r2.text
    assert "error" in r2.json()
    # neither created on disk / listed
    names = [s["name"] for s in client.get("/api/skills").json()]
    assert "nodesc" not in names
    assert "nocontent" not in names


def test_invalid_skill_on_disk_never_listed(client, tmp_path):
    import novi.webui_server as ws

    bad = ws.SKILLS_DIR / "broken-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here\n", "utf-8")
    names = [s["name"] for s in client.get("/api/skills").json()]
    assert "broken-skill" not in names


def test_skill_execution_requires_permission():
    """A tool invoked under a skill still passes the ToolExecutor gate."""
    from unittest.mock import MagicMock

    from novi.runtime.tool_executor import ToolExecutor
    from novi.runtime.tool_registry import ToolRegistry

    called = []
    reg = ToolRegistry()
    reg.register("write_file", lambda path, content: called.append(path) or "ok")

    perms = MagicMock()
    perms.resolve.return_value = "deny"  # user denied
    ex = ToolExecutor(
        registry=reg,
        perms=perms,
        lesson_store=MagicMock(),
        lc_tools={},
        tool_fallbacks={},
        max_tool_output=8000,
    )
    result = ex.execute("write_file", {"path": "x.md", "content": "from skill"})
    assert result.success is False
    assert "DENIED" in result.output
    assert called == [], "denied tool must not execute"


def test_skill_loader_skips_invalid(tmp_path, monkeypatch):
    """Runtime loader never activates skills missing name/description."""
    import novi.runtime.runtime as rt

    skills_dir = tmp_path / "skills"
    good = skills_dir / "good-skill"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text(
        '---\nname: good-skill\ndescription: "fine"\n---\n\n# ok\n', "utf-8"
    )
    bad = skills_dir / "bad-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("no frontmatter\n", "utf-8")
    monkeypatch.setattr(rt, "SKILLS_DIR", skills_dir)
    skills = rt._load_all_skills()
    assert "good-skill" in skills
    assert "bad-skill" not in skills


def _upload(client, filename: str, payload: bytes):
    return client.post(
        "/api/skills/upload", files={"file": (filename, payload, "text/markdown")}
    )


def test_upload_skill_valid(client):
    r = _upload(
        client,
        "my-upload.md",
        b'---\nname: my-upload\ndescription: "does things"\n---\n\n# hi\n',
    )
    assert r.status_code == 200, r.text
    names = [s["name"] for s in client.get("/api/skills").json()]
    assert "my-upload" in names


def test_upload_skill_invalid_name_rejected_no_dir(client):
    import novi.webui_server as ws

    r = _upload(
        client,
        "Bad Name!.md",
        b'---\nname: Bad Name!\ndescription: "x"\n---\n\n# hi\n',
    )
    assert r.status_code == 400, r.text
    assert "error" in r.json()
    assert not (ws.SKILLS_DIR / "Bad Name!").exists()


def test_upload_skill_malformed_bytes_rejected(client):
    import novi.webui_server as ws

    r = _upload(client, "broken.md", b"\xff\xfe\x00not-utf8")
    assert r.status_code == 400, r.text
    assert "error" in r.json()
    assert not (ws.SKILLS_DIR / "broken").exists()


def test_upload_skill_bad_yaml_rejected(client):
    import novi.webui_server as ws

    r = _upload(
        client, "badyml.md", b"---\nname: [unclosed\ndescription: x\n---\n\n# hi\n"
    )
    assert r.status_code == 400, r.text
    assert "error" in r.json()
    assert not (ws.SKILLS_DIR / "badyml").exists()


def test_upload_skill_missing_description_rejected(client):
    r = _upload(client, "nodesc.md", b"no frontmatter here\n")
    assert r.status_code == 400, r.text
    assert "error" in r.json()


def test_upload_skill_invalid_frontmatter_name_rejected(client):
    import novi.webui_server as ws

    r = _upload(
        client,
        "ok-name.md",
        b'---\nname: "../evil"\ndescription: "x"\n---\n\n# hi\n',
    )
    assert r.status_code == 400, r.text
    assert "error" in r.json()
    assert not (ws.SKILLS_DIR / "ok-name").exists()


def test_loader_and_get_skip_invalid_frontmatter_name(tmp_path, monkeypatch, client):
    """An invalid frontmatter name can neither activate nor show as dead."""
    import novi.webui_server as ws
    import novi.runtime.runtime as rt

    evil = ws.SKILLS_DIR / "sneaky-skill"
    evil.mkdir()
    (evil / "SKILL.md").write_text(
        '---\nname: "../evil"\ndescription: "x"\n---\n\n# hi\n', "utf-8"
    )
    names = [s["name"] for s in client.get("/api/skills").json()]
    assert "sneaky-skill" not in names
    monkeypatch.setattr(rt, "SKILLS_DIR", ws.SKILLS_DIR)
    skills = rt._load_all_skills()
    assert "sneaky-skill" not in skills
    assert "../evil" not in skills
