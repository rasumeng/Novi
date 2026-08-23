# AI Runtime Architecture (Phase 7 Stage 3 — final)

Durable spec for Cozmo's model runtime and LangGraph workflow composition as
of Phase 7 Stage 3E. This document describes the current system, not history.
For point-in-time reviews see `docs/audits/STAGE0-AI-RUNTIME-AUDIT.md` and
`docs/audits/STAGE3A-AI-RUNTIME-AUDIT.md`.

---

## 1. Immutable contracts

These are load-bearing invariants. Guards for most of them live in
`tests/test_architecture.py` and the graph/model-runtime test suites.

| Contract | Enforcement |
|---|---|
| `recommend()` is advisory only — never executes | pure function (`cozmo.configuration.resolver`) |
| Selected model is the **verbatim** `llm.workloads.<workload>.model` | `ModelSelector.resolve` |
| `apply_selection()` is the sole selection writer | `cozmo.configuration.resolver.apply_selection` |
| `""` means genuinely unset → raises `ModelUnavailableError` at execution | `ModelSelector.resolve` / `ModelService` |
| No automatic selection, no fallback/substitution, no execution-time model replacement | runtime + graph AST guards |
| No LangGraph checkpointer / persistence | graph modules + AST guards |
| Job/Checkpoint/ContinuationService remain the resume authority | `cozmo.services.job_lifecycle`, `cozmo.services.continuation` |
| Tool permissions/risk stay inside ToolExecutor | `cozmo.runtime.tool_executor` |

---

## 2. Model resolution & construction chain (single seam)

```
ModelSelector.resolve(workload)            cozmo/runtime/model_selector.py
        │   returns llm.workloads.<workload>.model VERBATIM
        │   raises ModelUnavailableError when unset/empty
        ▼
ModelService                               cozmo/models/service.py
        │   .resolve(workload) -> (provider, model)
        │   .bind_model(name, tools)      -> ModelRuntime.bind_tools
        │   .client_for_model(name)       -> ModelRuntime.create_chat_model
        ▼
ModelRuntime                               cozmo/runtime/models/factory.py
        │   ResolvedModel(provider, model, config)  [frozen]
        │   create_chat_model(resolved, temperature)
        │   accepts already-resolved identity ONLY — never recommends,
        │   selects, substitutes, falls back, or parses model names
        ▼
create_provider(name)                      cozmo/providers/base.py
        │   PROVIDER_REGISTRY {ollama, openai}; parse_model_spec
        ▼
LangChain ChatModel                        ChatOllama / ChatOpenAI
        constructed ONLY here (providers/base.py)
```

Rules:

* `ChatOllama` / `ChatOpenAI` are constructed in exactly one place:
  `cozmo/providers/base.py`. Any new provider joins via
  `PROVIDER_REGISTRY`, not a new construction site.
* The runtime binds through exactly one method:
  `CozmoRuntime._bind_runnable` (`cozmo/runtime/runtime.py`). Recovery and
  escalation re-binds call it too — no inline reconstruction.
* `model_capabilities()` is descriptive metadata only; it never routes or
  selects.

## 3. Selection vs recommendation ownership

* **Selection (write):** `apply_selection()`
  (`cozmo/configuration/resolver.py`) is the only code path that persists a
  workload model choice. The WebUI settings API and config CLI both funnel
  through it.
* **Recommendation (advisory):** `recommend()` in the same module is a pure
  function of (hardware profile, installed models, catalog). It never writes,
  never executes, and its output never reaches the runtime unless a human
  applies it through `apply_selection()`.
* The catalog is descriptive evidence — it does not define the model universe.

## 4. Execution topology

All six entry surfaces (WebUI WebSocket, background jobs, CLI, Telegram,
scheduler, task queue) converge on one seam:

```
<entry surface>
   └─> ExecutionCoordinator.run_stream          cozmo/services/execution.py
         └─> Orchestrator.plan                  cozmo/orchestrator/
               │   IntentDetector / ComplexityEstimator / EvidenceDetector /
               │   RetrievalPolicy  (analysis computed ONCE, upstream)
               └─> CozmoRuntime.run_stream      cozmo/runtime/runtime.py
                     ├─ pre-loop retrieval      RetrievalExecutor.execute
                     ├─ model resolve           ModelSelector → … → ChatModel
                     ├─ runnable bind           _bind_runnable
                     └─ branch on intent:
                          ├─ research + graph wired → ResearchGraph   (§5)
                          ├─ coding   + graph wired → CodingGraph     (§6)
                          └─ otherwise              → generic ReAct   (§7)
```

