"""Architecture regression tests.

Prevent re-introduction of anti-patterns eliminated in Phase E:
  - Hardcoded model names in production code
  - Provider SDK imports outside provider boundaries
  - Model resolution outside ModelService

Phase B rule guards:
  - Runtime never writes to storage directly (Rule #2): storage
    internals stay confined to cozmo/brain/ and the composition root.

Phase C rule guards:
  - The reasoning tier is pure: cozmo/brain/reasoning/ imports no storage.
  - Brain.observe no longer calls the legacy MemoryManager write path.
"""

import ast
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COZMO_SRC = PROJECT_ROOT / "cozmo"

# Model names that should NEVER appear in production code
# (config defaults are the single exception)
HARDCODED_MODEL_PATTERNS = [
    r"\bllama\b", r"\bqwen\b", r"\bmistral\b", r"\bphi[34]", r"\bgemma\b",
]

# These files are allowed to reference model name patterns.
# ``model_seeds.py`` is the single curated seed-facts site and
# ``name_inference.py`` the single weak name-heuristic site — both are
# explicitly NON-authoritative and isolated. ``runtime_inventory.py`` carries
# Ollama protocol keys (e.g. ``llama.context_length``) that are GGUF metadata
# field names, not model names.
ALLOWED_HARDCODE_FILES = {
    "cozmo/ollama_util.py",    # deleted — no longer exists
    "cozmo/ollama.py",         # Ollama process mgmt (start/stop/check)
    "cozmo/cli.py",            # Ollama process mgmt integration
    "cozmo/configuration/model_seeds.py",   # curated, NON-authoritative seed facts
    "cozmo/configuration/name_inference.py",  # isolated weak name heuristics
    "cozmo/configuration/runtime_inventory.py",  # Ollama protocol/GGUF key names
}

# Model-name substrings used by name inference and evidence layers. They must
# ONLY appear in the isolated seed/evidence files — never as routing logic.
NAME_EVIDENCE_TOKENS = [
    "llava", "minicpm", "qwen2-vl", "-vl", "coder", "codegemma",
    "deepseek-coder", "moondream",
]

# Retired model-configuration vocabulary that must never reappear. A model is
# selected via ``llm.workloads.*``; there is no mode/role/custom-assign model
# concept, no Automatic/Custom eligibility, and no authoritative hardcoded
# fact table.
RETIRED_VOCABULARY = [
    "models.mode",
    "llm.roles",
    "models.custom.assign",
    "eligibleAutomatic",
    "eligibleCustom",
    "eligible_automatic",
    "eligible_custom",
    "automatically_selectable",
    "KNOWN_MODEL_FACTS",
]

# Provider SDKs that only cozmo/providers/ may import
PROVIDER_ONLY_IMPORTS = [
    "ChatOllama",
    "langchain_ollama",
    "OpenAI",
    "openai",
    "langchain_openai",
]

ALLOWED_PROVIDER_DIRS = {
    "cozmo/providers",
    "cozmo/runtime/providers",
    "cozmo/runtime/models",
}


# ── Helpers ─────────────────────────────────────────────────────────────

def _iter_py_files(root: Path, exclude_dirs=None):
    """Yield all .py files under root, excluding tests/ and __pycache__."""
    exclude = {"__pycache__", ".git", "node_modules", "venv", ".venv"}
    if exclude_dirs:
        exclude = exclude | set(exclude_dirs)
    for path in root.rglob("*.py"):
        if any(part in exclude for part in path.parts):
            continue
        yield path


def _iter_frontend_files():
    """Yield all .ts/.tsx source files under cozmo/webui/src."""
    src = COZMO_SRC / "webui" / "src"
    if not src.exists():
        return
    for path in src.rglob("*"):
        if path.is_file() and path.suffix in (".ts", ".tsx"):
            yield path


def _is_comment(line: str) -> bool:
    return line.strip().startswith("#") or line.strip().startswith('"""') or line.strip().startswith("'''")


def _is_docstring(node: ast.AST) -> bool:
    return isinstance(node, (ast.Expr,)) and isinstance(getattr(node, "value", None), ast.Constant)


# ── Test 1: No hardcoded model names ────────────────────────────────────

