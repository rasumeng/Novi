# Novi Main-Model Memory Implementation Plan

Use Superpowers executing-plans with the repository's solo-engineer adaptations.
No mandatory delegation, commits, deployment or repeated design approvals.

**Goal:** Periodically turn permitted conversations into coherent, editable,
evidence-backed memories using the user's selected main model, with visible
progress and foreground chat priority.

**Architecture:** Brain owns memory lifecycle; the primary provider supplies
bounded proposals and a separate consistency review. Existing SQLite journals
durable progress, Markdown owns editable notes, and LanceDB/graph remain derived
retrieval projections. A shared inference coordinator arbitrates chat and memory.

**Stack:** Existing Python, Pydantic, SQLite, model-provider infrastructure,
React/TypeScript, WebSocket events and current UI components. No new model,
inference framework, database or notification dependency is planned.

**Design:** `docs/architecture/main-model-memory-direction.md`, amended by the
user's September 13 request for visible memory activity and removal of a separate
model-usage budget. Read the unified lifecycle and Gemma test findings as well.
This is the active implementation assignment; the September 11 plan is historical.

## Execution status — September 14

Follow-up architecture validation: controlled tests now cover model/configuration
pinning across both passes and repair, subsequent model switching, unsupported
provider deferral, and the disabled worker state. The final consolidated backend
suite passes.
The old per-model qualification-report gate has now been removed. Automatic saving
defaults on, with a separate toggle in Memory settings. A controlled worker run
applied and reconstructed a reviewed note through temporary Markdown, SQLite,
LanceDB and graph stores. An owned Ollama server check confirmed transport handoff
for one installed selected model. See the implementation document for remaining
end-to-end and semantic-quality limits.

A subsequent [disposable real-model trial](../../architecture/main-model-memory-disposable-trial-2026-09-14.md)
ran six development cases through automatic worker/application: one applied, three
abstained and two deferred. Real embeddings ranked the saved work/home memories
correctly in two vector queries. This is architectural evidence and agent inspection,
not a human-reviewed quality qualification or full assistant recall test.

Beta scope is one desktop client. The existing Tauri single-instance plugin
enforces that desktop boundary; simultaneous browser/API clients are unsupported.
The normal memory adapter plus selected model answered the work/home questions
correctly after the real-embedding trial exposed and corrected an overly strict
`0.5` recall cutoff. WebUI shutdown context cleanup is covered and the desktop
crate passes `cargo check`; packaged-app quit during active generation remains a
manual end-to-end check.

The coordinator, durable worker, evidence/review pipeline, journaled application,
projection reconstruction and visible activity interface are implemented. See
[implementation and measurements](../../architecture/main-model-memory-implementation-2026-09-14.md)
for exact behavior and limitations. Tests are grouped in `test_memory_curation.py`
and `test_memory_worker.py` rather than every filename proposed below.

The production-adapter 30-case Gemma development run completed, with 3 approved
proposals, 13 deferrals, 7 abstentions and 7 rejections. Those historical model
outcomes are not the new admission gate. The detailed checkboxes below remain acceptance criteria: an unchecked
compound item may be partly implemented but is not fully verified. In particular,
actual Ollama cancellation/foreground measurements, human-reviewed held-out quality,
human-reviewed broad quality evaluation and native packaged-app acceptance remain
outstanding. Real Ollama handoff and the focused assistant recall path are complete.

Further [prompt and output-contract experiments](../../architecture/main-model-memory-prompt-development-2026-09-14.md)
are recorded separately. A simpler wire contract now delegates bookkeeping to
code; temperature remains zero for memory only. Automatic application is enabled
by default and remains controlled by the Memory settings toggle and deterministic
validation guards.

## User experience and policy

Show a small, non-modal popup when inference starts: **“Novi is updating its
memory.”** Keep a persistent **“Updating memory”** indicator until the job ends.
The popup does not steal focus, cover the composer or require acknowledgment.
The indicator offers **Pause** and **View activity**. Dismissing the popup hides
the notification, not the work or its persistent status.

Typing, scrolling, navigation and settings remain usable. Sending a message
immediately acknowledges the send, requests memory cancellation, and transfers
the main-model slot to chat after confirmed release. During handoff show
**“Pausing memory update to respond…”**. Never show the model as generating a
chat answer while it is still occupied by memory. Do not issue concurrent model
calls as a workaround. A provider that cannot release the slot promptly fails
automatic-background readiness; a popup is not an acceptable substitute for
responsiveness. Test actual Ollama cancellation before promising instant replies.

