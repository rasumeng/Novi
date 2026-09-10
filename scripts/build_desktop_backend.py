#!/usr/bin/env python3
"""Build the self-contained Novi backend used by release desktop packages.

Run this from the repository root before ``npm run tauri build``.  The output
is intentionally ignored by Git: it is a platform-specific release artifact,
not source code.  Tauri bundles it as a resource named ``novi-backend``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TAURI_RESOURCES = ROOT / "novi" / "webui" / "src-tauri" / "resources"
DIST = ROOT / "build" / "desktop-backend"


def main() -> int:
    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        print("PyInstaller is required. Install with: pip install -e .[desktop-build]", file=sys.stderr)
        return 2

    name = "novi-backend"
    command = [
        pyinstaller,
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build" / "pyinstaller-work"),
        "--specpath",
        str(ROOT / "build" / "pyinstaller-spec"),
        "--collect-submodules",
        "novi",
        str(ROOT / "novi" / "desktop_backend.py"),
    ]
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        return result.returncode

    suffix = ".exe" if sys.platform == "win32" else ""
    produced = DIST / f"{name}{suffix}"
    if not produced.exists():
        print(f"PyInstaller did not produce {produced}", file=sys.stderr)
        return 1

    TAURI_RESOURCES.mkdir(parents=True, exist_ok=True)
    target = TAURI_RESOURCES / produced.name
    shutil.copy2(produced, target)
    print(f"Bundled backend prepared at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