Graph wiring is injection-based and opt-out-able: `CozmoRuntime(research_graph=
None, coding_graph=None)` restores the legacy inline paths unchanged. The two
production composition roots both wire them (see §8).

## 5. ResearchGraph execution path

Module: `cozmo/graphs/research_graph.py` (Phase 8B upgrade). State:
`ResearchState` (`cozmo/graphs/state.py`). Deterministic helpers:
`cozmo/graphs/research_intel.py`.

```
START → understand → plan → decompose → search → evaluate ─┬─ sufficient, no gaps → synthesize
                                                           ├─ blocked (no search /
                                                           │  budget exhausted) → synthesize
                                                           └─ gaps / pending sub-questions → search
                                                                             (bounded by max_search_attempts)
synthesize → validate ─┬─ insufficient coverage → search (bounded)
                       └─ otherwise → END
```

Phase 8B capabilities layered on the Phase 7 skeleton:

* **decompose** — LLM-assisted, bounded sub-questions via a deterministic
  JSON contract (`model.invoke` → parse → ≤1 retry → fallback to the original
  question). Trivial questions skip the model call entirely.
* **evaluate** — gaps are key terms NOT covered by evidence; uncovered terms
  deterministically derive the next refined query. Covered/no-gap cases keep
  the original query, so the coordinator's duplicate gate behaves exactly as
  before refinement existed.
* **accumulate** — `evidence_bundles` accumulate across attempts with strict
  URL-identity dedup and hard bounds (≤4 bundles); state never overwrites
  evidence between attempts.
* **conflicts** — reuses the existing `EvidenceProcessor`/`ConflictDetector`
  per bundle; output is descriptive and surfaced to the synthesis prompt.
* **citation manifest** — built deterministically from ACTUAL retrieved
  results (`[S1…]` ids); synthesis is prompted to cite it; validation
  resolves citations against exactly this manifest (invented ids are
  recorded, never trusted).
* **grounding budget** — accumulated evidence is merged newest-first within a
  character budget derived from the bound model's DESCRIPTIVE context length
  (never influences selection; unknown ⇒ conservative default).

Per-run collaborators injected by the runtime (`_research_graph_state`):

| State key | Meaning |
|---|---|
| `model` | the ALREADY-bound LangChain runnable for this run |
| `search` | callable wrapping `RetrievalExecutor.execute_search` |
| `coordinator` | the run's `RetrievalCoordinator` — single budget authority |

Loop-prevention invariants (all regression-tested):

* Pre-loop SUFFICIENT evidence is reused, never re-searched (the graph never
  double-pays the web budget).
* A blocked search node forces `synthesize`; validation can then only END.
* `run()` force-writes `max_search_attempts` into state — stale caller values
  cannot unbound the loop.
* Refinement cannot launder near-duplicates past the coordinator: when no
  genuinely uncovered term exists, the query is left untouched and the
  duplicate gate blocks the retry as before.
* The final answer is yielded once as `("token", final)` by the runtime.

## 6. CodingGraph execution path

Module: `cozmo/graphs/coding_graph.py` (Phase 8C upgrade). State:
`CodingState`. Verification contracts: `cozmo/graphs/coding_intel.py`.

```
START → understand → plan → implement → verify ─┬─ all passed ────────────→ END
                                                ├─ skipped (no verifier /
                                                │  no edits) ─────────────→ END (8A retry gate)
                                                └─ any failure → analyze ─┬─ implementation failure
                                                                          │   → implement (repair,
                                                                          │     feedback-injected,
                                                                          │     bounded by max_attempts)
                                                                          └─ environment / permission
                                                                              → END (honest terminal)
```

* The `implement` node delegates each attempt to the runtime's existing ReAct
  agent loop via an injected `run_loop` callable
  `(state) -> (events, final, reason, ok)`. Every tool call inside that loop
  goes through ToolExecutor's permission/risk gate — the graph adds zero tool
  authority. Repair attempts receive `state["repair_context"]` (bounded real
  verification output) appended to their prompt by the runtime, so attempt
  N+1 never repeats attempt N blind.
