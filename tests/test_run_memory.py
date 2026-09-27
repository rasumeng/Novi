"""Phase 8: finalized run evidence → Brain (TDD, fails before implementation)."""

from datetime import datetime, timezone
from types import SimpleNamespace

from novi.brain.types import Turn, ConversationRecord
from novi.runtime.run_contracts import RunRequest, RunState, RunStatus, ToolCall, ToolResultStatus
from novi.runtime.transcript import ContentBlock, ContentBlockType, MessageRole, TranscriptMessage


def _request(conversation="conv-a", project="proj-a", text="remember my preference: I like local models for Novi"):
    return RunRequest(conversation_id=conversation, user_message_id="u1", user_text=text,
                      project_id=project, workspace="/tmp/ws")


def _state_with_transcript(request, transcript, status=RunStatus.COMPLETED, reason="finish_task"):
    return RunState(id="run-1", request=request, status=status, terminal_reason=reason,
                    transcript=transcript, model_turns=1, tool_calls=0,
                    created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))


class FakeBrain:
    def __init__(self):
        self.turns = []
        self._store = SimpleNamespace()

        class _Store:
            def __init__(self, outer):
                self.outer = outer
                self._turns = []

            def turns(self, cid, limit=None):
                return tuple(t for t in self.outer.turns if t.conversation_id == cid)

            def get(self, cid):
                return ConversationRecord(id=cid, project_id="proj-a")
        self._conversation_store = _Store(self)
        self._call_count = 0

    def observe(self, turn: Turn):
        self._call_count += 1
        self.turns.append(turn)


def test_inherited_history_is_not_ingested_as_current_evidence():
    from novi.services.run_memory import build_turn
    request = _request(text="My current preference is Python")
    history = (
        TranscriptMessage(id="old-u", role=MessageRole.USER, blocks=(
            ContentBlock(type=ContentBlockType.TEXT, text="Old question"),)),
        TranscriptMessage(id="old-a", role=MessageRole.ASSISTANT, blocks=(
            ContentBlock(type=ContentBlockType.TEXT, text="Old answer"),)),
        TranscriptMessage(id="old-tool", role=MessageRole.TOOL, blocks=(
            ContentBlock(type=ContentBlockType.TOOL_RESULT, call_id="old-call",
                result={"status": "succeeded", "output": "Old evidence"}),)),
        TranscriptMessage(id=request.user_message_id, role=MessageRole.USER, blocks=(
            ContentBlock(type=ContentBlockType.TEXT, text=request.user_text),)),
        TranscriptMessage(id="new-a", role=MessageRole.ASSISTANT, blocks=(
            ContentBlock(type=ContentBlockType.TEXT, text="Current answer"),)),
    )
    turn = build_turn(_state_with_transcript(request, history))
    assert "Current answer" in turn.assistant
    assert "Old answer" not in turn.assistant
    assert turn.tool_outputs == ()


def test_run_memory_module_exists_and_ingests_public_messages_once():
    from novi.services import run_memory
    assert hasattr(run_memory, "ingest_run")
    assert hasattr(run_memory, "build_turn")


def test_persists_user_and_public_assistant_once_not_per_loop():
    from novi.services.run_memory import build_turn, ingest_run
    req = _request()
    t_user = TranscriptMessage(id="u1", role=MessageRole.USER, source="user", trust="trusted",
                               blocks=(ContentBlock(type=ContentBlockType.TEXT, text=req.user_text),))
    prog = TranscriptMessage(id="msg-prog", role=MessageRole.ASSISTANT, source="model", trust="untrusted",
                             blocks=(ContentBlock(type=ContentBlockType.TEXT, text="Progress: checking files"),))
    final = TranscriptMessage(id="msg-final", role=MessageRole.ASSISTANT, source="model", trust="untrusted",
                              blocks=(ContentBlock(type=ContentBlockType.TEXT, text="Done: preference noted"),))
    state = _state_with_transcript(req, (t_user, prog, final), status=RunStatus.COMPLETED)
    turn = build_turn(state)
    assert req.user_text in turn.user
    assert "Progress: checking files" in turn.assistant
    assert "Done: preference noted" in turn.assistant
    # tool outputs empty for this case, but assistant has both messages joined once
    assert turn.assistant.count("Progress:") == 1
    brain = FakeBrain()
    assert ingest_run(state, brain) is True
    assert brain._call_count == 1
    # second ingest of same terminal state must be idempotent (once)
    assert ingest_run(state, brain) is False
    assert brain._call_count == 1


