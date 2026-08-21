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

Module: `cozmo/graphs/research_graph.py`. State: `ResearchState`
(`cozmo/graphs/state.py`).

```
START → understand → plan → search → evaluate ─┬─ sufficient, no gaps → synthesize
                                               ├─ blocked (no search /
                                               │  budget exhausted) → synthesize
                                               └─ gaps → search (bounded by
                                                          max_search_attempts)
synthesize → validate ─┬─ insufficient coverage → search (bounded)
                       └─ otherwise → END
```

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
* The final answer is yielded once as `("token", final)` by the runtime.

## 6. CodingGraph execution path

Module: `cozmo/graphs/coding_graph.py`. State: `CodingState`.

```
START → understand → plan → implement → verify ─┬─ empty answer or max_steps
                                                │  → implement (bounded by
                                                │    max_attempts)
                                                └─ completed → END
```

* The `implement` node delegates each attempt to the runtime's existing ReAct
  agent loop via an injected `run_loop` callable
  `(state) -> (events, final, reason, ok)`. Every tool call inside that loop
  goes through ToolExecutor's permission/risk gate — the graph adds zero tool
  authority.
* The loop's stream events are captured into `state["events"]` and replayed by
  the runtime, so token streaming, thinking events, and tool events reach the
  UI unchanged. A safety net yields `("token", answer)` if no token was
  streamed.
* Sentinel/stream knowledge stays in the runtime; the graph knows only the
  plain tuple contract.
* Plan lifecycle (`plan.started/completed`) remains the runtime's contract;
  the graph never emits bus events.

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

## 13. Dependency posture

* LangChain usage is narrow by policy: `langchain-core` messages/tools types,
  `langchain-ollama`, `langchain-openai`. No agents frameworks, no loaders,
  no vector store adapters from the ecosystem.
* LangGraph is workflow composition only — no persistence, no checkpointing,
  no configuration, no model authority.
* CrewAI is absent by decision; LangGraph covers the composition need and a
  second orchestration abstraction would violate the dependency-minimization
  policy.