* **verify** runs ONLY when the attempt actually edited files; it calls the
  injected `verify` collaborator, which routes commands through
  `ToolExecutor.execute("run_command", …)` — the workspace-pinned shell
  runner attaches STRUCTURED results (exit_code / stdout_tail /
  stderr_tail / duration_ms), and the executor treats the exit code as the
  success authority. The graph never spawns processes.
* **analyze** classifies failures: implementation → repair with bounded
  feedback; environment (missing interpreter/test runner) or permission
  denial → terminate honestly WITHOUT touching project code.
* Cross-attempt dedup (8F): identical MUTATING calls (same tool + args) from
  prior attempts are seeded into the loop's dedup gate; reads/commands stay
  repeatable.
* Terminal taxonomy: `completed | stopped | environment_error |
  permission_denied | verification_failed | empty | error` — and `_finalize_
  graph_plan_step` marks the logical step COMPLETED only on `completed`
  (partial output under a failing reason is a FAILED step).
* The loop's stream events are captured into `state["events"]` and replayed by
  the runtime, so token streaming, thinking events, and tool events reach the
  UI unchanged. A safety net yields `("token", answer)` if no token was
  streamed.
* Plan lifecycle (`plan.started/completed`) remains the runtime's contract;
  the graph never emits bus events.
* Workspace confinement (8C): `write_file`/`edit_file` resolve through the
  shared `file_ops.resolve_in_workspace` root check (absolute escapes and
  traversal rejected); `run_command`/`execute_python` subprocesses run pinned
  to the workspace root; destructive commands remain blocklisted.

## 7. General / non-graph execution path

Intents other than research/coding (conversation, planning, vision,
filesystem…) — and any research/coding run where graphs are not wired — use
the generic path:

```
plan steps (PlannerEngine templates, deterministic) or unplanned single loop
   └─> _run_agent_loop  (ReAct)
          model stream → tool calls → ToolExecutor gate → ToolMessage → loop
          recovery hooks: UPGRADE_SEARCH / ESCALATE_WEB (re-bind via
          _bind_runnable; budget committed through RetrievalExecutor)
```

This path is deliberately retained: it serves bare `CozmoRuntime()`
construction (evaluation harness), documents the `None = legacy unchanged`
opt-out, and keeps PlannerEngine's sequential-step semantics (with
checkpointable step boundaries) for non-graph intents.

## 8. Composition wiring (who builds what)

| Surface | Builder | Graphs wired |
|---|---|---|
| CLI / background / Telegram / scheduler / task queue | `CozmoContext.create_runtime` (`cozmo/services/context.py`) | yes (defaults constructed there) |
| WebUI sessions | `build_runtime` (`cozmo/webui_server.py`) | **yes since Stage 3E** — built once, cached on the shared backend dict (`b["research_graph"]` / `b["coding_graph"]`) |
| Evaluation harness | bare `CozmoRuntime()` (`cozmo/evaluation/drivers.py`) | no (intentional baseline) |

Both production builders construct graphs without resolving a model — graphs
are stateless until the runtime injects per-run collaborators.

## 9. Persistence / resume authority

* **Canonical conversation identity:** Brain (`cozmo/brain/`). Runtime turns
  are observed via `brain.observe(Turn(...))`.
* **Durable job state:** `JobLifecycle` (`cozmo/services/job_lifecycle.py`)
  derives Jobs/Checkpoints from runtime events. `Checkpoint.step` is written
  as *last-completed index + 1*; `ExecutionCoordinator` resumes by feeding it
  back as `resume_from` — plan object reused, no replanning.
* **Resume candidates:** `ContinuationService`
  (`cozmo/services/continuation.py`) surfaces resumable work to CLI/WebUI.
* **LangGraph owns none of this.** Graph state is in-memory per-run workflow
  state; graphs compile without a checkpointer, carry no persistence fields
  (AST-guarded), and never read/write configuration.

## 10. ToolExecutor — permission/risk boundary

`cozmo/runtime/tool_executor.py` is the single execution pipeline: extraction
of model tool calls → permission callback gate → risk classification
(`tool_risk.get_tool_risk`) → validation → execution → sanitization →
normalization → fallback chains → trace records. MCP server permissions layer
through `mcp_permissions`. Both graphs and the generic loop execute tools
exclusively through it; neither constructs tools nor bypasses gating.
Tool-to-LangChain wrapping happens in `ToolRegistry.as_lc_tools()`
(`StructuredTool.from_function`) — intentionally narrow LangChain usage.

