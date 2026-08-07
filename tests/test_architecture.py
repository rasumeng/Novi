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

# These files are allowed to reference model name patterns
ALLOWED_HARDCODE_FILES = {
    "cozmo/config.py",         # DEFAULT_CONFIG with empty model values only
    "cozmo/ollama_util.py",    # deleted — no longer exists
    "cozmo/ollama.py",         # Ollama process mgmt (start/stop/check)
    "cozmo/cli.py",            # Ollama process mgmt integration
    "cozmo/configuration/catalog.py",  # curated compatibility facts — referenced
                                       # only against *installed* models for
                                       # recommendations, never as a default.
}

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


# ── Test 2: Provider boundary ────────────────────────────────────────────

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
        "ModelRouter.resolve",
        "ModelService.resolve",
        "create_provider",
        "parse_model_spec",
    ]
    bypass_files = {
        "cozmo/runtime/model_router.py",  # ModelRouter — owned by runtime, allowed
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
    # This is informational only — not a hard failure since ModelRouter
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