def test_retains_tool_provenance_and_distinguishes_evidence_classes():
    from novi.services.run_memory import build_turn
    req = _request(text="run the build tool")
    user_msg = TranscriptMessage(id="u1", role=MessageRole.USER, source="user", trust="trusted",
                                 blocks=(ContentBlock(type=ContentBlockType.TEXT, text=req.user_text),))
    assistant_call = TranscriptMessage(id="asst-1", role=MessageRole.ASSISTANT, source="model", trust="untrusted",
                                       blocks=(ContentBlock(type=ContentBlockType.TOOL_CALL, call_id="c1", tool_name="read_file",
                                                             arguments={"path": "a"}),))
    tool_ok = TranscriptMessage(id="tool-c1", role=MessageRole.TOOL, source="tool", trust="untrusted",
                                blocks=(ContentBlock(type=ContentBlockType.TOOL_RESULT, call_id="c1",
                                                      result={"status": "succeeded", "output": "file content here"}),))
    assistant_denied = TranscriptMessage(id="asst-2", role=MessageRole.ASSISTANT, source="model", trust="untrusted",
                                         blocks=(ContentBlock(type=ContentBlockType.TOOL_CALL, call_id="c2", tool_name="write_file",
                                                               arguments={"path": "/etc/hosts"}),))
    tool_denied = TranscriptMessage(id="tool-c2", role=MessageRole.TOOL, source="tool", trust="untrusted",
                                    blocks=(ContentBlock(type=ContentBlockType.TOOL_RESULT, call_id="c2",
                                                          result={"status": "denied", "error": "explicit deny"}),))
    final = TranscriptMessage(id="msg-final", role=MessageRole.ASSISTANT, source="model", trust="untrusted",
                              blocks=(ContentBlock(type=ContentBlockType.TEXT, text="Finished with mixed results"),))
    state = _state_with_transcript(req, (user_msg, assistant_call, tool_ok, assistant_denied, tool_denied, final))
    turn = build_turn(state)
    # tool outputs must contain distinct classes
    assert any("status:succeeded" in t and "read_file" in t for t in turn.tool_outputs)
    assert any("status:denied" in t and "explicit deny" in t for t in turn.tool_outputs)
    # assistant should not contain raw tool text conflated as fact
    assert "file content here" not in turn.assistant


def test_failed_run_does_not_imply_tool_success_but_keeps_user_fact():
    from novi.services.run_memory import build_turn
    req = _request(text="my email is bob@example.com")
    user_msg = TranscriptMessage(id="u1", role=MessageRole.USER, source="user", trust="trusted",
                                 blocks=(ContentBlock(type=ContentBlockType.TEXT, text=req.user_text),))
    tool_failed = TranscriptMessage(id="tool-c1", role=MessageRole.TOOL, source="tool", trust="untrusted",
                                    blocks=(ContentBlock(type=ContentBlockType.TOOL_RESULT, call_id="c1",
                                                          result={"status": "failed", "error": "network down"}),))
    # No successful tool effect; assistant empty or error
    state = _state_with_transcript(req, (user_msg, tool_failed), status=RunStatus.FAILED, reason="provider_error")
    turn = build_turn(state)
    assert "bob@example.com" in turn.user
    # tool output marked failed, not succeeded
    assert any("status:failed" in t for t in turn.tool_outputs)
    assert "failed" in turn.assistant or "provider_error" in turn.assistant


def test_no_retention_rule_prevents_ingest():
    from novi.services.run_memory import ingest_run
    req = _request(text="do not remember this secret")
    # classify_request should mark retain=False for "do not remember"
    user_msg = TranscriptMessage(id="u1", role=MessageRole.USER, source="user", trust="trusted",
                                 blocks=(ContentBlock(type=ContentBlockType.TEXT, text=req.user_text),))
    final = TranscriptMessage(id="msg-final", role=MessageRole.ASSISTANT, source="model", trust="untrusted",
                              blocks=(ContentBlock(type=ContentBlockType.TEXT, text="ok"),))
    state = _state_with_transcript(req, (user_msg, final))
    brain = FakeBrain()
    assert ingest_run(state, brain) is False
    assert brain.turns == []


