# Novi agent state audit — September 16, 2026

**Verdict:** Novi has real tool-loop and long-term-memory infrastructure, but the AgentRun migration is incomplete. It is not yet a reliable implementation of “receive a request, work, communicate, continue working, and finish.” The broken UI and permissions are consequences of incompatible lifecycle contracts, not just presentation defects.

**Scope:** Audit and replacement plan only, as clarified by the user. Reviewed the current working tree, including its pre-existing uncommitted AgentRun and online-lookup changes. Application source was not changed. Added this report, the replacement plan, and a read-only reproducer. Builds regenerated ignored frontend output. No real model, personal memory vault, external tool, deployment, or migration was exercised by the reproducer.

Baseline HEAD: `a977ca60e510d5790f2f8e2768a3f4ce0cb1cec3`, plus the working-tree changes present on September 16. Findings apply to that combined tree, not the commit alone.

## Assessment by subsystem

| Subsystem | Assessment | Recommendation |
|---|---|---|
| Long-term memory storage and worker | Substantial implementation: durable source turns/jobs, evidence checks, revision checks, cancellation, journaled writes and reconciliation | Retain these mechanisms; replace their run ingestion boundary and test semantic quality separately |
| Active working context | Fragmented across local model messages, runtime history, ExecutionContext history, stable state and graph state | Replace with one authoritative run transcript and context builder |
| Agent lifecycle | A new wrapper around older execution paths; normal runtime consumes actions that the wrapper needs | Rebuild the controller and eliminate competing continuation/finalization owners |
| Tools | Useful registry, implementations and normalized results; dispatch and progress control violate protocol invariants | Retain individual tools; rebuild dispatch/control handling and enforcement |
| Permissions | Some good timeout/cancellation handling; multiple policy paths and real bypasses | Replace policy evaluation and pending-request ownership |
| Skills | Mostly prompt injection through regex activation, with divergent CRUD/loading rules | Replace with a validated catalog and explicit activation operation |
| Chat rendering | Designed around one anonymous streaming answer; partial AgentRun event support | Replace run state/reducer and rendering conditions |
| Tests | Many passing unit/component checks, but several mock away exactly the integration that is broken | Add real-controller contract tests with fake providers/tools, then browser and real-model acceptance |

“Agent” does not require displaying private model reasoning. A working/thinking indicator, optional provider reasoning display, useful progress messages, tool activity and a final result are different presentation concerns. Lifecycle correctness must not depend on reasoning tokens being available.

## Findings

Severity here expresses release priority. **P1** blocks a dependable agent cutover; **P2** needs correction in the replacement. “Reproduced” means the supplied fake-model/tool script executed production components; “source-traced” means the conclusion follows from inspected paths but was not exercised in a live application.

### A01 — P1: the outer controller loses the actual outcome and invents success

**Evidence:** `novi/runtime/runtime.py:1257` consumes `AgentAction` through its tuple shim, records `final/stop_reason`, and does not yield the action after the normal run tail (`1291`). `novi/services/execution.py:245` expects an action; its fallback at `257` assumes successful FINISH if the stream simply ends. The stop checks in the runtime can also return without yielding a terminal outcome. The wrapper does not derive cancellation from the stop flag in this path.

**Impact:** A run can be marked completed without an explicit completion; interrupted or error text can be presented as successful completion. The outer continuation loop cannot reliably respond to the inner loop's actual continuation request.

**Reproduced:** a runtime fixture emitted error text and exhausted without an action. Production `ExecutionCoordinator.execute` emitted `run.completed` and set `status=completed`.

**Replacement:** one controller owns outcomes; an exhausted provider/executor without a declared outcome is a protocol failure. Cancellation, failure, blocked work and successful completion have distinct recorded outcomes. Remove the tuple/dictionary behavior from `AgentAction`, not just the fallback condition.

### A02 — P1: progress drops sibling tool calls and leaves the provider transcript invalid

