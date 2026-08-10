"""Milestone 5 Phase 5E-2B — Telegram dependency boundary + adapter behavior.

Covers:
  - ``cozmo/telegram_bot.py`` stays importable WITHOUT python-telegram-bot
    (no top-level SDK import).
  - ``_require_telegram_sdk`` distinguishes: SDK missing (install hint),
    conflicting ``telegram`` package shadowing (conflict message), and the
    real SDK surface present (accept).
  - ``TelegramBot`` builds the app from a fake SDK and keeps reply formatting
    and message plumbing adapter-owned.
  - async adapter behavior: a non-allowed chat is denied before any execution;
    errors surface as ``Error: ...`` replies; the handler routes the message.

No live Telegram bot/token is required — everything is faked.
"""

import asyncio
import importlib
import sys
import types

import pytest

from cozmo.telegram_bot import TelegramBot, TelegramIntegrationError
from cozmo.telegram_bot import _require_telegram_sdk

MODULE = "cozmo.telegram_bot"


# ── fake SDK / transport ─────────────────────────────────────────────────────

class _FakeApp:
    def __init__(self):
        self.handlers = []
        self.polled = None

    def add_handler(self, handler):
        self.handlers.append(handler)

    def run_polling(self, allowed_updates=None):
        self.polled = allowed_updates


class _FakeBuilder:
    def __init__(self):
        self._token = None

    def token(self, token):
        self._token = token
        return self

    def build(self):
        return _FakeApp()


class _FakeApplication:
    """``Application.builder()`` — the only call the adapter makes."""

    @staticmethod
    def builder():
        return _FakeBuilder()


class _FakeMsgFilter:
    def __invert__(self):
        return self

    def __and__(self, other):
        return self

    def __or__(self, other):
        return self

    def __call__(self, msg):
        return True


class _FakeFilters:
    TEXT = _FakeMsgFilter()
    COMMAND = _FakeMsgFilter()


class _FakeCommandHandler:
    def __init__(self, name, callback):
        self.name = name
        self.callback = callback


class _FakeMessageHandler:
    def __init__(self, _filter, callback):
        self._filter = _filter
        self.callback = callback


class _FakeExt(types.ModuleType):
    Application = _FakeApplication          # real SDK exposes it under telegram.ext
    CommandHandler = _FakeCommandHandler
    MessageHandler = _FakeMessageHandler
    filters = _FakeFilters()


class _FakeSdk(types.ModuleType):
    def __init__(self, *, update=True, bot=True, ext=True):
        super().__init__("telegram")
        self.__version__ = "21.0"
        self.Application = _FakeApplication
        if update:
            self.Update = object
        if bot:
            self.Bot = object
        if ext:
            self.ext = _FakeExt("telegram.ext")


def _install_sdk(monkeypatch, sdk: _FakeSdk):
    """Install a fake telegram SDK into sys.modules for the probe."""
    monkeypatch.setitem(sys.modules, "telegram", sdk)
    ext = getattr(sdk, "ext", None)
    if ext is not None:
        monkeypatch.setitem(sys.modules, "telegram.ext", ext)
    else:
        monkeypatch.delitem(sys.modules, "telegram.ext", raising=False)
    return sdk


class _BlockImports:
    """Meta-path finder that raises ImportError for a set of top-level names."""

    def __init__(self, names):
        self._names = names

    def find_spec(self, name, path=None, target=None):
        if name in self._names or any(
                name.startswith(f"{n}.") for n in self._names):
            raise ImportError(f"blocked import: {name}")
        return None


def _clear_sdk(monkeypatch):
    monkeypatch.delitem(sys.modules, "telegram", raising=False)
    monkeypatch.delitem(sys.modules, "telegram.ext", raising=False)


# ── import boundary ──────────────────────────────────────────────────────────

def test_telegram_module_importable_without_sdk(monkeypatch):
    _clear_sdk(monkeypatch)
    mod = importlib.reload(importlib.import_module(MODULE))
    assert hasattr(mod, "TelegramBot")
    assert hasattr(mod, "_require_telegram_sdk")