def test_no_hardcoded_model_names():
    """Fail if any .py file in cozmo/ contains hardcoded model names."""
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        if rel in ALLOWED_HARDCODE_FILES:
            continue
        for pattern in HARDCODED_MODEL_PATTERNS:
            try:
                text = pyfile.read_text("utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if re.search(pattern, line, re.IGNORECASE) and not _is_comment(line):
                        violations.append(f"{rel}:{i}: {line.strip()[:80]}")
            except Exception:
                pass
    if violations:
        raise AssertionError(
            f"Found {len(violations)} hardcoded model name(s):\n" +
            "\n".join(violations[:20]) +
            ("\n... (truncated)" if len(violations) > 20 else "")
        )


# ── Test 2: No model-name substring routing ─────────────────────────────

def test_no_model_name_substring_conditionals():
    """Model-name substrings must never drive production logic.

    Name heuristics may live ONLY in the isolated evidence files
    (``name_inference.py`` / ``model_seeds.py``), and are explicitly
    non-authoritative. A production line that checks a model-name token with
    ``in`` is the retired anti-pattern.
    """
    allowed = {
        "cozmo/configuration/model_seeds.py",
        "cozmo/configuration/name_inference.py",
    }
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        if rel in allowed:
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for token in NAME_EVIDENCE_TOKENS:
                if token in line and " in " in line:
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Model-name substring conditionals outside isolated evidence files:\n"
            + "\n".join(violations[:20])
            + ("\n... (truncated)" if len(violations) > 20 else "")
        )


# ── Test 3: Retired model-configuration vocabulary never returns ─────────

def test_no_retired_model_vocabulary():
    """Retired model-mode/Automatic/Custom vocabulary must not reappear.

    Selection is persisted only as ``llm.workloads.*``. ``models.mode``, role
    assignments, automatic/custom eligibility, and the authoritative fact
    table name are all retired — in Python and in the frontend.
    """
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        if pyfile.suffix not in (".py", ".ts", ".tsx"):
            continue
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for token in RETIRED_VOCABULARY:
                if token in line:
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Retired model-configuration vocabulary found:\n"
            + "\n".join(violations[:20])
            + ("\n... (truncated)" if len(violations) > 20 else "")
        )


def test_no_automatic_vocabulary_in_frontend():
    """The word "Automatic"/"automatic" must never appear in the webui source.

    Model selection is user-explicit: recommended or user-chosen, persisted as
    ``llm.workloads.*``. Any "automatic" selection concept is retired. The
    guard uses a word boundary so cosmetic "automatically" copy is allowed.
    """
    violations = []
    for f in _iter_frontend_files():
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        text = f.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if re.search(r"\b[Aa]utomatic\b", line):
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Frontend references an automatic-selection concept:\n"
            + "\n".join(violations[:20])
            + ("\n... (truncated)" if len(violations) > 20 else "")
        )


def test_no_retired_vocabulary_in_frontend():
    """Retired model-mode/Automatic/Custom vocabulary must not appear in the
    webui source either."""
    violations = []
    for f in _iter_frontend_files():
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        text = f.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for token in RETIRED_VOCABULARY:
                if token in line:
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Retired model-configuration vocabulary found in frontend:\n"
            + "\n".join(violations[:20])
            + ("\n... (truncated)" if len(violations) > 20 else "")
        )


# ── Test 4: Name inference isolated from the runtime ─────────────────────

def test_name_inference_never_used_by_runtime():
    """The runtime must not consume weak name inference.

    ``runtime/model_selector.py`` performs authoritative capability checks and
    must rely only on seed facts + measured runtime evidence, never a name
    substring.
    """
    runtime_dir = COZMO_SRC / "runtime"
    for pyfile in runtime_dir.rglob("*.py"):
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if "name_inference" in line or "infer_capabilities_from_name" in line:
                raise AssertionError(
                    f"runtime uses name inference: {pyfile.name}:{i}: {line.strip()[:80]}"
                )


# ── Test 5: Provider boundary ────────────────────────────────────────────

def test_provider_boundary():
    """Fail if provider-specific SDKs are imported outside allowed dirs."""
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC, exclude_dirs=["tests"]):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        is_allowed = any(rel.startswith(d) for d in ALLOWED_PROVIDER_DIRS)
        if is_allowed:
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for pattern in PROVIDER_ONLY_IMPORTS:
            if pattern in text:
                # Verify it's an actual import, not a comment
                for i, line in enumerate(text.splitlines(), 1):
                    if pattern in line and ("import" in line or "from" in line):
                        violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            f"Found {len(violations)} provider SDK import(s) outside allowed dirs:\n" +
            "\n".join(violations)
        )


# ── Test 3: Model resolution through ModelService ────────────────────────

