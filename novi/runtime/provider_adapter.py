"""Strict adapter from LangChain provider messages to replacement-runtime turns."""

from __future__ import annotations

import base64
import json
from pathlib import Path
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
        # Provider-neutral accumulation: support finalized tool_calls,
        # streamed tool_call_chunks with partial JSON deltas, and
        # equivalent native representations (additional_kwargs).
        pending: dict[object, dict] = {}  # index/id -> {id, name, args}
        finalized: dict[str, dict] = {}  # id -> {id, name, args dict}
        accumulated_text = ""
        finish_reason: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        any_chunk = False
        for chunk in self._model.stream(messages):
            any_chunk = True
            content = getattr(chunk, "content", "")
            txt = _text_content(content)
            reasoning = (getattr(chunk, "additional_kwargs", None) or {}).get("reasoning_content", "")
            if isinstance(reasoning, str) and reasoning:
                yield ModelTurn(reasoning_delta=reasoning)
            if txt:
                accumulated_text += txt
                # Yield incremental text delta
                yield ModelTurn(
                    text=accumulated_text,
                    calls=(),
                    finish_reason=None,
                    input_tokens=None,
                    output_tokens=None,
                    is_complete=False,
                )
            # --- tool_call_chunks (streamed partials, provider-neutral) ---
            tcc = getattr(chunk, "tool_call_chunks", None)
            if isinstance(tcc, list) and tcc:
                for raw in tcc:
                    if not isinstance(raw, dict):
                        continue
                    idx = raw.get("index")
                    if isinstance(idx, int):
                        key: object = idx
                    elif raw.get("id"):
                        key = str(raw.get("id"))
                    else:
                        # Single anonymous call without index/id
                        key = 0
                        if key in pending and pending[key].get("name") and raw.get("name") and pending[key]["name"] != raw.get("name"):
                            # Multiple anonymous calls without index – allocate new integer key
                            key = max([k for k in pending.keys() if isinstance(k, int)], default=0) + 1
                    if key not in pending:
                        pending[key] = {"id": None, "name": None, "args": ""}
                    if raw.get("id"):
                        if not pending[key]["id"]:
                            pending[key]["id"] = str(raw["id"])
                    if raw.get("name"):
                        pending[key]["name"] = str(raw["name"])
                    args_val = raw.get("args")
                    if args_val is not None:
                        if isinstance(args_val, dict):
                            pending[key]["args"] += json.dumps(args_val)
                        elif isinstance(args_val, str):
                            pending[key]["args"] += args_val
            # --- finalized tool_calls (dict args, non-streaming) ---
            # Only treat as independent source when tool_call_chunks absent,
            # because AIMessageChunk validator derives tool_calls from chunks.
            tc_list = getattr(chunk, "tool_calls", None)
            has_tcc = isinstance(tcc, list) and bool(tcc)
            if isinstance(tc_list, list) and tc_list and not has_tcc:
                for raw in tc_list:
                    if not isinstance(raw, dict):
                        continue
                    cid = raw.get("id") or raw.get("call_id") or ""
                    cname = raw.get("name") or raw.get("function", {}).get("name") if isinstance(raw.get("function"), dict) else raw.get("name") or ""
                    if not cname:
                        cname = str(raw.get("name") or "")
                    cargs = raw.get("args", raw.get("arguments", {}))
                    if isinstance(cargs, str):
                        parsed = _parse_args_string(cargs)
                        cargs = parsed if isinstance(parsed, dict) else {}
                    if not isinstance(cargs, dict):
                        cargs = {}
                    key_str = str(cid) if cid else f"auto-{len(finalized)}"
                    finalized[key_str] = {"id": str(cid) if cid else key_str, "name": str(cname), "args": cargs}
            # --- additional_kwargs native representation ---
            ak = getattr(chunk, "additional_kwargs", None)
            if isinstance(ak, dict) and not has_tcc and not (isinstance(tc_list, list) and tc_list):
                ak_tc = ak.get("tool_calls")
                if isinstance(ak_tc, list) and ak_tc:
                    for raw in ak_tc:
                        if not isinstance(raw, dict):
                            continue
                        cid = raw.get("id") or ""
                        func = raw.get("function", {})
                        if isinstance(func, dict):
                            cname = func.get("name") or raw.get("name") or ""
                            args_raw: object = func.get("arguments", raw.get("args", {}))
                        else:
                            cname = raw.get("name") or ""
                            args_raw = raw.get("args", {})
                        if isinstance(args_raw, str):
                            parsed = _parse_args_string(args_raw)
                            args_dict = parsed if isinstance(parsed, dict) else {}
                        elif isinstance(args_raw, dict):
                            args_dict = args_raw
                        else:
                            args_dict = {}
                        key_str = str(cid) if cid else f"ak-{len(finalized)}"
                        finalized[key_str] = {"id": str(cid) if cid else key_str, "name": str(cname), "args": args_dict}
            fr = _finish_reason(chunk)
            if fr:
                finish_reason = fr
            it = _usage_value(chunk, "input_tokens", "prompt_tokens")
            if it is not None:
                input_tokens = it
            ot = _usage_value(chunk, "output_tokens", "completion_tokens")
            if ot is not None:
                output_tokens = ot

        if not any_chunk:
            return

        # Build final tool calls
        calls: list[ToolCall] = []
        if pending:
            for key, data in pending.items():
                cid = data["id"] or f"call-{key}"
                cname = data["name"] or ""
                args_str = data["args"] or ""
                if not cid or not cname:
                    continue
                args_dict: dict = {}
                if args_str.strip():
                    parsed = _parse_args_string(args_str)
                    if parsed is None or not isinstance(parsed, dict):
                        # Malformed/incomplete args – skip this call safely
                        continue
                    args_dict = parsed
                try:
                    calls.append(ToolCall(id=str(cid), name=str(cname), arguments=args_dict))
                except ValueError:
                    continue
        elif finalized:
            for key, data in finalized.items():
                try:
                    calls.append(ToolCall(id=str(data["id"]), name=str(data["name"]), arguments=data["args"] if isinstance(data["args"], dict) else {}))
                except ValueError:
                    continue
        # Deduplicate by id, preserve order
        seen: set[str] = set()
        uniq: list[ToolCall] = []
        for c in calls:
            if c.id in seen:
                continue
            seen.add(c.id)
            uniq.append(c)

        # Yield final turn with tool calls (if any) and usage info
        yield ModelTurn(
            text=accumulated_text,
            calls=tuple(uniq),
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_complete=True,
        )


def transcript_to_messages(transcript: tuple[TranscriptMessage, ...]) -> list:
    messages = []
    for message in transcript:
        text = "".join(block.text or "" for block in message.blocks
                       if block.type is ContentBlockType.TEXT)
        if message.role is MessageRole.SYSTEM:
            messages.append(SystemMessage(content=text))
        elif message.role is MessageRole.USER:
            attachments = [block for block in message.blocks
                           if block.type is ContentBlockType.ATTACHMENT]
            if not attachments:
                messages.append(HumanMessage(content=text))
                continue
            content: list[dict] = [{"type": "text", "text": text}]
            for block in attachments:
                item = block.result or {}
                if item.get("type") != "image":
                    continue
                path = Path(str(item.get("path") or ""))
                if not path.is_file():
                    content.append({"type": "text", "text":
                                    f"[Image {item.get('name') or block.artifact_id} could not be loaded]"})
                    continue
                mime = str(item.get("mime") or "image/png")
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append({"type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{encoded}"}})
            messages.append(HumanMessage(content=content))
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


def _parse_args_string(value: str) -> dict | None:
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        return None


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
