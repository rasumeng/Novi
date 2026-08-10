# Telegram real-device validation

Milestone 5 Phase E-3 unifies every normal execution surface through the
`ExecutionCoordinator`. The Telegram adapter (`cozmo/telegram_bot.py` +
`cozmo/services/telegram.py`) is coordinator-backed: a message arrives on the
async loop, execution is bridged to a worker thread, and the coordinator owns
the Task / Plan / Job / ExecutionHistory lifecycle. Conversation identity is
`telegram:<chat_id>`.

This document records the manual setup requirements for a real end-to-end
validation from a physical phone. **Never commit a bot token.**

## Prerequisites

- `python-telegram-bot` installed: `pip install "cozmo[telegram]"`
- Ollama running (the runtime's model backend)
- Cozmo installed editable (`pip install -e .`)

## 1. Create a bot with BotFather

1. Message [@BotFather](https://t.me/BotFather) in Telegram.
2. `/newbot`, pick a display name and a username ending in `bot`.
3. Copy the `HTTP API` token it returns (`123456:ABC...`). This is a secret.
4. (Optional) `/setprivacy` — decide whether the bot sees all group messages.
5. Add yourself to the allowed list: `/mybots` → select the bot → Bot Settings →
   allow groups / set private chat access as needed.

## 2. Configure the token locally

The configuration framework owns settings
(`cozmo/configuration/bootstrap.py` carries the `[telegram]` schema; the
snapshot is `~/.cozmo/config.toml`). Edit `~/.cozmo/config.toml`:

```toml
[telegram]
enabled = true
bot_token = "REPLACE_WITH_BOT_TOKEN"          # never commit this
allowed_chat_ids = ["<your_chat_id>"]          # leave empty to allow all
```

`telegram.allowed_chat_ids` is enforced by the adapter before any execution —
a denied chat produces no Task and no Job (`test_telegram_denied_chat_rejected_without_execution`).

Config CLI alternative: `cozmo config set telegram.bot_token <token>`.

## 3. Run Cozmo locally

```sh
cozmo telegram
```

The bot answers via a worker thread and the shared `ExecutionCoordinator`
path. All lifecycle (Task/Plan/Job/History) is visible through the WebUI
conversation views and the `TaskStore` at `~/.cozmo`.

## 4. Live validation checklist

1. **Phone → Cozmo → Coordinator → Runtime → Telegram** — send a message from a
   physical phone; confirm the answer streams back.
2. **Conversation persistence** — send again; subsequent turns reuse
   `telegram:<chat_id>` so continuation is discoverable.
3. **Continuation** — interrupt a run (kill the process mid-execution), restart,
   and send "continue". The coordinator reopens a NEW Job attempt (the interrupted
   Job is never resurrected) and resumes from the last checkpoint.
4. **Unauthorized chat** — from a chat id NOT in `allowed_chat_ids`, expect the
   "not allowed" reply and no Task/Job creation.
5. **Shutdown / restart** — restart the process; persisted Tasks/Jobs/schedules
   reload and remain consistent.

## 5. What is deliberately out of scope

- Discord adapter: not implemented (future work — same coordinator pattern).
- MCP: intentionally untouched until a dedicated architecture pass.

## Test coverage mapping

| Behavior | Test |
|---|---|
| SDK lazy-import boundary | `tests/test_telegram_boundary.py` |
| Allowed chat executes full chain | `tests/test_execution_surfaces.py::test_telegram_allowed_chat_executes_full_chain` |
| Denied chat rejected, no Task/Job | `...::test_telegram_denied_chat_rejected_without_execution` |
| Off-loop execution (no event-loop block) | `...::test_telegram_handler_runs_off_event_loop` |
| `telegram:<chat_id>` isolation | `...::test_conversation_identity_isolation` |
| Continuation via coordinator | `...::test_telegram_continuation_uses_coordinator_path` |