"""M5.6 — Telegram Connector Conformance.

Telegram conforms to the M5.4/M5.5 connector architecture:

    Configuration Framework V2
        ↓
    Connector Registry (thin identity/status)
        ↓
    Telegram Connector Definition
        ↓
    TelegramLifecycle (lifecycle owner)
        ↓
    TelegramBot / runtime client (owned by the lifecycle)
        ↓
    Telegram tool adapter (bound via composition-root injection)

Key M5.6 shifts:
  * module-global ``_bot_instance`` in ``cozmo/tools/telegram.py`` is GONE —
    the tool resolves the ACTIVE lifecycle-owned runtime client through a
    bound accessor (``make_telegram_send`` + ``TelegramLifecycle.get_runtime_client``)
  * the CLI owns its own command-scoped bot (allowed exception) and no longer
    registers a module-global
  * the Connector Registry relays TelegramLifecycle's safe status; the registry
    never fabricates or stores credentials/lifecycle.

Covers the M5.6 acceptance tests:
   1. Telegram registers as connector type ``telegram``.
   2. Registry status is safe.
   3. Registry never exposes credentials.
   4. Lifecycle owns the active TelegramBot/runtime client.
   5. No module-global Telegram runtime client remains.
   6. Start creates exactly one runtime client.
   7. Repeated start does not create duplicates.
   8. Stop removes/invalidates the runtime client.
   9. Restart creates a fresh runtime client.
  10. Disabled state has no active runtime client.
  11. Tool receives/invokes the active runtime client through the injection seam.
  12. Tool fails safely when Telegram unavailable.
  13. Config unchanged by lifecycle operations.
  14. Connector registry remains runtime-only.
  15. Enable/disable transitions flow through the integrations apply hook.
  16. Shutdown is idempotent.
  17. Failed start leaves no stale client.
  18. Failed start exposes safe error status.
  19. CLI ``cozmo telegram`` remains functional.
  20. Generic ``/api/connectors/status`` reports Telegram safely.
  21. Existing ``/api/telegram/status`` stays compatible.
  22. No token/chat credentials appear in status responses.
  23. No Telegram runtime state persisted to disk.

Hermetic: fake bot factory / fake Telegram SDK — no network, token, or API.
"""

import asyncio
import pytest

from cozmo.connectors import ConnectorDefinition, ConnectorRegistry
from cozmo.configuration.bootstrap import build_registry as _build_registry
from cozmo.configuration.manager import Configuration
from cozmo.runtime.tool_registry import ToolRegistry
from cozmo.services.telegram import TelegramLifecycle
import cozmo.tools.telegram as tg_tools


# ═══════════════════════════════════════════════════════════════════════════
# Fakes
# ═══════════════════════════════════════════════════════════════════════════


class FakeBotApp:
    """Mimics PTB Application exposing ``.bot`` (the sendable SDK Bot)."""

    def __init__(self, bot):
        self.bot = bot
        bot.app = self


class FakeBot:
    instances: list["FakeBot"] = []

    def __init__(self, token="", allowed=None):
        self.token = token
        self.allowed = allowed or []
        self.start_calls = 0
        self.stop_calls = 0
        self.fail_start = False
        self.ran = False
        self.sent = []
        self.app = FakeBotApp(self)
        FakeBot.instances.append(self)

    def start(self, **kwargs):
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("telegram start failed")

    def stop(self):
        self.stop_calls += 1

    def run(self):
        self.ran = True

    async def send_message(self, chat_id=None, text=None, **kw):
        self.sent.append((chat_id, text))
        return "ok"


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


def _life(factory, ctx=object()):
    return TelegramLifecycle(ctx, bot_factory=factory)


# ═══════════════════════════════════════════════════════════════════════════
# 1/2/3. Connector registration + safe status
# ═══════════════════════════════════════════════════════════════════════════

