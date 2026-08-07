"""Configuration store — owns the persistent config file.

Only the Configuration Framework writes config files. Subsystems register
settings; the store persists them. Reads are merge of defaults + file.
"""

from __future__ import annotations

import logging
import shutil
from copy import deepcopy
from pathlib import Path

import tomli_w

log = logging.getLogger("cozmo.config.store")


class ConfigStore:
    """TOML-backed persistence for the configuration framework."""

    def __init__(self, path: Path, defaults: dict | None = None):
        self.path = Path(path)
        self._defaults = defaults or {}

    # ── reading ─────────────────────────────────────────────────────

    def load(self) -> dict:
        """Return merged config: defaults filled under file values."""
        data = self._read_file()
        merged = deepcopy(self._defaults)
        self._deep_merge(merged, data)
        return merged

    def _read_file(self) -> dict:
        import tomllib

        if not self.path.exists():
            return {}
        try:
            with open(self.path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            log.warning("failed to parse %s: %s; using defaults", self.path, e)
            return {}

    def write(self, config: dict):
        """Persist the whole config (None values stripped, TOML-safe)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cleaned = _strip_none(config)
        with open(self.path, "wb") as f:
            tomli_w.dump(cleaned, f)

    def backup(self):
        if self.path.exists():
            shutil.copy2(str(self.path), str(self.path) + ".bak")

    @staticmethod
    def _deep_merge(base: dict, override: dict):
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                ConfigStore._deep_merge(base[k], v)
            else:
                base[k] = v


def _strip_none(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            cleaned = _strip_none(v)
            if cleaned:
                out[k] = cleaned
        elif isinstance(v, list):
            out[k] = [_strip_none(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out