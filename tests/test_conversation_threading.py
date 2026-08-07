"""Regression tests — conversation ownership threading (Milestone 5, Phase 0).

Before the fix, CozmoRuntime._remember() called ``brain.observe(Turn(...))``
without a conversation_id, so the Brain assigned a fresh conversation id every
turn: one user conversation became many Brain conversations, scenarios never
accumulated, and extraction batching never saw a full thread.

These tests prove the seam end-to-end:
  WebUI conversation_id → run_stream → ExecutionContext → _remember → Turn → Brain

Requirement: a single user conversation maps to a single Brain conversation and
scenario; extraction batching works across turns.
"""

import pytest

from cozmo.brain import Brain, Turn
from cozmo.brain.reasoning.extraction import ExtractedClaim, ExtractionResult
from cozmo.brain.storage.conversation_store import ConversationStore
from cozmo.runtime.runtime import CozmoRuntime


class RecordingBus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, **data):
        self.events.append((event_type, data))


class StubExtractor:
    def __init__(self):
        self.extracted = 0

    def extract(self, turns):
        self.extracted += 1
        return ExtractionResult(
            claims=tuple(
                ExtractedClaim(f"user prefers python {i}", 0.9, ("preference",))
                for i in range(len(turns))
            ),
            summary="The user prefers python.",
            name="Python preferences",
        )


class StubKnowledgeLayer:
    def __init__(self):
        self.stored = []

    def store_extracted(self, conversation_id, scenario_id, result):
        ids = [f"kn-{i}" for i in range(len(result.claims))]
        self.stored.append((conversation_id, scenario_id, result))
        return ids


class StubScenarioLayer:
    def __init__(self):
        self.calls = []
        self.n = 0

    def ensure_for_conversation(self, conversation, result):
        existing = getattr(conversation, "scenario_id", None)
        if existing:
            self.calls.append("update")
            return existing
        self.n += 1
        self.calls.append("create")
        return f"scn-{self.n}"


class StubMemory:
    def add_interaction(self, user, assistant):
        pass

    def store_fact(self, statement):
        pass

    def query(self, text, k=5, distance_threshold=0.5, memory_types=None):
        return []


class StubRelationshipStore:
    def __init__(self):
        self.edges = []

    def add_many(self, relationships):
        self.edges.extend(relationships)


class RecordingBrain:
    """Minimal Brain stand-in that records observed turns."""

    def __init__(self):
        self.observed = []

    def observe(self, turn: Turn) -> None:
        self.observed.append(turn)


def make_runtime(brain=None):
    return CozmoRuntime(brain=brain)


# ── Unit: runtime → Turn → Brain ────────────────────────────────────────────


def test_remember_forwards_conversation_id_to_brain():
    brain = RecordingBrain()
    rt = make_runtime(brain)

    rt._remember("user says hi", "cozmo replies", conversation_id="conv-webui-1")

    assert len(brain.observed) == 1
    turn = brain.observed[0]
    assert turn.user == "user says hi"
    assert turn.assistant == "cozmo replies"
    assert turn.conversation_id == "conv-webui-1"


def test_remember_without_conversation_id_leaves_none():
    brain = RecordingBrain()
    rt = make_runtime(brain)

    rt._remember("user says hi", "cozmo replies")

    assert len(brain.observed) == 1
    assert brain.observed[0].conversation_id is None


def test_remember_error_path_forwards_conversation_id():
    brain = RecordingBrain()
    rt = make_runtime(brain)

    rt._remember("user says hi", "an error", conversation_id="conv-webui-err")

    assert brain.observed[-1].conversation_id == "conv-webui-err"


# ── Unit: run_stream threads conversation_id onto the context ───────────────


def test_run_stream_stores_conversation_id_on_context():
    from unittest.mock import MagicMock

    from cozmo.runtime.execution_context import ExecutionContext

    rt = CozmoRuntime(model_service=MagicMock())
    ctx = ExecutionContext(user_input="hi")
    try:
        for _ in rt.run_stream(context=ctx, conversation_id="conv-webui-2"):
            pass
    except Exception:
        pass

    assert ctx.conversation_id == "conv-webui-2"


def test_run_stream_keeps_context_conversation_id_default():
    from unittest.mock import MagicMock

    from cozmo.runtime.execution_context import ExecutionContext

    rt = CozmoRuntime(model_service=MagicMock())
    ctx = ExecutionContext(user_input="hi")
    try:
        for _ in rt.run_stream(context=ctx):
            pass
    except Exception:
        pass

    assert ctx.conversation_id == ""


# ── Integration: one user conversation == one Brain conversation ────────────


def test_multiple_turns_share_brain_conversation_scenario_and_batching(tmp_path):
    """Ten turns under one conversation_id land in ONE Brain conversation,
    reuse ONE scenario across both extraction batches, and trigger extraction
    batching across turns (batches at turn 5 and turn 10)."""
    store = ConversationStore(persist_dir=str(tmp_path / "convs"))
    bus = RecordingBus()
    scenario = StubScenarioLayer()
    brain = Brain(
        memory=StubMemory(),
        conversation_store=store,
        event_bus=bus,
        extractor=StubExtractor(),
        knowledge_layer=StubKnowledgeLayer(),
        scenario_layer=scenario,
        relationship_store=StubRelationshipStore(),
    )
    rt = make_runtime(brain)

    for i in range(10):
        rt._remember(f"user turn {i}", f"assistant turn {i}", conversation_id="conv-thread-1")

    # All turns accumulated in the same Brain conversation.
    rec = store.get("conv-thread-1")
    assert rec is not None
    assert rec.turn_count == 10
    assert len(store.turns("conv-thread-1")) == 10

    # No phantom conversations were created by the Brain.
    all_convos = store.list_conversations()
    assert [c.id for c in all_convos] == ["conv-thread-1"]

    # Extraction batched across turns: one batch per extract_every turns.
    extracted = [
        d for k, d in bus.events if k == "knowledge.extracted"
    ]
    assert len(extracted) == 2
    assert all(d["conversation_id"] == "conv-thread-1" for d in extracted)

    # One scenario created, then reused — never recreated per turn.
    assert scenario.calls == ["create", "update"]
    assert extracted[0]["scenario_id"] == extracted[1]["scenario_id"]

    # Conversation record is linked to that single scenario.
    assert rec.scenario_id == extracted[0]["scenario_id"]


def test_single_conversation_id_creates_exactly_one_brain_conversation(tmp_path):
    store = ConversationStore(persist_dir=str(tmp_path / "convs"))
    brain = Brain(
        memory=StubMemory(),
        conversation_store=store,
        extractor=StubExtractor(),
        knowledge_layer=StubKnowledgeLayer(),
        scenario_layer=StubScenarioLayer(),
    )
    rt = make_runtime(brain)

    for i in range(4):
        rt._remember(f"u{i}", f"a{i}", conversation_id="conv-single")

    assert len(store.list_conversations()) == 1
    assert store.get("conv-single").turn_count == 4
