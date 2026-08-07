"""Legacy config shim — delegates to the Configuration Framework.

Kept so existing callers (``import cozmo.config; config.load()``) keep working
during the migration. The framework owns the true state; this module is a thin
read/dict bridge only. New code should use ``cozmo.configuration`` directly.

Constants (CONFIG_DIR, CONFIG_PATH, ...) are retained for backward compat.
"""

import logging
from pathlib import Path

from .configuration.bootstrap import (
    CONFIG_PATH,
    get_configuration,
    legacy_config,
)

log = logging.getLogger("cozmo.config")

CONFIG_DIR = Path.home() / ".cozmo"
CONFIG_BACKUP_PATH = CONFIG_DIR / "config.toml.bak"

# Retained so other modules can import DEFAULT_CONFIG by name without a
# hardcoded-model-bearing literal.
from .configuration.bootstrap import DEFAULT_CONFIG  # noqa: E402

__all__ = [
    "CONFIG_DIR",
    "CONFIG_PATH",
    "CONFIG_BACKUP_PATH",
    "DEFAULT_CONFIG",
    "load",
    "init",
    "get_configuration",
]


def load() -> dict:
    """Load resolved configuration as a dict (read-only snapshot)."""
    return legacy_config()


def init() -> dict:
    """Ensure config file + dir exist, then load. Returns snapshot."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cfg = get_configuration()
        if not CONFIG_PATH.exists():
            cfg.store.write(cfg.state.as_dict())
            log.info("created default config at %s", CONFIG_PATH)
    except (PermissionError, OSError) as e:
        log.warning("could not create config dir at %s: %s", CONFIG_DIR, e)
    return legacy_config()