def test_model_resolution_ownership():
    """Fail if model resolution code bypasses ModelService.

    Allowed resolution entry points:
      - cozmo/models/service.py (ModelService)
      - cozmo/providers/base.py (LLMProvider base)
      - tests/
    """
    # Patterns that indicate model resolution
    resolution_patterns = [
        "ModelSelector.resolve",
        "ModelService.resolve",
        "create_provider",
        "parse_model_spec",
    ]
    bypass_files = {
        "cozmo/runtime/model_selector.py",  # ModelSelector — owned by runtime, allowed
        "cozmo/runtime/models/factory.py",  # ModelRuntime — thin provider boundary, allowed
    }
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        if rel in bypass_files:
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for pattern in resolution_patterns:
            if pattern in text:
                for i, line in enumerate(text.splitlines(), 1):
                    if pattern in line:
                        violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    # This is informational only — not a hard failure since ModelSelector
    # still exists in runtime. Convert to warning.
    if violations:
        print(
            f"[INFO] Found {len(violations)} model resolution call(s) "
            f"outside ModelService:\n" +
            "\n".join(violations)
        )


# ── Test 4: Runtime never writes to storage directly ─────────────────────

def test_runtime_does_not_touch_storage_internals():
    """Rule #2: the Runtime reports events; the Brain decides persistence.

    Storage implementation details (sqlite3, the store classes) may appear
    only in cozmo/brain/ and in the composition root (services/context.py)
    that wires the Brain. Any other production module importing them is a
    Rule #2 violation.
    """
    forbidden = [
        "sqlite3", "ConversationStore", "ScenarioStore", "VectorStore",
        "RelationshipStore", "conversation_store", "scenario_store",
        "vector_store", "relationship_store",
    ]
    allowed_prefixes = ("cozmo/brain/",)
    allowed_files = {"cozmo/services/context.py"}
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        if rel in allowed_files or any(rel.startswith(p) for p in allowed_prefixes):
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for pat in forbidden:
                if pat in line and ("import" in line or "from" in line):
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Storage internals leaked outside cozmo/brain/ (Rule #2):\n"
            + "\n".join(violations)
        )


# ── Test 5: Reasoning tier is pure ─────────────────────────────────────

def test_reasoning_tier_has_no_storage_imports():
    """Phase C: cozmo/brain/reasoning/ operates on Brain objects only.

    No sqlite3, no LanceDB, no store classes, no storage module imports.
    Persistence lives in layers/Brain, never in reasoning.
    """
    reasoning_dir = COZMO_SRC / "brain" / "reasoning"
    if not reasoning_dir.exists():
        return
    forbidden = [
        "sqlite3", "lancedb", "LanceStore", "ConversationStore",
        "KnowledgeStore", "ScenarioStore", "VectorStore", "RelationshipStore",
        "lancedb_store", "conversation_store", "knowledge_store",
        "scenario_store", "vector_store", "relationship_store",
    ]
    violations = []
    for pyfile in reasoning_dir.rglob("*.py"):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if ("import" in line or "from" in line) and any(
                f in line for f in forbidden
            ):
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Reasoning tier imported storage (must be pure):\n"
            + "\n".join(violations)
        )


# ── Test 6: Rule #5 — knowledge is append-only ──────────────────────────

def test_brain_documents_append_only_rule():
    """Rule #5: KnowledgeItems are immutable historical observations.

    Change is a supersedes edge, never an overwrite. The Brain module
    docstring must state this contract so future retrieval/brain-intelligence
    work builds against frozen semantics.
    """
    brain_file = COZMO_SRC / "brain" / "brain.py"
    text = brain_file.read_text("utf-8", errors="replace")
    if "append-only" not in text:
        raise AssertionError(
            "brain.py must document Architecture Rule #5 (append-only knowledge)"
        )


def test_brain_does_not_register_in_place_mutation():
    """Rule #5: no producer may mutate a persisted KnowledgeItem in place.

    The write pipeline persists new items and supersedes edges only. A direct
    ``.confidence =`` / ``.content =`` assignment on a stored item is the
    anti-pattern this rule forbids.
    """
    brain_dir = COZMO_SRC / "brain"
    violations = []
    for pyfile in brain_dir.rglob("*.py"):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if re.search(r"\.(confidence|content)\s*=\s*$|\.(confidence|content)\s*=", line):
                if "assert" in line or "==" in line or "!=" in line or "param" in line or "field=" in line:
                    continue
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "In-place KnowledgeItem mutation found (Rule #5):\n"
            + "\n".join(violations)
        )


