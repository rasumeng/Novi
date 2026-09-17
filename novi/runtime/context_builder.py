"""Budget and compact the exact transcript and fixed inputs sent to a provider."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Protocol

from novi.runtime.run_contracts import RunState
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage


class TokenCounter(Protocol):
    source: str
    def count(self, text: str) -> int: ...


class ConservativeCounter:
    source = "conservative_estimate"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class ContextInputs:
    system_instructions: tuple[str, ...] = ()
    tool_schemas: tuple[dict, ...] = ()
    skill_blocks: tuple[str, ...] = ()
    retrieval_evidence: tuple[str, ...] = ()
    attachment_token_costs: tuple[int, ...] = ()
    output_reserve: int = 1024
    fallback_context_window: int = 8192


@dataclass(frozen=True)
class ContextBreakdown:
    context_window: int
    system_instructions: int
    tool_schemas: int
    skill_blocks: int
    transcript: int
    retrieval_evidence: int
    attachments: int
    output_reserve: int
    total: int
    available_for_input: int
    fits: bool
    count_source: str


@dataclass(frozen=True)
class ProviderContext:
    system_instructions: tuple[str, ...]
    tool_schemas: tuple[dict, ...]
    skill_blocks: tuple[str, ...]
    transcript: tuple[TranscriptMessage, ...]
    retrieval_evidence: tuple[str, ...]
    breakdown: ContextBreakdown


@dataclass(frozen=True)
class CompactionResult:
    state: RunState
    before_tokens: int
    after_tokens: int
    removed_message_ids: tuple[str, ...]


class ContextOverflowError(RuntimeError):
    pass


class ContextBuilder:
    def __init__(self, counter: TokenCounter | None = None) -> None:
        self._counter = counter or ConservativeCounter()

    def build(self, state: RunState, inputs: ContextInputs) -> ProviderContext:
        window = (state.request.model.context_window
                  if state.request.model and state.request.model.context_window
                  else inputs.fallback_context_window)
        system = self._count_sequence(inputs.system_instructions)
        schemas = self._counter.count(json.dumps(inputs.tool_schemas, sort_keys=True,
                                                   separators=(",", ":")))
        skills = self._count_sequence(inputs.skill_blocks)
        transcript = self._counter.count(json.dumps(
            [message.to_dict() for message in state.transcript], sort_keys=True,
            separators=(",", ":")))
        retrieval = self._count_sequence(inputs.retrieval_evidence)
        attachments = sum(max(0, int(cost)) for cost in inputs.attachment_token_costs)
        input_total = system + schemas + skills + transcript + retrieval + attachments
        total = input_total + inputs.output_reserve
        breakdown = ContextBreakdown(context_window=window,
            system_instructions=system, tool_schemas=schemas, skill_blocks=skills,
            transcript=transcript, retrieval_evidence=retrieval,
            attachments=attachments, output_reserve=inputs.output_reserve,
            total=total, available_for_input=max(0, window - inputs.output_reserve),
            fits=total <= window, count_source=self._counter.source)
        return ProviderContext(system_instructions=inputs.system_instructions,
            tool_schemas=inputs.tool_schemas, skill_blocks=inputs.skill_blocks,
            transcript=state.transcript, retrieval_evidence=inputs.retrieval_evidence,
            breakdown=breakdown)

    def _count_sequence(self, values: tuple[str, ...]) -> int:
        return self._counter.count(json.dumps(values, separators=(",", ":"))) if values else 0


class TranscriptCompactor:
    def __init__(self, builder: ContextBuilder) -> None:
        self._builder = builder

    def compact(self, state: RunState, inputs: ContextInputs) -> CompactionResult:
        before = self._builder.build(state, inputs)
        groups = _completed_groups(state.transcript)
        pending = set(state.pending_call_ids)
        required: list[tuple[TranscriptMessage, ...]] = []
        removable: list[tuple[TranscriptMessage, ...]] = []
        for index, group in enumerate(groups):
            call_ids = _call_ids(group)
            is_goal = any(message.role is MessageRole.USER for message in group) and not required
            is_pending = bool(call_ids & pending)
            if is_goal or is_pending or _must_keep(group):
                required.append(group)
            else:
                removable.append(group)
        recent: list[tuple[TranscriptMessage, ...]] = []
        if removable:
            recent.append(removable.pop())
        removed_groups = removable
        removed = tuple(message for group in removed_groups for message in group)
        summary_source = tuple(message for group in removed_groups for message in group)
        summary_text = _summarize(state, summary_source, max_chars=600)
        candidate = _assemble(required, summary_text, recent)
        compacted_state = replace(state, transcript=candidate, context_summary=summary_text)
        after = self._builder.build(compacted_state, inputs)

        for max_chars in (400, 240, 120):
            if after.breakdown.fits:
                break
            summary_text = _summarize(state, summary_source + tuple(
                message for group in recent for message in group), max_chars=max_chars)
            removed += tuple(message for group in recent for message in group)
            recent = []
            candidate = _assemble(required, summary_text, recent)
            compacted_state = replace(state, transcript=candidate, context_summary=summary_text)
            after = self._builder.build(compacted_state, inputs)

        if not after.breakdown.fits:
            minimum_state = replace(state, transcript=_assemble(required, "", []), context_summary="")
            minimum = self._builder.build(minimum_state, inputs)
            if not minimum.breakdown.fits:
                raise ContextOverflowError("minimum required context does not fit selected model")
            compacted_state = minimum_state
            after = minimum
        if after.breakdown.total >= before.breakdown.total:
            raise ContextOverflowError("compaction did not reduce actual provider input")
        return CompactionResult(state=compacted_state,
            before_tokens=before.breakdown.total, after_tokens=after.breakdown.total,
            removed_message_ids=tuple(message.id for message in removed))


def _completed_groups(transcript: tuple[TranscriptMessage, ...]) -> list[tuple[TranscriptMessage, ...]]:
    groups: list[tuple[TranscriptMessage, ...]] = []
    index = 0
    while index < len(transcript):
        message = transcript[index]
        calls = {block.call_id for block in message.blocks
                 if block.type is ContentBlockType.TOOL_CALL and block.call_id}
        if not calls:
            groups.append((message,))
            index += 1
            continue
        group = [message]
        found: set[str] = set()
        cursor = index + 1
        while cursor < len(transcript) and found != calls:
            next_message = transcript[cursor]
            results = {block.call_id for block in next_message.blocks
                       if block.type is ContentBlockType.TOOL_RESULT and block.call_id}
            if not results:
                break
            group.append(next_message)
            found.update(results & calls)
            cursor += 1
        groups.append(tuple(group))
        index = cursor if cursor > index + 1 else index + 1
    return groups


def _call_ids(group: tuple[TranscriptMessage, ...]) -> set[str]:
    return {block.call_id for message in group for block in message.blocks
            if block.type is ContentBlockType.TOOL_CALL and block.call_id}


def _must_keep(group: tuple[TranscriptMessage, ...]) -> bool:
    for message in group:
        for block in message.blocks:
            if block.type is not ContentBlockType.TOOL_RESULT or not block.result:
                continue
            status = block.result.get("status")
            if status in {"failed", "denied", "cancelled", "timed_out"}:
                return True
            if block.result.get("artifact_ids") or block.result.get("diff"):
                return True
    return False


def _assemble(required: list[tuple[TranscriptMessage, ...]], summary: str,
              recent: list[tuple[TranscriptMessage, ...]]) -> tuple[TranscriptMessage, ...]:
    messages = [message for group in required for message in group]
    if summary:
        summary_message = TranscriptMessage(id="context-summary", role=MessageRole.SYSTEM,
            visible_to_user=False, source="context_compaction", trust="trusted",
            blocks=(ContentBlock(ContentBlockType.TEXT, text=summary),))
        insert_at = 1 if messages and messages[0].role is MessageRole.USER else 0
        messages.insert(insert_at, summary_message)
    messages.extend(message for group in recent for message in group)
    return tuple(messages)


def _summarize(state: RunState, messages: tuple[TranscriptMessage, ...], max_chars: int) -> str:
    goal = state.request.user_text
    for message in state.transcript:
        if message.role is MessageRole.USER:
            text = next((block.text for block in message.blocks
                         if block.type is ContentBlockType.TEXT and block.text), None)
            if text:
                goal = text
                break
    parts = [f"Goal: {goal}"]
    for message in messages:
        for block in message.blocks:
            if block.type is ContentBlockType.TEXT and block.text:
                parts.append(block.text)
            elif block.type is ContentBlockType.TOOL_CALL:
                parts.append(f"Called {block.tool_name} ({block.call_id})")
            elif block.type is ContentBlockType.TOOL_RESULT and block.result is not None:
                status = block.result.get("status", "unknown")
                output = str(block.result.get("output", ""))
                error = str(block.result.get("error", "") or "")
                artifacts = block.result.get("artifact_ids") or []
                parts.append(f"Result {block.call_id} [{status}]: {output[:240]} {error[:120]} artifacts={artifacts}")
    return "\n".join(parts)[:max_chars]