def test_telegram_module_has_no_top_level_sdk_import():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "cozmo" / "telegram_bot.py")
    tree = ast.parse(src.read_text("utf-8"))
    imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            imports.extend(n for n in names if n == "telegram" or n == "telegram.ext")
    assert imports == [], "telegram_bot.py must not import the SDK at module scope"


# ── SDK probe ────────────────────────────────────────────────────────────────

def test_probe_raises_install_hint_when_sdk_missing(monkeypatch):
    _clear_sdk(monkeypatch)
    blocker = _BlockImports({"telegram"})
    sys.meta_path.insert(0, blocker)
    try:
        mod = importlib.import_module(MODULE)   # live module — identity-safe after reload
        with pytest.raises(mod.TelegramIntegrationError, match="cozmo.*\\[telegram\\]"):
            mod._require_telegram_sdk()
    finally:
        sys.meta_path.remove(blocker)


def test_probe_detects_conflicting_shadowing_package(monkeypatch):
    _clear_sdk(monkeypatch)
    shadow = _FakeSdk(update=False, bot=False, ext=True)   # like `telegram` 0.0.1
    _install_sdk(monkeypatch, shadow)
    mod = importlib.import_module(MODULE)
    with pytest.raises(mod.TelegramIntegrationError, match="NOT python-telegram-bot"):
        mod._require_telegram_sdk()


def test_probe_accepts_real_sdk_surface(monkeypatch):
    _clear_sdk(monkeypatch)
    sdk = _FakeSdk()
    _install_sdk(monkeypatch, sdk)
    assert _require_telegram_sdk() is sdk


# ── adapter construction + behaviour ─────────────────────────────────────────

class _FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replied = None

    async def reply_text(self, text):
        self.replied = text
        return True


class _FakeUpdate:
    def __init__(self, text, chat_id):
        self.message = _FakeMessage(text)
        self._chat_id = chat_id
        self.effective_chat = types.SimpleNamespace(id=chat_id)


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk = _FakeSdk()
    return _install_sdk(monkeypatch, sdk)


def test_bot_builds_app_and_registers_handlers(fake_sdk):
    calls = []
    async def handler(chat_id, text):
        calls.append((chat_id, text))
        return "ok"
    bot = TelegramBot("TOKEN", handler, sdk=fake_sdk)
    assert len(bot.app.handlers) == 3   # start + help + message
    bot.run()
    assert bot.app.polled == []


def test_message_routes_text_and_chat_identity(fake_sdk):
    seen = []
    async def handler(chat_id, text):
        seen.append((chat_id, text))
        return "hello world"
    bot = TelegramBot("TOKEN", handler, sdk=fake_sdk)
    update = _FakeUpdate("do the thing", chat_id=42)
    asyncio.run(bot.handle_message(update, None))
    assert seen == [("42", "do the thing")]
    assert update.message.replied == "hello world"


def test_not_allowed_chat_denied_before_execution(fake_sdk):
    called = []
    async def handler(chat_id, text):
        called.append(chat_id)
        return "executed"
    bot = TelegramBot("TOKEN", handler, allowed_chat_ids=["1"], sdk=fake_sdk)
    update = _FakeUpdate("anything", chat_id=99)
    asyncio.run(bot.handle_message(update, None))
    assert called == []
    assert update.message.replied is not None
    assert "not allowed" in update.message.replied


def test_handler_error_surfaces_as_error_reply(fake_sdk):
    async def handler(chat_id, text):
        raise RuntimeError("exploded")
    bot = TelegramBot("TOKEN", handler, sdk=fake_sdk)
    update = _FakeUpdate("do it", chat_id=7)
    asyncio.run(bot.handle_message(update, None))
    assert update.message.replied is not None
    assert "Error: exploded" in update.message.replied


def test_empty_message_ignored(fake_sdk):
    called = []
    async def handler(chat_id, text):
        called.append(text)
        return "x"
    bot = TelegramBot("TOKEN", handler, sdk=fake_sdk)
    update = _FakeUpdate("   ", chat_id=7)
    asyncio.run(bot.handle_message(update, None))
    assert called == []
    assert update.message.replied is None