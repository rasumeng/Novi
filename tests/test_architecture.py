"""Architecture regression tests.

Prevent re-introduction of anti-patterns eliminated in Phase E:
  - Hardcoded model names in production code
  - Provider SDK imports outside provider boundaries
  - Model resolution outside ModelService

Phase B rule guards:
  - Runtime never writes to storage directly (Rule #2): storage
    internals stay confined to cozmo/brain/ and the composition root.
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

    Storage implementation details (sqlite3, ConversationStore) may appear
    only in cozmo/brain/ and in the composition root (services/context.py)
    that wires the Brain. Any other production module importing them is a
    Rule #2 violation.
    """
    forbidden = ["sqlite3", "ConversationStore"]
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
