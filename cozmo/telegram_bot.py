"""Telegram surface adapter — routes messages through the ExecutionCoordinator.

Milestone 5 Phase 5E-2B.

python-telegram-bot is an OPTIONAL dependency (``pip install cozmo[telegram]``,
see ``pyproject.toml`` ``[project.optional-dependencies].telegram``). There is
also a small unrelated PyPI package named ``telegram`` that can shadow the
real SDK. To keep `cozmo` importable without the SDK, this module performs NO
top-level ``from telegram import ...``. The SDK is resolved lazily by
:func:`_require_telegram_sdk`, which distinguishes:

  1. SDK not installed        -> TelegramIntegrationError (install hint)
  2. a conflicting ``telegram``package shadows it -> TelegramIntegrationError
     (remove the conflict; the real SDK exposes ``telegram.Update`` +
     ``telegram.ext``)

The bot is a thin adapter: it owns message/chat plumbing and reply formatting.
It never builds runtimes, Tasks, or Jobs. Execution goes through the injected
async ``handler(chat_id, text) -> str``, which the entrypoint wires to the
coordinator factory (``cozmo/services/telegram.py``). The async loop is never
blocked by synchronous execution — the handler runs the coordinator off-loop.
"""

import logging
from typing import Callable, Optional

log = logging.getLogger("cozmo.telegram")


class TelegramIntegrationError(RuntimeError):
    """python-telegram-bot is missing, shadowed, or conflicting."""


_REQUIRED_SDK_ATTRS = ("Update", "Bot")


def _require_telegram_sdk():
    """Import the official python-telegram-bot lazily; raise otherwise.

    ``import telegram`` succeeds even when only the shadowing ``telegram``
    package is present, so we additionally verify the real SDK surface
    (``Update``/``Bot``) and the ``telegram.ext`` submodule (Application).
    """
    try:
        import telegram
        import telegram.ext  # noqa: F401  official SDK submodule
    except ImportError as exc:
        raise TelegramIntegrationError(
            "python-telegram-bot is optional. Install it with "
            "'pip install cozmo[telegram]'."
        ) from exc

    missing = [a for a in _REQUIRED_SDK_ATTRS if not hasattr(telegram, a)]
    if missing:
        raise TelegramIntegrationError(
            "The installed 'telegram' package is NOT python-telegram-bot "
            f"(missing: {', '.join(missing)}). Install the official library "
            "with 'pip install \"python-telegram-bot>=21\"' and remove any "
            "conflicting 'telegram' package from the environment."
        )
    return telegram


class TelegramBot:
    """PTB adapter. Transparently usable with the real SDK or a fake in tests."""

    def __init__(self, token: str, handler: Callable,
                 *, allowed_chat_ids=(), sdk=None):
        self.handler = handler
        self.allowed = set(str(c) for c in (allowed_chat_ids or ()))
        self._sdk = sdk or _require_telegram_sdk()
        app_builder = self._sdk.Application.builder()
        self.app = app_builder.token(token).build()
        self.app.add_handler(
            self._sdk.ext.CommandHandler("start", self.cmd_start))
        self.app.add_handler(
            self._sdk.ext.CommandHandler("help", self.cmd_help))
        self.app.add_handler(
            self._sdk.ext.MessageHandler(
                self._sdk.ext.filters.TEXT & ~self._sdk.ext.filters.COMMAND,
                self.handle_message,
            )
        )


    async def cmd_start(self, update, context):
        await update.message.reply_text(
            "Hello! I'm Cozmo, your local AI agent. Send me a message and I'll help."
        )

    async def cmd_help(self, update, context):
        await update.message.reply_text(
            "Send me any question or task. I can search the web, read files, "
            "do calculations, and remember our conversations."
        )

    async def handle_message(self, update, context):
        """One inbound message: coordinator execution runs OFF the loop.

        The coordinator (and the Task/Plan/Job/History it owns) is driven by
        ``self.handler``, an async callable wired to the coordinator factory.
        Reply formatting stays telegram-owned. A denial for a non-allowed chat
        happens before any execution, so no Task/Job is created for it.
        """
        try:
            chat_id = str(update.effective_chat.id)
            text = (update.message.text or "").strip()
            if self.allowed and chat_id not in self.allowed:
                await update.message.reply_text(
                    "Sorry, this chat is not allowed to use Cozmo.")
                return
            if not text:
                return
            response = await self.handler(chat_id, text)
            output = response if isinstance(response, str) else str(response or "")
            if not output:
                output = "(no response)"
            await update.message.reply_text(output)
        except Exception as e:
            log.exception("telegram message handling failed")
            try:
                await update.message.reply_text(f"Error: {e}")
            except Exception:
                pass

    def run(self):
        self.app.run_polling(allowed_updates=[])