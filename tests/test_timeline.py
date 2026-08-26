"""Milestone 4 Phase A tests: timeline store, service bridge, knowledge overview.

Covers:
- TimelineStore JSONL persistence (bounded, newest-first, reload).
- TimelineService bridging Brain events → user-facing entries + on_entry callback.
- build_knowledge_overview user-shaped output (no internal ids/scores/paths).
"""

import pytest

from novi.runtime.event_bus import EventBus
from novi.timeline import (
    CONVERSATION_OBSERVED,
    JOB_CHECKPOINTED,
    JOB_COMPLETED,
    JOB_CREATED,
    JOB_FAILED,
    JOB_INTERRUPTED,
    JOB_STARTED,
    KNOWLEDGE_EXTRACTED,
    KNOWLEDGE_PROMOTED,
    SURFACED_EVENTS,
    TimelineService,
    TimelineStore,
    build_knowledge_overview,
)


# ── TimelineStore persistence ─────────────────────────────────────────────


def test_store_persists_and_lists_newest_first(tmp_path):
    store = TimelineStore(persist_dir=tmp_path)
    e1 = store.append({"kind": "a", "title": "A", "detail": "x"})
    e2 = store.append({"kind": "b", "title": "B", "detail": "y"})
    assert e1["id"] and e1["timestamp"]
    assert e2["id"] != e1["id"]

    # New instance reading the same file sees prior entries, newest first.
    reloaded = TimelineStore(persist_dir=tmp_path)
    rows = reloaded.list()
    assert [r["title"] for r in rows] == ["B", "A"]


def test_store_bounded(tmp_path):
    store = TimelineStore(persist_dir=tmp_path, max_entries=5)
    for i in range(12):
        store.append({"kind": "x", "title": f"t{i}", "detail": str(i)})
    rows = store.list()
    assert len(rows) == 5
    # Only the 5 newest survive.
    assert rows[0]["title"] == "t11"


def test_store_clear(tmp_path):
    store = TimelineStore(persist_dir=tmp_path)
    store.append({"kind": "a", "title": "A", "detail": "x"})
    store.clear()
    assert store.list() == []


# ── TimelineService event bridge ──────────────────────────────────────────


def _service(tmp_path, bus):
    captured = []
    store = TimelineStore(persist_dir=tmp_path)
    svc = TimelineService(bus, store=store, on_entry=captured.append)
    svc.start()
    return svc, store, captured


def test_bridge_surfaces_conversation_observed(tmp_path):
    bus = EventBus()
    svc, store, captured = _service(tmp_path, bus)

    bus.emit(CONVERSATION_OBSERVED, conversation_id="conv-1",
             user="Tell me about local AI", assistant="Sure.", timestamp="2026-08-05T10:00:00")

    rows = svc.recent()
    assert len(rows) == 1
    assert rows[0]["kind"] == CONVERSATION_OBSERVED
    assert rows[0]["title"] == "Conversation logged"
    assert "local AI" in rows[0]["detail"]
    # Internal conversation id must NOT leak to the entry.
    assert "conv-x" not in rows[0]
    assert len(captured) == 1


def test_bridge_surfaces_knowledge_extracted(tmp_path):
    bus = EventBus()
    svc, store, captured = _service(tmp_path, bus)
    bus.emit(KNOWLEDGE_EXTRACTED, knowledge_ids=["k1", "k2"],
             conversation_id="conv-z", scenario_id="sc-1",
             summary="Prefers local-first AI systems", timestamp="2026-08-05T10:40:00")

    entry = svc.recent()[0]
    assert entry["title"] == "Memory updated"
    assert "local-first AI" in entry["detail"]
    # Internal identifiers never leak.
    assert "k1" not in entry["detail"]
    assert "sc-1" not in entry["detail"]


def test_bridge_surfaces_knowledge_promoted(tmp_path):
    bus = EventBus()
    svc, store, captured = _service(tmp_path, bus)

    bus.emit(KNOWLEDGE_PROMOTED, item_ids=["i1"], promotions=2,
             corroborated=1, superseded=1, conflicts=0, timestamp="2026-08-05T11:00:00")

    entry = svc.recent()[0]
    assert entry["kind"] == KNOWLEDGE_PROMOTED
    assert entry["title"] == "Knowledge refined"
    assert "2" in entry["detail"]
    assert "i1" not in entry["detail"]


def test_bridge_ignores_non_surfaced_events(tmp_path):
    bus = EventBus()
    svc, store, captured = _service(tmp_path, bus)

    bus.emit("tool_called", tool="read", args={}, call_id="c1")
    bus.emit("knowledge.extracted.other", summary="should be ignored")

    assert svc.recent() == []
    assert captured == []