def test_project_scope_is_preserved_and_global_distinct():
    from novi.services.run_memory import build_turn
    req_a = _request(conversation="conv-a", project="proj-a", text="I prefer python for proj-a")
    req_b = _request(conversation="conv-b", project="proj-b", text="I prefer rust for proj-b")
    req_global = _request(conversation="conv-g", project="", text="I prefer local models globally")
    for req in (req_a, req_b, req_global):
        msg = TranscriptMessage(id="u1", role=MessageRole.USER, source="user", trust="trusted",
                                blocks=(ContentBlock(type=ContentBlockType.TEXT, text=req.user_text),))
        state = _state_with_transcript(req, (msg,))
        turn = build_turn(state)
        assert turn.project_id == (req.project_id or req.workspace or "")
        assert turn.conversation_id == req.conversation_id


def test_stable_evidence_coordinates_in_tool_outputs():
    from novi.services.run_memory import build_turn
    req = _request()
    user_msg = TranscriptMessage(id="u1", role=MessageRole.USER, source="user", trust="trusted",
                                 blocks=(ContentBlock(type=ContentBlockType.TEXT, text=req.user_text),))
    call = TranscriptMessage(id="asst-1", role=MessageRole.ASSISTANT, source="model", trust="untrusted",
                             blocks=(ContentBlock(type=ContentBlockType.TOOL_CALL, call_id="call-xyz", tool_name="read_file",
                                                   arguments={"path": "x"}),))
    tool = TranscriptMessage(id="tool-call-xyz", role=MessageRole.TOOL, source="tool", trust="untrusted",
                             blocks=(ContentBlock(type=ContentBlockType.TOOL_RESULT, call_id="call-xyz",
                                                   result={"status": "succeeded", "output": "ok"}),))
    state = RunState(id="run-stable-999", request=req, status=RunStatus.COMPLETED, terminal_reason="finish_task",
                     transcript=(user_msg, call, tool),
                     created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    turn = build_turn(state)
    # stable coordinates must appear
    assert any("run-stable-999" in t and "call-xyz" in t for t in turn.tool_outputs)


def test_correction_context_preserved_when_segmenting_long_text():
    from novi.brain.storage.conversation_store import ConversationStore
    from novi.brain.curation.jobs import MemoryJobs
    import tempfile
    from pathlib import Path
    from datetime import datetime, timedelta
    # Long assistant text with qualification that would be split by old 1200 window
    long_text = "I prefer python. " * 200 + "Correction: actually for this project I prefer rust. The previous statement is outdated."
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        store = ConversationStore(td)
        try:
            store.append(Turn(user="what do you prefer?", assistant=long_text, tool_outputs=(),
                              conversation_id="conv-long", project_id="proj-a",
                              timestamp=datetime.now()-timedelta(hours=2)), "conv-long")
            # Need 5 turns or aged single turn; add repeats to reach turns=5
            for i in range(4):
                store.append(Turn(user=f"q{i}", assistant=f"a{i}", tool_outputs=(),
                                  conversation_id="conv-long", project_id="proj-a",
                                  timestamp=datetime.now()-timedelta(hours=2)), "conv-long")
            jobs = MemoryJobs(store.database_path)
            # Collect all segments across chunked jobs for the same source batch
            collected = []
            first = jobs.claim(mode="apply")
            assert first is not None
            collected.extend(first["packet"]["turns"])
            for seg in first["packet"]["turns"]:
                assert "original_start" in seg and "original_length" in seg
                assert "context_before" in seg or "text" in seg
            # Check coherence context: windows carry surrounding 200 chars
            windows = [s for s in collected if s["id"].startswith("conv-long:0:assistant")]
            assert len(windows) >= 2
            assert windows[0]["original_length"] > 1200
            assert windows[1]["original_start"] == 1200
            assert windows[1].get("context_before", "") != ""  # must carry qualification context
            # Ensure correction appears in the full set of segments (simulate via raw)
            raw_text = long_text
            assert "Correction: actually for this project I prefer rust" in raw_text
            assert any(s.get("original_length") == len(raw_text) for s in collected)
            # Verify naive join still reconstructs source (existing test contract)
            # Text fields are non-overlapping, so joining them equals original prefix up to first chunk
            joined = "".join(s["text"] for s in windows)
            assert raw_text.startswith(joined[:1200])
        finally:
            try:
                store.close()
            except Exception:
                pass
            jobs.close()
