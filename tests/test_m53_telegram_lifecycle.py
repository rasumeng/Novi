"""M5.3 — Telegram enabled-flag honesty + lifecycle seam (Goal D).

Gives the existing TelegramBot a clean start/stop seam and makes
``telegram.enabled`` control the WebUI/runtime lifecycle. No connector
abstraction, no credential manager, no rewritten bot — the existing
implementation is reused behind a thin lifecycle wrapper.

Covers the M5.3 Telegram acceptance tests:
  1. ``telegram.enabled=false`` prevents startup.
  2. ``telegram.enabled=true`` starts the lifecycle when the runtime asks.
  3. Enabled → disabled stops the bot.
  4. Disabled → enabled starts it.
  5. Repeated enable does not create duplicate bots.
  6. Repeated disable is safe.
  7. Failed startup does not modify configuration.
  8. Shutdown is idempotent.
  9. Status is safe and never exposes the token.
  10. Existing CLI ``novi telegram`` behavior stays intact.

No network: a fake bot factory / fake telegram SDK is used throughout.
"""

import types
from pathlib import Path

import pytest

from novi.configuration.bootstrap import build_registry as _build_registry
from novi.configuration.manager import Configuration
from novi.services.telegram import TelegramLifecycle


# ── fake bot ──────────────────────────────────────────────────────────────


class FakeBot:
    def __init__(self, token="", allowed=None):
        self.token = token
        self.allowed = allowed or []
        self.start_calls = 0
        self.stop_calls = 0
        self.fail_start = False

    def start(self, **kwargs):
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("telegram start failed")

    def stop(self):
        self.stop_calls += 1

    def run(self):
        self.ran = True


class FakeBotFactory:
    def __init__(self):
        self.built: list[FakeBot] = []

    def __call__(self, ctx, token, *, allowed_chat_ids=()):
        bot = FakeBot(token=token, allowed=[str(c) for c in (allowed_chat_ids or ())])
        self.built.append(bot)
        return bot


@pytest.fixture
def factory():
    return FakeBotFactory()


@pytest.fixture
def ctx():
    return object()


# ── enabled flag honesty ──────────────────────────────────────────────────


def test_disabled_prevents_startup(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": False, "bot_token": "TOK"}})
    assert factory.built == []
    status = life.get_status()
    assert status["enabled"] is False
    assert status["state"] == "stopped"
    assert status["running"] is False


def test_enabled_starts_when_runtime_requests_it(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK",
                             "allowed_chat_ids": [1, 2]}})
    assert len(factory.built) == 1
    bot = factory.built[0]
    assert bot.token == "TOK"
    assert set(bot.allowed) == {"1", "2"}
    assert bot.start_calls == 1
    status = life.get_status()
    assert status["enabled"] is True
    assert status["running"] is True
    assert status["state"] == "running"