**Evidence:** `novi/runtime/react_attempt.py:479` appends the assistant tool-call message. At `491`, any `emit_progress` call sends the whole batch into a special branch. It executes only the first progress call, emits PROGRESS and continues without adding its `ToolMessage`. It never reaches the remaining tool calls at `513`.

**Impact:** A response containing “progress + read/write/search” drops the actual work. The next model input contains unanswered tool calls. Providers may reject that transcript; tolerant models may repeat or lose track of the task.

**Reproduced:** requested `[emit_progress, read_file]`; executed `[emit_progress]`; next model input contained zero tool results.

**Replacement:** process every declared call exactly once, with a result for every provider call ID. Progress is a control operation in the same batch processor, not an early exit. Denied or cancelled calls still receive explicit result records, without executing the action.

### A03 — P1: there is no authoritative working transcript or effective live-context compaction

**Evidence:** the inner loop owns `msgs = list(base_msgs)` (`react_attempt.py:267`), including tool results (`572`), but does not persist that list to the run. The next runtime call rebuilds history from `self.history` (`runtime.py:885`). `ExecutionContext.history` starts as `(user_text, assistant_text)` pairs, while `ExecutionCoordinator.execute` appends `("assistant", text)` (`execution.py:440`, `462`). These are incompatible representations.

`ContextManager.budget_for` (`context_manager.py:34`) estimates only six history entries truncated to 200 characters each, retrieved strings, a fixed system allowance and optional extra tool text. It does not measure the actual live `msgs` list, tool schemas or activated skill content. `compact_history` edits `ctx.history`, not the messages being sent in the inner loop. The coordinator emits `context.compacted` even if compaction raised (`execution.py:484–495`).

**Impact:** long-running work can forget tool results on continuation, repeat operations, underestimate context usage, or announce a compaction that did not happen. Persistent memory does not solve this active-context problem.

**Evidence class:** source-traced. No long real-model run was performed.

**Replacement:** one typed run transcript; build and budget the exact provider input; compact complete assistant/tool groups and retain outstanding calls, results, approval decisions and current task state. Compaction must change the next provider input and verify that it fits.

### A04 — P1: tool identity and message boundaries are lost before rendering

**Evidence:** `react_attempt.py:521` creates a call ID but the `tool.started`/`tool.completed` events at `522`/`565` do not carry it, category or diff. `Session._map_agent_event_to_ws` (`webui_server.py:616`) drops call/run/conversation identity and diff from tool payloads even when supplied. The frontend matches completion by tool name (`useNoviChat.ts:531`) and `pushStep` marks every running item complete when another item starts (`438`). Failures have no reliable structured tool status in that wire shape.

The normal content-plus-tools branch does not complete the content message before tool execution. Frontend `message_start` is a no-op (`460`), tokens append to the last streaming bubble, and `message_end` closes all streaming messages rather than the named one (`465`). `run.started` is not mapped to the WebSocket at all.

**Impact:** repeated calls to the same tool cannot be tracked reliably; pending tools appear done early; diffs disappear; progress and final text can merge into the wrong bubble.

**Reproduced:** text-before-tool produced two started messages but one unclosed message; both tool event IDs were null. Mapping a fully populated tool result removed call ID, run ID, conversation ID, category and diff.

**Replacement:** serialize one typed event envelope end to end, and reduce it by run/message/call identity. End each user-visible message independently of ending the run. Never infer success from the next event's arrival.

### A05 — P1: permissions can become invisible after multiple progress messages

**Evidence:** `Conversation.tsx:170` shows the pending area only beneath a user message at the final or penultimate array position. At `178`, the other permission location requires the last assistant message to be streaming. After `[user, completed progress, completed progress]`, neither condition holds. The backend can meanwhile wait for permission for 120 seconds (`webui_server.py:554`).

**Impact:** Novi waits for a decision the user cannot see; the tool eventually reports a timeout. The same conditions can hide active thinking/working state after multiple messages.

**Evidence class:** source-traced and reproduced as the exact render-predicate calculation, **not a browser test**.

