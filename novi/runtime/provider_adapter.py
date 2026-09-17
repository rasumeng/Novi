"""Strict adapter from LangChain provider messages to replacement-runtime turns."""

from __future__ import annotations

import json
from typing import Iterable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from novi.runtime.run_contracts import ModelTurn, ToolCall
from novi.runtime.transcript import ContentBlockType, MessageRole, TranscriptMessage


class LangChainTurnProvider:
    """Expose a bound LangChain chat model through the ``TurnProvider`` contract.

    Executable calls are accepted only from the provider's native ``tool_calls``
    field. Text that happens to contain JSON remains ordinary assistant text.
    """

    def __init__(self, model, *, system_prompt: str = "") -> None:
        self._model = model
        self._system_prompt = system_prompt

    def stream(self, transcript: tuple[TranscriptMessage, ...]) -> Iterable[ModelTurn]:
        messages = transcript_to_messages(transcript)
        if self._system_prompt:
            messages.insert(0, SystemMessage(content=self._system_prompt))
        chunks = list(self._model.stream(messages))
        if not chunks:
            return
        combined = None
        try:
            for chunk in chunks:
                combined = chunk if combined is None else combined + chunk
        except (TypeError, ValueError):
            combined = None
        source = combined or chunks[-1]
        text = (_text_content(getattr(combined, "content", "")) if combined is not None
                else "".join(_text_content(getattr(chunk, "content", "")) for chunk in chunks))
        calls_value = getattr(source, "tool_calls", None)
        if not calls_value:
            calls_value = next((getattr(chunk, "tool_calls", None)
                                for chunk in reversed(chunks)
                                if getattr(chunk, "tool_calls", None)), None)
        yield ModelTurn(
            text=text,
            calls=_native_calls(calls_value),
            finish_reason=next((_finish_reason(chunk) for chunk in reversed(chunks)
                                if _finish_reason(chunk)), None),
            input_tokens=next((_usage_value(chunk, "input_tokens", "prompt_tokens")
                               for chunk in reversed(chunks)
                               if _usage_value(chunk, "input_tokens", "prompt_tokens") is not None), None),
            output_tokens=next((_usage_value(chunk, "output_tokens", "completion_tokens")
                                for chunk in reversed(chunks)
                                if _usage_value(chunk, "output_tokens", "completion_tokens") is not None), None),
        )


def transcript_to_messages(transcript: tuple[TranscriptMessage, ...]) -> list:
    messages = []
    for message in transcript:
        text = "".join(block.text or "" for block in message.blocks
                       if block.type is ContentBlockType.TEXT)
        if message.role is MessageRole.SYSTEM:
            messages.append(SystemMessage(content=text))
        elif message.role is MessageRole.USER:
            messages.append(HumanMessage(content=text))
        elif message.role is MessageRole.ASSISTANT:
            calls = [{"id": block.call_id, "name": block.tool_name,
                      "args": block.arguments or {}}
                     for block in message.blocks
                     if block.type is ContentBlockType.TOOL_CALL]
            messages.append(AIMessage(content=text, tool_calls=calls))
        elif message.role is MessageRole.TOOL:
            for block in message.blocks:
                if block.type is not ContentBlockType.TOOL_RESULT:
                    continue
                result = block.result or {}
                content = result.get("output") or result.get("error") or ""
                if not isinstance(content, str):
                    content = json.dumps(content, sort_keys=True)
                messages.append(ToolMessage(content=content,
                                            tool_call_id=block.call_id or ""))
    return messages


def _native_calls(value) -> tuple[ToolCall, ...]:
    if not value:
        return ()
    calls = []
    for raw in value:
        if not isinstance(raw, dict):
            raise TypeError("native tool call must be an object")
        arguments = raw.get("args", raw.get("arguments", {}))
        if not isinstance(arguments, dict):
            raise TypeError("native tool call arguments must be an object")
        calls.append(ToolCall(id=str(raw.get("id") or ""),
                              name=str(raw.get("name") or ""),
                              arguments=arguments))
    return tuple(calls)


def _text_content(value) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "".join(str(item.get("text", "")) for item in value
                   if isinstance(item, dict) and item.get("type") == "text")


def _usage_value(chunk, primary: str, legacy: str) -> int | None:
    usage = getattr(chunk, "usage_metadata", None)
    if isinstance(usage, dict):
        value = usage.get(primary, usage.get(legacy))
        return value if isinstance(value, int) else None
    metadata = getattr(chunk, "response_metadata", None)
    if isinstance(metadata, dict):
        nested = metadata.get("usage") or metadata.get("token_usage") or {}
        if isinstance(nested, dict):
            value = nested.get(primary, nested.get(legacy))
            return value if isinstance(value, int) else None
    return None


def _finish_reason(chunk) -> str | None:
    metadata = getattr(chunk, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("finish_reason") or metadata.get("stop_reason")
    return str(value) if value is not None else None