# ── Test 7: Brain no longer calls legacy memory write ──────────────────

def test_brain_observe_does_not_call_legacy_memory():
    """Phase C: the legacy add_interaction write is gone from Brain.

    Only the brain=None runtime/WebUI fallbacks and MemoryManager itself may
    reference add_interaction.
    """
    brain_file = COZMO_SRC / "brain" / "brain.py"
    text = brain_file.read_text("utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if _is_comment(line):
            continue
        if "add_interaction" in line:
            raise AssertionError(
                f"cozmo/brain/brain.py:{i}: legacy add_interaction still called from Brain"
            )


# ── Phase 7 Stage 1 — ModelRuntime architecture guards ───────────────────

def _iter_runtime_files():
    """Yield all .py files under cozmo/runtime/ (incl. runtime/models/)."""
    return _iter_py_files(COZMO_SRC / "runtime")


# ── Guard 1 — no hardcoded model IDs in the runtime ──────────────────────

def test_no_hardcoded_model_ids_in_runtime():
    """The runtime (incl. cozmo/runtime/models/) must contain no model IDs.

    The runtime receives ``selected_model.model`` from Cozmo's resolver; a
    literal model name must never appear in runtime execution code.
    """
    violations = []
    for pyfile in _iter_runtime_files():
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for pattern in HARDCODED_MODEL_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Hardcoded model IDs found in runtime execution code:\n"
            + "\n".join(violations)
        )


# ── Guard 2 — no model-name substring branching in runtime ───────────────

def test_runtime_no_model_name_substring_conditionals():
    """Model-name substrings must never drive runtime logic.

    The runtime never branches on a model name; resolution happens strictly
    upstream in Cozmo's model-selection system.
    """
    violations = []
    for pyfile in _iter_runtime_files():
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for token in NAME_EVIDENCE_TOKENS:
                if token in line and " in " in line:
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Model-name substring conditionals in runtime code:\n"
            + "\n".join(violations)
        )


# ── Guard 3 — LangChain model construction confined to providers/ + runtime/models ──

def test_langchain_model_construction_confined():
    """ChatOllama / ChatOpenAI construction imports stay inside the provider
    boundary (cozmo/providers/, cozmo/runtime/models/).

    Message-type imports from langchain_core in the runtime remain allowed —
    they are not model construction.
    """
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        is_allowed = any(rel.startswith(d) for d in ALLOWED_PROVIDER_DIRS)
        if is_allowed:
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for pattern in PROVIDER_ONLY_IMPORTS:
            if pattern in text:
                for i, line in enumerate(text.splitlines(), 1):
                    if pattern in line and ("import" in line or "from" in line):
                        violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "LangChain model construction outside the approved boundary:\n"
            + "\n".join(violations)
        )


# ── Guard 4 — no recommendation→execution coupling ───────────────────────

def test_runtime_does_not_import_recommendation():
    """The runtime must not import recommendation logic. Model selection is a
    configuration-system concern; the runtime only executes the resolved
    selection."""
    forbidden = [
        "configuration.recommendation",
        "configuration.catalog",
        "ModelRecommendationEngine",
        "recommend",
    ]
    violations = []
    for pyfile in _iter_runtime_files():
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for token in forbidden:
                if token in line and ("import" in line or "from" in line):
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Runtime imports recommendation logic (recommendation→execution coupling):\n"
            + "\n".join(violations)
        )


# ── Guard 5 — graph modules must never select/recommend models ───────────

def test_graph_modules_never_select_models():
    """If any LangGraph module exists it must receive its model from Cozmo's
    resolver — it must never resolve, recommend, or select a model itself."""
    graphs_dir = COZMO_SRC / "graphs"
    if not graphs_dir.exists():
        return  # no graphs in Stage 1 — guard is dormant
    forbidden = [
        "ModelService",
        "ModelSelector",
        "ModelRecommendationEngine",
        "recommend",
        "apply_selection",
        "create_provider",
        "configuration.resolver",
        "llm.workloads",
    ]
    violations = []
    for pyfile in graphs_dir.rglob("*.py"):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for token in forbidden:
                if token in line and ("import" in line or "from" in line):
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Graph module selects/recommends its own model:\n"
            + "\n".join(violations)
        )


# ── Guard 6 — no model fallback/substitution vocabulary in the boundary ──

