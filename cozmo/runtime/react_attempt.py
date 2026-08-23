"""Generic single-attempt ReAct executor (Phase 9B; sole loop since 9C).

The loop body formerly inline in ``CozmoRuntime._run_agent_loop``, moved
VERBATIM into a runtime-owned collaborator so callers other than that
historical entry point can drive one implement/execution attempt:

    model.stream → tool_calls? → dedup gate → ToolExecutor.execute
                 → ToolMessage feedback → recovery escalation → repeat
                 ↘ no calls / budget exhausted / stop / error → outcome

Since Phase 9C this is the ONLY generic ReAct loop: the historical
``CozmoRuntime._run_agent_loop`` wrapper was retired and every consumer —
sequential planned steps, the unplanned path, and the coding graph's
``run_loop`` collaborator — drives ``run_react_attempt`` directly.

Ownership boundaries (unchanged):

* Model execution  — the ALREADY-bound runnable is passed in; nothing here
  resolves, selects, or substitutes models. Rebinding during search recovery
  goes exclusively through the caller-supplied ``bind_runnable`` seam.
* Tool execution   — every call flows through the injected ToolExecutor
  (permission/risk/validation/coordinator pipeline). Nothing here executes
  tools itself.
* Cancellation     — owned by the runtime: ``stop_probe()`` is evaluated live
  at each checkpoint (mid-stream, pre-call, post-result) so an event swap via
  ``set_config(stop_event=...)`` is honored exactly as before.
* Persistence      — none. Traces/metrics are appended to the caller's
  ``ctx.trace``; no storage is touched.

Event contract (frozen; consumers replay these tuples verbatim):
    ("reasoning", text) ("token", piece)
    ("thinking", title, detail, None)
    ("tool_call", name, args, call_id, category)
    ("tool_result", name, output, call_id, diff)
    (_LOOP_DONE, final_text, stop_reason, success)

The function is a lazy generator: nothing executes until iterated, and any
internal failure is converted to the ``(…, "error", False)`` sentinel rather
than raised — identical to the pre-extraction behavior.
"""

import json
import time

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from .retrieval import RecoveryAction
from .trace import DebugTraceEvent, StepTrace

# Sentinel emitted at the end of every attempt carrying the loop outcome.
# Payload: (_LOOP_DONE, final_text, stop_reason, success). Re-exported by
# cozmo.runtime.runtime so existing consumers keep their import path.
_LOOP_DONE = "__plan_step_done__"


