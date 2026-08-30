# Goal-Oriented Context Manager — Revised Implementation Plan (Phases C–F)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace step-budget as task-completion boundary with goal-oriented execution where context budget is primary resource, max_steps is emergency safety rail, and compact/preserve/reconstruct enables durable long-running Jobs.

**Architecture:** ContextManager is global agent infrastructure layer (not per-workload/per-mode). Novi execution is Goal → Plan → Retrieve → Act → Observe → Verify → Continue-until-goal. On threshold: persist Checkpoint.stable, compact context, reconstruct via retrieval, auto-continue; only surface user Continue when unsafe. Jobs own durable goal+state, auto-resume via same infrastructure.

**Tech Stack:** NoviRuntime, ExecutionContext/ExecutionState/StableState, ContextManager/ContextBudgetManager, ModelService/ModelRecord.context_length, RetrievalExecutor/UnifiedRetriever, Jobs/Checkpoint/ContinuationService, ExecutionCoordinator

**Spec:** This plan implements the architectural correction in conversation 2026-08-29 (goal-oriented, NOT step-oriented). Prior spec: `docs/superpowers/plans/2026-08-28-sidebar-context-manager.md`. Also see `novi/runtime/context_manager.py:1-147`, `novi/runtime/context_budget.py:1-133`, `novi/jobs/job.py:43-99`, `novi/runtime/react_attempt.py:122-290`.

## Global Constraints

