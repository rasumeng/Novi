"""Read-only agent audit probes; fake models/tools, no backend or user vault.

Run from the repository root with its existing Python dependencies:
    python scripts/audit_agent_state.py
These report current behavior, not passing acceptance criteria for a replacement.
"""
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from novi.runtime.agent_events import AgentEvent
from novi.runtime.agent_run import AgentRun, AgentRunStatus
from novi.runtime.execution_context import ExecutionContext
from novi.runtime.permissions import PermissionResolver
from novi.runtime.react_attempt import run_react_attempt
from novi.runtime.retrieval import RecoveryAction
from novi.runtime.tool_executor import ToolExecutor
from novi.runtime.tool_registry import ToolRegistry
from novi.runtime.trace import ExecutionTrace
from novi.services.execution import ExecutionCoordinator
from novi.webui_server import Session


def context():
    ctx = ExecutionContext(user_input='Audit fixture', conversation_id='audit-conv')
    ctx.run_id = 'audit-run'
    ctx.trace = ExecutionTrace(user_input=ctx.user_input)
    return ctx


def run_loop(first_calls, first_text=''):
    inputs, executed = [], []

    class Model:
        def stream(self, messages):
            inputs.append(list(messages))
            if len(inputs) == 1:
                yield AIMessageChunk(content=first_text, tool_calls=first_calls)
            else:
                yield AIMessageChunk(content='Finished fixture.')

    class Executor:
        def extract_calls(self, ai):
            return ai.tool_calls

        def execute(self, name, args, **kwargs):
            executed.append(name)
            return SimpleNamespace(output='ok', success=True, diff=None, structured=(
                {'pseudo': 'emit_progress', 'message': args['message']}
                if name == 'emit_progress' else None))

    noop = lambda *a, **k: None
    recovery = lambda *a: SimpleNamespace(action=RecoveryAction.NONE)
    events = list(run_react_attempt(
        ctx=context(), runnable=Model(), tool_executor=Executor(),
        tracer=SimpleNamespace(finalize=noop, debug=noop, record_tool=noop),
        retrieval_executor=SimpleNamespace(recommend_when_model_answered=recovery,
                                          recommend_after_tool=recovery),
        capability_registry=SimpleNamespace(get_tool_names=lambda _: []),
        scan_skills=lambda *_: [], skill_block=lambda _: '', bind_runnable=noop,
        stop_probe=lambda: False, step_budget=3,
        base_msgs=[HumanMessage(content='Audit fixture')],
    ))
    return events, inputs, executed


def coordinator_without_terminal():
    class Runtime:
        def run_stream(self, **kwargs):
            yield AgentEvent(type='message.delta', run_id='audit-run',
                             conversation_id='audit-conv', message_id='m',
                             message='I hit an error: fixture')

    ctx = context()
    run = AgentRun(id=ctx.run_id, conversation_id=ctx.conversation_id,
                   goal=ctx.user_input, status=AgentRunStatus.RUNNING, context=ctx)
    events = []
    ExecutionCoordinator().execute(run, events.append, runtime=Runtime())
    return {'status': run.status.value, 'events': [e.type for e in events]}


def main():
    progress = {'name': 'emit_progress', 'args': {'message': 'Found useful evidence.'}, 'id': 'p1'}
    read = {'name': 'read_file', 'args': {'path': 'fixture.txt'}, 'id': 'read1'}
    events, inputs, executed = run_loop([progress, read])
    result = {
        'mixed_progress_and_tool': {
            'requested': ['emit_progress', 'read_file'], 'executed': executed,
            'tool_results_in_next_model_input': [m.tool_call_id for m in inputs[1]
                                                 if isinstance(m, ToolMessage)],
        },
        'missing_terminal_action': coordinator_without_terminal(),
    }
    events, _, _ = run_loop([read], 'I will inspect the file.')
    started = [e.message_id for e in events if isinstance(e, AgentEvent) and e.type == 'message.started']
    completed = {e.message_id for e in events if isinstance(e, AgentEvent) and e.type == 'message.completed'}
    result['text_before_tool'] = {
        'message_count': len(started), 'unclosed_message_count': len(set(started) - completed),
        'tool_call_ids': [e.call_id for e in events if isinstance(e, AgentEvent) and e.type.startswith('tool.')],
    }
    session = Session.__new__(Session)
    result['websocket_tool_payload'] = session._map_agent_event_to_ws(AgentEvent(
        type='tool.completed', run_id='audit-run', conversation_id='audit-conv',
        tool='read_file', call_id='read1', result='ok', category='workspace',
        diff={'text': '+fixture', 'added': 1, 'removed': 0}))
    executor = ToolExecutor(registry=ToolRegistry(), perms=PermissionResolver({
        'permissions': {'read_file': 'deny', 'write_file': 'deny'}}))
    result['explicit_denials'] = {
        'manual_read_allowed': executor._check_permission('read_file', {}, 'manual'),
        'auto_read_allowed': executor._check_permission('read_file', {}, 'auto'),
        'accept_edits_write_allowed': executor._check_permission('write_file', {}, 'accept-edits'),
    }
    invoked = []
    registry = ToolRegistry()

    def first():
        """Fail without side effects."""
        return 'Error: fixture failure'

    def forbidden():
        """Record invocation in memory only."""
        invoked.append('forbidden')
        return 'fixture success'

    registry.register('fixture_first', first)
    registry.register('fixture_forbidden', forbidden)
    fallback_executor = ToolExecutor(registry=registry, perms=PermissionResolver({
        'permissions': {'fixture_first': 'allow', 'fixture_forbidden': 'deny'}}),
        tool_fallbacks={'fixture_first': ['fixture_forbidden']})
    fallback_result = fallback_executor.execute('fixture_first', {})
    result['configured_fallback_denial'] = {
        'forbidden_tool_invoked': bool(invoked), 'reported_success': fallback_result.success}
    session._perm_lock = threading.Lock()
    session._perm_event = threading.Event()
    session._perm_request_id = 'pending-request'
    session._perm_allowed = False
    session.answer_permission(True)
    result['approval_without_request_id'] = {
        'allowed': session._perm_allowed, 'resolved': session._perm_event.is_set()}
    result['permission_ui_after_two_progress_messages'] = {
        # These are the production Conversation.tsx render predicates, with
        # [user, completed assistant progress, completed assistant progress].
        'pending_area_visible': any(role == 'user' and (i == 2 or i == 1)
                                    for i, role in enumerate(['user', 'assistant', 'assistant'])),
        'streaming_assistant_artifacts_visible': False,
        'evidence_kind': 'render-predicate calculation, not browser execution',
    }
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
