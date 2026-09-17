from novi.runtime.agent_events import AgentEvent, EVENT_TYPES


def test_event_message_id_stable():
    e1 = AgentEvent(type="message.started", run_id="r1", conversation_id="c1", message_id="m1")
    assert e1.message_id == "m1"
    assert AgentEvent(type="tool.started", run_id="r1", conversation_id="c1").message_id is None


def test_event_types_include_lifecycle():
    assert "run.started" in EVENT_TYPES
    assert "run.completed" in EVENT_TYPES
    assert "run.failed" in EVENT_TYPES
    assert "run.cancelled" in EVENT_TYPES
    assert "message.started" in EVENT_TYPES
    assert "message.delta" in EVENT_TYPES
    assert "message.completed" in EVENT_TYPES
    assert "context.compacting" in EVENT_TYPES
    assert "context.compacted" in EVENT_TYPES
