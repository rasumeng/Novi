"""M1 — knowledge directory ownership regression tests.

The knowledge base directory is owned by ``workspace.knowledge`` config
(default ``~/.cozmo/knowledge``). It is never CWD-relative. These tests pin:

* ``file_ops.knowledge_dir()`` resolves the configured value (expanduser).
* ``read_knowledge``/``write_knowledge`` target the configured directory.
* Changing the configured workspace changes the write target.
* ``KnowledgeIndex`` defaults to the configured directory when none is given.
* No CWD-relative ``./knowledge`` path remains in production code.
"""

from pathlib import Path
from unittest.mock import patch

import cozmo.tools.file_ops as file_ops


class _StubConfig:
    """Read-only config facade implementing the framework ``.get`` dotted API."""

    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        node = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def snapshot(self):
        return dict(self._data)


def _patch_config(cfg, module="cozmo.configuration.bootstrap"):
    return patch(f"{module}.get_configuration", return_value=_StubConfig(cfg))


def _cfg(knowledge_dir):
    return {"workspace": {"knowledge": str(knowledge_dir)}}


# ── knowledge_dir() resolution ──────────────────────────────────────────────


def test_knowledge_dir_reads_configured_value(tmp_path):
    with _patch_config(_cfg(tmp_path / "kb")):
        assert file_ops.knowledge_dir() == (tmp_path / "kb").resolve()


def test_knowledge_dir_expands_home_relative_value():
    with _patch_config(_cfg("~/.cozmo/knowledge")):
        assert file_ops.knowledge_dir() == Path("~/.cozmo/knowledge").expanduser().resolve()


def test_knowledge_dir_defaults_to_profile_knowledge():
    with _patch_config({}):
        assert file_ops.knowledge_dir() == (Path.home() / ".cozmo" / "knowledge").resolve()


def test_knowledge_dir_never_cwd_relative():
    """The module must not bake a CWD-relative knowledge path at import time."""
    assert not hasattr(file_ops, "KNOWLEDGE")


# ── write_knowledge targets configured dir ──────────────────────────────────


def test_write_knowledge_writes_to_configured_dir(tmp_path):
    kb = tmp_path / "kb"
    with _patch_config(_cfg(kb)), \
         patch("cozmo.memory.knowledge_index.get_knowledge_index", return_value=None):
        result = file_ops.write_knowledge("learnings/new-thing.md", "body", type="Learning")
    assert "[ok]" in result
    written = kb / "learnings" / "new-thing.md"
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "body" in text
    assert "type: Learning" in text


def test_write_knowledge_does_not_write_to_cwd(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    monkeypatch.chdir(tmp_path)
    with _patch_config(_cfg(kb)), \
         patch("cozmo.memory.knowledge_index.get_knowledge_index", return_value=None):
        file_ops.write_knowledge("cwd-leak.md", "body")
    assert not (tmp_path / "cwd-leak.md").exists()
    assert not (tmp_path / "knowledge").exists()
    assert (kb / "cwd-leak.md").exists()


def test_write_knowledge_changes_target_with_config(tmp_path):
    kb_a = tmp_path / "kb-a"
    kb_b = tmp_path / "kb-b"
    with _patch_config(_cfg(kb_a)), \
         patch("cozmo.memory.knowledge_index.get_knowledge_index", return_value=None):
        file_ops.write_knowledge("one.md", "one")
    with _patch_config(_cfg(kb_b)), \
         patch("cozmo.memory.knowledge_index.get_knowledge_index", return_value=None):
        file_ops.write_knowledge("two.md", "two")
    assert (kb_a / "one.md").exists()
    assert (kb_b / "two.md").exists()
    assert not (kb_a / "two.md").exists()
    assert not (kb_b / "one.md").exists()


# ── read_knowledge reads configured dir ─────────────────────────────────────


def test_read_knowledge_reads_from_configured_dir(tmp_path):
    kb = tmp_path / "kb"
    (kb / "facts").mkdir(parents=True)
    (kb / "facts" / "python.md").write_text("# Python", encoding="utf-8")
    with _patch_config(_cfg(kb)):
        result = file_ops.read_knowledge("facts/python.md")
    assert result == "# Python"


def test_read_knowledge_traversal_rejected(tmp_path):
    kb = tmp_path / "kb"
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    with _patch_config(_cfg(kb)):
        result = file_ops.read_knowledge("../outside.md")
    assert "traversal" in result


# ── KnowledgeIndex default resolution ───────────────────────────────────────


def test_knowledge_index_defaults_to_configured_dir(tmp_path):
    from cozmo.memory.knowledge_index import KnowledgeIndex
    with _patch_config(_cfg(tmp_path / "kb"), module="cozmo.memory.knowledge_index"):
        idx = KnowledgeIndex(embed_model="n/a")
    assert idx.knowledge_dir == (tmp_path / "kb").resolve()


def test_knowledge_index_explicit_dir_wins(tmp_path):
    from cozmo.memory.knowledge_index import KnowledgeIndex
    explicit = tmp_path / "explicit"
    with _patch_config(_cfg(tmp_path / "cfg"), module="cozmo.memory.knowledge_index"):
        idx = KnowledgeIndex(knowledge_dir=explicit, embed_model="n/a")
    assert idx.knowledge_dir == explicit.resolve()


def test_knowledge_index_defaults_to_profile_knowledge():
    from cozmo.memory.knowledge_index import KnowledgeIndex
    with _patch_config({}, module="cozmo.memory.knowledge_index"):
        idx = KnowledgeIndex(embed_model="n/a")
    assert idx.knowledge_dir == (Path.home() / ".cozmo" / "knowledge").resolve()