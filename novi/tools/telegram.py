from . import register_tool

# M5.6: no module-global Telegram runtime client. The tool reads the active
# TelegramBot from an injected accessor (bound by the composition root to the
# TelegramLifecycle that OWNS the runtime client). ``make_telegram_send``
# produces a telegram_send bound to a zero-arg accessor; the default registered
# tool fails safely when nothing was injected.

def _no_runtime_client():
    return None


def make_telegram_send(get_runtime_client):
    """Bind ``telegram_send`` to a runtime-client accessor.

    ``get_runtime_client`` is a zero-arg callable returning the active
    ``TelegramBot`` owned by the TelegramLifecycle (or None when Telegram is
    disabled/stopped). The bound tool resolves the client at call time so a
    restart cleanly replaces the reference.
    """
    def telegram_send(chat_id: str, message: str) -> str:
        bot = get_runtime_client()
        if bot is None:
            return "Error: Telegram bot is not running"
        try:
            import asyncio
            coro = bot.app.bot.send_message(chat_id=chat_id, text=message)
            asyncio.create_task(coro)
            return f"Message sent to {chat_id}"
        except Exception as e:
            return f"Error sending message: {e}"
    telegram_send.__name__ = "telegram_send"
    telegram_send.__doc__ = "Send a message to a Telegram chat."
    return telegram_send


@register_tool()
def telegram_send(chat_id: str, message: str) -> str:
    """Send a message to a Telegram chat."""
    return make_telegram_send(_no_runtime_client)(chat_id, message)