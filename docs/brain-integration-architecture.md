# Brain Integration & Assistant Identity — Architecture

Milestone 4 foundation. Turns Novi's invisible intelligence layer into a
persistent-assistant surface (a **Timeline** and **What Novi knows**) without
rewriting the Brain, its retrieval, or its runtime.

## 1. Principle

The Brain already *produces* the events and *has* the knowledge we want to
surface. The problem was never the cognition — it was that the Brain's own
`EventBus` has no consumers, and its knowledge shape was only reachable as an
LLM tool. Milestone 4 is a **bridge + a small store + two user-shaped
endpoints + thin frontend routes**, not a Brain rewrite.

Hard rules:

- No Brain-internal changes. No moving EventBus ownership.
- No modification of retrieval or runtime behavior.
- Never expose vector databases, embeddings, scores, distances, ids, or
  internal cognitive objects to the user.
- Prefer additive event surfaces.

## 2. Current Brain event architecture

The Brain is wired in `novi/services/context.py` with a **private** EventBus
(`_brain_event_bus`) passed as `event_bus=` to `Brain(...)`. After a durable
write, the Brain emits exactly three domain events (`novi/brain/events.py`):

| Event | Emitted | Payload (Brain-canonical) |
|---|---|---|
| `conversation.observed` | `brain.py:650` | conversation_id, user, assistant, timestamp |
| `knowledge.extracted` | `brain.py:635` | knowledge_ids, conversation_id, scenario_id, summary |
| `knowledge.promoted` | `brain.py:531` | item_ids, promotions, corroborated, superseded, conflicts |

**Current gap:** nothing ever subscribes to `_brain_event_bus`. The runtime /
session use a *different*, per-session `EventBus` (`webui_server.py:306`),
which only bridges `tool_called` and `tool_result` to the WebSocket. So all
three brain events are emitted into a void.

Constraints honored by Milestone 4:

- Do **not** add new Brain events.
- Do **not** move where the Brain keeps its bus.
- Everything below is a separate read-only bridge + consumer.

## 3. EventBus bridge design

A **read-only bridge** lives in the WebUI server layer, not in the Brain:

```
Brain (owns _brain_event_bus)                 WebUI HTTP layer
        │ emit(conversation.observed,                │
        │       knowledge.extracted,               │  TimelineStore (JSONL)
        │       knowledge.promoted)               │
        ▼                                        ▼
  NoviContext.brain_event_bus  (read-only accessor)
        │
        ▼
  TimelineService (novi/timeline)
        ├─ shape → TimelineEntry              ──▶ TimelineStore.append
        └─ on_entry callback                  ──▶ _broadcast_sync({"type":"assistant_event", ...})
```

- `NoviContext.brain_event_bus` is a **read-only accessor**. It lazily ensures
  the brain is built, then returns the existing bus reference. It never takes
  ownership, never mutates Brain state.
- `TimelineService` subscribes with the fabric existing `EventBus.on`/`on_any`
  API — it is a consumer, like any logging or WS forwarder already is.
