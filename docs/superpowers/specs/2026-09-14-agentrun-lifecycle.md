# AgentRun Lifecycle Architecture

**Status**: Design — ready for review
**Date**: 2026-09-14
**Scope**: Beta — single foreground run per session; no persistence/restore, no concurrent runs

---

## 1. Problem Statement

Novi currently treats one user message → one complete agent execution → one final response as a single interaction. The goal: **Novi can emit a user-visible message, continue working (tools, reasoning, compaction), emit another message, and eventually finish the run** — all within one logical `AgentRun`.

Current event protocol:
```
token → thinking → tool_call → tool_result → status → plan → done
```

Target event protocol:
```
run.started
message.started
message.delta (streaming)
message.completed          ← Novi spoke; run continues
tool.started
tool.completed
status (ephemeral)
context.compacting
context.compacted
message.started
message.delta
message.completed
run.completed              ← Entire task finished
```

**Key invariant**: `message.completed ≠ run.completed`

---

## 2. Current Architecture (Preserved)

| Component | Responsibility | File |
|-----------|----------------|------|
| `ExecutionContext` | Unified per-run state (input, analysis, routing, grounding, tools, history, trace) | `novi/runtime/execution_context.py` |
| `ContextManager` | L1/L2/L3 compaction, budget management, `StableState` checkpointing | `novi/runtime/context_manager.py` |
| `ExecutionCoordinator` | Owns Task→Plan→Job lifecycle; drives `run_stream` with auto-continue (3x) | `novi/services/execution.py` |
| `RuntimeWorkflowGraph` | LangGraph: analyze → retrieve → reason → act → reflect → answer | `novi/graphs/runtime_graph.py` |
| `run_react_attempt` | Generic ReAct loop: model.stream → tool_calls → ToolExecutor → feedback | `novi/runtime/react_attempt.py` |
| `Job` / `Checkpoint` | Durable execution record; `checkpoint.step` = completed step count | `novi/jobs/job.py` |
| `NoviRuntime.run_stream` | Entry point; builds context, runs graph or ReAct, yields event tuples | `novi/runtime/runtime.py` |

**All of these stay.** The change is *who owns the outer loop* and *how lifecycle events are signaled*.

---

## 3. New Architecture

### 3.1 AgentRun — State Container Only

```python
# novi/runtime/agent_run.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from novi.runtime.execution_context import ExecutionContext
from novi.jobs.job import Checkpoint

@dataclass
class AgentRun:
    id: str
    conversation_id: str
    goal: str
    status: str  # "running" | "paused" | "completed" | "failed" | "cancelled"
    context: ExecutionContext
    checkpoint: Optional[Checkpoint] = None
    iteration: int = 0
    token_usage: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    @property
    def finished(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")
```

**No execution logic.** Pure state. `ExecutionCoordinator` operates on it.

### 3.2 AgentAction — Runtime Boundary Protocol

```python
# novi/runtime/agent_action.py
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any

class AgentActionType(Enum):
    CONTINUE = "continue"   # Model reasoned; no user message; continue silently
    PROGRESS = "progress"   # Model emitted user-visible progress message; continue
    FINISH = "finish"       # Model produced final answer; run ends

@dataclass
class AgentAction:
    type: AgentActionType
    message: Optional[str] = None          # For PROGRESS and FINISH
    metadata: Dict[str, Any] = field(default_factory=dict)
```

**Produced by**: `run_react_attempt` (yields) and `RuntimeWorkflowGraph.run()` (returns).
**Consumed by**: `ExecutionCoordinator.execute(run)`.

**Tool execution stays internal** to `run_react_attempt` / `RuntimeWorkflowGraph`. They emit `tool.started` / `tool.completed` events during execution, but the outer loop never sees a `TOOL` action.

### 3.3 ExecutionCoordinator — Execution Logic (Unchanged Ownership)

