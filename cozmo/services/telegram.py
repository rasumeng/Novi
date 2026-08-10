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