After a committed change, show **“Memory updated — View changes”** with actual
counts and links to changed notes. If no change was needed, record that in
activity without a success-count notification. Deferred/failed work says it was
not saved and remains pending. In evaluation mode use **“Reviewing memory
suggestions — no changes saved”**, never “Memory updated.”

Users can pause automatic updates and view when/why a note changed, its source
conversation and evidence. Current Markdown editing remains available. Expose
original data only in the appropriate detail view, not global notification text.
An undo operation must be revision-aware; use existing editing initially rather
than introduce an unsafe blanket revert button.

There is **no daily token quota, 1 GB curator cap or separate memory model**.
Use the selected model's normal supported device/context configuration. Keep
finite packets, an operation limit and bounded retries for correctness, plus
headroom admission, pressure cancellation and a configurable stalled-job timeout
for reliability. These are not a quota on user model usage. Do not hardcode the
earlier CPU-only two-thread experiment or its 120-second deadline as production
policy. Model recommendation status alone does not prove memory-edit accuracy.

Eligibility: five new turns or ten minutes of pending work; start after 60 seconds
without foreground model activity and only with sufficient resources. These are
initial configurable values. Conversation switching may wake the eligibility
check. Shutdown persists work and requests cancellation; it never starts or waits
for memory inference. No service continues after Novi exits just to finish memory.

## 1. Shared primary-model admission and cancellation

**Inspect/modify:** `novi/models/service.py`, `novi/runtime/models/factory.py`,
`novi/providers/base.py`, `novi/services/context.py`, `novi/runtime/runtime.py`,
`novi/runtime/knowledge_cycle.py`, `novi/services/simple_llm.py`.
**Add:** `novi/services/inference_coordinator.py`,
`tests/test_memory_inference_coordinator.py`.

- [ ] Write tests for foreground priority, simultaneous foreground arrival and
  idle admission, cancellation, timeout, configuration switches and no fallback.
- [ ] Introduce a context-owned coordinator shared across all runtime instances
  using the selected model. Audit chat, agent/tool-driven model calls, retrieval
  decisions and background runs; do not gate only one WebSocket session.
- [ ] Define `acquire_foreground(cancel_event)` and
  `try_acquire_memory(cancel_event)` leases. Pending foreground demand prevents
  new memory admission; only one active memory job exists application-wide.
- [ ] Resolve the primary provider/model once per job and preserve the resolved
  configuration through both passes and repair. A model switch invalidates or
  finishes the old lease safely before the next job uses the new selection.
- [ ] Extend the existing provider boundary with strict schema, output/context
  controls and cancellation. Do not silently discard unsupported controls.
  Confirm cancellation ends backend generation, not just the awaiting Python task.
- [ ] Reuse main-model residency. Release the memory lease without evicting chat;
  unload only a memory-owned load with no remaining consumer. Never kill the
  user's shared Ollama server. If provider cancellation is unreliable, leave
  automatic background memory disabled for that configuration and explain why.
- [ ] Test installed Gemma4:e2b through the production adapter in isolation with
  no vault writes; measure cancellation-to-foreground handoff. No new download.

**Deliverable:** Main-model inference can yield safely to chat. No memory writes.

## 2. Durable pending work outside foreground Brain locks

**Modify:** `novi/brain/brain.py`, `novi/brain/storage/conversation_store.py`,
`novi/brain/types.py`, `novi/runtime/runtime.py`, `novi/services/context.py`,
`novi/configuration/schema.py` and existing configuration registration as needed.
**Add:** `novi/brain/curation/jobs.py`, `tests/test_memory_jobs.py`.

- [ ] Add versioned tables in existing conversation SQLite for source segments,
  job state, lease, attempts, next retry time, resolved model and disposition.
  Use an additive migration preserving all existing turns and watermarks.
- [ ] Define stable source references from conversation/sequence/actor, project
  identity and source timestamp. Unknown values remain unknown. Preserve
  retention opt-outs before raw data enters this queue.
- [ ] `observe` persists and marks pending; snapshot work under a short lock,
  then release it before retrieval/model work. `recall` and `maintain` must not
  invoke main-model inference. Avoid running legacy heuristic extraction on the
  same segments as the new path; preserve explicit user memory commands.
