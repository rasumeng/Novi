"""Configuration state — resolved runtime values at the current moment.

Provides dotted-path get/set over the merged config, the layer the runtime
reads from. Never polled: changes are emitted by the manager through the bus.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def deep_get(d: dict, path: str, default: Any = None) -> Any:
    parts = path.split(".")
    cur = d
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def deep_set(d: dict, path: str, value: Any):
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


class ConfigState:
    """Thread-safe in-memory configuration state."""

    def __init__(self, initial: dict | None = None):
        self._data: dict = deepcopy(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return deep_get(self._data, key, default)

    def set(self, key: str, value: Any):
        deep_set(self._data, key, value)

    def snapshot(self) -> dict:
        return deepcopy(self._data)

    def as_dict(self) -> dict:
        return self._data