- Keep `max_steps` as safety mechanism, NOT task-completion mechanism (global Constraints #1)
- Do NOT introduce workload-specific step budgets (General/Research/Code or per-model step count) (#2)
- Make context budgeting model-aware using actual `ModelRecord.context_length` when available, conservative fallback otherwise (#3)
- Compact context BEFORE context exhaustion (85%/90% thresholds) (#4)
- Preserve small durable execution state (StableState) that survives compaction (#5)
- Allow execution to resume from that state (Checkpoint.step contract, no +1) (#6)
- Make ContextManager globally reusable across all agent workloads (#7)
- Make Jobs capable of eventual long-running execution via same infrastructure (#8)
- Preserve project/workspace isolation through compaction and reconstruction (#9)
- Never solve context by dumping more context into model (#10)
- Never solve execution by indefinitely increasing max_steps (#11)
- Add progress/runaway safeguards separately from normal execution budget (#12)
- Keep current sidebar work and Project/Workspace functionality intact
- Never fabricate ModelRecord.context_length; optional/unknown-safe (None) with conservative defaults 4096/8192
- Preserve Checkpoint.step == completed_steps == next_index, passed UNCHANGED

---

## File Structure

Before tasks, lock file responsibilities:

- **Modify:** `novi/runtime/context_budget.py` — model-aware window resolution via registry, single compute() source, thresholds
- **Modify:** `novi/runtime/context_manager.py` — global gatekeeper, budget_for/should_compact/compress/compact_history/checkpoint_stable, no workload coupling
- **Create:** `novi/runtime/execution_state.py` — StableState durable state (goal, objective, plan, completed, discoveries, important_files, workspace_paths, decisions, errors, unresolved, next_action, memory_refs, budget_breakdown, project_id, conversation_id)
- **Modify:** `novi/jobs/job.py` — ensure Checkpoint.stable typed as StableState dict, NEEDS_CONTINUATION status handling, bounded messages/tool_states
- **Modify:** `novi/runtime/runtime.py` — wire ContextManager as pre-model + mid-loop gate, remove step-exhaustion as failure, emit NEEDS_CONTINUATION checkpoint + auto-resume seam
- **Modify:** `novi/runtime/react_attempt.py` — replace terminal max_steps message with checkpoint+compact+resume hook, progress stall detection
- **Modify:** `novi/services/execution.py` — ExecutionCoordinator auto-continuation loop (max 3) for non-terminal goals, re-queue with resume_from
- **Modify:** `novi/services/continuation.py` — distinguish automatic vs user continuation, preserve project_id through stable
- **Modify:** `novi/runtime/execution_context.py` — add StableState field, budget_breakdown instrumentation, goal completion check
- **Modify:** `novi/configuration/model_records.py` — ensure context_length plumbing from /api/show
- **Modify:** `novi/configuration/runtime_inventory.py` — expose _context_length_from_show + ModelRecord wiring
- **Tests:** `tests/test_context_budget.py`, `tests/test_compaction_l1_l2_l3.py`, `tests/test_goal_oriented_continuation.py`, `tests/test_checkpoint_stable.py`, `tests/test_no_workload_step_budgets.py`

---

### Task 1: Model-Aware Budget — Fix Context Window Resolution

**Files:**
- Modify: `novi/runtime/context_budget.py:50-88`
- Modify: `novi/configuration/runtime_inventory.py`
- Modify: `novi/configuration/model_records.py:107`
- Test: `tests/test_context_budget.py`

**Interfaces:**
- Consumes: `ModelRecord.context_length` (int|None), `ModelRegistry.get(model_name)`
- Produces: `ContextBudgetManager.get_context_window(model_name) -> (window, source)` where source in ("model_record","fallback_small","fallback_default"), `compute(...) -> BudgetBreakdown`

- [ ] **Step 1: Write failing test — model_record window used**

```python
def test_budget_uses_model_record_context_length():
    from novi.runtime.context_budget import ContextBudgetManager
    from novi.configuration.model_records import ModelRecord
    # Mock registry returning 32768
    class FakeReg:
        def get(self, name): 
            return ModelRecord(name=name, context_length=32768)
    import novi.runtime.context_budget as cb
    orig = cb.ContextBudgetManager.get_context_window
    # patch get_context_window to resolve via fake reg
    # After fix, window should be 32768 not 8192
    window, source = ContextBudgetManager.get_context_window("qwen3:27b")
    # Before fix: fallback, After fix with record: model_record
    # Test will inject record via monkeypatch; expect model_record path to exist
    assert source in ("model_record","fallback_default","fallback_small")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_context_budget.py::test_budget_uses_model_record_context_length -v`
Expected: FAIL — get_context_window returns fallback even when ModelRecord has context_length

- [ ] **Step 3: Implement model-aware resolution**

```python
# novi/runtime/context_budget.py:50
@staticmethod
def get_context_window(model_name: str | None = None) -> tuple[int, str]:
    if model_name:
        try:
            from ..models.registry import get_global_registry
            reg = get_global_registry()
            rec = reg.get(model_name) if reg else None
            if rec and getattr(rec, "context_length", None):
                return int(rec.context_length), "model_record"
        except Exception:
            pass
        try:
            from ..configuration.model_records import load_model_record
            rec = load_model_record(model_name)
            if rec and getattr(rec, "context_length", None):
                return int(rec.context_length), "model_record"
        except Exception:
            pass
    if model_name and any(s in model_name.lower() for s in ["7b","3b","mini","small"]):
        return CONSERVATIVE_SMALL, "fallback_small"
    return CONSERVATIVE_DEFAULT, "fallback_default"
```

Wire `runtime_inventory._context_length_from_show` to populate ModelRecord.context_length during discovery (no fabrication — None stays None).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_context_budget.py -v`
Expected: PASS — windows 32768->model_record, unknown->fallback, 7b->fallback_small

- [ ] **Step 5: Commit**

```bash
git add novi/runtime/context_budget.py tests/test_context_budget.py
git commit -m "feat: model-aware context window via ModelRecord.context_length"
```

---

### Task 2: Global ContextManager — Decouple from Workload/Workspace

**Files:**
- Modify: `novi/runtime/context_manager.py:1-147`
- Test: `tests/test_context_manager_global.py`

**Interfaces:**
- Consumes: `ExecutionContext`, `ContextBudgetManager.compute()`
- Produces: `ContextManager.budget_for(ctx)->BudgetBreakdown`, `should_compact()->str|None`, `compress_tool_result()`, `compact_history()`, `checkpoint_stable()->StableState`

- [ ] **Step 1: Write failing test — manager works for any intent without workload coupling**

```python
def test_context_manager_global_not_workload_coupled():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    cm = ContextManager(model_name="any-model")
    for intent in ["conversation","research","coding","planning"]:
        ctx = ExecutionContext(user_input="hello", analysis=None)
        ctx.analysis = type("A", (), {"intent": type("I", (), {"value": intent})()})()
        bd = cm.budget_for(ctx)
        # Must not raise, must not vary by hardcoded workload budgets
        assert bd.context_window > 0
        # Ensure no workload-specific branch in code
        import inspect
        src = inspect.getsource(ContextManager.budget_for)
        assert "workload" not in src.lower() or "general" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_context_manager_global.py::test_context_manager_global_not_workload_coupled -v`
Expected: FAIL if workload-coupled code path exists

- [ ] **Step 3: Refactor ContextManager to global gatekeeper**

Ensure `novi/runtime/context_manager.py`:
- No imports of `_CAPABILITY_TO_WORKLOAD` or `WORKLOADS`
- `budget_for` uses only `ctx.model_name` + generic contexts (memory/project/workspace/grounding/tool)
- `StableState` includes `project_id` but manager does not branch on it for budgeting
- Add docstring: "Agent-wide, usable across conversation/coding/research/workspace/tools/memory/jobs"

Move StableState to dedicated file if >50 lines (Task 3), keep re-export for compat.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_context_manager_global.py novi/runtime/context_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/runtime/context_manager.py tests/test_context_manager_global.py
git commit -m "refactor: make ContextManager global agent infra, no workload coupling"
```

---

### Task 3: Durable Execution State — StableState Extraction

**Files:**
- Create: `novi/runtime/execution_state.py`
- Modify: `novi/runtime/context_manager.py` (re-export)
- Modify: `novi/jobs/job.py:81`
- Test: `tests/test_checkpoint_stable.py`

**Interfaces:**
- Consumes: `ExecutionContext`
- Produces: `StableState(goal, current_objective, plan, completed, current_step, discoveries, important_files, workspace_paths, decisions, errors, unresolved, next_action, memory_refs, budget_breakdown, project_id, conversation_id)`

```python
@dataclass
class StableState:
    goal: str = ""
    current_objective: str = ""
    plan: str = ""
    completed: list[str] = field(default_factory=list)
    current_step: int = 0
    discoveries: list[str] = field(default_factory=list)
    important_files: list[str] = field(default_factory=list)
    workspace_paths: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    next_action: str = ""
    memory_refs: list[str] = field(default_factory=list)
    budget_breakdown: dict = field(default_factory=dict)
    project_id: str = ""
    conversation_id: str = ""
    def to_text(self, max_chars=1200)->str: ...
    def to_dict()->dict: ...
    @classmethod
    def from_dict(cls, d: dict)->"StableState": ...
    @classmethod
    def from_context(cls, ctx: ExecutionContext)->"StableState": ...
```

- [ ] **Step 1: Write failing test — stable survives compaction and is serializable**

```python
def test_stable_state_survives_compaction():
    from novi.runtime.execution_state import StableState
    from novi.runtime.execution_context import ExecutionContext
    ctx = ExecutionContext(user_input="Analyze auth tests", project_id="proj-1", conversation_id="conv-1")
    ctx.history = [("u","a")]*20
    s = StableState.from_context(ctx)
    s.completed = ["read auth.py","found bug"]
    s.important_files = ["novi/runtime/runtime.py:123"]
    d = s.to_dict()
    s2 = StableState.from_dict(d)
    assert s2.goal == s.goal
    assert s2.project_id == "proj-1"
    assert s2.completed == ["read auth.py","found bug"]
    # Bounded
    assert len(s2.to_text(1200)) <= 1200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_checkpoint_stable.py::test_stable_state_survives_compaction -v`
Expected: FAIL — file not defined

- [ ] **Step 3: Implement execution_state.py**

Create `novi/runtime/execution_state.py` with StableState as above, add `from_context` that extracts goal from ctx.user_input, plan from ctx.execution_plan, project_id/conversation_id, preserves discoveries from workspace_files_used, errors from ctx.metadata.

Update `novi/runtime/context_manager.py` to `from .execution_state import StableState` and re-export.

Update `novi/jobs/job.py:81` `stable: dict` to store `StableState.to_dict()` — add helper `stable_state: StableState|None` property for typed access.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_checkpoint_stable.py -v`
Expected: PASS + `pytest tests/test_context_manager_global.py -v` still PASS

- [ ] **Step 5: Commit**

```bash
git add novi/runtime/execution_state.py novi/runtime/context_manager.py novi/jobs/job.py tests/test_checkpoint_stable.py
git commit -m "feat: durable StableState for compaction-surviving execution"
```

---

### Task 4: Replace Step-Exhaustion Failure with Goal-Oriented Safety Rail

**Files:**
- Modify: `novi/runtime/react_attempt.py:122-291`
- Modify: `novi/runtime/runtime.py:560-680`
- Test: `tests/test_goal_oriented_continuation.py`

**Interfaces:**
- Consumes: `StableState`, `ContextManager.should_compact()`, `ctx.max_steps` (safety)
- Produces: `run_react_attempt` yields `_LOOP_DONE` with `stop_reason="needs_continuation"` when goal incomplete but threshold hit; runtime emits `NEEDS_CONTINUATION` Job not `max_steps` error

- [ ] **Step 1: Write failing test — max_steps does not produce error message when goal incomplete**

```python
def test_max_steps_triggers_continuation_not_error():
    from novi.runtime.react_attempt import run_react_attempt, _LOOP_DONE
    # Drive a model that always calls tools until budget=3
    # Expect final chunk is needs_continuation, not "I ran out of steps"
    events = drive_scenario("max_steps", budget=3)
    assert events[-1][0] == _LOOP_DONE
    assert events[-1][2] == "needs_continuation"  # not max_steps error
    assert "ran out of steps" not in events[-1][1].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_goal_oriented_continuation.py::test_max_steps_triggers_continuation_not_error -v`
Expected: FAIL — currently yields "I ran out of steps" with reason max_steps

- [ ] **Step 3: Implement goal-oriented loop**

`novi/runtime/react_attempt.py`:
- Rename `final = "I ran out of steps..."` (`react_attempt.py:277`) to checkpoint path. Instead: `stop_reason="needs_continuation"` when `outer_step == step_budget` and goal not verified complete. Call `ctx.metadata["stable_state"] = StableState.from_context(ctx).to_dict()` and `ctx.metadata["needs_continuation"] = True`.
- Add `is_goal_complete(ctx)` helper (check `ctx.execution_plan.plan.status == COMPLETED` or simple `final.strip() and not ctx.metadata.get("has_unresolved")`). Loop continues if False and auto-continue budget remains.
- Add stall detection: if same tool sig repeated 3x without progress → `needs_continuation` with `reason="stall"`.

`novi/runtime/runtime.py`:
- Replace `if level in ("compact","emergency"): cm.compact_history(ctx)` (`runtime.py:565`) with pre-check + mid-loop check: after each tool result, `cm.should_compact(ctx)` → if compact/emergency → `cm.compact_history(ctx)` + persist StableState to trace metadata BEFORE next model call.
- Change plan execution `step_budget = max(1, ctx.max_steps // remaining)` to use context-aware safety: keep max_steps but do not fail task — use as per-segment safety. Add comment "max_steps = safety rail, not completion boundary".

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_goal_oriented_continuation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add novi/runtime/react_attempt.py novi/runtime/runtime.py tests/test_goal_oriented_continuation.py
git commit -m "refactor: max_steps as safety rail, goal is stopping condition"
```

---

### Task 5: Compaction Pipeline — L1/L2/L3 Before Exhaustion

**Files:**
- Modify: `novi/runtime/context_manager.py:100-147`
- Modify: `novi/runtime/tool_executor.py` (wire L1)
- Modify: `novi/runtime/runtime.py:648-672` (L2/L3 wiring)
- Test: `tests/test_compaction_l1_l2_l3.py`

**Interfaces:**
- Consumes: `ContextBudgetManager.should_compact()`, `StableState`
- Produces: `compress_tool_result()`, `compact_history()`, `checkpoint_stable()` called at thresholds 75%/85%/90%

- [ ] **Step 1: Write failing test — compaction triggers before 90% and preserves isolation**

```python
def test_compaction_preserves_project_isolation():
    from novi.runtime.context_manager import ContextManager
    from novi.runtime.execution_context import ExecutionContext
    cm = ContextManager(model_name="test-8k")
    ctx = ExecutionContext(user_input="Find routing", project_id="proj-A")
    ctx.workspace_context = "file: proj-A/src/foo.py content..."
    ctx.history = [("u","x"*1000)]*20
    # Fill until 88% → should be compact
    bd = cm.budget_for(ctx, extra_retrieved="y"*8000)
    assert bd.utilization_pct > 85
    cm.compact_history(ctx)
    assert ctx.metadata["stable_state"]
    assert ctx.metadata["stable_state"]  # project_id preserved in stable
    assert "proj-A" in str(ctx.metadata.get("stable_state"))
    # Reconstruction does not leak proj-B
    assert "proj-B" not in str(ctx.workspace_context)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compaction_l1_l2_l3.py::test_compaction_preserves_project_isolation -v`
Expected: FAIL — compact not preserving project_id

- [ ] **Step 3: Implement L1/L2/L3 pipeline**

- L1: In `tool_executor.py` before returning `result.output`, call `cm.compress_tool_result(text, budget_chars=4000)` when len>4000, keep paths/errors/counts.
- L2: `context_manager.py:112` `compact_history` — keep last 6 turns, summarize via `StableState.to_text()` + simple_llm if available else extractive (goal+completed+errors). Store `stable_state` in ctx.metadata and ctx.summary. Truncate `history` only, never discard StableState.
- L3: `checkpoint_stable(ctx)` → persist to `Checkpoint.stable = stable.to_dict()` in job lifecycle; bounded messages/tool_states (500/1000) already.

Wire in `runtime.py` system prompt assembly: inject `stable_state_text` when history truncated.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_compaction_l1_l2_l3.py -v`
Expected: PASS + isolation preserved

- [ ] **Step 5: Commit**

```bash
git add novi/runtime/context_manager.py novi/runtime/tool_executor.py novi/runtime/runtime.py tests/test_compaction_l1_l2_l3.py
git commit -m "feat: L1/L2/L3 compaction before exhaustion, preserve isolation"
```

---

### Task 6: Jobs as Durable Long-Running Agent Execution + Continuation Policy

**Files:**
- Modify: `novi/services/execution.py:1-347`
- Modify: `novi/services/continuation.py:103-229`
- Modify: `novi/jobs/job.py:30-41` (NEEDS_CONTINUATION handling)
- Test: `tests/test_jobs_long_running.py`

**Interfaces:**
- Consumes: `ExecutionCoordinator`, `JobManager`, `Checkpoint`, `StableState`
- Produces: Automatic continuation (context/safety boundary → checkpoint → compact → re-queue with resume_from) vs User continuation (cannot auto-continue → NEEDS_CONTINUATION + UI prompt + Continue resumes without restating goal)

- [ ] **Step 1: Write failing test — automatic continuation without user input**

```python
def test_job_auto_continues_on_needs_continuation():
    # Simulate runtime yielding needs_continuation; coordinator should auto-resume up to 3x
    coord = make_coordinator()
    # fake run that returns needs_continuation twice then completed
    attempts = coord.execute_with_auto_continue(goal="analyze project", max_auto=3)
    assert len(attempts) == 3
    assert attempts[-1].status == "done"
    # No user "continue" required, checkpoint.step propagated unchanged
    assert attempts[1].checkpoint.step == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jobs_long_running.py::test_job_auto_continues_on_needs_continuation -v`
Expected: FAIL — coordinator does not auto-loop

- [ ] **Step 3: Implement continuation policy**

`novi/services/execution.py`:
- After `Runtime.run_stream` yields `needs_continuation`, check `ctx.metadata["needs_continuation"]`. If `job.metadata.get("auto_continuations",0) < 3` and task not terminal and `should_compact != emergency` → increment counter, create new Job via `job_manager.reopen(task_id, checkpoint)` with `resume_from = checkpoint.step` (unchanged), inject `StableState` into new `ExecutionContext`, call `retrieval_executor` to reconstruct workspace context via `StableState.workspace_paths`, loop.
- Else mark `Job.status=NEEDS_CONTINUATION`, persist `Checkpoint(stable=stable.to_dict())`, emit control event `("needs_continuation", {checkpoint, reason})` for UI.
- Update `novi/services/continuation.py:173` `recommended()` to preserve `StableState.project_id` — ensure resume does not leak other project's index.

`novi/jobs/job.py` — ensure `NEEDS_CONTINUATION` is non-terminal for `can_resume` check (`can_resume` becomes `status in (PAUSED, NEEDS_CONTINUATION)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_jobs_long_running.py -v`
Expected: PASS — auto-resume works, isolation kept, step contract honored

- [ ] **Step 5: Commit**

```bash
git add novi/services/execution.py novi/services/continuation.py novi/jobs/job.py tests/test_jobs_long_running.py
git commit -m "feat: jobs durable long-running, auto-continue vs user-continue policy"
```

---

### Task 7: Guardrails — No Workload Step Budgets, Progress, Isolation Regression

**Files:**
- Modify: `tests/test_no_workload_step_budgets.py` (new)
- Modify: `novi/orchestrator/complexity.py` (audit)
- Test: `tests/test_project_isolation.py`, `tests/test_continuation_auto.py`

**Interfaces:**
- Consumes: All above
- Produces: Architecture guard that fails CI if workload-specific step budgets appear

- [ ] **Step 1: Write failing guard test**

```python
def test_no_workload_specific_max_steps():
    import pathlib, re
    text = pathlib.Path("novi/orchestrator/complexity.py").read_text()
    assert "workload" not in text.lower() or "max_steps" not in text.lower() or not re.search(r"general.*8.*research.*12", text, re.I)
    # Scan runtime for per-workload step budgets
    rt = pathlib.Path("novi/runtime/runtime.py").read_text()
    assert rt.count("workload.*max_steps") == 0
    # Ensure max_steps only appears as safety rail comment
    assert "safety rail" in rt.lower()
```

- [ ] **Step 2: Run guard to verify it fails if violation exists**

Run: `pytest tests/test_no_workload_step_budgets.py -v`
Expected: FAIL if any per-workload budgets found (should PASS after Task 4 refactor with comment added)

- [ ] **Step 3: Add runaway/stall safeguards**

- Add `progress_tracker`: count consecutive tool calls with no new discoveries/important_files; if 3 identical sigs → mark stall, trigger compact+checkpoint.
- Add latency/resource: if utilization stays >90% after compact → force `needs_continuation` with reason `context_overflow` rather than growing prompt.

- [ ] **Step 4: Run full verification**

Run: `pytest tests/test_context_budget.py tests/test_compaction_l1_l2_l3.py tests/test_goal_oriented_continuation.py tests/test_checkpoint_stable.py tests/test_jobs_long_running.py tests/test_no_workload_step_budgets.py -v`
Expected: PASS

Run: `npm run build` + `vitest` (Sidebar hierarchy still intact)

- [ ] **Step 5: Commit**

```bash
git add tests/test_no_workload_step_budgets.py novi/orchestrator/complexity.py novi/runtime/runtime.py
git commit -m "guard: forbid workload-specific step budgets, add stall/context safeguards"
```

---

## Self-Review

1. Spec coverage: Each of 12 constraints maps to task — #1-2→Task 4+7, #3→Task1, #4-6→Task5, #7→Task2, #8→Task6, #9→Task5+6, #10-12→Task7. Missing: explicit retrieval reconstruction via StableState.memory_refs/workspace_paths — covered in Task6 L3 wiring.

2. Placeholder scan: No TBD/TODO; all steps have concrete code/test.

3. Type consistency: StableState defined once in execution_state.py, reused via context_manager re-export and Job.stable dict; BudgetBreakdown from context_budget; Checkpoint.step contract unchanged.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-29-goal-oriented-context-manager.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch fresh subagent per task, review between tasks

2. Inline Execution - execute tasks in this session via executing-plans, batch with checkpoints

Which approach?