**Replacement:** a run-owned pending-actions area independent of message streaming or array position. Show the pending tool, effect, scope, arguments/diff and available decisions until that specific request resolves.

### A06 — P1: explicit denials can be bypassed, including by fallback tools

**Evidence:** `ToolExecutor._check_permission` (`tool_executor.py:536`) returns for `bypass`, `accept-edits` and LOW-risk `auto` before consulting the resolver or MCP server denial. The network-only extra check at `355` does not fix file/other operations. Configured fallback dispatch at `485–491` calls `fb_info.fn(**args)` directly, bypassing that fallback tool's permission, MCP and normal execution gates. The runtime accepts these mappings from `runtime.tools.fallbacks` (`runtime.py:317`).

**Reproduced:** a denied `read_file` was allowed in auto mode; a denied `write_file` was allowed in accept-edits mode. A configured fake fallback explicitly marked deny was invoked and the outer call reported success. No file/system action was performed by the probe.

**Impact:** a deny setting is not a dependable execution boundary. A failure can trigger an operation that was never approved.

**Replacement:** explicit denial wins before mode defaults/grants; fail closed on policy evaluation errors. Every retry, fallback, MCP operation and nested search request must pass the same authorization boundary. Prefer model-visible failure and an explicit next tool request over hidden fallback execution.

**Behavior change to make explicit:** convenience modes will no longer override deny settings. Removing hidden fallbacks may expose errors previously reported as successful alternative operations.

### A07 — P1: foreground execution bypasses task preparation and explicit research routing

**Evidence:** `Session.start_run` (`webui_server.py:695`) creates an empty ExecutionContext and calls the new `execute`. That method never calls `_prepare`, which remains on the old `run_stream` path (`execution.py:578`, `976`) and owns plan/job/task/continuation preparation. `deep_research` is written to metadata (`webui_server.py:709`); no production read of those keys appears in the runtime/new execute path. The old coordinator explicitly passed `force_intent` through preparation.

CLI, Telegram and background entry points still call the old coordinator stream (`cli.py:104`, `services/telegram.py:57`, `services/background.py:71`). The new graph delegation also binds with an empty tool list and builds state with empty base messages (`execution.py:303`), despite `_runtime_graph_state` requiring already prepared context (`runtime.py:1664`).

**Impact:** entry points no longer share the same planning, job tracking, continuation or mode behavior. A research toggle reaching the server does not establish that it controls execution.

**Evidence class:** source-traced. Do not treat existing foreground UI or fake-coordinator tests as job-lifecycle validation.

**Replacement:** all entry points submit the same typed RunRequest to the same service. User-selected behavior is an explicit request field, not an unused metadata hint. Research/coding collaborators return results to the run controller rather than owning competing terminal behavior.

### A08 — P1: conversation state is connection-scoped, not conversation-scoped

**Evidence:** each WebSocket constructs one Session/runtime (`webui_server.py:2510`); chat changes `current_conv_id` at `2555` but does not load that conversation's transcript. `start_run` copies that runtime's history (`702`), and provider input uses it (`runtime.py:885`). A reset clears history, but selecting an existing chat is not a server-side transcript load. On disconnect the server stops the run (`webui_server.py:2834`), while the frontend reconnect logic deliberately retains its generation owner (`useNoviChat.ts:792`).

**Impact:** switching existing conversations can carry the previous conversation's short-term context; reopening/reconnecting loses server working history despite visible saved bubbles. A disconnected run can leave the UI waiting for events that the new Session cannot supply.

**Evidence class:** source-traced. This is a short-term conversation isolation defect, not a claim that all intentional long-term cross-conversation recall is wrong.

**Replacement:** backend transcript ownership by conversation ID; server run ownership independent of sockets; event cursor/snapshot reattachment. If the process ends and recovery cannot be safe, record interrupted work rather than silently resuming or implying completion.

### A09 — P2: cancellation, exceptions and approvals do not have one owner

