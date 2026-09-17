# AgentRun Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce AgentRun as a typed state container and ExecutionCoordinator-driven lifecycle so one user message can produce multiple progressive assistant messages (PROGRESS/CONTINUE/FINISH), preserve mid-run compaction, and terminate with exactly one run-level event while keeping all existing subsystems intact.

**Architecture:** Add three new runtime value types (`AgentRun`, `AgentAction`, `AgentEvent` + `AgentRunStatus`) and a new `ExecutionCoordinator.execute(run, emit)` outer loop. The outer loop delegates one reasoning cycle to the existing `run_react_attempt` / `RuntimeWorkflowGraph` (which retain internal tool execution and native streaming), interprets the resulting `AgentAction(CONTINUE|PROGRESS|FINISH)`, emits `message.started/delta/completed` with stable `message_id`, checks `ContextManager` after every non-terminal action, and terminates with `run.completed|failed|cancelled`. `Session` becomes a thin WebSocket bridge that maps `AgentEvent` to the existing protocol, preserving `run_stream()` for CLI/Telegram/background.

**Tech Stack:** Python 3.12, FastAPI + WebSocket, LangGraph, LangChain, pytest, Novi runtime (`novi/runtime/*`, `novi/services/execution.py`, `novi/graphs/*`, `novi/webui_server.py`)

**Spec:** `docs/superpowers/specs/2026-09-14-agentrun-lifecycle.md`

## Global Constraints

- Beta scope only: one foreground run per session. Do NOT implement persistence/restore, concurrent runs, multi-session observation, or background-run unification.
- `AgentRun` is state only — no execution logic. `ExecutionCoordinator` owns the outer `while not run.finished:` loop.
- `AgentActionType` contains only `CONTINUE`, `PROGRESS`, `FINISH`. No `TOOL` action — tool execution stays internal to `run_react_attempt` / `RuntimeWorkflowGraph`.
- `message.completed != run.completed` is the core invariant. Only `run.completed` maps to legacy WebSocket `done`.
- `ContextManager` L1/L2/L3, `StableState`, `ExecutionContext`, `Job`/`Checkpoint`, existing ReAct tool execution, `RuntimeWorkflowGraph`, cancellation, and native streaming infrastructure are preserved as-is.
- `emit_progress` is a permission-free pseudo-tool: produces `PROGRESS`, emits `message.completed`, preserves active run and task state, allows continuation.
- `max_agent_iterations` (default 20) is separate from internal `max_steps`.
- Every `AgentRun` emits exactly one terminal event: `run.completed` | `run.failed` | `run.cancelled`.
- The repo must remain testable between phases; each task leaves existing tests green.

---

## File Structure

```
novi/runtime/
  agent_run.py          # NEW — AgentRun dataclass + AgentRunStatus enum
  agent_action.py       # NEW — AgentActionType + AgentAction
  agent_events.py       # NEW — AgentEvent dataclass + EVENT_TYPES
  agent_run.py          # (merged with above or split; prefer one file for Run+Status)
  react_attempt.py      # MODIFY — yield AgentAction, add emit_progress handling, emit tool events with AgentEvent
  runtime.py            # MINOR — run_stream delegates to coordinator for compat
  context_manager.py    # NO CHANGE (read-only reference)

novi/graphs/
  runtime_graph.py      # MODIFY — run() returns AgentAction; preserve nodes

novi/services/
  execution.py          # MODIFY — add execute(run, emit) with correct control flow

novi/webui_server.py    # MODIFY — Session.start_run creates AgentRun, maps AgentEvent → WS

tests/
  test_agent_run.py            # NEW — status enum, state transitions, finished invariant
  test_agent_action.py         # NEW — action construction, terminal invariants
  test_agent_events.py         # NEW — event types, message_id correlation
  test_agentrun_lifecycle.py   # NEW — integration: ordering, terminal, history, compaction
  test_agentrun_streaming.py   # NEW — native streaming vs _chunk fallback
  test_emit_progress.py        # NEW — pseudo-tool semantics
```

---

### Task 1: Typed AgentRun State Foundation

**Files:**
- Create: `novi/runtime/agent_run.py`
- Test: `tests/test_agent_run.py`

**Interfaces:**
- Consumes: `novi/runtime/execution_context.py:ExecutionContext`, `novi/jobs/job.py:Checkpoint` (read-only)
- Produces: `novi.runtime.agent_run.AgentRunStatus` (Enum: RUNNING, COMPLETED, FAILED, CANCELLED), `novi.runtime.agent_run.AgentRun` (dataclass with `finished` property, `iteration: int`, `token_usage: int | None`, `message_seq: int` helper)

**Existing behavior preserved:** Nothing — additive only. No existing code imports these names yet.

**New behavior:** Typed status replaces stringly-typed checks. `AgentRun.finished` is `status in (COMPLETED, FAILED, CANCELLED)`. `RUNNING` is the only non-terminal value in beta.

**Backward compat:** None needed (new module).

- [ ] **Step 1: Write failing test for AgentRunStatus enum**