def test_bridge_persists_to_disk(tmp_path):
    bus = EventBus()
    svc, store, _ = _service(tmp_path, bus)
    bus.emit(CONVERSATION_OBSERVED, user="hello", assistant="hi",
             timestamp="2026-08-05T10:00:00")

    # A fresh store over the same dir still sees it.
    fresh = TimelineStore(persist_dir=tmp_path)
    assert fresh.list()[0]["kind"] == CONVERSATION_OBSERVED


def test_surfaced_events_set_is_exactly_three():
    assert SURFACED_EVENTS == {
        CONVERSATION_OBSERVED, KNOWLEDGE_EXTRACTED, KNOWLEDGE_PROMOTED,
        JOB_CREATED, JOB_STARTED, JOB_COMPLETED, JOB_FAILED,
        JOB_CHECKPOINTED, JOB_INTERRUPTED,
    }


def test_webui_bridge_wires_service_from_context(tmp_path, monkeypatch):
    """The WebUI read-only bridge subscribes to the context's brain bus and
    surfaces brain events into the timeline (never into the brain)."""
    import novi.timeline.timeline_service as ts_mod
    from novi.timeline.timeline_store import TimelineStore

    class _TempStore(TimelineStore):
        def __init__(self, *a, **k):
            super().__init__(persist_dir=tmp_path, *a, **k)

    monkeypatch.setattr(ts_mod, "TimelineStore", _TempStore)

    from novi.webui_server import _build_timeline_bridge

    bus = EventBus()

    class _FakeCtx:
        @property
        def brain_event_bus(self):
            return bus

    service = _build_timeline_bridge({"context": _FakeCtx()})
    assert service is not None

    bus.emit(CONVERSATION_OBSERVED, user="hello world", assistant="hi",
             timestamp="2026-08-05T10:00:00")
    bus.emit(KNOWLEDGE_EXTRACTED, summary="Learned a preference",
             conversation_id="c", scenario_id="s", timestamp="2026-08-05T10:05:00")

    rows = service.recent()
    assert len(rows) == 2
    assert rows[0]["title"] == "Memory updated"
    assert "preference" in rows[0]["detail"]
    assert all("scenario" not in str(r) for r in rows)


def test_webui_bridge_disabled_without_context():
    from novi.webui_server import _build_timeline_bridge
    assert _build_timeline_bridge({}) is None


# ── Knowledge overview shape ──────────────────────────────────────────────


class _FakeBrain:
    def __init__(self, view):
        self._view = view

    def inspect_memory(self):
        return self._view


def test_knowledge_overview_without_brain():
    out = build_knowledge_overview(None)
    assert out == {"categories": [], "total": 0, "updated": ""}


def test_knowledge_overview_shape_and_privacy():
    brain = _FakeBrain({
        "categories": {
            "preference": [
                {"content": "Prefers local AI models", "evidence": "verified"},
                {"content": "Likes architectural explanations", "evidence": "candidate"},
            ],
            "project": [
                {"content": "Novi", "evidence": "corroborated"},
            ],
        },
        "items": [
            {"id": "i-1", "last_seen_at": "2026-08-05T11:00:00", "status": "verified"},
            {"id": "i-2", "last_seen_at": "2026-08-04T11:00:00", "status": "candidate"},
        ],
    })
    out = build_knowledge_overview(brain)

    assert out["total"] == 3
    assert out["updated"] == "2026-08-05T11:00:00"

    by_cat = {c["category"]: c for c in out["categories"]}
    assert by_cat["preference"]["label"] == "Preferences"
    assert by_cat["project"]["label"] == "Projects"

    pref = by_cat["preference"]["entries"][0]
    assert set(pref.keys()) == {"content", "evidence"}
    assert pref["content"] == "Prefers local AI models"

    # Forbidden internal surfaces must never appear as keys at any nesting level.
    forbidden = {"id", "knowledge_id", "item_id", "scenario_id", "score",
                 "distance", "importance", "status", "path", "metadata"}
    for values in [out] + list(out.values()):
        _assert_no_forbidden_keys(values, forbidden, out)


def _assert_no_forbidden_keys(value, forbidden, root):
    if isinstance(value, dict):
        for k, v in value.items():
            assert k not in forbidden, f"knowledge overview leaked key {k!r}: {root}"
            _assert_no_forbidden_keys(v, forbidden, root)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_keys(item, forbidden, root)