def test_telegram_registers_as_connector(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})

    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition(
        "telegram", "telegram", label="Telegram",
        enabled=life.get_status()["enabled"], status_fn=life.get_status))

    assert reg.has("telegram")
    assert "telegram" in reg.types()
    conn = reg.require("telegram")
    assert conn.connector_id == "telegram"
    assert conn.connector_type == "telegram"
    assert conn.status()["running"] is True


def test_registry_status_is_safe(factory):
    life = _life(factory)
    reg = ConnectorRegistry()
    reg.register(ConnectorDefinition(
        "telegram", "telegram", status_fn=life.get_status))
    status = reg.require("telegram").status()
    # safe keys only: enabled/state/running/last_error
    assert set(status) == {"enabled", "state", "running", "last_error"}


def test_registry_never_exposes_credentials(tmp_path):
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()
    configuration.set("telegram.bot_token", "SUPER_SECRET_RAW_TOKEN", by="test")

    life = TelegramLifecycle(object())
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "telegram", "telegram", enabled=False, status_fn=life.get_status))

    text = repr(connectors.statuses()) + repr(connectors.require("telegram").describe())
    assert "SUPER_SECRET_RAW_TOKEN" not in text
    assert "bot_token" not in text
    assert "allowed_chat_ids" not in text


# ═══════════════════════════════════════════════════════════════════════════
# 4/5. Lifecycle owns the client; no module-global
# ═══════════════════════════════════════════════════════════════════════════