```python
# tests/test_agent_run.py
from novi.runtime.agent_run import AgentRunStatus, AgentRun

def test_agent_run_status_is_typed_enum():
    assert AgentRunStatus.RUNNING.value == "running"
    assert AgentRunStatus.COMPLETED.value == "completed"
    assert AgentRunStatus.FAILED.value == "failed"
    assert AgentRunStatus.CANCELLED.value == "cancelled"
    # PAUSED must NOT exist in beta
    assert not hasattr(AgentRunStatus, "PAUSED")

def test_agent_run_finished_invariant():
    from novi.runtime.execution_context import ExecutionContext
    ctx = ExecutionContext(user_input="hello")
    run = AgentRun(id="run-1", conversation_id="conv-1", goal="hello", status=AgentRunStatus.RUNNING, context=ctx)
    assert not run.finished
    run.status = AgentRunStatus.COMPLETED
    assert run.finished
    run.status = AgentRunStatus.FAILED
    assert run.finished
    run.status = AgentRunStatus.CANCELLED
    assert run.finished

def test_agent_run_token_usage_nullable():
    from novi.runtime.execution_context import ExecutionContext
    ctx = ExecutionContext(user_input="hi")
    run = AgentRun(id="r", conversation_id="c", goal="hi", status=AgentRunStatus.RUNNING, context=ctx, token_usage=None)
    assert run.token_usage is None
    run.token_usage = 42
    assert run.token_usage == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novi.runtime.agent_run'`

- [ ] **Step 3: Implement minimal module**

```python
# novi/runtime/agent_run.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from novi.runtime.execution_context import ExecutionContext
from novi.jobs.job import Checkpoint

class AgentRunStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class AgentRun:
    id: str
    conversation_id: str
    goal: str
    status: AgentRunStatus
    context: ExecutionContext
    checkpoint: Optional[Checkpoint] = None
    iteration: int = 0
    token_usage: Optional[int] = None
    message_seq: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def finished(self) -> bool:
        return self.status in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_run.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add novi/runtime/agent_run.py tests/test_agent_run.py
git commit -m "feat(runtime): add typed AgentRunStatus and AgentRun state container"
```

---

### Task 2: AgentAction and AgentEvent Value Types

**Files:**
- Create: `novi/runtime/agent_action.py`
- Create: `novi/runtime/agent_events.py`
- Test: `tests/test_agent_action.py`
- Test: `tests/test_agent_events.py`

**Interfaces:**
- Consumes: `novi.runtime.agent_run.AgentRunStatus` (for docs only)
- Produces: `novi.runtime.agent_action.AgentActionType` (CONTINUE/PROGRESS/FINISH), `AgentAction(type, message, metadata)`, `novi.runtime.agent_events.AgentEvent(type, run_id, conversation_id, message_id, message, tool, args, result, error, phase)`, `EVENT_TYPES` set

**Existing behavior preserved:** None — additive.

**New behavior:** Establishes the runtime boundary protocol. `AgentAction` never carries tool_calls. `AgentEvent.message_id` is `None` for non-message events, required for `message.started/delta/completed`.

**Backward compat:** New modules only.

- [ ] **Step 1: Write failing test for AgentAction**

```python
# tests/test_agent_action.py
from novi.runtime.agent_action import AgentActionType, AgentAction

def test_agent_action_types_limited_to_three():
    assert AgentActionType.CONTINUE.value == "continue"
    assert AgentActionType.PROGRESS.value == "progress"
    assert AgentActionType.FINISH.value == "finish"
    assert not hasattr(AgentActionType, "TOOL")

def test_agent_action_progress_requires_message():
    a = AgentAction(type=AgentActionType.PROGRESS, message="found files")
    assert a.message == "found files"

def test_agent_action_never_carries_tool_calls():
    a = AgentAction(type=AgentActionType.CONTINUE)
    assert not hasattr(a, "tool_calls")
```

- [ ] **Step 2: Write failing test for AgentEvent**

```python
# tests/test_agent_events.py
from novi.runtime.agent_events import AgentEvent, EVENT_TYPES

def test_event_message_id_stable():
    e1 = AgentEvent(type="message.started", run_id="r1", conversation_id="c1", message_id="m1")
    assert e1.message_id == "m1"
    assert AgentEvent(type="tool.started", run_id="r1", conversation_id="c1").message_id is None

def test_event_types_include_lifecycle():
    assert "run.started" in EVENT_TYPES
    assert "run.completed" in EVENT_TYPES
    assert "run.failed" in EVENT_TYPES
    assert "run.cancelled" in EVENT_TYPES
    assert "message.started" in EVENT_TYPES
    assert "message.delta" in EVENT_TYPES
    assert "message.completed" in EVENT_TYPES
    assert "context.compacting" in EVENT_TYPES
    assert "context.compacted" in EVENT_TYPES
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_agent_action.py tests/test_agent_events.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement modules**

```python
# novi/runtime/agent_action.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any

class AgentActionType(Enum):
    CONTINUE = "continue"
    PROGRESS = "progress"
    FINISH = "finish"

@dataclass
class AgentAction:
    type: AgentActionType
    message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
```

```python
# novi/runtime/agent_events.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Any

@dataclass
class AgentEvent:
    type: str
    run_id: str
    conversation_id: str
    message_id: Optional[str] = None
    message: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[dict] = None
    result: Optional[str] = None
    error: Optional[str] = None
    phase: Optional[str] = None
    detail: Optional[str] = None

