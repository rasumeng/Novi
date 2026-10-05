# Novi Backend Architecture

A map of the backend: what each piece owns, how a request flows through it, and
where the trust boundaries are. Written to be read end-to-end in one sitting.

If you're changing something and this document is wrong, fix the document in
the same change.

---

## 1. The 30-second version

```
  Browser / Tauri webview
          |  WebSocket  "chat"
          v
  webui_server.py          FastAPI app, routes, WS endpoint
          |
          v
  Session.start_run        resolves attachments -> RunRequest (immutable)
          |
          v
  RunService               owns the run lifecycle + persistence
          |
          v
  AgentLoop  <------------>  LangChainTurnProvider  <---->  ChatOllama
      |                              |
      |                              v
      |                       transcript_to_messages()
      |                       (text + images -> provider format)
      v
  AuthorizedToolDispatcher  ->  PermissionService  ->  your tool function
```

Everything above is `novi/`. The two files that matter most if you are learning
the system are `runtime/agent_loop.py` (the control flow) and
`services/run_composition.py` (what gets wired together).

## 2. Layers

### Entry points — `novi/`

| File | Role |
|---|---|
| `webui_server.py` | The real app. FastAPI routes, the `/ws/chat` endpoint, attachment upload, all HTTP APIs. Largest file in the repo. |
| `webui.py` | Builds the backend object graph and owns its lifecycle (`WebUIBackend`). |
| `desktop_backend.py` | Thin launcher used by the Tauri shell. |
| `cli.py` | Terminal entry point. Shares the same backend. |
| `paths.py` | Resolves the profile dir (`~/.novi`). **All** mutable state hangs off this. |

### Composition root — `novi/services/context.py`

`NoviContext` is the dependency container. Services are lazily constructed
properties; asking for one builds it.

Two flags gate startup work:

- `memory.enabled` (default **false**) — skips the boot-time knowledge re-index
- `brain.enabled` (default **false**) — the Brain is never constructed

This is the single most important file for understanding what the app does at
launch, because everything expensive is reachable from here.

### Run pipeline — the core

| File | Responsibility |
|---|---|
| `runtime/run_contracts.py` | The data shapes: `RunRequest`, `RunState`, `RunEvent`, `RunImage`, `ToolCall`, `ToolResult`. Immutable dataclasses. |
| `runtime/transcript.py` | The message model: `TranscriptMessage` holding `ContentBlock`s (text, image, tool call, tool result). |
| `services/run_service.py` | Run lifecycle: create, prepare the transcript, start, snapshot, cancel. |
| `services/run_store.py` | Persistence for runs and events (SQLite). Also where startup interruption recovery happens. |
| `runtime/agent_loop.py` | **The control flow.** The model/tool turn loop, limits, empty-turn handling, cancellation. |
| `runtime/provider_adapter.py` | The bridge to LangChain. Turns a transcript into provider messages; turns provider chunks into `ModelTurn`s. |
| `services/run_composition.py` | Wiring: which tools exist, the system prompt, permission policy. |
| `services/permission_service.py` | Decides whether a tool may run: allow / ask / deny. |
| `services/run_transport.py` | Maps `RunEvent` → the JSON the UI receives. |

### Model access — `novi/models/`, `novi/providers/`

- `models/service.py` (`ModelService`) — resolves *which* model, from
  `llm.primary_model`. Never chooses or falls back.
- `runtime/models/factory.py` (`ModelRuntime`) — turns an already-resolved
  identity into a runnable LangChain model. Execution only, no policy.
- `providers/base.py` — provider implementations (`ChatOllama`, `ChatOpenAI`).
  This is where the HTTP request timeout lives.

The split is deliberate: policy (`ModelService`) never constructs a client;
execution (`ModelRuntime`) never picks a model.

### Tools — `novi/tools/`

Modules self-register via the `@register_tool()` decorator into
`TOOL_REGISTRY`. `run_composition` filters that registry per run, which is how
tools get withheld (e.g. memory tools when `memory.enabled` is false).

Tool descriptions come from each function's **docstring**, first line only
(`_as_model_tool`). That line is prompt — write it for the model.

### Dormant in beta — `novi/brain/`, `novi/memory/`

Present, not running. See `docs/BETA_SCOPE.md`. `brain/` is the largest package
in the repo (36 modules) and owns the vector store; `memory/` owns long-term
recall and the knowledge index.

## 3. How one message flows

1. User sends. `PromptInput.tsx` posts the text plus attachment metadata
   (ids only — never paths) over the WebSocket.
2. `ws_chat` receives a `chat` frame. `Session.start_run` resolves attachment
   ids against the upload directory (`resolve_uploaded_images`) and builds an
   immutable `RunRequest`.
3. `RunService.prepare` builds the transcript: a `USER` message with a text
   block plus one `IMAGE` block per attachment.
4. `loop_factory` decides the tool set and system prompt, then constructs
   `AgentLoop(LangChainTurnProvider(model), dispatcher)`.
5. `AgentLoop.run` loops:
   - `provider.stream(transcript)` → `ModelTurn`s, emitted as UI events as they
     arrive, so the UI renders while the model is still generating
   - if the turn has tool calls: authorize → execute → append results
   - if it has text and no calls: the run completes naturally
6. `RunService` persists state and events; `run_transport` streams them to the UI.

## 4. Trust boundaries

These are the lines that matter for security review.

**Model output is untrusted.** Every assistant block is written with
`source="model", trust="untrusted"`. Model text is never treated as an
instruction, and JSON in prose is never promoted to a tool call
(`provider_adapter._native_calls` accepts only the provider's native
`tool_calls` field).

**Attachment paths are server-owned.** The client sends ids; the server resolves
them inside the upload directory only, re-derives the MIME type from the stored
filename, and rejects anything resolving outside (`resolve_uploaded_images`).

**Tools are permissioned.** `AuthorizedToolDispatcher` checks a
`ToolDescriptor` (effects + which arguments are paths/commands) against the
policy before execution. Denials come back as tool results the model can read,
not crashes.

**WebSocket origins are enforced.** CORS middleware does not protect WebSocket
upgrades, so the endpoint checks `Origin` itself.

## 5. Safety rails

`AgentLoopLimits` (`runtime/agent_loop.py`) bounds a run:

| Limit | Default |
|---|---|
| `max_model_turns` | 20 |
| `max_tool_calls` | 100 |
| `max_progress_messages` | 20 |

Plus: context-budget compaction, one retry for an empty model turn (and a
fallback that answers from gathered tool output instead of failing), and
per-provider HTTP timeouts so a stalled model surfaces an error instead of
hanging.

## 6. Where to add things

| To add... | Do this |
|---|---|
| A tool | New function in `novi/tools/` with a docstring. It self-registers. |
| A tool only for some runs | Filter it in `loop_factory` (`run_composition.py`) |
| An HTTP endpoint | `webui_server.py`, inside `create_app` |
| A UI capability | `webui/src/components/`, following `PromptInput.tsx` |
| Persistent per-run state | A new `ContentBlockType` + a case in `transcript_to_messages` |
| A model provider | `novi/providers/base.py` + register in `PROVIDER_REGISTRY` |

## 7. Reading order for the rework

1. `runtime/run_contracts.py` and `runtime/transcript.py` — the vocabulary.
   Almost everything else is expressed in these types.
2. `runtime/agent_loop.py` — the control flow, start to finish.
3. `services/run_composition.py` — what a run is made of.
4. `services/context.py` — what exists and what is gated.
5. `webui_server.py:ws_chat` — the request edge.

`docs/RISK_AUDIT.md` flags which of these to trust.