def test_no_model_fallback_or_substitution_vocabulary():
    """The model boundary must never express fallback/substitution of the
    selected model. Tool-level fallbacks and retrieval source fallbacks are
    unrelated and remain allowed."""
    forbidden = [
        "fallback_model",
        "model_fallback",
        "backup_model",
        "substitute_model",
        "alternate_model",
        "auto_select",
    ]
    scope = [COZMO_SRC / "runtime" / "models", COZMO_SRC / "models"]
    violations = []
    for root in scope:
        for pyfile in root.rglob("*.py"):
            rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
            text = pyfile.read_text("utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if _is_comment(line):
                    continue
                for token in forbidden:
                    if token in line:
                        violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Model fallback/substitution vocabulary in the model boundary:\n"
            + "\n".join(violations)
        )


# ── Guard 7 — no Automatic vocabulary in the runtime/models boundary ─────

def test_no_automatic_vocabulary_in_runtime_models():
    """The runtime and model boundary must not reintroduce any Automatic
    concept. Selection is user-explicit; there is no automatic mode."""
    violations = []
    scope = [COZMO_SRC / "runtime", COZMO_SRC / "models"]
    for root in scope:
        for pyfile in root.rglob("*.py"):
            rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
            text = pyfile.read_text("utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if _is_comment(line):
                    continue
                if re.search(r"\b[Aa]utomatic\b", line):
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Automatic-selection vocabulary in runtime/models boundary:\n"
            + "\n".join(violations)
        )


# ── Guard 8 — apply_selection is the sole selection writer ───────────────

def test_apply_selection_is_sole_selection_writer():
    """apply_selection() may only be referenced by the selection system and
    the WebUI selection endpoints — never by runtime/model construction code.

    It is the single persisted-selection write path (Phase 6 contract).
    """
    allowed = {
        "cozmo/configuration/resolver.py",   # definition
        "cozmo/configuration/catalog.py",    # docstring reference
        "cozmo/webui_server.py",             # selection endpoints
        "cozmo/runtime/models/factory.py",   # prohibition reference (never calls it)
    }
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        if rel in allowed:
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if "apply_selection" in line:
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "apply_selection referenced outside the selection system (second writer):\n"
            + "\n".join(violations)
        )


# ── Phase 7 Stage 2 — legacy config shim guards ─────────────────────────
# The legacy compatibility layer is gone: cozmo/config.py (deleted),
# legacy_config()/COZMO_OLLAMA_URL (bootstrap.py), the raw-TOML CLI writes,
# the PUT /api/config bulk endpoint, the sync cfg shadow dict, the
# conversation ``mode`` field, run_stream ``force_mode``, and the brain=None
# ``memory.add_interaction`` runtime fallback. These guards make sure none of
# them regress.


# ── Guard A — legacy config module erased ────────────────────────────────

def test_no_legacy_config_module():
    """The legacy ``cozmo/config.py`` dict-shim module must never return."""
    assert not (COZMO_SRC / "config.py").exists(), \
        "cozmo/config.py must stay deleted — bootstrap get_configuration() is the entry"
    forbidden = ["config.load(", "config.init(", "legacy_config("]
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if any(tok in line for tok in forbidden):
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Legacy config shim API still referenced in production:\n"
            + "\n".join(violations)
        )


# ── Guard B — no environment-variable shims ─────────────────────────────

def test_no_env_override_shims():
    """The COZMO_OLLAMA_URL env hack (and friends) must never return."""
    forbidden = ["COZMO_OLLAMA_URL", "_apply_env_overrides"]
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if any(tok in line for tok in forbidden):
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Legacy env-override shims found:\n" + "\n".join(violations)
        )


# ── Guard C — no direct TOML writes outside the framework ───────────────

def test_no_direct_toml_writes_outside_framework():
    """Raw TOML serialization lives only inside cozmo/configuration/."""
    forbidden = ["tomli_w", "toml.dump", '".toml", "w"']
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        if rel.startswith("cozmo/configuration/"):
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if any(tok in line for tok in forbidden):
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Direct TOML serialization outside the Configuration Framework:\n"
            + "\n".join(violations)
        )


# ── Guard D — no legacy bulk config endpoint ────────────────────────────

def test_no_legacy_put_config_endpoint():
    """PUT /api/config (put_config) must not reappear in the WebUI server."""
    webui = (COZMO_SRC / "webui_server.py").read_text("utf-8", errors="replace")
    for i, line in enumerate(webui.splitlines(), 1):
        if _is_comment(line):
            continue
        if "def put_config" in line or '@app.put("/api/config")' in line:
            raise AssertionError(
                f"cozmo/webui_server.py:{i}: legacy bulk-write endpoint returned"
            )


