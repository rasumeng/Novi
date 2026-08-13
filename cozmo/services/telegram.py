"""Telegram service wiring — message → conversation_id → ExecutionCoordinator.

Milestone 5 Phase 5E-2B.

The surface adapter (``cozmo/telegram_bot.py``) owns chat plumbing; this module
owns the execution hand-off:

    telegram:<chat_id>  (stable per chat)
        ↓
    build_application_execution(ctx)  (fresh runtime + ExecutionCoordinator)
        ↓
    run_stream  →  Task / Plan / Job / ExecutionHistory

Each message gets a fresh runtime (chat isolation: no cross-chat history
leakage). Task/Job lifecycle is owned by the coordinator; the async loop never
blocks because :func:`handle_telegram_message` bridges to a worker thread via
``asyncio.to_thread`` (the same thread/loop bridge the WebUI uses).

Configuration boundary: this service CONSUMES ``ctx.config["telegram"]``
(already part of ``DEFAULT_CONFIG``). Future integration settings belong in the
configuration framework under an ``integrations``/``telegram`` group (adapters
consume settings, they never own them). Until that Settings UI section has a
real feature, no registry change is needed — adapters read the resolved dict.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable, Optional

from ..telegram_bot import TelegramBot

log = logging.getLogger("cozmo.services.telegram")


def build_telegram_handler(ctx) -> Callable:
    """Return an async ``(chat_id, text) -> str`` handler wired to the coordinator.

    The handler derives the stable conversation identity, runs the coordinator
    off the event loop (``asyncio.to_thread``), and returns the assistant
    answer text for the adapter to render.
    """
    async def handler(chat_id: str, text: str) -> str:
        return await asyncio.to_thread(_handle_sync, ctx, chat_id, text)
    return handler


def _handle_sync(ctx, chat_id: str, text: str) -> str:
    """Coordinator run for one Telegram message (worker thread)."""
    from .execution import build_application_execution

    conversation_id = f"telegram:{chat_id}"
    runtime, coordinator, _ = build_application_execution(ctx)
    parts = []
    for item in coordinator.run_stream(runtime, text,
                                       conversation_id=conversation_id):
        if not item:
            continue
        kind = item[0]
        if kind == "control":
            payload = item[1]
            ctype = payload.get("type")
            if ctype == "error":
                return payload.get("text", "Error")
            if ctype == "continuation_candidates":
                return _format_candidates(payload.get("candidates", []))
        elif kind == "token":
            parts.append(str(item[1]))
    return "".join(parts).strip()


def _format_candidates(candidates: list) -> str:
    """Telegram-friendly rendering of resumable work candidates."""
    if not candidates:
        return "You have resumable work, but nothing is clearly continue-able."
    lines = ["Multiple pieces of work can be continued:"]
    for i, c in enumerate(candidates, 1):
        title = c.get("title", "") or c.get("task_id", "")
        progress = c.get("progress", "") or ""
        lines.append(f"{i}. {title} ({progress} steps done)")
    lines.append("Tell me which one to keep going on.")
    return "\n".join(lines)


def build_telegram_bot(ctx, token: str, *, allowed_chat_ids=()) -> TelegramBot:
    """Wire a TelegramBot adapter to the coordinator-backed handler."""
    return TelegramBot(token, build_telegram_handler(ctx),
                       allowed_chat_ids=allowed_chat_ids)


class TelegramLifecycle:
    """Minimal start/stop/status seam driving the existing TelegramBot.

    M5.3 lifecycle owner: ``telegram.enabled`` under the ``integrations``
    config owner. NOT the M5.6 connector abstraction — just enough seam so the
    enabled flag controls the real bot lifecycle from the WebUI/runtime path.

    State machine: ``stopped -> starting -> running`` (or ``error``), then
    ``running -> stopping -> stopped``. Startup failure never rewrites the
    persisted configuration — the user's intent stays and the error is exposed
    through :meth:`get_status` as ``last_error``.
    """

    def __init__(self, ctx, *, bot_factory=None):
        self._ctx = ctx
        self._bot_factory = bot_factory or build_telegram_bot
        self._bot: TelegramBot | None = None
        self._enabled = False
        self._state = "stopped"
        self._last_error: str | None = None
        self._lock = threading.Lock()

    # ── lifecycle ─────────────────────────────────────────────────

    def apply(self, config: dict) -> None:
        """Reconcile the bot against the configured intent (idempotent)."""
        with self._lock:
            self._enabled = bool(config.get("telegram", {}).get("enabled", False))
            if self._enabled:
                self._start_locked(config)
            else:
                self._stop_locked()

    def start(self, config: dict) -> None:
        """Initial start from the application path. Respects the setting."""
        self.apply(config)

    def stop(self) -> None:
        """Stop the bot. Idempotent; safe when never started."""
        with self._lock:
            self._stop_locked()

    # ── runtime client access (M5.6 injection seam) ────────────────

    def get_runtime_client(self):
        """The active TelegramBot runtime client, or None when not running.

        M5.6: the lifecycle OWNS the active runtime client. Tools reach it only
        through this accessor (never a module-global). Returns None while
        disabled, stopped, errored, or between stop and next start, so a bound
        tool fails safely whenever Telegram is unavailable.
        """
        with self._lock:
            return self._bot

    # ── status ────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Safe status surface. Never exposes the token or any secret."""
        with self._lock:
            return {
                "enabled": self._enabled,
                "state": self._state,
                "running": self._state == "running",
                "last_error": self._last_error,
            }

    # ── internals ─────────────────────────────────────────────────

    def _start_locked(self, config: dict) -> None:
        if self._state in ("running", "starting"):
            return
        token = config.get("telegram", {}).get("bot_token", "")
        if not token:
            self._last_error = "telegram.bot_token is not configured"
            self._state = "error"
            return
        self._state = "starting"
        try:
            allowed = config.get("telegram", {}).get("allowed_chat_ids", [])
            bot = self._bot_factory(self._ctx, token, allowed_chat_ids=allowed)
            bot.start()
            self._bot = bot
            self._state = "running"
            self._last_error = None
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            self._state = "error"

    def _stop_locked(self) -> None:
        if self._bot is not None:
            try:
                self._bot.stop()
            except Exception:
                pass
            self._bot = None
        self._state = "stopped"