```python
# novi/services/execution.py — modified execute() signature
class ExecutionCoordinator:
    def execute(self, run: AgentRun, emit: Callable[[AgentEvent], None]) -> Iterator[AgentEvent]:
        """Drive one AgentRun to completion. Emits events via callback."""
        emit(AgentEvent(type="run.started", run_id=run.id, conversation_id=run.conversation_id))
        
        while not run.finished:
            # 1. Build/refresh ExecutionContext from run.context + run.checkpoint
            ctx = self._prepare_context(run)
            
            # 2. Execute one reasoning cycle (graph or ReAct)
            #    Internal tool execution happens here; tool.started/tool.completed emitted
            if run.context.execution_plan and run.context.execution_plan.plan:
                action = self._run_graph(ctx, run, emit)
            else:
                action = self._run_react(ctx, run, emit)
            
            # 3. Handle action
            if action.type == AgentActionType.CONTINUE:
                # Silent continuation — no user message
                pass
            
            elif action.type == AgentActionType.PROGRESS:
                # User-visible progress message
                message_id = f"msg-{uuid.uuid4().hex[:8]}"
                emit(AgentEvent(type="message.started", run_id=run.id, conversation_id=run.conversation_id, message_id=message_id))
                for chunk in self._chunk_message(action.message):
                    emit(AgentEvent(type="message.delta", run_id=run.id, conversation_id=run.conversation_id, message_id=message_id, message=chunk))
                emit(AgentEvent(type="message.completed", run_id=run.id, conversation_id=run.conversation_id, message_id=message_id, message=action.message))
                run.context.history.append(("assistant", action.message))
            
            elif action.type == AgentActionType.FINISH:
                # Final answer
                message_id = f"msg-{uuid.uuid4().hex[:8]}"
                emit(AgentEvent(type="message.started", run_id=run.id, conversation_id=run.conversation_id, message_id=message_id))
                for chunk in self._chunk_message(action.message):
                    emit(AgentEvent(type="message.delta", run_id=run.id, conversation_id=run.conversation_id, message_id=message_id, message=chunk))
                emit(AgentEvent(type="message.completed", run_id=run.id, conversation_id=run.conversation_id, message_id=message_id, message=action.message))
                run.context.history.append(("assistant", action.message))
                run.status = "completed"
                emit(AgentEvent(type="run.completed", run_id=run.id, conversation_id=run.conversation_id))
                break
            
            # 4. Check if run finished (FINISH handled above; error may set it)
            if run.finished:
                break
            
            # 5. Compaction check (delegated to ContextManager) — runs AFTER action handling
            if ContextManager.should_compact(ctx) in ("compact", "emergency"):
                emit(AgentEvent(type="context.compacting", run_id=run.id, conversation_id=run.conversation_id))
                ContextManager.compact_history(ctx)
                run.checkpoint = ContextManager.checkpoint_stable(ctx)
                emit(AgentEvent(type="context.compacted", run_id=run.id, conversation_id=run.conversation_id))
            
            # 6. Iteration + safety limits
            run.iteration += 1
            if run.iteration >= MAX_AGENT_ITERATIONS:
                run.status = "failed"
                emit(AgentEvent(type="run.failed", run_id=run.id, conversation_id=run.conversation_id, error="Max agent iterations exceeded"))
                break
        
        return

    def _chunk_message(self, text: str) -> Iterator[str]:
        """Split message into streaming chunks. Default: word-level."""
        for word in text.split():
            yield word + " "
```

### 3.4 Event Types — Transport-Agnostic

```python
# novi/runtime/agent_events.py
from dataclasses import dataclass
from typing import Optional, Any

@dataclass
class AgentEvent:
    type: str  # see EVENT_TYPES below
    run_id: str
    conversation_id: str
    # Optional payload fields per type
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

**Session (WebSocket)** provides `emit = lambda e: ws_queue.put(e)`.

**Message streaming lifecycle** (per message_id):
```
message.started   message_id=m1
message.delta     message_id=m1  (repeated per token/chunk)
message.delta     message_id=m1
message.completed message_id=m1

tool.started      (internal, no message_id)
tool.completed

message.started   message_id=m2
message.delta     message_id=m2
message.completed message_id=m2