## 11. Retained thin boundaries (deliberate)

* **`MCPManager`** (`cozmo/runtime/providers/mcp.py`) — facade over the M5.5
  seams (`MCPLifecycle`, `MCPRuntimeClient`/`MCPHost`, `MCPToolDiscovery`,
  `MCPStatus`). Kept because it has live production callers (`webui.py`) and
  four regression suites depend on its public surface. Not dead code.
* **`SimpleLLM`** (`cozmo/services/simple_llm.py`) — auxiliary invoke seam for
  intent classification / evidence grounding / compaction. Propagates
  `ModelUnavailableError`; never substitutes.
* **`cozmo/ollama.py`** — Ollama *process* management (start/stop/health),
  not a model client shim.
* **Desktop vision** (`cozmo/tools/desktop.py`) — uses the verbatim selected
  general-workload model with a capability check, but performs inference via
  direct Ollama HTTP rather than the LangChain seam. Contract-compliant on
  selection; consolidation deferred (see §12).

## 12. Deferred cleanup items (and why)

| Item | Why deferred |
|---|---|
| Duplicate SearXNG clients (`tools/web_search.py::_search_searxng` vs `tools/search_pipeline.py::_search_searxng`) | Both implementations are live and independently tested; merging is a behavior-risk refactor with no dead code to remove. |
| Desktop vision raw HTTP → LangChain multimodal | Selection contract already compliant; migrating payload format risks vision regressions for zero behavioral gain today. |
| Legacy inline research/coding loops | Reachable by design (bare-construction opt-out, evaluation baseline, shared by other intents). Removal would break documented contracts, not cleanup. |
| Three fetch tools (`web_fetch` / `fetch_url` / `webfetch`) | All registered, risk/category-mapped, referenced by `RetrievalCoordinator._FETCH_TOOLS`; removal changes the tool inventory, which is out of scope for cleanup. |
| Bare `CozmoRuntime()` in evaluation driver | Intentional measurement baseline; wiring graphs there would silently change benchmark semantics. |

### 12.1 Post-cutover legacy audit (LangGraph-default stabilization stage)

Audit verdict per retained legacy component — what remains, and the exact
dependency that blocks removal:

| Component | Verdict | Blocking dependency / reason |
|---|---|---|
| Legacy ReAct branch in `run_stream` (planned sequential + unplanned) | **RETAIN** | It IS the `workflow_engine="legacy"` escape hatch surface, the bare-`CozmoRuntime()` evaluation baseline, and the fallback when graphs are not wired. Removing it is Phase-5-final work, gated on the conditions below. |
| `_run_agent_loop` | **RETAIN** | Live production dependency: `CodingGraph`'s injected `run_loop` (`runtime.py` `_coding_graph_state`) delegates every implement attempt to it. Migration would mean re-platforming the coding workflow onto `RuntimeWorkflowGraph` — an architecture project, not retirement. |
| `_rank_memories` (`RetrievalExecutor`, sole caller `_setup_memory_context`) | **RETAIN** | Implements memory-context importance ranking (frequency × recency × (1−distance)). `ResultMerger` has no frequency/recency semantics (its deltas: confidence×status, scenario affinity, hop penalty). Routing memory context through the merger changes prompt-context ordering — a behavior change, not parity. Known debt: a second importance formula exists at store level (`LanceStore.search_with_importance`, relevance × recency × frequency); consolidation requires new ranking semantics in one component + eval gates. |
| `search_with_importance` (`LanceStore`) | **RETAIN** | Not a legacy path: it is the store's candidate-fetch primitive used by `KnowledgeIndex` (canonical retrieval architecture) and `MemoryManager.query` (Brain's flat-memory read). Both live. |
| `MemoryManager` | **RETAIN** | It is Brain's storage engine (`Brain.recall` fallback without resolver, `learn → store_fact`, `consolidate`), the WebUI `/api/memory/*` + agent_memory backend, the no-brain runtime/tool fallback, and the source corpus for `storage/migrations.py`. Removal = Brain storage-layer rewrite (tracked as Phase G roadmap, explicitly out of scope for this stage). |
| `get_memory_manager` global | **RETAIN** | Mirrors the `get_brain`/`get_knowledge_index` accessor pattern; consumed by `tools/memory_ops.py` fallback and Brain's bootstrap default. Phase G item 3 will retire it with the brain=None fallback. |
| `workflow_engine="legacy"` escape hatch | **RETAIN** | Phase-5 preconditions unmet: legacy branch + `_run_agent_loop` still live (coding graph), and the parity harness itself exercises the legacy engine as its comparison baseline. |