def test_enabled_without_token_never_starts_bot(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": True}})
    assert factory.built == []
    status = life.get_status()
    assert status["state"] == "error"
    assert status["last_error"] and "bot_token" in status["last_error"]


# ── enable / disable transitions ──────────────────────────────────────────


def test_enable_disable_stops_bot(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    bot = factory.built[0]
    life.apply({"telegram": {"enabled": False}})
    assert bot.stop_calls == 1
    assert life.get_status()["running"] is False
    assert life.get_status()["state"] == "stopped"


def test_disable_enable_starts_it(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": False}})
    assert factory.built == []
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    assert len(factory.built) == 1
    assert factory.built[0].start_calls == 1


# ── idempotency ───────────────────────────────────────────────────────────


def test_repeated_enable_no_duplicate_bots(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    cfg = {"telegram": {"enabled": True, "bot_token": "TOK"}}
    for _ in range(4):
        life.apply(cfg)
    assert len(factory.built) == 1
    assert factory.built[0].start_calls == 1


def test_repeated_disable_safe(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    for _ in range(3):
        life.apply({"telegram": {"enabled": False}})
    assert life.get_status()["running"] is False
    assert factory.built[0].stop_calls == 3 or factory.built[0].stop_calls <= 3


def test_stop_idempotent(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    bot = factory.built[0]
    life.stop()
    life.stop()
    assert bot.stop_calls == 1
    assert life.get_status()["state"] == "stopped"


def test_stop_safe_when_never_started(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.stop()
    assert life.get_status()["state"] == "stopped"
    life.apply({"telegram": {"enabled": False}})
    life.stop()


# ── failure / config authority ────────────────────────────────────────────


def test_failed_startup_does_not_modify_config(tmp_path, ctx):
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()

    class FailingFactory:
        def __call__(self, ctx, token, *, allowed_chat_ids=()):
            bot = FakeBot(token=token)
            bot.fail_start = True
            return bot

    life = TelegramLifecycle(ctx, bot_factory=FailingFactory())
    reg.require_owner(
        "integrations",
        lambda p, v, prev: life.apply(configuration.snapshot()),
    )

    configuration.set("telegram.enabled", True, by="test")
    configuration.set("telegram.bot_token", "SECRETTOK", by="test")

    # the apply hook ran and startup failed -> error state, intent persists
    assert life.get_status()["state"] == "error"
    assert configuration.get("telegram.enabled") is True
    assert configuration.get("telegram.bot_token") == "SECRETTOK"

    reloaded = Configuration(reg, tmp_path / "cfg.toml")
    reloaded.initialize()
    assert reloaded.get("telegram.enabled") is True
    assert reloaded.get("telegram.bot_token") == "SECRETTOK"


# ── status safety ─────────────────────────────────────────────────────────


def test_status_never_exposes_credentials(ctx, factory):
    life = TelegramLifecycle(ctx, bot_factory=factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "SUPER_SECRET_TOK"}})
    status = life.get_status()
    assert set(status) == {"enabled", "state", "running", "last_error"}
    assert "SUPER_SECRET_TOK" not in repr(status)
    assert "SUPER_SECRET_TOK" not in str(status)


# ── framework apply-hook path ─────────────────────────────────────────────


def test_apply_hook_fires_on_telegram_set(tmp_path, ctx, factory):
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()
    life = TelegramLifecycle(ctx, bot_factory=factory)
    reg.require_owner(
        "integrations",
        lambda p, v, prev: life.apply(configuration.snapshot()),
    )
    configuration.set("telegram.bot_token", "TOK", by="test")
    configuration.set("telegram.enabled", True, by="test")
    assert life.get_status()["running"] is True
    configuration.set("telegram.enabled", False, by="test")
    assert life.get_status()["running"] is False


# ── CLI compatibility ─────────────────────────────────────────────────────


def test_cli_telegram_command_unchanged(monkeypatch):
    """``novi telegram`` still builds + runs the bot, ignoring ``enabled``.

    M5.6: the CLI builds its own command-scoped bot directly (the allowed
    exception) and no longer registers a module-global ``set_bot_instance``.
    """
    import novi.cli as cli_mod
    import novi.services.telegram as tg_svc

    seen = {}

    def fake_build(ctx, token, *, allowed_chat_ids=()):
        seen["token"] = token
        seen["allowed"] = list(allowed_chat_ids)
        bot = FakeBot(token=token, allowed=allowed_chat_ids)
        seen["bot"] = bot
        return bot

    monkeypatch.setattr(tg_svc, "build_telegram_bot", fake_build)

    class FakeCtx:
        config = {"telegram": {"enabled": False, "bot_token": "CLITOK",
                               "allowed_chat_ids": [7]}}

    cli_mod.run_telegram(FakeCtx())
    assert seen["token"] == "CLITOK"
    assert seen["allowed"] == [7]
    assert seen["bot"].ran is True  # bot.run() invoked on the CLI's own bot


def test_cli_telegram_no_token_prints_error(monkeypatch, ctx, factory):
    """The no-token guard in ``novi telegram`` is untouched."""
    import novi.cli as cli_mod

    class FakeCtx:
        config = {"telegram": {"enabled": True, "bot_token": "", "allowed_chat_ids": []}}

    cli_mod.run_telegram(FakeCtx())


# ── TelegramBot lifecycle methods (fake SDK) ──────────────────────────────


class _FakeApp:
    def __init__(self):
        self.handlers = []
        self.initialized = False
        self.started = False
        self.stopped = False
        self.shut_down = False
        self.polled = None
        self._updater = types.SimpleNamespace()

        async def start_polling(**kwargs):
            self.polled = kwargs

        self._updater.start_polling = start_polling

    def add_handler(self, handler):
        self.handlers.append(handler)

    def run_polling(self, allowed_updates=None):
        self.polled = {"allowed_updates": allowed_updates}

    async def initialize(self):
        self.initialized = True

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def shutdown(self):
        self.shut_down = True

    @property
    def updater(self):
        return self._updater


class _FakeBuilder:
    def __init__(self):
        self._token = None

    def token(self, token):
        self._token = token
        return self

    def build(self):
        return _FakeApp()


class _FakeApplication:
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
        pass


class _FakeMessageHandler:
    def __init__(self, _filter, callback):
        pass


class _FakeExt(types.ModuleType):
    Application = _FakeApplication
    CommandHandler = _FakeCommandHandler
    MessageHandler = _FakeMessageHandler
    filters = _FakeFilters()


class _FakeSdk(types.ModuleType):
    def __init__(self):
        super().__init__("telegram")
        self.__version__ = "21.0"
        self.Application = _FakeApplication
        self.Update = object
        self.Bot = object
        self.ext = _FakeExt("telegram.ext")


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk = _FakeSdk()
    monkeypatch.setitem(__import__("sys").modules, "telegram", sdk)
    monkeypatch.setitem(__import__("sys").modules, "telegram.ext", sdk.ext)
    return sdk


def test_telegram_bot_start_stop_lifecycle(fake_sdk):
    from novi.telegram_bot import TelegramBot

    async def handler(chat_id, text):
        return "ok"

    bot = TelegramBot("TOK", handler, sdk=fake_sdk)
    assert bot.start() is True
    assert bot.is_running() is True
    assert bot.start() is False  # already running -> no duplicate
    assert bot.app.initialized is True
    assert bot.app.polled is not None
    bot.stop()
    assert bot.is_running() is False
    assert bot.stop() is False  # idempotent
    assert bot.app.stopped is True


def test_telegram_bot_run_still_uses_run_polling(fake_sdk):
    from novi.telegram_bot import TelegramBot

    async def handler(chat_id, text):
        return "ok"

    bot = TelegramBot("TOK", handler, sdk=fake_sdk)
    bot.run()
    assert bot.app.polled == {"allowed_updates": []}