run.completed
```

---

## 4. Integration Points

### 4.1 run_react_attempt → yields AgentAction

```python
# novi/runtime/react_attempt.py — modified yield at loop end
# Current: yield (_LOOP_DONE, final_text, stop_reason, success)
# New: yield AgentAction

# When model answers without tool calls:
if is_goal_complete(ctx, final):
    yield AgentAction(type=AgentActionType.FINISH, message=final)
else:
    # Model wants to continue silently
    yield AgentAction(type=AgentActionType.CONTINUE)

# When model calls tools:
# (tools execute, loop continues, eventually yields above)

# When model explicitly wants to update user:
# Detect via special tool_call or structured output → yield AgentAction(PROGRESS, message=...)
```

**Minimal change**: Replace `_LOOP_DONE` sentinel with `AgentAction` yield. All existing streaming (`token`, `reasoning`, `thinking`, `tool_call`, `tool_result`) stays identical.

### 4.2 RuntimeWorkflowGraph.run() → returns AgentAction

```python
# novi/graphs/runtime_graph.py — modified run() return
def run(self, state: dict) -> AgentAction:
    result = self._graph.invoke(state)
    # Map graph completion_reason + answer to AgentAction
    if result.get("completion_reason") == "completed":
        return AgentAction(type=AgentActionType.FINISH, message=result.get("answer", ""))
    elif result.get("completion_reason") == "max_steps":
        return AgentAction(type=AgentActionType.CONTINUE)  # or PROGRESS if partial answer
    else:
        return AgentAction(type=AgentActionType.FINISH, message=result.get("answer", ""))
```

Graph nodes unchanged. Only the final return type changes.

### 4.3 ContextManager — Unchanged

Compaction logic stays exactly as-is. `ExecutionCoordinator` calls:
```python
cm = ContextManager(model_name=ctx.model_name)
if cm.should_compact(ctx) in ("compact", "emergency"):
    emit(context.compacting)
    cm.compact_history(ctx)
    run.checkpoint = cm.checkpoint_stable(ctx)
    emit(context.compacted)
```

`StableState` already captures `goal`, `completed`, `errors`, `project_id`, `workspace_paths`, `current_step` — perfect for checkpointing.

### 4.4 Session — Thin WebSocket Bridge

```python
# novi/webui_server.py — Session.start_run()
def start_run(self, user_input: str, ...):
    run = AgentRun(
        id=f"run-{uuid.uuid4().hex[:8]}",
        conversation_id=self.current_conv_id,
        goal=user_input,
        status="running",
        context=ExecutionContext.from_input(user_input, ...),
    )
    self.current_run = run
    
    def emit(event: AgentEvent):
        # Map AgentEvent → WebSocket message
        ws_msg = self._map_agent_event_to_ws(event)
        if ws_msg:
            self._emit(ws_msg)
    
    # Run in worker thread
    def work():
        try:
            self.coordinator.execute(run, emit)
        except AgentCancelled:
            emit(AgentEvent(type="run.cancelled", run_id=run.id, conversation_id=run.conversation_id))
        except ExpectedExecutionError as e:
            run.status = "failed"
            emit(AgentEvent(type="run.failed", run_id=run.id, conversation_id=run.conversation_id, error=str(e)))
        except Exception:
            logger.exception("AgentRun failed")
            run.status = "failed"
            emit(AgentEvent(type="run.failed", run_id=run.id, conversation_id=run.conversation_id, error="Internal error"))
            raise  # Preserve for supervising boundary
        finally:
            self.current_run = None
    
    threading.Thread(target=work, daemon=True).start()

def _map_agent_event_to_ws(self, event: AgentEvent) -> Optional[dict]:
    """Translate AgentEvent to WebSocket protocol. No 'done' for intermediate messages."""
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
    # reasoning, plan.*, step.* → pass through or map as needed
    return None