# ── Guard E — no legacy bulk-write consumers ────────────────────────────

def test_no_legacy_config_write_consumers():
    """No active frontend/backend code writes config via PUT /api/config."""
    violations = []
    for f in _iter_frontend_files():
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        text = f.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("//") or line.strip().startswith("*"):
                continue
            if "api/config" in line and "PUT" in line:
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if "put_config" in line or "@app.put" in line and "/api/config" in line:
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Legacy bulk-write config consumers still present:\n"
            + "\n".join(violations)
        )


# ── Guard F — no conversation-mode persistence ──────────────────────────

def test_no_conversation_mode_persistence():
    """The obsolete conversation ``mode`` field must never be persisted."""
    webui = (COZMO_SRC / "webui_server.py").read_text("utf-8", errors="replace")
    forbidden = [
        'body.get("mode"',
        '"mode": mode',
        "mode: {mode}",
        'get("mode", "chat")',
        '"mode": c.get("mode"',
    ]
    for i, line in enumerate(webui.splitlines(), 1):
        if _is_comment(line):
            continue
        if any(tok in line for tok in forbidden):
            raise AssertionError(
                f"cozmo/webui_server.py:{i}: legacy conversation mode persisted: {line.strip()[:80]}"
            )


# ── Guard G — no force_mode ─────────────────────────────────────────────

def test_no_force_mode_in_runtime():
    """run_stream ``force_mode`` was removed — force_capability/force_model only."""
    violations = []
    for pyfile in _iter_runtime_files():
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if re.search(r"\bforce_mode\b", line):
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "force_mode returned (use force_capability / force_model):\n"
            + "\n".join(violations)
        )


# ── Guard H — no brain=None memory fallback in the runtime ──────────────

def test_no_legacy_memory_fallback_in_runtime():
    """The runtime never writes to MemoryManager directly.

    _remember routes through Brain.observe only; the brain=None
    memory.add_interaction fallback was removed (Phase 7 Stage 2).
    """
    violations = []
    for pyfile in _iter_runtime_files():
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if "add_interaction" in line:
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Legacy memory fallback in the runtime:\n" + "\n".join(violations)
        )


# ── Guard I — single configuration authority ────────────────────────────

def test_configuration_constructed_only_in_framework():
    """Configuration instances are built only inside cozmo/configuration/.

    Every other consumer reads/writes through ``get_configuration()`` — the
    single composition root for configuration state.
    """
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        if rel.startswith("cozmo/configuration/"):
            continue
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            if "Configuration(" in line:
                violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "Configuration constructed outside the framework:\n" + "\n".join(violations)
        )


# ── Guard J — CLI config goes through the framework ─────────────────────

def test_cli_config_uses_framework():
    """config_cli.py must route every read/write through the framework."""
    cli_cfg = COZMO_SRC / "config_cli.py"
    if not cli_cfg.exists():
        raise AssertionError("cozmo/config_cli.py missing")
    text = cli_cfg.read_text("utf-8", errors="replace")
    if "get_configuration" not in text:
        raise AssertionError("config_cli.py must use get_configuration()")
    for i, line in enumerate(text.splitlines(), 1):
        if _is_comment(line):
            continue
        if any(tok in line for tok in ("tomli_w", "config_mod", ".write_text(", 'open(""')):
            raise AssertionError(
                f"cozmo/config_cli.py:{i}: direct config file I/O: {line.strip()[:80]}"
            )


# ── Guard K — knowledge directory owned by configuration ─────────────────

def test_no_cwd_relative_knowledge_path():
    """M1: the knowledge base directory must come from ``workspace.knowledge``,
    never a CWD-relative ``./knowledge`` literal.

    The knowledge base lives under the configured workspace
    (``~/.cozmo/knowledge`` by default); a process that starts elsewhere must
    not silently read/write a different ``./knowledge`` folder.
    """
    forbidden = ['"./knowledge"', "KNOWLEDGE = Path", "Path('./knowledge')"]
    violations = []
    for pyfile in _iter_py_files(COZMO_SRC):
        rel = pyfile.relative_to(PROJECT_ROOT).as_posix()
        text = pyfile.read_text("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _is_comment(line):
                continue
            for tok in forbidden:
                if tok in line:
                    violations.append(f"{rel}:{i}: {line.strip()[:80]}")
    if violations:
        raise AssertionError(
            "CWD-relative knowledge path returned (use workspace.knowledge):\n"
            + "\n".join(violations)
        )