def test_lifecycle_owns_runtime_client(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    bot = life.get_runtime_client()
    assert bot is not None
    assert bot is factory.built[0]


def test_no_module_global_telegram_client():
    assert not hasattr(tg_tools, "_bot_instance")
    assert not hasattr(tg_tools, "set_bot_instance")
    assert tg_tools.__dict__.get("_active_bot") is None


# ═══════════════════════════════════════════════════════════════════════════
# 6/7/8/9/10. Start/stop/restart/disable ownership transitions
# ═══════════════════════════════════════════════════════════════════════════

def test_start_creates_exactly_one_client(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    assert len(factory.built) == 1
    assert life.get_runtime_client() is factory.built[0]


def test_repeated_start_no_duplicates(factory):
    life = _life(factory)
    cfg = {"telegram": {"enabled": True, "bot_token": "TOK"}}
    for _ in range(4):
        life.apply(cfg)
    assert len(factory.built) == 1
    assert life.get_runtime_client() is factory.built[0]


def test_stop_invalidates_client(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    bot = life.get_runtime_client()
    life.stop()
    assert life.get_runtime_client() is None
    assert bot.stop_calls == 1


def test_restart_creates_fresh_client(factory):
    life = _life(factory)
    cfg = {"telegram": {"enabled": True, "bot_token": "TOK"}}
    life.apply(cfg)
    first = life.get_runtime_client()
    life.stop()
    life.apply(cfg)
    second = life.get_runtime_client()
    assert second is not None
    assert first is not second
    assert first.stop_calls == 1
    assert len(factory.built) == 2


def test_disabled_has_no_active_client(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": False, "bot_token": "TOK"}})
    assert life.get_runtime_client() is None
    assert factory.built == []


# ═══════════════════════════════════════════════════════════════════════════
# 11/12. Tool injection seam + safe failure
# ═══════════════════════════════════════════════════════════════════════════

async def _run_bind(bind, chat_id, message):
    """Run the synchronous tool body inside a live event loop (it
    schedules ``asyncio.create_task``) and pump the loop until the
    record is observed."""
    result = bind(chat_id=chat_id, message=message)
    for _ in range(5):
        await asyncio.sleep(0)
    return result


def test_tool_receives_active_client_through_seam(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})

    bind = tg_tools.make_telegram_send(life.get_runtime_client)
    result = asyncio.run(_run_bind(bind, "123", "hello"))
    assert result == "Message sent to 123"
    assert life.get_runtime_client().sent == [("123", "hello")]


def test_tool_fails_safely_when_not_running():
    life = TelegramLifecycle(object(), bot_factory=FakeBotFactory())
    life.apply({"telegram": {"enabled": False}})
    bind = tg_tools.make_telegram_send(life.get_runtime_client)
    result = bind(chat_id="1", message="hi")
    assert "not running" in result


def test_tool_fails_safely_when_errored():
    bf = FakeBotFactory()
    life = TelegramLifecycle(object(), bot_factory=bf)
    life.apply({"telegram": {"enabled": True}})  # no token -> error
    bind = tg_tools.make_telegram_send(life.get_runtime_client)
    assert "not running" in bind(chat_id="1", message="hi")


def test_tool_unbound_default_fails_safely():
    # the registered TOOL_REGISTRY default has no injected accessor
    assert "not running" in tg_tools.telegram_send(chat_id="1", message="hi")


# ═══════════════════════════════════════════════════════════════════════════
# 13/14. Configuration authority + runtime-only registry
# ═══════════════════════════════════════════════════════════════════════════

def test_lifecycle_does_not_modify_config(tmp_path):
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()
    configuration.set("telegram.enabled", True, by="test")
    configuration.set("telegram.bot_token", "KEEPTOK", by="test")
    configuration.store.write(configuration.state.as_dict())
    before = (tmp_path / "cfg.toml").read_bytes()

    life = TelegramLifecycle(object(), bot_factory=FakeBotFactory())
    life.apply(configuration.snapshot())
    life.stop()
    life.apply({"telegram": {"enabled": False}})

    assert (tmp_path / "cfg.toml").read_bytes() == before


def test_registry_runtime_only(tmp_path):
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()
    configuration.set("telegram.enabled", True, by="test")
    configuration.store.write(configuration.state.as_dict())
    before = (tmp_path / "cfg.toml").read_bytes()

    life = TelegramLifecycle(object(), bot_factory=FakeBotFactory())
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "telegram", "telegram", status_fn=life.get_status))
    life.apply(configuration.snapshot())
    _ = connectors.statuses()
    life.stop()
    connectors.unregister("telegram")

    assert (tmp_path / "cfg.toml").read_bytes() == before
    # registry is in-memory only; fresh registry is empty
    assert ConnectorRegistry().list() == []


# ═══════════════════════════════════════════════════════════════════════════
# 15. Apply-hook enable/disable flow
# ═══════════════════════════════════════════════════════════════════════════

def test_integrations_apply_hook_drives_lifecycle(tmp_path, factory):
    reg = _build_registry()
    configuration = Configuration(reg, tmp_path / "cfg.toml")
    configuration.initialize()
    life = _life(factory)
    reg.require_owner("integrations", lambda p, v, prev: life.apply(configuration.snapshot()))

    configuration.set("telegram.bot_token", "TOK", by="test")
    configuration.set("telegram.enabled", True, by="test")
    assert life.get_runtime_client() is not None
    assert life.get_status()["running"] is True

    configuration.set("telegram.enabled", False, by="test")
    assert life.get_runtime_client() is None
    assert life.get_status()["running"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 16/17/18. Shutdown idempotency + failure safety
# ═══════════════════════════════════════════════════════════════════════════

def test_shutdown_idempotent(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    bot = life.get_runtime_client()
    life.stop()
    life.stop()
    assert bot.stop_calls == 1
    assert life.get_runtime_client() is None
    assert life.get_status()["state"] == "stopped"


def test_failed_start_leaves_no_stale_client():
    class FailFactory(FakeBotFactory):
        def __call__(self, ctx, token, *, allowed_chat_ids=()):
            bot = FakeBot(token=token)
            bot.fail_start = True
            return bot

    factory = FailFactory()
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    assert life.get_runtime_client() is None
    status = life.get_status()
    assert status["state"] == "error"
    assert status["last_error"] and "telegram start failed" in status["last_error"]


def test_failed_start_exposes_safe_error_status(factory):
    class FailFactory(FakeBotFactory):
        def __call__(self, ctx, token, *, allowed_chat_ids=()):
            bot = FakeBot(token=token)
            bot.fail_start = True
            return bot

    life = _life(FailFactory())
    life.apply({"telegram": {"enabled": True, "bot_token": "SECRETTOK"}})
    status = life.get_status()
    assert status["state"] == "error"
    assert "SECRETTOK" not in repr(status)


# ═══════════════════════════════════════════════════════════════════════════
# 19. CLI compatibility
# ═══════════════════════════════════════════════════════════════════════════

def test_cli_telegram_command_functional(monkeypatch):
    import cozmo.cli as cli_mod
    import cozmo.services.telegram as tg_svc

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
    assert seen["bot"].ran is True


def test_cli_telegram_no_token_prints_error(monkeypatch, capsys):
    import cozmo.cli as cli_mod

    class FakeCtx:
        config = {"telegram": {"enabled": True, "bot_token": "",
                               "allowed_chat_ids": []}}

    cli_mod.run_telegram(FakeCtx())
    out = capsys.readouterr().out
    assert "bot_token not set" in out


# ═══════════════════════════════════════════════════════════════════════════
# 20/21/22. WebUI status endpoints + secret safety
# ═══════════════════════════════════════════════════════════════════════════

def test_generic_connectors_status_reports_telegram_safely(monkeypatch):
    import cozmo.webui_server as ws
    from fastapi.testclient import TestClient

    life = TelegramLifecycle(object(), bot_factory=FakeBotFactory())
    life.apply({"telegram": {"enabled": True, "bot_token": "GHOST_TOKEN"}})
    connectors = ConnectorRegistry()
    connectors.register(ConnectorDefinition(
        "telegram", "telegram", label="Telegram",
        enabled=True, status_fn=life.get_status))
    ws._shared_backend = {"connectors": connectors}
    try:
        client = TestClient(ws.create_app(cfg={}))
        resp = client.get("/api/connectors/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"telegram"}
        assert body["telegram"]["state"] == "running"
        assert "GHOST_TOKEN" not in repr(body)
        assert "bot_token" not in repr(body)
        assert "chat_id" not in repr(body)
    finally:
        ws._shared_backend = None


def test_telegram_status_endpoint_compatible(monkeypatch):
    import cozmo.webui_server as ws
    from fastapi.testclient import TestClient

    life = TelegramLifecycle(object(), bot_factory=FakeBotFactory())
    life.apply({"telegram": {"enabled": False}})
    ws._shared_backend = {"telegram": life}
    try:
        client = TestClient(ws.create_app(cfg={}))
        resp = client.get("/api/telegram/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"enabled", "state", "running", "last_error"}
        assert body["running"] is False
        assert "bot_token" not in repr(body)
    finally:
        ws._shared_backend = None


def test_telegram_status_endpoint_fallback_when_no_backend(monkeypatch):
    import cozmo.webui_server as ws
    from fastapi.testclient import TestClient

    ws._shared_backend = None
    try:
        client = TestClient(ws.create_app(cfg={}))
        resp = client.get("/api/telegram/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"enabled": False, "state": "stopped",
                        "running": False, "last_error": None}
    finally:
        ws._shared_backend = None


def test_status_never_exposes_token_or_chats(factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOPSECRET",
                             "allowed_chat_ids": [1000001]}})
    text = repr(life.get_status()) + repr(life.get_runtime_client().allowed)
    assert "TOPSECRET" not in repr(life.get_status())
    # chat ids are only present via the tool, never via status
    assert "chat_id" not in repr(life.get_status())


# ═══════════════════════════════════════════════════════════════════════════
# 23. No runtime state persisted
# ═══════════════════════════════════════════════════════════════════════════

def test_no_telegram_runtime_state_persisted(tmp_path, factory):
    life = _life(factory)
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    before = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    life.apply({"telegram": {"enabled": True, "bot_token": "TOK"}})
    life.stop()
    after = sorted(p.name for p in tmp_path.rglob("*")) if tmp_path.exists() else []
    assert after == before
    assert life.get_runtime_client() is None
    assert life.get_status()["state"] == "stopped"