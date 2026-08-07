"""Configuration event bus — typed change notification.

Tiny internal bus. Every configuration change is emitted as a
``config.updated`` event carrying ``path``, ``value``, ``previous``, ``by``.
Subscribers filter by path prefix; no polling anywhere.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger("cozmo.config.events")


@dataclass
class ConfigEvent:
    path: str
    value: Any
    previous: Any = None
    by: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "value": self.value,
            "previous": self.previous,
            "by": self.by,
            "timestamp": self.timestamp,
        }


ConfigHandler = Callable[[ConfigEvent], None]


class ConfigBus:
    """Thread-safe config change bus with prefix matching."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[tuple[str, ConfigHandler]] = []
        self._wildcards: list[ConfigHandler] = []

    def subscribe(self, prefix: str, handler: ConfigHandler):
        with self._lock:
            self._subscribers.append((prefix, handler))

    def on_any(self, handler: ConfigHandler):
        with self._lock:
            self._wildcards.append(handler)

    def emit(self, event: ConfigEvent):
        with self._lock:
            subs = list(self._subscribers)
            wild = list(self._wildcards)
        for prefix, handler in subs:
            if prefix and (event.path == prefix or event.path.startswith(prefix + ".")):
                self._run(handler, event)
        for handler in wild:
            self._run(handler, event)

    @staticmethod
    def _run(handler: ConfigHandler, event: ConfigEvent):
        try:
            handler(event)
        except Exception as e:
            log.warning("config handler failed for %s: %s", event.path, e)


# Module-level singleton — one config bus per process.
_bus: ConfigBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> ConfigBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = ConfigBus()
        return _bus