- [ ] Implement oldest-eligible selection with bounded batches, fair retries and
  cancellation. Keep short pending conversations eligible by age after restart.
  Oversized turns split into tracked segments; never acknowledge unseen text.
- [ ] Persist transitions: queued, proposing, verifying, approved, applying,
  applied, deferred, rejected and abstained. Expired leases recover after restart.
- [ ] Wire lifecycle through context; inspect server and desktop close paths.
  Stop accepting jobs on close and exit without joining a blocked inference task.
- [ ] Test restart, short batches, duplicate wakeups, opting out, foreground
  recall, shutdown during loading, and failure without advancing the watermark.

**Deliverable:** Restart-safe queue and idle worker, still operating in shadow mode.
Do not submit fake user tasks to `JobLifecycle`; existing background jobs require
real TaskStore ownership. Use the Brain SQLite job records and existing event bus.

## 3. Evidence packet, proposal and consistency review

**Add:** `novi/brain/curation/contracts.py`, `packet.py`, `pipeline.py`,
`tests/test_memory_curation_pipeline.py`.
**Reuse:** current retrieval, vault identities, source records and development
fixtures. Evaluation-only scripts are reference material, not production imports.

- [ ] Build `EvidencePacket` containing exact actor-tagged source spans and
  relevant existing note sections with stable IDs/revision hashes. Include likely
  competing statements. Budget against the selected model's actual context;
  record truncation/deferred segments explicitly rather than silently losing them.
- [ ] Define `MemoryOperation`: ID, add/update/link/ignore, supplied target and
  revision, actor, subject, scope/project, temporal qualifiers, Markdown section,
  evidence references and relation if applicable. Start with at most four
  operations per packet. No model filesystem paths, status or truth confidence.
- [ ] Scope and qualifiers must be explicit in the structured claim and retained
  in user-visible prose. Add the observed “For Novi” regression. Unknown scope
  causes deferral where changing the scope would alter the claim.
- [ ] Prompt the model to compare new experience with retrieved memory and propose
  coherent changes, preserving conditions, uncertainty, history and meaningful
  wiki links. Label source content untrusted and deny tools/network/filesystem.
- [ ] Validate schema, identities, exact quotations, actors, targets, operation
  bounds and revision references. Reject path traversal and malformed link targets.
  Deterministic validation does not claim to prove semantic entailment.
- [ ] Send the proposal and original packet to a fresh reviewer context using
  the same pinned model. One repair maximum; validate and review repairs again.
  Record reject/abstain/defer separately. Repeated retries never create evidence.
- [ ] Add scripted tests for injection, attribution, scope, negation, temporal
  correction, duplication, invented relations, unsupported clauses and abstention.
  Keep external public-evidence learning and its provenance policy intact.

**Deliverable:** Auditable shadow proposals through the actual primary adapter.

## 4. Journaled Markdown application and projections

**Add:** `novi/brain/curation/apply.py`, `tests/test_memory_curation_apply.py`.
**Modify:** existing Brain, Markdown/vault, relationship store, knowledge layer
and reflection modules only where needed to preserve the unified lifecycle.

- [ ] Apply only validated/reviewed operations from durable approved job records.
  Use idempotency keys and per-operation checkpoints. Code assigns IDs and
  evidence-derived policy status; repetition does not verify curated claims.
- [ ] Extend existing notes by stable section identity rather than producing one
  file per sentence. Persist a versioned evidence/claim manifest alongside the
  readable Markdown; preserve unknown frontmatter and user-authored sections.
- [ ] Recheck file bytes/revisions before replacement; stage writes and record
  revisions. Defer stale edits. Document external-editor race limits and detect
  post-write conflicts; a Python lock is not filesystem compare-and-swap.
- [ ] Corrections preserve old validity/history and require matching scope and
  supported supersession. Links use existing wiki-link/typed-edge infrastructure
  and must survive reconstruction. Keep destructive deletion out of v1.
- [ ] Reconcile Markdown, LanceDB and graph after interrupted writes. Advance
  each source segment only after its durable terminal disposition. “Saved” is
  emitted only after all required persistence/projection work succeeds.
- [ ] Exercise crashes at store boundaries, replay, user edits between passes,
  rename/delete, retained metadata, scope isolation and graph reconstruction with
  real SQLite/LanceDB and deterministic embeddings.

