#!/usr/bin/env python3
"""
Beta verification gate runner — Task 5.1.

Runs:
  1. pytest -q
  2. tsc --noEmit  (in novi/webui)
  3. npm --prefix novi/webui run build
  4. pytest on four targeted gate files -v
Prints manual matrix reminder. Exits non-zero if any step fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "novi" / "webui"

TARGETED_TESTS = [
    "tests/test_conversation_persistence_atomic.py",
    "tests/test_project_conversation_linking.py",
    "tests/test_capability_verification.py",
    "tests/test_discovery_honest_errors.py",
]

MANUAL_MATRIX_REMINDER = """
Manual first-run matrix (14 scenarios) -- must all pass with evidence:
  1. First launch -- Unknown not 0, no phantom models
  2. Ollama unavailable -- degraded + retry banner
  3. Ollama available, no models -- install CTA
  4. New uncached vision model -- not falsely rejected (verify -> allow)
  5. Known unsupported model -- correctly blocked (unsupported)
  6. Capability verification failure -- verification_failed + retry (never 'does not support')
  7. Image attachment -- validation + execution
  8. Search disabled -- not_configured guidance
  9. Permission ignored (timeout) -- explicit timeout/denial trace
 10. App restart -- conversations/projects persist
 11. Rapid conversation updates -- no corruption
 12. Project reassignment -- canonical consistency
 13. Attachment deletion -- GC sweep
 14. Runtime failure -- actionable ModelUnavailableError etc.
See docs/BETA_CHECKLIST.md -- fill checkboxes + sign-off.
"""


def run(cmd: list[str], cwd: Path | None = None, shell: bool = False) -> int:
    label = " ".join(cmd) if not shell else cmd  # type: ignore
    where = f" (cwd={cwd})" if cwd else ""
    print(f"\n{'='*72}\n$ {label}{where}\n{'='*72}", flush=True)
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, shell=shell)
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"[{status}] {label} -> exit {result.returncode}", flush=True)
    return result.returncode


def main() -> int:
    print("Beta verification gate -- Task 5.1", flush=True)
    print(f"Repo root: {REPO_ROOT}", flush=True)
    failures: list[str] = []

    # 1. pytest -q
    py = sys.executable
    rc = run([py, "-m", "pytest", "-q"], cwd=REPO_ROOT)
    if rc != 0:
        failures.append("pytest -q")

    # 2. tsc --noEmit in novi/webui
    # Prefer local tsc via npx (cross-platform)
    tsc_found = shutil.which("tsc") or (WEBUI_DIR / "node_modules" / ".bin" / "tsc").exists()
    npx = shutil.which("npx")
    if tsc_found or npx:
        # Use npx if available for consistent resolution; fallback to tsc
        if npx and (WEBUI_DIR / "node_modules" / ".bin" / "tsc").exists():
            rc = run([npx, "tsc", "--noEmit"], cwd=WEBUI_DIR)
        elif shutil.which("tsc"):
            rc = run(["tsc", "--noEmit"], cwd=WEBUI_DIR)
        else:
            # Try npm exec
            rc = run(["npm", "exec", "--", "tsc", "--noEmit"], cwd=WEBUI_DIR)
        if rc != 0:
            failures.append("tsc --noEmit")
    else:
        print("[SKIP] tsc not found — run `npm install` in novi/webui first", flush=True)
        failures.append("tsc --noEmit (not found)")

    # 3. npm --prefix novi/webui run build  (vite build via `npm run build` which is `tsc && vite build`)
    npm = shutil.which("npm")
    if npm:
        rc = run([npm, "--prefix", str(WEBUI_DIR), "run", "build"], cwd=REPO_ROOT)
        if rc != 0:
            failures.append("npm --prefix novi/webui run build")
    else:
        print("[SKIP] npm not found", flush=True)
        failures.append("npm --prefix novi/webui run build (npm not found)")

    # 4. targeted gate tests -v
    rc = run([py, "-m", "pytest", *TARGETED_TESTS, "-v"], cwd=REPO_ROOT)
    if rc != 0:
        failures.append("targeted gate tests")

    print("\n" + "=" * 72, flush=True)
    if failures:
        print(f"BETA GATE FAILED -- {len(failures)} step(s) failed:", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
    else:
        print("BETA GATE PASSED -- all automated checks green", flush=True)
    print(MANUAL_MATRIX_REMINDER, flush=True)
    print("Full checklist: docs/BETA_CHECKLIST.md", flush=True)
    print("=" * 72, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
