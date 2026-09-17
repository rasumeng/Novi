import pytest

from novi.runtime.context_builder import (
    ContextBuilder, ContextInputs, ContextOverflowError, TranscriptCompactor,
)
from novi.runtime.run_contracts import ModelSnapshot, RunRequest, RunState
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage


def msg(mid, role, text=None, *, call=None, result=None, visible=True):
    blocks = []
    if text is not None:
        blocks.append(ContentBlock(ContentBlockType.TEXT, text=text))
    if call is not None:
        blocks.append(ContentBlock(ContentBlockType.TOOL_CALL, call_id=call,
                                   tool_name="read_file", arguments={"path": "a"}))
    if result is not None:
        blocks.append(ContentBlock(ContentBlockType.TOOL_RESULT, call_id=result[0], result=result[1]))
    return TranscriptMessage(mid, role, tuple(blocks), visible_to_user=visible,
                             source="tool" if role is MessageRole.TOOL else "model")


def run(transcript, *, window=300):
    request = RunRequest(conversation_id="c", user_message_id="u", user_text="goal",
                         model=ModelSnapshot("test", "scripted", context_window=window))
    return RunState(id="r", request=request, transcript=tuple(transcript))


def test_budget_counts_every_actual_provider_input_category():
    state = run([msg("u", MessageRole.USER, "goal"), msg("a", MessageRole.ASSISTANT, "answer")], window=1000)
    inputs = ContextInputs(
        system_instructions=("system rule",),
        tool_schemas=({"name": "read_file", "schema": {"path": "string"}},),
        skill_blocks=("skill instructions",), retrieval_evidence=("source evidence",),
        attachment_token_costs=(25,), output_reserve=50)
    built = ContextBuilder().build(state, inputs)
    assert built.breakdown.system_instructions > 0
    assert built.breakdown.tool_schemas > 0
    assert built.breakdown.skill_blocks > 0
    assert built.breakdown.transcript > 0
    assert built.breakdown.retrieval_evidence > 0
    assert built.breakdown.attachments == 25
    assert built.breakdown.output_reserve == 50
    assert built.transcript == state.transcript
    assert built.breakdown.count_source == "conservative_estimate"


def test_compaction_keeps_goal_early_evidence_and_call_result_pairing():
    transcript = [msg("u", MessageRole.USER, "Fix the parser and retain MAGIC_FACT")]
    transcript += [
        msg("a1", MessageRole.ASSISTANT, call="c1"),
        msg("t1", MessageRole.TOOL, result=("c1", {"status": "succeeded", "output": "MAGIC_FACT=" + "x" * 300})),
    ]
    for i in range(8):
        transcript += [msg(f"a{i+2}", MessageRole.ASSISTANT, call=f"c{i+2}"),
                       msg(f"t{i+2}", MessageRole.TOOL,
                           result=(f"c{i+2}", {"status": "succeeded", "output": "noise" * 40}))]
    state = run(transcript, window=260)
    inputs = ContextInputs(system_instructions=("system",), output_reserve=40)
    compacted = TranscriptCompactor(ContextBuilder()).compact(state, inputs)
    assert "Fix the parser" in compacted.state.context_summary
    assert "MAGIC_FACT" in compacted.state.context_summary
    assert compacted.after_tokens < compacted.before_tokens
    call_ids = [b.call_id for m in compacted.state.transcript for b in m.blocks if b.type is ContentBlockType.TOOL_CALL]
    result_ids = [b.call_id for m in compacted.state.transcript for b in m.blocks if b.type is ContentBlockType.TOOL_RESULT]
    assert set(call_ids) == set(result_ids)


def test_pending_approval_group_survives_compaction_intact():
    transcript = [msg("u", MessageRole.USER, "goal")]
    for i in range(6):
        transcript += [msg(f"a{i}", MessageRole.ASSISTANT, call=f"done{i}"),
                       msg(f"t{i}", MessageRole.TOOL,
                           result=(f"done{i}", {"status": "succeeded", "output": "x" * 120}))]
    transcript.append(msg("pending", MessageRole.ASSISTANT, call="pending-call"))
    state = run(transcript, window=220)
    state = RunState(**{**state.__dict__, "pending_call_ids": ("pending-call",),
                        "pending_permission_ids": ("perm-1",)})
    compacted = TranscriptCompactor(ContextBuilder()).compact(
        state, ContextInputs(system_instructions=("system",), output_reserve=40))
    pending = [m for m in compacted.state.transcript if m.id == "pending"]
    assert len(pending) == 1
    assert pending[0].blocks[0].call_id == "pending-call"
    assert compacted.state.pending_permission_ids == ("perm-1",)


def test_oversized_fixed_inputs_block_without_resetting_run():
    state = run([msg("u", MessageRole.USER, "goal")], window=100)
    inputs = ContextInputs(tool_schemas=({"schema": "x" * 1000},), output_reserve=30)
    with pytest.raises(ContextOverflowError, match="minimum required context"):
        TranscriptCompactor(ContextBuilder()).compact(state, inputs)
    assert state.id == "r"
    assert state.transcript[0].id == "u"


def test_exact_counter_is_reported_when_provider_counter_exists():
    class ExactCounter:
        source = "provider_tokenizer"
        def count(self, text):
            return len(text.split())

    state = run([msg("u", MessageRole.USER, "one two three")], window=100)
    built = ContextBuilder(counter=ExactCounter()).build(state, ContextInputs(output_reserve=10))
    assert built.breakdown.count_source == "provider_tokenizer"
    assert built.breakdown.transcript >= 3


def test_failures_and_completed_effect_artifacts_are_not_summarized_away():
    transcript = [msg("u", MessageRole.USER, "goal")]
    transcript += [msg("af", MessageRole.ASSISTANT, call="failed"),
                   msg("tf", MessageRole.TOOL,
                       result=("failed", {"status": "failed", "error": "compile broke"}))]
    transcript += [msg("ae", MessageRole.ASSISTANT, call="effect"),
                   msg("te", MessageRole.TOOL,
                       result=("effect", {"status": "succeeded", "output": "written",
                                          "artifact_ids": ["artifact-1"]}))]
    for i in range(5):
        transcript += [msg(f"a{i}", MessageRole.ASSISTANT, "noise" * 30)]
    state = run(transcript, window=450)
    compacted = TranscriptCompactor(ContextBuilder()).compact(
        state, ContextInputs(output_reserve=30))
    kept = {message.id for message in compacted.state.transcript}
    assert {"af", "tf", "ae", "te"} <= kept
