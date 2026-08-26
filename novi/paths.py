"""Central Novi profile-directory resolution with legacy Cozmo migration.

Novi keeps all persistent state under ``~/.novi``. Installations created
before the rebrand stored everything under ``~/.cozmo`` (config.toml,
memory/LanceDB, brain SQLite, chats, jobs, tasks, timeline, skills...).
The first Novi process that resolves the profile directory migrates the
old directory by renaming it, so no user data is lost or duplicated.

If the rename fails (for example because a file inside is locked by
another running instance), Novi falls back to reading/writing the legacy
directory so the app keeps working, and retries the migration on the
next launch.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("novi.paths")

HOME = Path.home() / ".novi"


def _rewrite_migrated_paths(config_path: Path) -> None:
    """Rewrite ``.cozmo`` path strings inside a migrated config.toml."""
    try:
        text = config_path.read_text("utf-8")
    except OSError:
        return
    if ".cozmo" not in text:
        return
    try:
        config_path.write_text(text.replace(".cozmo", ".novi"), "utf-8")
        log.info("migrated legacy .cozmo paths in %s", config_path)
    except OSError as exc:
        log.warning("could not patch legacy paths in %s: %s", config_path, exc)


def home() -> Path:
    """Return the Novi profile directory, migrating legacy data if needed.

    Legacy layout ``~/.cozmo`` is moved to ``~/.novi`` exactly once; when
    the move cannot be performed the legacy directory is returned so the
    application remains functional against existing data.
    """
    legacy = Path.home() / ".cozmo"
    if legacy.is_dir():
        if not HOME.exists():
            try:
                shutil.move(str(legacy), str(HOME))
                log.info("migrated profile directory %s -> %s", legacy, HOME)
                config = HOME / "config.toml"
                if config.is_file():
                    _rewrite_migrated_paths(config)
            except OSError as exc:
                log.warning(
                    "could not migrate %s -> %s (%s); using legacy directory",
                    legacy, HOME, exc,
                )
                return legacy
    return HOME
