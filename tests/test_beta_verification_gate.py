"""Smoke tests for beta verification gate — Task 5.1."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_SCENARIOS = [
    "First launch",
    "Ollama unavailable",
    "Ollama available, no models",
    "New uncached vision model",
    "Known unsupported model",
    "Capability verification failure",
    "Image attachment",
    "Search disabled",
    "Permission ignored",
    "App restart",
    "Rapid conversation updates",
    "Project reassignment",
    "Attachment deletion",
    "Runtime failure",
]


def test_beta_checklist_exists_and_has_14_scenarios():
    p = REPO_ROOT / "docs" / "BETA_CHECKLIST.md"
    assert p.exists(), "docs/BETA_CHECKLIST.md missing"
    text = p.read_text(encoding="utf-8")
    for s in EXPECTED_SCENARIOS:
        assert s in text, f"scenario missing in checklist: {s}"
    # checkboxes
    assert text.count("☐") >= 14, "checklist should have at least 14 checkboxes"
    assert "Sign-off" in text
    assert "Automated Gate" in text
    assert "scripts/verify_beta.py" in text


def test_verify_beta_script_exists_and_has_required_commands():
    p = REPO_ROOT / "scripts" / "verify_beta.py"
    assert p.exists(), "scripts/verify_beta.py missing"
    text = p.read_text(encoding="utf-8")
    assert "pytest -q" in text or 'pytest", "-q' in text or "pytest" in text
    assert "tsc --noEmit" in text
    assert "npm" in text and "run build" in text
    for f in [
        "test_conversation_persistence_atomic.py",
        "test_project_conversation_linking.py",
        "test_capability_verification.py",
        "test_discovery_honest_errors.py",
    ]:
        assert f in text, f"targeted test missing in verify script: {f}"
    assert "Manual first-run matrix" in text


def test_verify_beta_importable():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_beta", str(REPO_ROOT / "scripts" / "verify_beta.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    assert hasattr(mod, "main")
    assert hasattr(mod, "TARGETED_TESTS")
    assert len(mod.TARGETED_TESTS) == 4