**Deliverable:** Correct application to temporary evaluation vaults; the current
implementation also permits real automatic writes when the user setting is on and
the local provider, idle and headroom checks admit the job. The remaining unchecked
items are release-quality evidence, not a second hidden admission gate.

## 5. Visible status, pause and change inspection

**Inspect/modify:** `novi/webui_server.py`, existing TimelineService,
`novi/webui/src/hooks/useNoviChat.ts`, `novi/webui/src/hooks/useToast.tsx`,
`novi/webui/src/components/chat/GlobalActivityIndicator.tsx`,
`novi/webui/src/components/chat/PromptInput.tsx`, `novi/webui/src/App.tsx`.
**Add:** a focused `MemoryActivity` component/hook and corresponding frontend tests.
Use current styles and primitives; apply frontend-design only if a substantial
new interface is needed. Use webapp-testing for actual browser verification.

- [ ] Emit typed memory events with job ID, monotonic state version, conversation
  ID, stage, outcome and committed note IDs/counts. Keep source text out of global
  events. Add a current-status snapshot for reconnects and chat switching.
- [ ] Reuse the Brain-to-timeline bridge and WebSocket transport. Implement pause
  as an application command, scoped to the memory worker rather than chat stop.
- [ ] Implement the popup and persistent indicator specified above. Render true
  stages, not invented percentages. Make actions keyboard accessible and status
  announcements polite; avoid repeating the same popup on every reconnect.
- [ ] Keep memory busy state separate from chat `generating`/composer `disabled`.
  A new message triggers the foreground lease before model generation; preserve
  drafts, attachments and submitted messages during handoff/failure.
- [ ] Link committed activity to readable before/after changes and source evidence
  using existing memory/timeline views. Do not label proposals as saved notes.
- [ ] Test typing/sending while proposing, verifying and applying; multi-chat and
  multi-client events; stale events; reconnect; failure; pause/resume; and close.

**Deliverable:** End-to-end temporary-vault demo with a responsive composer and
truthful visible memory activity. Show a short handoff state if backend release
is not instantaneous; do not mask a blocked provider as a responsive model.

## 6. Qualification, rollout and retirement

- [x] Run the 30 development episodes through the production path with installed
  Gemma4:e2b. Keep tuning on development data. Pin prompt/schema/model/runtime
  settings, report abstention and misses, and retain raw auditable results.
- [ ] Qualify the final configuration using the separate human-reviewed held-out
  set from the prior acceptance design: 150 episodes, including at least 50 link
  and 30 correction decisions. No self-generated/self-graded accuracy claim.
  Retain the proposed 98% supported accepted claims/links, 85% worthwhile-memory
  recall and zero critical wrong-person/source/destructive-correction errors as
  release targets. These are quality targets, not guarantees.
- [ ] Measure cold and warm cycles and compare foreground latency with memory
  disabled across representative workloads. The owned-runtime smoke test measured
  cancellation-to-chat handoff and confirmed release before foreground inference.
  Do not reuse the tiny-model 1 GB gate. Retain the proposed <=5% foreground
  regression target and report handoff delay separately. If unmet, fix scheduling
  or keep automatic updates disabled for that configuration.
- [x] Run focused new suites plus existing brain/unified-brain, reflection,
  Markdown, graph, retention/external-evidence and frontend chat suites. Repair
  the interpreter/test environment first; never report missing tests as passing.
  Run a small real-embedding recall check to verify saved notes are useful.
- [x] Enable automatic application through the user toggle with local-provider,
  idle, headroom, validation and journal guards; unsupported providers defer without
  fallback. Model quality evaluation remains separate from architecture admission.
- [x] Update the active lifecycle documentation to implemented behavior. Preserve
  historical tests and follow the retirement inventory for tiny-only tooling and
  caches, without reverting unrelated edits or removing normal Ollama models.

## Execution recommendation

Use Codex to implement this plan in this repository: the current task already
contains the architecture inspection, observed failures and unrelated-edit
inventory, reducing handoff risk. This is a continuity recommendation, not a
benchmark ranking against OpenCode. The plan is self-contained enough to hand to
another implementation tool if preferred. Avoid simultaneous independent edits
to the same Brain/provider files. Review each milestone before proceeding.

The Beta implementation is complete for the supported single-client desktop scope.
Unchecked items above are future quality evidence or broader product acceptance,
not missing runtime architecture and not a hidden gate on the user's chosen model.
