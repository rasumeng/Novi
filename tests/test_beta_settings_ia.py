"""Task 10 — Beta Settings IA gate.

Locks the beta IA settled by Tasks 1-9:
- agent namespace persists in the registry (agent.*, agents.*) but no
  agent UI contract: no USER-visible setting under Category.AGENT.
- user-facing settings categories == {general, models, memory, skills,
  connectors, permissions} (developer/advanced/hidden excluded).
- embedding default stays canonical (nomic-embed-text:v1.5).
- jobs stays out of the workspace NAV_ORDER.
"""

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_beta_settings_categories():
    from novi.configuration.bootstrap import build_registry
    from novi.configuration.schema import Visibility

    reg = build_registry()
    cats = {
        s.category.value
        for g in reg.groups()
        for s in g.settings
        if s.visibility != Visibility.HIDDEN
    }
    assert "agent" not in cats  # Agent hidden from user-facing nav
    # Registry-backed sections: General is a discovery-driven overview and
    # Skills is /api/skills-driven, so neither owns registry settings.
    # The full six-destination IA is locked by test_beta_sections_match_expected_ia
    # (frontend SECTIONS) plus the SettingsModal six-destination test.
    assert {"models", "memory", "connectors", "permissions"} <= cats


def test_agent_namespace_persists_without_ui_contract():
    from novi.configuration.bootstrap import build_registry
    from novi.configuration.schema import Category, Visibility

    reg = build_registry()
    for sid in ("agent", "agents"):
        setting = reg.get(sid)
        assert setting.namespace is True
        assert setting.category == Category.AGENT
        assert setting.visibility == Visibility.HIDDEN


def test_embedding_default_canonical():
    from novi.configuration.bootstrap import DEFAULT_CONFIG, build_registry
    from novi.configuration.install import DEFAULT_EMBEDDING_MODEL

    assert DEFAULT_EMBEDDING_MODEL == "nomic-embed-text:v1.5"
    reg = build_registry()
    assert reg.get("embedding.model").default == DEFAULT_EMBEDDING_MODEL
    assert DEFAULT_CONFIG["embedding"]["model"] == DEFAULT_EMBEDDING_MODEL


def test_jobs_not_in_nav():
    text = (REPO_ROOT / "novi" / "webui" / "src" / "components" / "sidebar" / "workspaceModes.ts").read_text(
        encoding="utf-8"
    )
    m = re.search(r"NAV_ORDER[^=]*=\s*\[(.*?)\]", text, re.S)
    assert m, "NAV_ORDER not found in workspaceModes.ts"
    assert "'jobs'" not in m.group(1) and '"jobs"' not in m.group(1)


def test_beta_sections_match_expected_ia():
    text = (REPO_ROOT / "novi" / "webui" / "src" / "components" / "settings" / "constants.tsx").read_text(
        encoding="utf-8"
    )
    ids = set(re.findall(r"id:\s*'([^']+)'", text))
    assert ids == {"general", "models", "memory", "skills", "connectors", "permissions"}
    assert "agent" not in ids