Retired in this stage (audit-proven zero production callers):
`Brain.retrieve_memory_rows` (flat compat adapter; MemoryRetrievalSource
reads `recall` directly) and `MemoryManager.store_project_context` (dead
method). Guards: `tests/test_architecture.py`
(`test_no_retired_retrieve_memory_rows_adapter`,
`test_no_retired_store_project_context_method`). The composition roots'
`"langgraph"` default is pinned by
`test_composition_roots_default_langgraph_engine`.

Phase-5 (escape-hatch removal) preconditions checklist, for the record:
legacy branch gone ☐ · `_run_agent_loop` gone ☐ · coding-graph loop
migrated ☐ · parity harness re-based ☐ · full suite green under
langgraph-only ☐

## 13. Dependency posture

* LangChain usage is narrow by policy: `langchain-core` messages/tools types,
  `langchain-ollama`, `langchain-openai`. No agents frameworks, no loaders,
  no vector store adapters from the ecosystem.
* LangGraph is workflow composition only — no persistence, no checkpointing,
  no configuration, no model authority.
* CrewAI is absent by decision; LangGraph covers the composition need and a
  second orchestration abstraction would violate the dependency-minimization
  policy.

## 14. Phase 8 evaluation harness (how to run, how to read)

The single evaluation framework lives in `cozmo/evaluation`. Phase 8E extends
it additively — there is no second harness anywhere in the repo.

```bash
# Offline orchestrator decision baseline (no model required)
python -m cozmo.evaluation analyze   [--dataset tests/regression_corpus.json]

# Research workflow: citations / conflicts / search discipline.
# Deterministic offline driver by default (scripted synthesis + stub search);
# pass nothing and it is reproducible byte-for-byte.
python -m cozmo.evaluation research  [--dataset tests/research_corpus.json] [--save out.json]

# Coding workflow: fixture repositories materialized into temp workspaces;
# verification executes REAL pytest through ToolExecutor's workspace-pinned
# shell runner. Offline; requires local pytest on sys.executable.
python -m cozmo.evaluation coding    [--dataset tests/coding_corpus.json] [--save out.json]

# Regression: compare two saved MetricSet snapshots.
python -m cozmo.evaluation compare BASELINE.json CANDIDATE.json
```

**Metric families.** `MetricSet` now carries `retrieval`, `answer`, `tools`,
`research`, `coding`, `latency`. Research metrics are judge-free:
citation_resolvability (valid/total cited ids against the graph's own
manifest), citation_coverage, insufficiency_honesty, conflict_acknowledgment,
unnecessary_search_rate (searches beyond the case's `max_searches` budget).
Coding metrics: task_completion (final verification verdict matches fixture
expectation), test_pass_rate (passed verifications / total verifications),
regression_rate (first attempt passed then failed later = repair churn),
avg_repair_attempts, unnecessary_edit_rate (edits with zero failing
verifications), tool_failure_rate.

**Regression thresholds** (`cozmo/evaluation/regression.py`). Comparison is
per-metric ABSOLUTE delta against `DEFAULT_THRESHOLDS` (0.05 for quality
metrics, 0.02 for tools.recovery_rate); latency alone uses RELATIVE delta
with a 50 ms absolute noise floor so identical runs never false-flag. Higher
is better except `tools.recovery_rate` and `latency`. Exit code: 0 = PASS,
1 = regression found. New `research.*` / `coding.*` metrics ship WITHOUT
default thresholds — add explicit thresholds per metric when a baseline
history exists; absent thresholds never spuriously fail.

**LLM-as-judge** remains optional behind an explicit flag (`AnswerJudge`
protocol on `MetricCollector`; `evidence --judge-model`) and is never used by
the deterministic research/coding drivers.
