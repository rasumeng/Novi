from pathlib import Path

import pytest

from novi.skills.catalog import SkillCatalog, SkillValidationError


def test_create_list_and_load_valid_skill_with_underscore(tmp_path):
    catalog = SkillCatalog(tmp_path)
    created = catalog.create("code_review", "Review code carefully", "Follow these steps.")
    assert created.name == "code_review"
    assert created.description == "Review code carefully"
    assert catalog.get("code_review").content_hash == created.content_hash
    assert (tmp_path / "code_review" / "SKILL.md").exists()


def test_invalid_artifact_is_rejected_before_writing(tmp_path):
    catalog = SkillCatalog(tmp_path)
    with pytest.raises(SkillValidationError, match="name"):
        catalog.create("../escape", "description", "body")
    with pytest.raises(SkillValidationError, match="description"):
        catalog.create("valid", "", "body")
    assert list(tmp_path.iterdir()) == []


def test_folder_and_frontmatter_have_one_identity(tmp_path):
    folder = tmp_path / "folder-name"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: different-name\ndescription: desc\n---\nbody\n", encoding="utf-8")
    catalog = SkillCatalog(tmp_path)
    assert catalog.list() == ()
    assert "folder-name" in catalog.errors()
    with pytest.raises(SkillValidationError, match="identity"):
        catalog.get("folder-name")


def test_duplicate_creation_never_overwrites_existing_skill(tmp_path):
    catalog = SkillCatalog(tmp_path)
    first = catalog.create("same", "first", "body one")
    with pytest.raises(SkillValidationError, match="already exists"):
        catalog.create("same", "second", "body two")
    assert catalog.get("same").content_hash == first.content_hash


def test_support_paths_are_validated_and_large_files_are_only_indexed(tmp_path):
    catalog = SkillCatalog(tmp_path, max_support_file_bytes=2_000_000)
    large = b"x" * 200_000
    record = catalog.create("with_files", "desc", "body",
                            support_files={"references/large.txt": large})
    assert record.support_files == ("references/large.txt",)
    assert "x" * 100 not in record.body
    with pytest.raises(SkillValidationError, match="safe relative path"):
        catalog.create("bad_files", "desc", "body", support_files={"../outside": b"x"})


def test_scan_surfaces_invalid_skill_instead_of_silently_accepting(tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    catalog = SkillCatalog(tmp_path)
    assert catalog.list() == ()
    assert "frontmatter" in catalog.errors()["broken"]


def test_uploaded_filename_must_match_frontmatter_identity(tmp_path):
    catalog = SkillCatalog(tmp_path)
    text = "---\nname: actual\ndescription: desc\n---\nbody\n"
    with pytest.raises(SkillValidationError, match="identity"):
        catalog.install_text("different", text)
    assert list(tmp_path.iterdir()) == []
    assert catalog.install_text("actual", text).name == "actual"