def run_react_attempt(
    *,
    ctx,
    runnable,
    tool_executor,
    tracer,
    retrieval_executor,
    capability_registry,
    scan_skills,
    skill_block,
    bind_runnable,
    stop_probe,
    event_bus=None,
    debug_trace=False,
    step_budget,
    base_msgs,
    step=None,
    step_index_base=0,
    seed_seen=None,
):
    """Run ONE ReAct attempt; yields streaming events, ends with _LOOP_DONE.

    Collaborators (all runtime-owned, injected at call time):

    ``ctx``                  ExecutionContext for THIS run; the loop mutates
                             ``ctx.trace.steps``, ``ctx.activated_skills``
                             and ``ctx.allowed_tools`` exactly as before.
    ``runnable``             The bound LangChain runnable to stream from.
    ``tool_executor``        Sole tool-execution pipeline (extract_calls /
                             execute / tool_category / compute_diff).
    ``tracer``               RuntimeTracer (finalize / debug / record_tool).
    ``retrieval_executor``   Recovery authority (recommend_* / commit_recovery).
    ``capability_registry``  Search-tool name resolution for escalations.
    ``scan_skills``          Callable(text, already) -> newly activated skills.
    ``skill_block``          Callable(skill) -> rendered skill prompt block.
    ``bind_runnable``        The single binding seam (ModelService → runtime);
                             used ONLY by search-recovery rebinding.
    ``stop_probe``           Zero-arg callable; True ⇒ user cancellation.
                             Read LIVE at every checkpoint.
    ``event_bus``            Optional lifecycle bus (tool_called/tool_result).
    ``debug_trace``          Enables DebugTraceEvent capture in ctx.trace.
    ``step_budget``          Hard bound on reason→act rounds (max steps).
    ``base_msgs``            Seed messages (system/history/human); copied.
    ``step``                 Optional PlanStep whose objective becomes a
                             trailing system instruction (sequential path).
    ``step_index_base``      Offsets StepTrace indexing across plan steps.
    ``seed_seen``            Pre-populates the exact-call dedup set so a
                             repair attempt cannot repeat an identical
                             MUTATING call from a previous attempt (Phase 8F;
                             callers ACCUMULATE signatures across attempts).

    Future checkpoint execution: the loop is index-addressed and idempotent
    per step. A future Job-driven executor (which already owns Checkpoint
    step/messages/tool_states in cozmo/jobs) can resume by feeding
    ``Checkpoint.step`` as ``step_index_base`` and the restored messages as
    ``base_msgs`` — no re-planning and no new planning logic needed here.
    Runtime stays the generic per-step executor.
    """
    msgs = list(base_msgs)
    if step is not None:
        msgs.append(SystemMessage(
            content=f"CURRENT STEP ({step.id}): {step.description}"
        ))
    seen_calls: set[str] = set(seed_seen or ())
    final = ""
    try:
        for outer_step in range(step_budget):
            idx = step_index_base + outer_step
            acc = None
            content_buf = ""
            step_start = time.time()
            tokens_in_step = 0

            for chunk in runnable.stream(msgs):
                if stop_probe():
                    tracer.finalize(ctx.trace, "stopped")
                    yield (_LOOP_DONE, "", "stopped", False)
                    return
                acc = chunk if acc is None else acc + chunk
                reasoning_content = chunk.additional_kwargs.get("reasoning_content", "")
                if reasoning_content:
                    yield ("reasoning", reasoning_content)
                piece = chunk.content or ""
                if piece:
                    content_buf += piece
                    tokens_in_step += 1
                    yield ("token", piece)

            ai = acc if acc is not None else AIMessage(content=content_buf)
            model_ms = round((time.time() - step_start) * 1000, 2)
            while len(ctx.trace.steps) <= idx:
                ctx.trace.steps.append(StepTrace(step=len(ctx.trace.steps)))
            ctx.trace.steps[idx].model_inference_ms = model_ms
            ctx.trace.steps[idx].tokens_generated = tokens_in_step

            calls = tool_executor.extract_calls(ai)

            if not calls:
                newly = scan_skills(content_buf, ctx.activated_skills)
                if newly:
                    ctx.activated_skills.extend(newly)
                    names = ", ".join(s["name"] for s in newly)
                    yield ("thinking", f"Activating skill: {names}",
                           f"Loading skill instructions: {names}", None)
                    msgs.append(ai if isinstance(ai, AIMessage)
                                else AIMessage(content=content_buf))
                    for sk in newly:
                        msgs.append(SystemMessage(content=skill_block(sk)))
                    continue
                recovery_decision = retrieval_executor.recommend_when_model_answered(ctx)
                if recovery_decision.action != RecoveryAction.NONE:
                    if recovery_decision.action == RecoveryAction.UPGRADE_SEARCH:
                        search_tools = capability_registry.get_tool_names(["search"])
                        ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                        lc_tools = tool_executor.tools_for_mode(allowed_tools=ctx.allowed_tools)
                        runnable = bind_runnable(ctx, lc_tools)
                        msgs.append(SystemMessage(
                            content="[Web search tools (web_search, web_fetch) are now available. "
                                    "Use them if you need current information.]"
                        ))
                        state = retrieval_executor.commit_recovery(
                            ctx, recovery_decision, recovery_decision.action.value)
                        tracer.debug(ctx.trace, "recovery", {
                            "action": recovery_decision.action.value,
                            "reason": recovery_decision.reason,
                            "attempt": state.attempts_used,
                            "allowed_tools": list(ctx.allowed_tools),
                        })
                    continue
                final = content_buf.strip()
                break

            msgs.append(ai if isinstance(ai, AIMessage)
                        else AIMessage(content=content_buf))
            names = ", ".join(c["name"] for c in calls)
            arg_sigs = [json.dumps(c["args"], sort_keys=True, default=str) for c in calls]
            calls_detail = "; ".join(
                f"{c['name']}({sig[:200]})"
                for c, sig in zip(calls, arg_sigs)
            )
            yield ("thinking", f"Running: {names}", calls_detail, None)

            for c, args_sig in zip(calls, arg_sigs):
                if stop_probe():
                    tracer.finalize(ctx.trace, "stopped")
                    yield (_LOOP_DONE, "", "stopped", False)
                    return
                sig = f"{c['name']}:{args_sig}"
                call_id = f"call-{idx}-{c['name']}"
                yield ("tool_call", c["name"], c["args"], call_id, tool_executor.tool_category(c["name"]))
                if event_bus:
                    try:
                        event_bus.emit("tool_called", tool=c["name"], args=c["args"], step=idx)
                    except Exception:
                        pass
                if sig in seen_calls:
                    out = (f"Error: you already made this exact {c['name']} call "
                           f"and have its result above. Use it, or try a "
                           f"DIFFERENT call — do not repeat yourself.")
                    tool_success = False
                    tool_t0 = time.time()
                    diff = tool_executor.compute_diff(c["name"], c["args"])
                    tool_ms = round((time.time() - tool_t0) * 1000, 2)
                    tracer.record_tool(
                        step_idx=idx, name=c["name"], args=c["args"],
                        result=out, latency_ms=tool_ms, success=tool_success,
                        error=out if out.startswith("Error") else None,
                        trace=ctx.trace,
                    )
                else:
                    seen_calls.add(sig)
                    result = tool_executor.execute(
                        c["name"], c["args"],
                        coordinator=ctx.retrieval_coordinator,
                        step_idx=idx, trace=ctx.trace,
                    )
                    out = result.output
                    tool_success = result.success
                    diff = result.diff
                yield ("tool_result", c["name"], out, call_id, diff)
                if event_bus:
                    try:
                        event_bus.emit("tool_result", tool=c["name"], call_id=call_id,
                                       is_error=out.startswith("Error"))
                    except Exception:
                        pass
                msgs.append(ToolMessage(content=out, tool_call_id=c["id"]))

                # Post-tool recovery: executor detects empty knowledge result → escalate to web
                recovery_decision = retrieval_executor.recommend_after_tool(ctx, c["name"], out)
                if (recovery_decision.action == RecoveryAction.ESCALATE_WEB
                        and not any(s in ctx.allowed_tools for s in
                                    capability_registry.get_tool_names(["search"]))):
                    search_tools = capability_registry.get_tool_names(["search"])
                    ctx.allowed_tools = list(set(ctx.allowed_tools) | set(search_tools))
                    new_lc_tools = tool_executor.tools_for_mode(allowed_tools=ctx.allowed_tools)
                    runnable = bind_runnable(ctx, new_lc_tools)
                    state = retrieval_executor.commit_recovery(ctx, recovery_decision, "post_tool_escalation")
                    msgs.append(SystemMessage(
                        content="[Knowledge base returned no results. Web search tools "
                                "(web_search, web_fetch) are now available. Use them to find "
                                "current information.]"
                    ))
                    if debug_trace and ctx.trace is not None:
                        ctx.trace.debug_events.append(DebugTraceEvent(
                            category="recovery",
                            data={
                                "action": "post_tool_escalation",
                                "reason": recovery_decision.reason,
                                "attempt": state.attempts_used,
                                "step": idx,
                                "tool": c["name"],
                            },
                        ))

                if stop_probe():
                    tracer.finalize(ctx.trace, "stopped")
                    yield (_LOOP_DONE, "", "stopped", False)
                    return
            yield ("thinking", "Thinking...", "Processing tool results and forming response", None)
        else:
            final = ("I ran out of steps before finishing. Here's where I "
                     "got to — ask me to continue if you want me to keep going.")
            yield ("token", final)

        if not final:
            final = "(no response — the model returned empty output; try rephrasing)"
            yield ("token", final)

        stop_reason = "completed"
        if not final.strip():
            stop_reason = "empty"
        elif "ran out of steps" in final:
            stop_reason = "max_steps"
        success = stop_reason not in ("max_steps", "error")
        yield (_LOOP_DONE, final, stop_reason, success)
    except Exception as e:
        final = f"I hit an error: {e}"
        yield ("token", final)
        yield (_LOOP_DONE, final, "error", False)