- Because the bus dispatch runs in whatever thread emitted the event (runtime
  worker threads), the bridge must be thread-safe. It is: it delegates to
  `_broadcast_sync` (thread-safe, queues onto each connection's event loop) and
  to `TimelineStore` (a file write under an internal lock).

## 4. Timeline vs Notification distinction

| | Notifications (Milestone 3) | Timeline (Milestone 4) |
|---|---|---|
| Purpose | Channel + urgency ping | Durable chronological record |
| Lifetime | Session in-memory, capped 30 | Persisted JSONL, survives reload |
| Trigger | Job/response *transitions* (terminal) | Brain events (every signal) |
| UI | Bell + native OS | Day-grouped feed |
| Shape | title/message/severity/action | kind/title/detail/timestamp |
| Overlap | job completed/failed | conversation logged, knowledge |

The two are complementary and intentionally distinct. Milestone 3 is **not**
touched. The Timeline is additive and, where it intersects (a conversation
finishes), it simply records the lasting fact while the notification center
handles the urgent ping. No behavior is duplicated into a second pipeline.

## 4. Timeline entry schema (user-facing)

No internal ids, scores, embeddings, distances, or storage paths. `id` is a
per-row instance id for React keys / dedupe — never a Brain/storage id.

```json
{
  "id": "uuid",
  "kind": "conversation.observed" | "knowledge.extracted" | "knowledge.promoted",
  "title": "Conversation logged",
  "detail": "You asked about local-first AI architectures",
  "timestamp": "2026-08-05T10:42:00Z"
}
```

Rules: `title`/`detail` are human-readable; `detail` derived from the safest
user-facing field available (conversation user text, extraction summary, or a
count sentence for promotions). Counts (not scores) are allowed on
`knowledge.promoted`.

## 5. Knowledge Overview philosophy

Replace the "vector memories" mental model with **what Novi knows**.
`brain.inspect_memory()` already returns a per-category projection
(`projection.py` `_CATEGORY_TAGS`): preference, goal, skill, project, event,
relationship, identity.

The endpoint exposes only:

- `category` — the group key
- `label` — a friendly display name (Preferences, Goals, ...)
- `entries[].content` — human-readable statement
- `entries[].evidence` — verified / corroborated / candidate

**Forbidden** in the response: memory ids, embeddings, distances, vector
scores, storage paths, enumeration of knowledge ids, scenario ids.

This is a *new, user-shaped* endpoint. It must **not** reuse `/api/memory/list`
(which leaks ids + distance and is a diagnostics surface). Raw memory browsing
stays a maintenance/troubleshooting feature.

## 6. Frontend/backend responsibilities

**Backend owns:**
- The brain bridge and the 3 brain events → `assistant_event` WS feed.
- `TimelineStore` persistence (bounded JSONL under `~/.novi/timeline`).
- `GET /api/timeline` (stored feed history).
- `GET /api/knowledge/overview` (user-shaped knowledge projection).
- Privacy: never ship internal identifiers/scores/vectors to these surfaces.

**Frontend owns:**
- Rendering. Extends the `ServerEvent` union and adds API service methods.
- `useNoviChat` keeps a `timeline` slice (live feed + mount-time REST fetch).
- Timeline feed rendering (`TimelinePage`) and the "What Novi knows" view
  (`KnowledgeOverview`) consume those two sources only — see Phases B/C.

Frontend never calls backend memory internals directly — it consumes the two
user-facing endpoints / the `assistant_event` messages only.

## 7. Data privacy rules

1. **No internal ids.** Never deliver `knowledge_ids`, `item_ids`,
   `scenario_id`, storage row ids to the UI. Per-row UUIDs for the timeline
   are OK (they are timeline addresses, not brain ids).
2. **No vector internals.** No embeddings, distances, similarity/vector
   scores, or matching percentages on any user-facing endpoint.
3. **No storage paths.** Never expose `~/.novi/memory`, `~/.../brain`, index
   dirs. Raw "path" debug endpoint remains a separate troubleshooting surface.
4. **User-readable only.** `category`, `content`, `evidence` and count-based
   prose only.
5. **No fake data.** Empty sets render as empty; never synthesize attributes
   that were not persisted.

## 8. Milestone 4 phases

- **Phase A:** backend only — brain bridge, timeline store + service, the two
  endpoints, `assistant_event` WS, frontend event/API/state plumbing.
  Tests: event-bridge, persistence, overview.
- **Phase B:** Timeline page (day-grouped feed) + nav. The page merges live
  events with REST history (per-row `id` dedupe), groups by day, and rows with
  a `conversation_id` open the referenced conversation via the existing
  notification-open pattern.
- **Phase C:** "What Novi knows" page; demoted raw Memory Browser. The memory
  settings surface now defaults to a product-facing Knowledge Overview
  (evidence-labelled categories); vector internals remain only under a
  Developer tab, and the Landing Page previews real recent activity + learned
  knowledge.
- **Phase D (guarded, optional):** default-off idle reflection so
  `knowledge.promoted` fires in production.
- **Phase E:** tests, docs, hardening. Frontend coverage added for the timeline
  helpers (dedupe/merge, day grouping), TimelinePage interaction, and
  KnowledgeOverview rendering.

### Status (this commit)

Phase A, B, C, and E are complete. Landing page, Timeline page, and Knowledge
Overview render real persisted data only. Phase D remains default-off.
One known gap: `conversation_id` is a best-effort open target — a timeline
entry sourced from a Brain conversation created outside this app may not
resolve to a chat tab here.