**Evidence:** stopped FINISH is treated as failure because success is false (`execution.py:447`), while other stop paths lose the action entirely (A01). Unexpected exceptions can emit `run.failed` in both `execute` and Session (`execution.py:532`; `webui_server.py:769`). The Session catches `TypeError` and calls execute again (`740`), even when the TypeError arose inside execution after some work. Frontend error handling clears the owner without finishing streaming or clearing pending permission/plan state (`useNoviChat.ts:754`).

`answer_permission` accepts a missing request ID (`webui_server.py:578`). The ordinary UI sends an ID; nevertheless, the backend does not require it. Permission state is a session-global boolean/event; `ToolExecutor` inspects callback-owner private flags to distinguish timeout/cancellation (`tool_executor.py:584` onward).

**Reproduced:** approval without a request ID resolved a pending request. Duplicate terminals and TypeError re-execution are source-traced, not executed against real tools.

**Replacement:** one terminal transition guarded by the run state machine. No exception-based signature retry. Typed approval decisions keyed to immutable run/call/request/argument identity; late, duplicate or missing-ID answers cannot authorize another action.

### A10 — P2: skill creation, discovery and activation disagree

**Evidence:** description validation is commented out in `create_skill` (`webui_server.py:2129`), while listing/loading rejects a missing description (`2090` onward; `runtime.py:144`). The current test demonstrates this discrepancy. Runtime skills are loaded once (`runtime.py:263`), so API changes do not refresh a running session's catalog. The accepted skill-name regex includes underscores but `_SKILL_RE` at `runtime.py:92` does not. CRUD lists folder identity while runtime can activate under frontmatter identity (`runtime.py:139`). Runtime recursively reads support files and eagerly injects them on activation (`runtime.py:148`, `471`).

Model-triggered activation scans generated public answer text for `@skill`, then continues (`react_attempt.py:437`). The normal stream may already have displayed that control text; the continuation branch also skips message completion. Names `load_skill`/`list_skills` exist in risk labels, but the Python tool search found no implementations under those names.

**Impact:** a “created” skill may be unavailable, newly installed skills can remain stale, control syntax leaks into replies, and large supporting content competes with working context.

**Replacement:** one parser/catalog used by create/import/list/load; one canonical name; explicit `activate_skill(name)` control operation with a provider result; lazy support-file reads; a per-run content snapshot/version. Skills grant no tool permissions.

### A11 — P2: memory is better engineered, but its agent integration and semantic quality remain incomplete

**Strengths verified by inspection and focused tests:** `brain/curation/jobs.py` journals durable work; `pipeline.py:83` separates proposal and review contexts; `contracts.py:76` validates evidence and revisions; `apply.py:17` journals exact bytes and protects managed sections; `worker.py:117` schedules/cancels work and rechecks before saving; `services/inference_coordinator.py:53` waits for the owner to release memory inference. Production composition uses `extractor=None` and starts the curation worker (`novi/services/context.py:214–230`), so the old heuristic extractor is not processing those same production batches. `novi/providers/memory.py:19` restricts automatic inference to local Ollama with the pinned selected model; it does not silently substitute a cloud/tiny model. Those mechanisms are worth keeping.

**Integration gaps:** `_remember` still captures only a `(user, final)` exchange (`runtime.py:1762`), without the run ID, progress messages, structured tool results or run outcome. It is called at the runtime-attempt boundary, including the generic error path (`1297`). `Turn` supports tool outputs but that call does not supply them. A continued attempt can therefore be represented as another exchange instead of one ongoing run.

**Semantic limits:** `MemoryJobs.claim` splits source text into 1,200-character segments and packets of four segments (`jobs.py:126` onward). Exact span preservation does not guarantee that a qualifier or later correction appears in the same model packet. `validate` checks declared scope, not whether all necessary scope was declared. Existing September 14 development reports document both unsafe approvals and overly conservative deferrals in tested variants; they are historical evidence, not a fresh quality score for the current model.

