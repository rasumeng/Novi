"""Generic single-attempt ReAct executor (Phase 9B; sole loop since 9C).

The loop body formerly inline in ``NoviRuntime._run_agent_loop``, moved
VERBATIM into a runtime-owned collaborator so callers other than that
historical entry point can drive one implement/execution attempt:

    model.stream → tool_calls? → dedup gate → ToolExecutor.execute
                 → ToolMessage feedback → recovery escalation → repeat
                 ↘ no calls / budget exhausted / stop / error → outcome

Since Phase 9C this is the ONLY generic ReAct loop: the historical
``NoviRuntime._run_agent_loop`` wrapper was retired and every consumer —
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
import uuid

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from .retrieval import RecoveryAction
from .trace import DebugTraceEvent, StepTrace

from .agent_action import AgentAction, AgentActionType
from .agent_events import AgentEvent


def _generate_message_id() -> str:
    return f"msg-{uuid.uuid4().hex[:8]}"


def _chunk_message(text: str):
    """Fallback when runnable has no stream: word-split with same message_id contract."""
    if not text:
        return
        yield  # make generator
    # split preserving spaces: simple word + space
    for word in text.split():
        yield word + " "


def _get_run_ids(ctx):
    run_id = getattr(ctx, "run_id", "") or ""
    conv_id = getattr(ctx, "conversation_id", "") or ""
    # fallback to empty if not set; coordinator will set run_id for correlation
    return run_id, conv_id


def _extract_total_tokens(usage) -> int | None:
    """Prefer total_tokens; fallback to input+output sum. Returns int or None."""
    try:
        if usage is None:
            return None
        if isinstance(usage, dict):
            for k in ("total_tokens", "totalTokens", "total_tokens_usage", "total"):
                if k in usage and usage[k] is not None:
                    return int(usage[k])
            inp = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("inputTokens") or 0
            out = usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("outputTokens") or 0
            if inp or out:
                try:
                    return int(inp) + int(out)
                except Exception:
                    pass
            return None
        if hasattr(usage, "total_tokens"):
            v = getattr(usage, "total_tokens")
            if v is not None:
                return int(v)
        if hasattr(usage, "totalTokens"):
            v = getattr(usage, "totalTokens")
            if v is not None:
                return int(v)
        if hasattr(usage, "total"):
            v = getattr(usage, "total")
            if v is not None:
                return int(v)
        # dict-like object with get
        try:
            if hasattr(usage, "get"):
                for k in ("total_tokens", "totalTokens", "total"):
                    try:
                        v = usage.get(k)
                        if v is not None:
                            return int(v)
                    except Exception:
                        pass
        except Exception:
            pass
    except Exception:
        return None
    return None


def _usage_from_chunk(chunk) -> object | None:
    """Extract provider usage from chunk/response_metadata."""
    try:
        u = getattr(chunk, "usage_metadata", None)
        if u is not None:
            return u
        rm = getattr(chunk, "response_metadata", None)
        if isinstance(rm, dict):
            return rm.get("usage") or rm.get("usage_metadata") or rm.get("token_usage")
        if rm is not None:
            v = getattr(rm, "usage", None) or getattr(rm, "usage_metadata", None) or getattr(rm, "token_usage", None)
            if v is not None:
                return v
        u2 = getattr(chunk, "usage", None)
        if u2 is not None:
            return u2
    except Exception:
        return None
    return None


def _apply_provider_usage(ctx, usage) -> None:
    total = _extract_total_tokens(usage)
    if total is not None:
        try:
            setattr(ctx, "token_usage", int(total))
        except Exception:
            pass
        try:
            ctx.metadata["token_usage"] = int(total)
        except Exception:
            pass

EMIT_PROGRESS_DESCRIPTION = (
    "Use only when you have discovered meaningful information worth communicating "
    "before continuing. Do not narrate routine actions or announce every tool call. "
    "Do not call emit_progress when you are ready to provide the final answer."
)

# Sentinel emitted at the end of every attempt carrying the loop outcome.
# Payload: (_LOOP_DONE, final_text, stop_reason, success). Re-exported by
# novi.runtime.runtime so existing consumers keep their import path.
_LOOP_DONE = "__plan_step_done__"
# TODO(cleanup): remove _LOOP_DONE after AgentRun migration — kept as deprecated
# alias mapping to AgentAction.FINISH for one release.


def is_goal_complete(ctx, final: str = "") -> bool:
    """Goal is stopping condition, max_steps is safety rail.

    True if plan marked COMPLETED or final non-empty and no unresolved flag.
    """
    try:
        if ctx is not None and getattr(ctx, "execution_plan", None) is not None:
            plan = getattr(ctx.execution_plan, "plan", None)
            if plan is not None:
                status = getattr(plan, "status", None)
                if status is not None:
                    val = getattr(status, "value", str(status))
                    if str(val).lower() == "completed" or str(status).lower().endswith("completed"):
                        return True
    except Exception:
        pass
    try:
        if final and final.strip():
            has_unresolved = ctx.metadata.get("has_unresolved") if hasattr(ctx, "metadata") else None
            unresolved = ctx.metadata.get("unresolved") if hasattr(ctx, "metadata") else None
            if has_unresolved or (isinstance(unresolved, list) and unresolved):
                return False
            return True
    except Exception:
        pass
    return False


def _checkpoint_needs_continuation(ctx, reason: str = "max_steps_safety") -> None:
    """Persist StableState and continuation flags to ctx + trace metadata."""
    try:
        from novi.common.execution_state import StableState

        stable = StableState.from_context(ctx)
        ctx.metadata["stable_state"] = stable.to_dict()
        ctx.metadata["needs_continuation"] = True
        ctx.metadata["continuation_reason"] = reason
        # also trace metadata for diagnostics
        if getattr(ctx, "trace", None) is not None:
            try:
                tr = ctx.trace
                if not hasattr(tr, "metadata") or not isinstance(getattr(tr, "metadata"), dict):
                    tr.metadata = {}  # type: ignore[attr-defined]
                tr.metadata["stable_state"] = ctx.metadata["stable_state"]  # type: ignore
                tr.metadata["needs_continuation"] = True  # type: ignore
                tr.metadata["continuation_reason"] = reason  # type: ignore
            except Exception:
                pass
        # opportunistic compaction
        try:
            from .context_manager import ContextManager

            cm = ContextManager(model_name=getattr(ctx, "model_name", None))
            level = cm.should_compact(ctx)
            if level in ("compact", "emergency"):
                cm.compact_history(ctx)
                if getattr(ctx, "trace", None) is not None:
                    try:
                        if not hasattr(ctx.trace, "metadata"):
                            ctx.trace.metadata = {}  # type: ignore[attr-defined]
                        ctx.trace.metadata["context_compacted"] = level  # type: ignore
                    except Exception:
                        pass
        except Exception:
            pass
    except Exception:
        try:
            ctx.metadata["needs_continuation"] = True
            ctx.metadata["continuation_reason"] = reason
        except Exception:
            pass



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
    """Run ONE ReAct attempt; yields AgentEvents/AgentActions with message_id, plus deprecated _LOOP_DONE tuple for compat.

    Ownership: this layer owns native model streaming and emits message.started/delta/completed
    with stable message_id. ExecutionCoordinator (Task 5) must NOT re-emit message lifecycle.
    """
    msgs = list(base_msgs)
    if step is not None:
        msgs.append(SystemMessage(
            content=f"CURRENT STEP ({step.id}): {step.description}"
        ))
    seen_calls: set[str] = set(seed_seen or ())
    sig_counts: dict[str, int] = {}
    final = ""
    run_id, conv_id = _get_run_ids(ctx)
    def _yield_terminal(text: str, reason: str, success: bool):
        if reason == "stopped":
            act_type = AgentActionType.FINISH
        elif reason in ("needs_continuation", "max_steps_safety", "stall", "context_overflow"):
            act_type = AgentActionType.CONTINUE
        elif reason == "error":
            act_type = AgentActionType.FINISH
        else:
            # normal completed/empty: use is_goal_complete
            if is_goal_complete(ctx, text):
                act_type = AgentActionType.FINISH
            else:
                act_type = AgentActionType.CONTINUE
                if reason in ("completed", "empty"):
                    # if empty but not needs_continuation, treat as FINISH to avoid infinite CONTINUE
                    act_type = AgentActionType.FINISH if text.strip() or reason=="empty" else AgentActionType.CONTINUE
        meta = {"stop_reason": reason, "success": success}
        action = AgentAction(type=act_type, message=text, metadata=meta)
        yield action
    try:
        for outer_step in range(step_budget):
            try:
                from .context_manager import ContextManager as _CMOverflow
                _cm = _CMOverflow(model_name=getattr(ctx, "model_name", None))
                _lvl = _cm.should_compact(ctx)
                if _lvl == "emergency":
                    _cm.compact_history(ctx)
                    if ctx.metadata.get("needs_continuation") and ctx.metadata.get("continuation_reason") == "context_overflow":
                        _checkpoint_needs_continuation(ctx, reason="context_overflow")
                        try:
                            _sd = ctx.metadata.get("stable_state", {})
                            _goal = _sd.get("goal", "") if isinstance(_sd, dict) else ""
                        except Exception:
                            _goal = ""
                        yield from _yield_terminal(_goal, "needs_continuation", True)
                        return
                    try:
                        _bd2 = _cm.budget_for(ctx)
                        if _bd2.utilization_pct >= 90:
                            _checkpoint_needs_continuation(ctx, reason="context_overflow")
                            try:
                                _sd2 = ctx.metadata.get("stable_state", {})
                                _goal2 = _sd2.get("goal", "") if isinstance(_sd2, dict) else ""
                            except Exception:
                                _goal2 = ""
                            yield from _yield_terminal(_goal2, "needs_continuation", True)
                            return
                    except Exception:
                        pass
            except Exception:
                pass
            idx = step_index_base + outer_step
            acc = None
            content_buf = ""
            step_start = time.time()
            tokens_in_step = 0
            message_id = None
            run_id, conv_id = _get_run_ids(ctx)
            has_native_stream = callable(getattr(runnable, "stream", None))
            if has_native_stream:
                try:
                    for chunk in runnable.stream(msgs):
                        if stop_probe():
                            tracer.finalize(ctx.trace, "stopped")
                            yield from _yield_terminal("", "stopped", False)
                            return
                        acc = chunk if acc is None else acc + chunk
                        # Prefer provider usage callback when available
                        try:
                            u = _usage_from_chunk(chunk)
                            if u is not None:
                                _apply_provider_usage(ctx, u)
                            # Some providers attach usage on chunk.usage_metadata + response_metadata usage
                            # Also handle response_metadata on accumulated ai (final chunks)
                        except Exception:
                            pass
                        reasoning_content = getattr(chunk, "additional_kwargs", {}).get("reasoning_content", "") if hasattr(chunk, "additional_kwargs") else ""
                        if reasoning_content:
                            yield AgentEvent(type="reasoning", run_id=run_id, conversation_id=conv_id, message=reasoning_content)
                        piece = getattr(chunk, "content", None) or ""
                        if piece:
                            if message_id is None:
                                message_id = _generate_message_id()
                                if run_id or conv_id:
                                    yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=message_id)
                            content_buf += piece
                            tokens_in_step += 1
                            yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=piece)
                    # After stream, check accumulated usage (final chunk may carry total_tokens)
                    try:
                        if acc is not None:
                            u_acc = _usage_from_chunk(acc)
                            if u_acc is not None:
                                _apply_provider_usage(ctx, u_acc)
                    except Exception:
                        pass
                except Exception as e:
                    final = f"I hit an error: {e}"
                    if message_id is None:
                        message_id = _generate_message_id()
                        if run_id or conv_id:
                            yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=message_id)
                    if run_id or conv_id:
                        yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
                        yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
                    else:
                        yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message=final)
                    yield from _yield_terminal(final, "error", False)
                    return
            else:
                try:
                    inv = runnable.invoke(msgs)
                    acc = inv
                    try:
                        u_inv = _usage_from_chunk(inv)
                        if u_inv is not None:
                            _apply_provider_usage(ctx, u_inv)
                        else:
                            # also try response_metadata on invoke result
                            rm2 = getattr(inv, "response_metadata", None)
                            if isinstance(rm2, dict):
                                u2 = rm2.get("usage") or rm2.get("usage_metadata")
                                if u2 is not None:
                                    _apply_provider_usage(ctx, u2)
                    except Exception:
                        pass
                    content = getattr(inv, "content", None) or str(getattr(inv, "content", "") or "")
                    reasoning_content = getattr(inv, "additional_kwargs", {}).get("reasoning_content", "") if hasattr(inv, "additional_kwargs") else ""
                    if reasoning_content:
                        yield AgentEvent(type="reasoning", run_id=run_id, conversation_id=conv_id, message=reasoning_content)
                    content_buf = str(content or "")
                    tokens_in_step = len(content_buf.split()) if content_buf else 0
                except Exception as e:
                    final = f"I hit an error: {e}"
                    if message_id is None:
                        message_id = _generate_message_id()
                        if run_id or conv_id:
                            yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=message_id)
                    if run_id or conv_id:
                        yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
                        yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
                    else:
                        yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message=final)
                    yield from _yield_terminal(final, "error", False)
                    return
            ai = acc if acc is not None else AIMessage(content=content_buf)
            if not has_native_stream and not content_buf:
                try:
                    content_buf = str(getattr(ai, "content", "") or "")
                except Exception:
                    content_buf = ""
            model_ms = round((time.time() - step_start) * 1000, 2)
            while len(ctx.trace.steps) <= idx:
                ctx.trace.steps.append(StepTrace(step=len(ctx.trace.steps)))
            ctx.trace.steps[idx].model_inference_ms = model_ms
            ctx.trace.steps[idx].tokens_generated = tokens_in_step
            calls = tool_executor.extract_calls(ai)
            fallback_needs_emit = False
            if not has_native_stream and not calls and content_buf.strip():
                fallback_needs_emit = True
                if message_id is None:
                    message_id = _generate_message_id()
                    if run_id or conv_id:
                        yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=message_id)
                for piece in _chunk_message(content_buf):
                    yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=piece)
            if not calls:
                newly = scan_skills(content_buf, ctx.activated_skills)
                if newly:
                    ctx.activated_skills.extend(newly)
                    names = ", ".join(s["name"] for s in newly)
                    yield AgentEvent(type="status", run_id=run_id, conversation_id=conv_id, message=f"Activating skill: {names}", phase="thinking", detail=f"Loading skill instructions: {names}")
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
                if has_native_stream and message_id is not None and final:
                    if run_id or conv_id:
                        yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
                elif fallback_needs_emit and message_id is not None:
                    if run_id or conv_id:
                        yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
                break
            msgs.append(ai if isinstance(ai, AIMessage)
                        else AIMessage(content=content_buf))
            names = ", ".join(c["name"] for c in calls)
            arg_sigs = [json.dumps(c["args"], sort_keys=True, default=str) for c in calls]
            calls_detail = "; ".join(
                f"{c['name']}({sig[:200]})"
                for c, sig in zip(calls, arg_sigs)
            )
            yield AgentEvent(type="status", run_id=run_id, conversation_id=conv_id, message=f"Running: {names}", phase="thinking", detail=calls_detail)
            has_progress = any(c["name"] == "emit_progress" for c in calls)
            if has_progress:
                prog_call = next(c for c in calls if c["name"] == "emit_progress")
                prog_msg = ""
                try:
                    prog_msg = prog_call.get("args", {}).get("message", "") or ""
                except Exception:
                    prog_msg = ""
                try:
                    pseudo_res = tool_executor.execute(prog_call["name"], prog_call.get("args", {}), coordinator=ctx.retrieval_coordinator, step_idx=idx, trace=ctx.trace)
                    if pseudo_res.structured and pseudo_res.structured.get("pseudo") == "emit_progress":
                        prog_msg = pseudo_res.structured.get("message", prog_msg) or prog_msg
                except Exception:
                    pass
                prog_mid = _generate_message_id()
                yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=prog_mid)
                for piece in _chunk_message(prog_msg):
                    yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=prog_mid, message=piece)
                yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=prog_mid, message=prog_msg)
                yield AgentAction(type=AgentActionType.PROGRESS, message=prog_msg, metadata={"stop_reason": "progress", "success": True})
                continue
            progress_yielded = False
            for c, args_sig in zip(calls, arg_sigs):
                if stop_probe():
                    tracer.finalize(ctx.trace, "stopped")
                    yield from _yield_terminal("", "stopped", False)
                    return
                sig = f"{c['name']}:{args_sig}"
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
                is_stall = sig_counts[sig] >= 3
                call_id = f"call-{idx}-{c['name']}"
                yield AgentEvent(type="tool.started", run_id=run_id, conversation_id=conv_id, tool=c["name"], args=c["args"])
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
                    if result.structured and isinstance(result.structured, dict) and result.structured.get("pseudo") == "emit_progress":
                        prog_msg2 = result.structured.get("message", "") or result.output or ""
                        prog_mid2 = _generate_message_id()
                        yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=prog_mid2)
                        for piece in _chunk_message(prog_msg2):
                            yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=prog_mid2, message=piece)
                        yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=prog_mid2, message=prog_msg2)
                        yield AgentAction(type=AgentActionType.PROGRESS, message=prog_msg2, metadata={"stop_reason": "progress", "success": True})
                        progress_yielded = True
                        # Still need to record tool completed for pseudo? Skip but emit
                        yield AgentEvent(type="tool.completed", run_id=run_id, conversation_id=conv_id, tool=c["name"], result=result.output)
                        msgs.append(ToolMessage(content=result.output, tool_call_id=c["id"]))
                        break
                    out = result.output
                    tool_success = result.success
                    diff = result.diff
                yield AgentEvent(type="tool.completed", run_id=run_id, conversation_id=conv_id, tool=c["name"], result=out)
                if event_bus:
                    try:
                        event_bus.emit("tool_result", tool=c["name"], call_id=call_id,
                                       is_error=out.startswith("Error"))
                    except Exception:
                        pass
                msgs.append(ToolMessage(content=out, tool_call_id=c["id"]))
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
                    yield from _yield_terminal("", "stopped", False)
                    return
                if is_stall:
                    _checkpoint_needs_continuation(ctx, reason="stall")
                    final = f"Stalled on {c['name']} repeated 3x without progress; checkpointing for continuation."
                    yield from _yield_terminal(final, "needs_continuation", True)
                    return
                if ctx.metadata.get("needs_continuation") and ctx.metadata.get("continuation_reason") == "context_overflow":
                    _checkpoint_needs_continuation(ctx, reason="context_overflow")
                    try:
                        _sd = ctx.metadata.get("stable_state", {})
                        _goal = _sd.get("goal", "") if isinstance(_sd, dict) else ""
                    except Exception:
                        _goal = ""
                    yield from _yield_terminal(_goal, "needs_continuation", True)
                    return
                try:
                    from .context_manager import ContextManager as _CMMid
                    _cm_mid = _CMMid(model_name=getattr(ctx, "model_name", None))
                    if _cm_mid.should_compact(ctx) == "emergency":
                        _cm_mid.compact_history(ctx)
                        if ctx.metadata.get("continuation_reason") == "context_overflow":
                            _checkpoint_needs_continuation(ctx, reason="context_overflow")
                            try:
                                _sd2 = ctx.metadata.get("stable_state", {})
                                _goal2 = _sd2.get("goal", "") if isinstance(_sd2, dict) else ""
                            except Exception:
                                _goal2 = ""
                            yield from _yield_terminal(_goal2, "needs_continuation", True)
                            return
                except Exception:
                    pass
            if progress_yielded:
                continue
            yield AgentEvent(type="status", run_id=run_id, conversation_id=conv_id, message="Processing tool results and forming response", phase="thinking", detail="Processing tool results and forming response")
        else:
            if not is_goal_complete(ctx, final):
                _checkpoint_needs_continuation(ctx, reason="max_steps_safety")
                try:
                    stable_dict = ctx.metadata.get("stable_state", {})
                    if isinstance(stable_dict, dict) and stable_dict.get("goal"):
                        final = stable_dict.get("goal", "")
                    else:
                        final = ""
                except Exception:
                    final = ""
                yield from _yield_terminal(final, "needs_continuation", True)
                return
            final = final or ""
            if not final:
                final = "(no response — the model returned empty output; try rephrasing)"
                # Single token for empty to preserve parity; no chunking
                yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message=final)
            else:
                if message_id is not None:
                    yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
                else:
                    mid2 = _generate_message_id()
                    yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=mid2)
                    for piece in _chunk_message(final):
                        yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=mid2, message=piece)
                    yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=mid2, message=final)
            yield from _yield_terminal(final, "completed", True)
            return
        if not final:
            final = "(no response — the model returned empty output; try rephrasing)"
            # Generate message_id for correlation even for parity single token
            if 'message_id' not in locals() or message_id is None:
                message_id = _generate_message_id()
            yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=message_id, message=final)
        if ctx.metadata.get("needs_continuation"):
            stop_reason = "needs_continuation"
            success = True
        else:
            stop_reason = "completed"
            if not final.strip():
                stop_reason = "empty"
            success = stop_reason not in ("error",)
        if is_goal_complete(ctx, final):
            if message_id is None and final.strip():
                mid4 = _generate_message_id()
                if run_id or conv_id:
                    yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=mid4)
                for piece in _chunk_message(final):
                    yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=mid4, message=piece)
                yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=mid4, message=final)
            elif message_id is not None and final.strip():
                # If we already emitted deltas but not completed, emit completed
                # Check if completed was already emitted for this message_id: we track via final already? For native case we emitted completed at break, so not needed
                # To avoid duplicate, only emit if not already emitted for native final break case
                # For safety, check if final was from break path with completed already emitted; skip
                # We can detect if we already yielded completed for this message_id by seeing if final was from the no-tool break where we emitted
                # Simpler: if message_id exists and we are here via normal break, we already emitted completed, so skip
                pass
            yield from _yield_terminal(final, stop_reason, success)
        else:
            yield AgentAction(type=AgentActionType.CONTINUE, message=None, metadata={"stop_reason": stop_reason, "success": success})
            return
    except Exception as e:
        final = f"I hit an error: {e}"
        mid_err = _generate_message_id()
        run_id, conv_id = _get_run_ids(ctx)
        if run_id or conv_id:
            yield AgentEvent(type="message.started", run_id=run_id, conversation_id=conv_id, message_id=mid_err)
            # For parity, single token; for new, also single but with lifecycle
            yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message_id=mid_err, message=final)
            yield AgentEvent(type="message.completed", run_id=run_id, conversation_id=conv_id, message_id=mid_err, message=final)
        else:
            yield AgentEvent(type="message.delta", run_id=run_id, conversation_id=conv_id, message=final)
        yield from _yield_terminal(final, "error", False)