```

**Session no longer contains the execution loop.** It creates `AgentRun`, provides `emit`, starts thread.

**Backward compatibility**: Legacy consumers (CLI, Telegram, background) continue calling `run_stream()` directly. `run_stream` internally delegates to `coordinator.execute()` with a no-op emit, collects final answer, returns as before. No ambiguous `done` for intermediate messages.

---

## 5. Migration Plan (Phased, Non-Breaking)

### Phase A — AgentRun Model + Action Type + Events (1-2 days)
- [ ] Add `novi/runtime/agent_run.py` with `AgentRun` dataclass
- [ ] Add `novi/runtime/agent_action.py` with `AgentActionType` (CONTINUE, PROGRESS, FINISH) + `AgentAction`
- [ ] Add `novi/runtime/agent_events.py` with `AgentEvent` + `EVENT_TYPES` (includes `message_id`)
- [ ] **No behavior change** — just new types available

### Phase B — ExecutionCoordinator.execute(run, emit) with Correct Control Flow (2-3 days)
- [ ] Add `execute(run, emit)` method alongside existing `run_stream`
- [ ] Implement loop: prepare context → run graph/ReAct → handle AgentAction → **compaction check** → iteration limit
- [ ] Wire `run_react_attempt` to yield `AgentAction` instead of `_LOOP_DONE`
- [ ] Wire `RuntimeWorkflowGraph.run()` to return `AgentAction`
- [ ] Keep `run_stream` working for backward compat (delegates to new `execute` internally)

### Phase C — Event Protocol + Session Integration (1-2 days)
- [ ] Update `Session` to create `AgentRun` and call `coordinator.execute(run, emit)`
- [ ] Implement `_map_agent_event_to_ws` with proper message streaming lifecycle (message_start, token, message_end, done)
- [ ] **No ambiguous intermediate `done`** — only `run.completed` maps to `done`
- [ ] Frontend: handle `message_start`/`token`/`message_end` for progressive messages

### Phase D — Progress Actions + Ephemeral Status (1 day)
- [ ] Add `emit_progress` pseudo-tool: model calls it to produce `AgentAction.PROGRESS`
- [ ] Add runtime ephemeral status: if no `message.completed` for 15s, emit `status: "Still working..."`
- [ ] Ensure ephemeral status **never** enters `ExecutionContext.history` or checkpoints

### Phase E — Error Handling + Token Accounting + Tests (1-2 days)
- [ ] Add `AgentCancelled`, `ExpectedExecutionError` exceptions; coordinator converts to terminal events
- [ ] Token accounting: prefer model/provider usage callbacks; tolerant of `None`
- [ ] Unit tests: `AgentRun` state transitions, control flow, compaction mid-run
- [ ] Integration test: deterministic fake-model producing PROGRESS → tools → CONTINUE → compaction → FINISH
- [ ] Manual QA: verify WebSocket events match target protocol

---

## 6. What Explicitly NOT in Beta

| Feature | Deferred To |
|---------|-------------|
| `AgentRun` persistence to disk | Post-beta |
| Restore `AgentRun` on reconnect | Post-beta |
| Multiple concurrent runs per session | Post-beta |
| Multiple Sessions observing one run | Post-beta |
| Background run unification | Post-beta |
| Immutable `ExecutionContext` snapshots | Post-beta (if needed) |

---

---

## 7. File Changes Summary

| File | Change Type |
|------|-------------|
| `novi/runtime/agent_run.py` | **New** — AgentRun dataclass |
| `novi/runtime/agent_action.py` | **New** — AgentActionType, AgentAction |
| `novi/runtime/agent_events.py` | **New** — AgentEvent, EVENT_TYPES |
| `novi/services/execution.py` | **Modify** — add `execute(run, emit)` with correct control flow |
| `novi/runtime/react_attempt.py` | **Modify** — yield `AgentAction` instead of `_LOOP_DONE`; add `emit_progress` pseudo-tool |
| `novi/graphs/runtime_graph.py` | **Modify** — `run()` returns `AgentAction`; internal tool execution unchanged |
| `novi/webui_server.py` | **Modify** — `Session.start_run()` creates `AgentRun`, calls `coordinator.execute()`, maps events |
| `novi/runtime/runtime.py` | **Minor** — `run_stream` delegates to coordinator for compat |
| `novi/runtime/context_manager.py` | **No change** — compaction logic reused as-is |

---

## 8. Success Criteria

1. **Single user message** → Novi emits `message.completed` → continues with tools → emits another `message.completed` → finishes with `run.completed`
2. **Context compaction** mid-run emits `context.compacting` → `context.compacted` → run continues seamlessly
3. **No conversation pollution**: ephemeral `status` events never appear in history/checkpoints
4. **Backward compat**: existing CLI, Telegram, background runs unchanged (they use `run_stream` directly)
5. **Frontend**: shows progressive messages in one conversation thread, "done" only at `run.completed`

---

## 9. Open Questions — **Resolved**

| Question | Decision |
|----------|----------|
| **Progress detection** | `emit_progress` pseudo-tool (runtime-internal, no permission needed). Model calls it when it has meaningful info to communicate. Description: *"Use only when you have discovered meaningful information worth communicating before continuing. Do not narrate routine actions or announce every tool call."* |
| **Max iterations** | Separate `max_agent_iterations` config (default: 20). Distinct from `max_steps` (internal plan/graph limit). |
| **Error handling** | Coordinator catches `AgentCancelled` → `run.cancelled`; `ExpectedExecutionError` → `run.failed` with message; unexpected `Exception` → log, `run.failed` with generic message, **re-raise** for supervising boundary. |
| **Token accounting** | Prefer model/provider usage callbacks as authoritative source. `AgentRun.token_usage: int | None` — tolerant of missing data. `ctx.trace.steps` for tracing only, not accounting. |

## 10. Success Criteria (Updated)

1. **Single user message** → Novi emits `message.started` → `message.delta*` → `message.completed` → continues with tools → emits another message cycle → finishes with `run.completed`
2. **Context compaction** mid-run emits `context.compacting` → `context.compacted` → run continues seamlessly (compaction check runs **after** action handling each cycle)
3. **No conversation pollution**: ephemeral `status` events never appear in history/checkpoints
4. **Tool ownership unchanged**: `run_react_attempt` / `RuntimeWorkflowGraph` own tool execution; emit `tool.started`/`tool.completed` internally
5. **Message IDs**: each `message.started`/`delta`/`completed` carries `message_id` for frontend correlation
6. **Backward compat**: existing CLI, Telegram, background runs unchanged (they use `run_stream` directly)
7. **Frontend**: shows progressive messages in one conversation thread, `done` only at `run.completed`

## 11. Deterministic Integration Test (Architectural Contract)

```python
def test_agentrun_multi_message_with_compaction():
    """Fake model produces: PROGRESS → tools → CONTINUE → compaction → tools → PROGRESS → tools → FINISH"""
    run = AgentRun(...)
    events = []
    
    def fake_emit(e): events.append(e)
    
    coordinator.execute(run, fake_emit)
    
    # Assert event sequence
    assert events[0].type == "run.started"
    
    # First progress message
    assert events[1].type == "message.started"
    assert events[2].type == "message.delta"
    assert events[3].type == "message.completed"
    msg1_id = events[1].message_id
    
    # Tool activity
    assert any(e.type == "tool.started" for e in events)
    assert any(e.type == "tool.completed" for e in events)
    
    # Compaction
    assert any(e.type == "context.compacting" for e in events)
    assert any(e.type == "context.compacted" for e in events)
    
    # Second progress message (different message_id)
    msg2_events = [e for e in events if e.message_id != msg1_id and e.type.startswith("message.")]
    assert any(e.type == "message.started" for e in msg2_events)
    assert any(e.type == "message.completed" for e in msg2_events)
    
    # Final
    assert events[-1].type == "run.completed"
    assert run.status == "completed"
    
    # No status events in history
    history = run.context.history
    assert all("Still working" not in msg for _, msg in history)
    
    # Progress and final messages in history
    assistant_msgs = [msg for role, msg in history if role == "assistant"]
    assert len(assistant_msgs) >= 3  # progress1, progress2, final
```

---

**Next step**: Review this design. On approval, I'll invoke `writing-plans` to create the implementation plan.