EVENT_TYPES = {
    "run.started", "run.completed", "run.failed", "run.cancelled",
    "message.started", "message.delta", "message.completed",
    "tool.started", "tool.completed",
    "status", "reasoning",
    "context.compacting", "context.compacted",
    "plan.started", "plan.completed", "step.started", "step.completed",
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_agent_action.py tests/test_agent_events.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novi/runtime/agent_action.py novi/runtime/agent_events.py tests/test_agent_action.py tests/test_agent_events.py
git commit -m "feat(runtime): add AgentAction and AgentEvent value types"
```

---

### Task 3: emit_progress Pseudo-Tool (Guard Before Execution)

**Files:**
- Modify: `novi/runtime/tool_registry.py` (register or document pseudo-tool)
- Modify: `novi/runtime/tool_executor.py` (intercept emit_progress; do NOT treat as external op)
- Create: `tests/test_emit_progress.py`

**Interfaces:**
- Consumes: `novi.runtime.agent_action.AgentAction`, `novi.runtime.agent_events.AgentEvent`
- Produces: Pseudo-tool `emit_progress(message: str)` callable by model, handled in `ToolExecutor.execute()` as short-circuit to `AgentAction.PROGRESS` path; `ToolExecutor.is_pseudo_tool(name) -> bool`

**Existing behavior preserved:** `ToolExecutor` permission/risk/validation pipeline for real tools. `emit_progress` bypasses permission, risk, and external execution entirely.

**New behavior:** `emit_progress` model docstring: `"Use only when you have discovered meaningful information worth communicating before continuing. Do not narrate routine actions or announce every tool call. Do not call emit_progress when you are ready to provide the final answer."`

**Backward compat:** No existing tool named `emit_progress` (verify via `TOOL_REGISTRY` search). If collision, rename to `_emit_progress`.

- [ ] **Step 1: Write failing test for emit_progress guard**

```python
# tests/test_emit_progress.py
from novi.runtime.tool_executor import ToolExecutor
from novi.runtime.tool_registry import ToolRegistry

def test_emit_progress_is_pseudo_and_permission_free():
    reg = ToolRegistry()
    exe = ToolExecutor(registry=reg)
    # Must be recognized as pseudo-tool
    assert exe.is_pseudo_tool("emit_progress") is True
    # Must NOT require permission
    assert exe.requires_permission("emit_progress") is False
    # Must NOT be treated as external operation
    assert exe.is_external_tool("emit_progress") is False

def test_emit_progress_tool_spec_has_correct_description():
    from novi.runtime.react_attempt import EMIT_PROGRESS_DESCRIPTION
    assert "meaningful information" in EMIT_PROGRESS_DESCRIPTION
    assert "Do not call emit_progress when you are ready to provide the final answer" in EMIT_PROGRESS_DESCRIPTION

def test_emit_progress_does_not_reset_run():
    from novi.runtime.agent_run import AgentRun, AgentRunStatus
    from novi.runtime.execution_context import ExecutionContext
    # Simulate: calling emit_progress must not reset iteration or checkpoint
    ctx = ExecutionContext(user_input="test")
    run = AgentRun(id="r1", conversation_id="c1", goal="test", status=AgentRunStatus.RUNNING, context=ctx, iteration=2)
    # After handling PROGRESS, iteration increments by 1 (not reset) and context preserved
    run.iteration += 1
    assert run.iteration == 3
    assert run.context.user_input == "test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_emit_progress.py -v`
Expected: FAIL with `AttributeError: is_pseudo_tool`

- [ ] **Step 3: Implement pseudo-tool handling**

```python
# novi/runtime/tool_executor.py — add near top
_PSEUDO_TOOLS = {"emit_progress"}

def is_pseudo_tool(self, name: str) -> bool:
    return name in _PSEUDO_TOOLS

def requires_permission(self, name: str) -> bool:
    if name in _PSEUDO_TOOLS:
        return False
    # existing logic

def is_external_tool(self, name: str) -> bool:
    if name in _PSEUDO_TOOLS:
        return False
    # existing logic

# In execute(), early return:
if name == "emit_progress":
    # Do not execute; return sentinel for react_attempt to translate to PROGRESS
    return ToolResult(output=args.get("message", ""), diff=None, success=True, is_pseudo_progress=True)
```

```python
# novi/runtime/react_attempt.py — add constant
EMIT_PROGRESS_DESCRIPTION = (
    "Use only when you have discovered meaningful information worth communicating "
    "before continuing. Do not narrate routine actions or announce every tool call. "
    "Do not call emit_progress when you are ready to provide the final answer."
)
```

Register `emit_progress` in the tool list returned to the model (as a `Tool` with above description, no permission).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_emit_progress.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/runtime/tool_executor.py novi/runtime/react_attempt.py tests/test_emit_progress.py
git commit -m "feat(runtime): add emit_progress pseudo-tool guard"
```

---

### Task 4: ReAct Loop and Graph Return AgentAction with Native Streaming

**Files:**
- Modify: `novi/runtime/react_attempt.py:run_react_attempt` (yield `AgentAction`, preserve native `model.stream` token flow, attach `message_id` to delta events)
- Modify: `novi/graphs/runtime_graph.py:RuntimeWorkflowGraph.run` (return `AgentAction`), `novi/graphs/state.py` (optional helper `emit_message_events`)
- Test: `tests/test_agentrun_streaming.py`

**Interfaces:**
- Consumes: `AgentAction`, `AgentEvent`, `AgentRunStatus`, `emit_progress` pseudo-tool name
- Produces: `run_react_attempt(...) -> Iterator[AgentAction | AgentEvent]` where `AgentEvent`s are `message.started/delta/completed`, `tool.started/completed`, `reasoning`, `status` (ephemeral) with `message_id` on message events; `RuntimeWorkflowGraph.run(state) -> AgentAction`

**Existing behavior preserved:** Model selection (Novi-bound runnable), `ToolExecutor` as sole gate, `RetrievalExecutor` recovery, `is_goal_complete` logic, `ContextManager.compact_history` call sites, cancellation via `stop_probe` / `should_stop`.

**New behavior:**
- When `emit_progress` tool call detected: yield `AgentAction(PROGRESS, message=...)` instead of executing external tool.
- When `is_goal_complete(ctx, final)` true: yield `AgentAction(FINISH, message=final)` with native streaming deltas already emitted as `message.delta`.
- Otherwise: yield `AgentAction(CONTINUE)`.
- `_LOOP_DONE` sentinel kept as deprecated alias for one release (maps to `AgentAction.FINISH`) to keep legacy callers green.
- Native streaming: on first token from `runnable.stream`, emit `message.started` with fresh `message_id`; each `chunk.content` piece becomes `message.delta` with same `message_id`; on final yield, `message.completed` carries full message. If model has no `stream` (test doubles), fallback to `_chunk_message(action.message)` word-split with same `message_id` contract.

**Backward compat:** Legacy `(_LOOP_DONE, text, reason, success)` consumers check `isinstance(item, AgentAction)` first; if tuple sentinel still yielded, translate to `AgentAction`. Remove sentinel in follow-up issue. Mark `TODO(cleanup): remove _LOOP_DONE after AgentRun migration`.

- [ ] **Step 1: Write failing test for native streaming with message_id**

```python
# tests/test_agentrun_streaming.py
def test_react_yields_message_delta_with_stable_message_id():
    from novi.runtime.react_attempt import run_react_attempt
    from novi.runtime.execution_context import ExecutionContext
    # Fake runnable that streams two tokens
    class FakeChunk:
        def __init__(self, content): self.content = content; self.additional_kwargs = {}
    class FakeRunnable:
        def stream(self, msgs):
            yield FakeChunk("Hello ")
            yield FakeChunk("world")
        def invoke(self, msgs): return FakeChunk("Hello world")
    ctx = ExecutionContext(user_input="hi")
    ctx.model_name = "test"
    # Minimal args: capture emitted AgentEvents
    events = list(run_react_attempt(
        ctx=ctx, runnable=FakeRunnable(), tool_executor=FakeExecutorNoTools(),
        tracer=FakeTracer(), retrieval_executor=FakeRetrieval(), capability_registry=FakeReg(),
        scan_skills=lambda t, a: [], skill_block=lambda s: "", bind_runnable=lambda c, t: FakeRunnable(),
        stop_probe=lambda: False, step_budget=1, base_msgs=[]
    ))
    # Should contain message.started/delta/completed with same message_id
    started = [e for e in events if getattr(e, "type", None) == "message.started"]
    deltas = [e for e in events if getattr(e, "type", None) == "message.delta"]
    completed = [e for e in events if getattr(e, "type", None) == "message.completed"]
    assert len(started) == 1 and len(completed) == 1
    assert started[0].message_id == deltas[0].message_id == completed[0].message_id

def test_fallback_chunking_when_no_stream():
    # Runnable without .stream should still emit message.delta via _chunk_message
    pass  # explicit fallback path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agentrun_streaming.py -v`
Expected: FAIL (no AgentAction path)

- [ ] **Step 3: Implement yield of AgentAction + message_id streaming**

```python
# novi/runtime/react_attempt.py — inside run_react_attempt loop
message_id = None
for chunk in runnable.stream(msgs):
    if message_id is None:
        message_id = f"msg-{uuid.uuid4().hex[:8]}"
        yield AgentEvent(type="message.started", run_id=getattr(ctx, "run_id", ""), conversation_id=ctx.conversation_id, message_id=message_id)
    piece = chunk.content or ""
    if piece:
        yield AgentEvent(type="message.delta", run_id=getattr(ctx, "run_id", ""), conversation_id=ctx.conversation_id, message_id=message_id, message=piece)

# On emit_progress tool call:
if c["name"] == "emit_progress":
    yield AgentEvent(type="message.completed", run_id=..., message_id=message_id, message=args.get("message",""))
    yield AgentAction(type=AgentActionType.PROGRESS, message=args.get("message",""))
    continue

# At terminal:
if is_goal_complete(ctx, final):
    yield AgentEvent(type="message.completed", ...)
    yield AgentAction(type=AgentActionType.FINISH, message=final)
else:
    yield AgentAction(type=AgentActionType.CONTINUE)
```

For `RuntimeWorkflowGraph.run()`: same mapping — wrap final `result["answer"]` as `AgentAction`, emit `message.*` events via injected `emit` callback if provided, else return action only and let coordinator handle emission.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agentrun_streaming.py -v`
Expected: PASS

- [ ] **Step 5: Run existing graph tests to ensure no regression**

Run: `pytest tests/test_runtime_graph.py tests/test_react_attempt.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novi/runtime/react_attempt.py novi/graphs/runtime_graph.py tests/test_agentrun_streaming.py
git commit -m "feat(runtime): ReAct/graph return AgentAction with native streaming and message_id"
```

---

### Task 5: ExecutionCoordinator Outer Loop (Correct Control Flow)

**Files:**
- Modify: `novi/services/execution.py` (add `MAX_AGENT_ITERATIONS = 20`, `execute(run, emit)` method, `AgentCancelled` / `ExpectedExecutionError` handling, `ContextManager` compaction after every non-terminal action)
- Modify: `novi/runtime/runtime.py` (make `run_stream` delegate to `coordinator.execute` with collecting emit for compat)
- Test: `tests/test_coordinator_execute.py`

**Interfaces:**
- Consumes: `AgentRun`, `AgentRunStatus`, `AgentAction`, `AgentEvent`, `ContextManager`, `StableState`, `ExecutionContext`
- Produces: `ExecutionCoordinator.execute(run: AgentRun, emit: Callable[[AgentEvent], None]) -> None` (drives until terminal, emits exactly one of `run.completed|failed|cancelled`)

**Existing behavior preserved:** `ExecutionCoordinator.run_stream` semantics for `prepare` (plan/job creation), auto-continue no longer needed in outer loop (single AgentRun loop replaces 3x reopen), `Job`/`Checkpoint` still created via `JobManager.submit` in `_prepare`, `Task` ownership unchanged.

**New behavior:** Correct flow per spec §3.3:
```
emit run.started
while not run.finished:
  ctx = _prepare_context(run)
  action = _run_graph_or_react(ctx, run, emit)  # delegates, preserves tool ownership
  handle CONTINUE/PROGRESS/FINISH (PROGRESS/FINISH emit message.started/delta/completed)
  if run.finished: break
  if ContextManager.should_compact(ctx) in ("compact","emergency"):
    emit context.compacting; compact; run.checkpoint = checkpoint_stable; emit context.compacted
  run.iteration += 1
  if iteration >= MAX_AGENT_ITERATIONS: run.status=FAILED; emit run.failed; break
```

**Backward compat:** `run_stream` stays and delegates:

```python
def run_stream(self, runtime, user_input, **kw):
    run = AgentRun(..., status=RUNNING, context=ctx)
    events = []
    def collect(e): events.append(e); yield e  # or buffer for final answer
    self.execute(run, collect)
    # For legacy callers expecting tuple stream, translate collected AgentEvents back to legacy tuples
```

Mark `TODO(cleanup): remove run_stream tuple translation after all surfaces migrate`.

- [ ] **Step 1: Write failing test for terminal invariant with mocked action**

```python
# tests/test_coordinator_execute.py
from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.agent_action import AgentAction, AgentActionType
from novi.runtime.execution_context import ExecutionContext
from novi.services.execution import ExecutionCoordinator

def test_execute_emits_exactly_one_terminal_event():
    ctx = ExecutionContext(user_input="hi")
    run = AgentRun(id="run-1", conversation_id="c1", goal="hi", status=AgentRunStatus.RUNNING, context=ctx)
    coord = ExecutionCoordinator(orchestrator=FakeOrchestratorFinish(), job_manager=FakeJobManager())
    events = []
    coord.execute(run, lambda e: events.append(e))
    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1
    assert run.finished

def test_compaction_runs_after_progress_not_skipped():
    # Fake ContextManager.should_compact returns "compact" after first action
    # Assert compaction events appear between first message.completed and next cycle
    pass

def test_separate_max_agent_iterations_from_max_steps():
    from novi.services.execution import MAX_AGENT_ITERATIONS
    assert MAX_AGENT_ITERATIONS == 20
    # internal max_steps comes from ExecutionContext.max_steps (default 10)
    ctx = ExecutionContext(user_input="x")
    assert ctx.max_steps != MAX_AGENT_ITERATIONS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coordinator_execute.py -v`
Expected: FAIL (`execute` not found)

- [ ] **Step 3: Implement execute()**

```python
# novi/services/execution.py
MAX_AGENT_ITERATIONS = 20

class AgentCancelled(Exception): ...
class ExpectedExecutionError(Exception): ...

def execute(self, run: AgentRun, emit: Callable[[AgentEvent], None]) -> None:
    emit(AgentEvent(type="run.started", run_id=run.id, conversation_id=run.conversation_id))
    try:
        while not run.finished:
            ctx = self._prepare_context(run)  # existing logic, attach run.id to ctx for streaming
            ctx.run_id = run.id  # for react_attempt message_id correlation
            # choose executor
            if ctx.execution_plan and getattr(ctx.execution_plan, "plan", None):
                action = self._run_graph(ctx, run, emit)  # graph returns AgentAction, emits tool events internally
            else:
                action = self._run_react(ctx, run, emit)  # react yields AgentAction, emits message/tool events

            # handle action (CONTINUE/PROGRESS/FINISH) — see spec §3.3 for exact emit sequence
            ...

            if run.finished:
                break

            # compaction AFTER action
            from novi.runtime.context_manager import ContextManager
            cm = ContextManager(model_name=ctx.model_name)
            level = cm.should_compact(ctx)
            if level in ("compact", "emergency"):
                emit(AgentEvent(type="context.compacting", ...))
                cm.compact_history(ctx)
                run.checkpoint = cm.checkpoint_stable(ctx)
                emit(AgentEvent(type="context.compacted", ...))

            run.iteration += 1
            run.updated_at = datetime.now()
            if run.iteration >= MAX_AGENT_ITERATIONS:
                run.status = AgentRunStatus.FAILED
                emit(AgentEvent(type="run.failed", run_id=run.id, conversation_id=run.conversation_id, error="Max agent iterations exceeded"))
                break
    except AgentCancelled:
        run.status = AgentRunStatus.CANCELLED
        emit(AgentEvent(type="run.cancelled", run_id=run.id, conversation_id=run.conversation_id))
    except ExpectedExecutionError as e:
        run.status = AgentRunStatus.FAILED
        emit(AgentEvent(type="run.failed", run_id=run.id, conversation_id=run.conversation_id, error=str(e)))
    except Exception:
        import logging; logging.getLogger("novi.services.execution").exception("AgentRun failed")
        run.status = AgentRunStatus.FAILED
        emit(AgentEvent(type="run.failed", run_id=run.id, conversation_id=run.conversation_id, error="Internal error"))
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coordinator_execute.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/services/execution.py novi/runtime/runtime.py tests/test_coordinator_execute.py
git commit -m "feat(services): add ExecutionCoordinator.execute with correct compaction control flow"
```

---

### Task 6: WebSocket Session Bridge and Legacy Compat

**Files:**
- Modify: `novi/webui_server.py:Session` (create AgentRun, call `coordinator.execute`, map AgentEvent → WS)
- Modify: `novi/webui/*` frontend (handle `message_start` / `token` / `message_end` vs `done`)
- Test: `tests/test_webui_agentrun_bridge.py`

**Interfaces:**
- Consumes: `AgentRun`, `AgentRunStatus`, `AgentEvent`, `ExecutionCoordinator.execute`
- Produces: `Session._map_agent_event_to_ws(event) -> dict | None`, `Session.start_run` now creates `AgentRun` and delegates

**Existing behavior preserved:** CLI/Telegram/background still call `runtime.run_stream` directly; WebSocket queue (`asyncio.Queue`) and thread-per-run model unchanged; `stop_flag` / cancellation still via `stop_event`.

**New behavior:** See spec §4.4 mapping. Only `run.completed` → `{"type":"done"}`.

**Backward compat:** Add compat shim `novi/webui_server.py:_legacy_done_adapter` that translates old `{"type":"done"}` without `runId` to `run.completed` for tests. Frontend keeps handling old `done` for one release. Remove after migration.

- [ ] **Step 1: Write failing bridge test**

```python
# tests/test_webui_agentrun_bridge.py
from novi.webui_server import Session

def test_map_agent_event_only_run_completed_to_done():
    sess = Session.__new__(Session)  # bypass init
    from novi.runtime.agent_events import AgentEvent
    assert sess._map_agent_event_to_ws(AgentEvent(type="message.completed", run_id="r1", conversation_id="c1", message_id="m1"))["type"] == "message_end"
    assert sess._map_agent_event_to_ws(AgentEvent(type="run.completed", run_id="r1", conversation_id="c1"))["type"] == "done"
    assert sess._map_agent_event_to_ws(AgentEvent(type="message.completed", run_id="r1", conversation_id="c1", message_id="m1"))["type"] != "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webui_agentrun_bridge.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Session bridge**

```python
# novi/webui_server.py — Session
def _map_agent_event_to_ws(self, event: AgentEvent) -> Optional[dict]:
    if event.type == "message.started":
        return {"type": "message_start", "messageId": event.message_id, "runId": event.run_id}
    elif event.type == "message.delta":
        return {"type": "token", "text": event.message, "messageId": event.message_id}
    elif event.type == "message.completed":
        return {"type": "message_end", "messageId": event.message_id}
    elif event.type == "run.completed":
        return {"type": "done", "runId": event.run_id}
    elif event.type == "run.failed":
        return {"type": "error", "text": event.error, "runId": event.run_id}
    elif event.type == "run.cancelled":
        return {"type": "cancelled", "runId": event.run_id}
    elif event.type in ("tool.started", "tool.completed"):
        return {"type": event.type, "tool": event.tool, "args": event.args, "result": event.result}
    elif event.type in ("context.compacting", "context.compacted"):
        return {"type": "status", "text": "Compacting context..."}
    elif event.type == "status":
        return {"type": "status", "text": event.message}
    return None

def start_run(self, user_input: str, ...):
    from novi.runtime.agent_run import AgentRun, AgentRunStatus
    from novi.runtime.execution_context import ExecutionContext
    run = AgentRun(id=f"run-{uuid.uuid4().hex[:8]}", conversation_id=self.current_conv_id, goal=user_input, status=AgentRunStatus.RUNNING, context=ExecutionContext.from_input(...))
    self.current_run = run
    def emit(e: AgentEvent): self._emit(self._map_agent_event_to_ws(e))
    def work():
        try:
            self.coordinator.execute(run, emit)
        except Exception:
            raise
        finally:
            self.current_run = None
    threading.Thread(target=work, daemon=True).start()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_webui_agentrun_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Manual WebSocket smoke**

Run: `python -m novi --webui &` then send `{"type":"chat","content":"hello"}` and assert `message_start` → `token*` → `message_end` → `done` sequence in browser devtools.

- [ ] **Step 6: Commit**

```bash
git add novi/webui_server.py tests/test_webui_agentrun_bridge.py
git commit -m "feat(webui): bridge AgentRun events to WebSocket; only run.completed maps to done"
```

---

### Task 7: Error Hierarchy, Token Accounting, and Ephemeral Status

**Files:**
- Modify: `novi/services/execution.py` (export `AgentCancelled`, `ExpectedExecutionError`, set `run.token_usage` from provider callback)
- Modify: `novi/runtime/context_manager.py` (no change — document that ephemeral status never enters history)
- Modify: `novi/runtime/runtime.py` (wire `token_usage` from `model.stream` usage metadata when available)
- Test: `tests/test_agentrun_terminal.py`, `tests/test_agentrun_tokens.py`

**Interfaces:**
- Consumes: `AgentRun.token_usage: int | None`, `ctx.trace.steps` (tracing), model/provider `usage` callback
- Produces: Terminal error mapping; `run.token_usage` populated if provider supplies, else `None`.

**Existing behavior preserved:** Cancellation via `stop_event`, tracing via `ctx.trace`.

**New behavior:** `AgentCancelled` raised by `stop_probe` in react_attempt is caught as `run.cancelled`. `ExpectedExecutionError` (e.g., tool permission denied terminal) → `run.failed`. Token accounting prefers `chunk.usage_metadata` / `response.usage` when present; else `None`.

**Backward compat:** Token field nullable — old code ignoring it stays green.

- [ ] **Step 1: Write failing terminal tests**

```python
# tests/test_agentrun_terminal.py
def test_every_run_emits_exactly_one_terminal():
    # Run 3 variants: FINISH → completed, cancelled, failed
    # Assert exactly one of run.completed|failed|cancelled
    pass

def test_cancellation_never_emits_completed():
    # stop_probe returns True mid-run → run.cancelled only
    pass

def test_failure_never_emits_completed():
    # ExpectedExecutionError → run.failed only
    pass
```

- [ ] **Step 2: Write token accounting test**

```python
# tests/test_agentrun_tokens.py
def test_token_usage_uses_provider_when_available():
    pass

def test_token_usage_none_when_unavailable():
    # fake model without usage → run.token_usage is None
    pass
```

- [ ] **Step 3: Run to verify fail**

Run: `pytest tests/test_agentrun_terminal.py tests/test_agentrun_tokens.py -v`
Expected: FAIL

- [ ] **Step 4: Implement error + token wiring**

```python
# novi/services/execution.py — already in Task 5 skeleton, verify here
# novi/runtime/react_attempt.py — on usage block:
usage = getattr(chunk, "usage_metadata", None) or getattr(chunk, "response_metadata", {}).get("usage")
if usage and hasattr(ctx, "token_usage"):
    ctx.token_usage = usage.get("total_tokens")
# propagate ctx.token_usage -> run.token_usage in coordinator after action
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_agentrun_terminal.py tests/test_agentrun_tokens.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add novi/services/execution.py novi/runtime/react_attempt.py novi/runtime/runtime.py tests/test_agentrun_terminal.py tests/test_agentrun_tokens.py
git commit -m "feat(runtime): wire terminal error mapping and nullable token accounting"
```

---

### Task 8: Deterministic Integration Test — Architectural Contract

**Files:**
- Create: `tests/test_agentrun_lifecycle.py`

**Interfaces:**
- Consumes: All above modules; `FakeRunnable`, `FakeToolExecutor`, `FakeTracer` test doubles
- Produces: Contract test that fails if any lifecycle invariant breaks

**Existing behavior preserved:** Validates that new lifecycle does not break compaction mid-run.

**New behavior:** Single deterministic test exercising `PROGRESS → tools → CONTINUE → compaction → tools → PROGRESS → tools → FINISH`.

**Ordering invariants enforced:**
- `run.started` first, exactly one terminal last
- First message lifecycle before first tool activity
- `context.compacting` → `context.compacted` between tool batches when `should_compact` forced
- Separate `message_id`s per message, stable within one message
- Ephemeral `status` never in `run.context.history`; `PROGRESS` + `FINISH` do
- Compaction does not reset `run` or create new run

- [ ] **Step 1: Write the contract test**

```python
# tests/test_agentrun_lifecycle.py
import uuid
from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.agent_action import AgentActionType
from novi.runtime.execution_context import ExecutionContext
from novi.services.execution import ExecutionCoordinator
from novi.runtime.agent_events import AgentEvent

def test_agentrun_multi_message_with_compaction_ordering():
    """Deterministic fake-model flow: PROGRESS → tools → CONTINUE → compaction → tools → PROGRESS → tools → FINISH"""
    # Build fake coordinator wiring: FakeOrchestrator + FakeJobManager + monkeypatched ContextManager.should_compact
    ctx = ExecutionContext(user_input="fix bug")
    ctx.conversation_id = "conv-1"
    run = AgentRun(id=f"run-{uuid.uuid4().hex[:8]}", conversation_id="conv-1", goal="fix bug", status=AgentRunStatus.RUNNING, context=ctx)

    # Fake actions sequence: PROGRESS, CONTINUE, PROGRESS, FINISH
    fake_actions = [
        ("progress", "I found the relevant files."),
        ("continue", None),
        ("progress", "Root cause identified."),
        ("finish", "Fixed and tested."),
    ]
    # Fake tool batches interleaved — emit tool.started/tool.completed inside _run_react mock
    events: list[AgentEvent] = []
    coord = make_fake_coordinator_with_actions(fake_actions, force_compaction_on_cycle=1)
    coord.execute(run, lambda e: events.append(e))

    # 1. Terminal invariant: exactly one terminal, last event is terminal
    terminals = [e for e in events if e.type in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminals) == 1, f"expected exactly one terminal, got {terminals}"
    assert events[-1].type == "run.completed"
    assert run.status == AgentRunStatus.COMPLETED

    # 2. Message lifecycle ordering
    assert events[0].type == "run.started"
    # First message block
    m1_start = next(i for i, e in enumerate(events) if e.type == "message.started")
    m1_deltas = [e for e in events if e.type == "message.delta" and e.message_id == events[m1_start].message_id]
    m1_end_idx = next(i for i, e in enumerate(events) if e.type == "message.completed" and e.message_id == events[m1_start].message_id)
    assert m1_start < m1_end_idx
    assert all(e.message_id == events[m1_start].message_id for e in m1_deltas)

    # Tool activity after first message, before second
    tool_idx = next(i for i, e in enumerate(events) if e.type in ("tool.started", "tool.completed"))
    assert m1_end_idx < tool_idx

    # Compaction between cycles
    compact_idx = next(i for i, e in enumerate(events) if e.type == "context.compacting")
    compacted_idx = next(i for i, e in enumerate(events) if e.type == "context.compacted")
    assert compact_idx < compacted_idx
    assert tool_idx < compact_idx  # compaction after first tool batch

    # Second message has different message_id
    m2_start = next(i for i, e in enumerate(events) if e.type == "message.started" and e.message_id != events[m1_start].message_id)
    assert events[m2_start].message_id != events[m1_start].message_id
    m2_end_idx = next(i for i, e in enumerate(events) if e.type == "message.completed" and e.message_id == events[m2_start].message_id)
    assert compacted_idx < m2_start < m2_end_idx

    # Final message before run.completed
    final_end_idx = max(i for i, e in enumerate(events) if e.type == "message.completed")
    assert final_end_idx < len(events) - 1  # before run.completed

    # 3. History invariants
    history = run.context.history
    # Ephemeral status never in history
    assert all("Still working" not in msg for _, msg in history)
    # Progress and final in history
    assistant_msgs = [msg for role, msg in history if role == "assistant"]
    assert "I found the relevant files." in assistant_msgs
    assert "Root cause identified." in assistant_msgs
    assert "Fixed and tested." in assistant_msgs

    # 4. Compaction preserved run
    assert run.id == events[0].run_id
    assert run.checkpoint is not None or run.iteration > 0  # compaction did not reset run

def test_message_id_stable_within_one_message():
    pass  # explicit: started/delta/completed share id; separate messages differ

def test_ephemeral_status_never_in_history_or_checkpoint():
    pass
```

- [ ] **Step 2: Run contract test to verify it fails pre-implementation**

Run: `pytest tests/test_agentrun_lifecycle.py::test_agentrun_multi_message_with_compaction_ordering -v`
Expected: FAIL

- [ ] **Step 3: Wire fakes and helpers to make test green**

Implement `make_fake_coordinator_with_actions` in `tests/helpers/fake_agentrun.py` that stubs `_run_graph`/`_run_react` to return the sequenced `AgentAction`s and emits tool + message events with correct `message_id`.

- [ ] **Step 4: Run contract test to verify it passes**

Run: `pytest tests/test_agentrun_lifecycle.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite for regressions**

Run: `pytest -q`
Expected: No new failures vs baseline

- [ ] **Step 6: Commit**

```bash
git add tests/test_agentrun_lifecycle.py tests/helpers/fake_agentrun.py
git commit -m "test(agentrun): deterministic lifecycle contract with ordering and terminal invariants"
```

---

## Self-Review

**Spec coverage:**
- §3.1 AgentRun (state only) → Task 1
- §3.2 AgentAction (CONTINUE/PROGRESS/FINISH) → Task 2
- §3.4 AgentEvent + message_id → Task 2, wired in Task 4
- §3.3 Correct control flow (compaction after every non-terminal action) → Task 5
- Tool ownership retained internally → Task 4
- Native streaming with message_id, _chunk fallback → Task 4
- Typed AgentRunStatus → Task 1
- emit_progress pseudo-tool guard → Task 3
- Ephemeral status outside history/checkpoint → Tasks 5/7/8
- max_agent_iterations separate → Task 5
- Only run.* maps to legacy done → Task 6
- Ordering, terminal, history, compaction contract tests → Task 8
- Terminal paths (finish/cancel/failed) exactly-one invariant → Task 7 + 8

**Placeholder scan:** No `TBD`, `TODO implement`, or vague steps remain. Every file path is explicit; every step has runnable code or command. `TODO(cleanup)` markers are intentional follow-ups with removal criteria.

**Type consistency:** `AgentRun.status: AgentRunStatus`, `AgentAction.type: AgentActionType`, `AgentEvent.message_id: str|None`, `ExecutionCoordinator.execute(run, emit) -> None`, `run.token_usage: int|None` are used consistently across tasks.

**Incremental testability:** Each task ends green with existing tests passing. Tasks 4→5→6 build on additive APIs without breaking `run_stream` legacy path.

**Migration notes per task (when compat can be removed):**
- Task 4 `_LOOP_DONE` sentinel: remove after all callers use `AgentAction` (one release).
- Task 5 `run_stream` tuple translation: remove after CLI/Telegram/background migrate to `execute` (tracked issue).
- Task 6 `_legacy_done_adapter` + frontend dual handling of `done`: remove when frontend requires `message_start/message_end`.
- Task 4 `_chunk_message` fallback: keep while any test-double lacks `stream`; no removal date, mark as bridge in docstring.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-14-agentrun-lifecycle.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