Project identity exists in a packet but is not an explicit field on each proposed Operation or on the new-note metadata constructed in `apply_verified`. Before relying on project isolation, test end-to-end storage and recall across two projects rather than assuming prompt prose preserves that boundary.

**Replacement:** memory consumes finalized, identified run evidence, with progress/tool content retaining its actor and trust level. Preserve no-retention behavior. Keep coherent context around claims; store explicit scope; establish held-out precision, unsupported-save and useful-recall measures using disposable stores. Same-model review is not independent truth verification.

### A12 — P2: current tests can validate the replacement mocks instead of production behavior

**Evidence:** `tests/helpers/fake_agentrun.py:103` globally replaces ContextManager methods and leaves restoration to the caller; it stubs both execution methods, manufactures the desired message lifecycle, and even assigns different random call IDs to a fake tool start and finish. `AgentEvent.__eq__` ignores run/message/call identity and several other fields (`agent_events.py:43`). `test_emit_progress_does_not_reset_run` manually increments a dataclass counter rather than executing progress. `test_webui_agentrun_bridge.py:46` fails on an undefined `_make_run` before testing its bridge.

**Impact:** passing tests do not establish valid provider transcripts, real continuation, correlated tools, actual UI approval visibility or exactly-one-terminal across the Session/controller boundary. Global test mutation also risks order dependence.

**Replacement:** fake the provider and external side effects, not the run controller, event projection, policy engine or reducer. Assert exact call/message/run identities and full assistant-call/tool-result pairing. Scope monkeypatches through fixtures and replace parity tests with behavior tests.

## Verification performed

| Check | Result | What it establishes |
|---|---|---|
| First focused backend group, 8 files | 70 passed, 2 failed | Existing lifecycle/streaming/bridge/progress, permissions, skills and basic memory coverage |
| Second focused backend group, 13 files | 98 passed | Curation, draft, worker, inference handoff, provider, recall merging, permission timeout and additional agent checks |
| Existing frontend suite | 125 passed, 19 files | Current component/hook expectations; React `act(...)` warnings were emitted |
| TypeScript + Vite production build | Passed | Frontend compiles and bundles; build reported plugin timing advice |
| `scripts/audit_agent_state.py` | Ran successfully | Confirms the broken behaviors above using production functions and fake providers/tools |

Backend total: **168 passed, 2 failed** across the two non-overlapping selected groups. Failures: undefined `_make_run` in the legacy-token bridge test, and skill creation returning 200 instead of expected 400 for a missing description. Warnings included a test's re-raised worker-thread exception and LanceDB deprecations. No failures were fixed during this audit.

The initial sandboxed Python invocation could not access the interpreter/packages; the existing environment ran successfully with approved sandbox escalation. No interpreter/dependency replacement was needed.

Not performed: full repository backend suite, live browser end-to-end session, packaged desktop acceptance, current-model quality evaluation, real tool mutation, or long-running recovery after a process crash. Render predicates are not a substitute for the browser acceptance work specified in the plan.

Reproduce the two backend groups using the file lists in the companion plan's verification section. Run the audit probe with `venv\Scripts\python.exe scripts/audit_agent_state.py`; its JSON describes defects, so exit code 0 means the probe ran, **not** that the agent is correct.

## Rebuild boundary

Replace orchestration, run state, working transcript, tool/control dispatch, permission flow, skill activation, wire protocol and UI run rendering as one coherent cutover. Delete tuple adapters, dual event names, inferred completion, session-private approval introspection and compatibility execution fallbacks. Migrate CLI, Telegram, background and scheduled work to the same run service in that cutover.

Retain useful implementations where they satisfy the new contracts: model/provider adapters, tool functions, retrieval/search services, memory evidence validation and journaled storage, and reusable UI primitives. Keeping a correct storage algorithm is not a compatibility shim. Do not discard user chats, memory notes or unrelated source changes merely to remove old runtime code.

The September 14 design's requirement that all old execution owners stay conflicts with this rebuild direction. The companion September 16 plan replaces that premise; it does not patch that design with another translation layer.
