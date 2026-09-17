from novi.runtime.agent_action import AgentActionType, AgentAction


def test_agent_action_types_limited_to_three():
    assert AgentActionType.CONTINUE.value == "continue"
    assert AgentActionType.PROGRESS.value == "progress"
    assert AgentActionType.FINISH.value == "finish"
    assert not hasattr(AgentActionType, "TOOL")


def test_agent_action_progress_requires_message():
    a = AgentAction(type=AgentActionType.PROGRESS, message="found files")
    assert a.message == "found files"


def test_agent_action_never_carries_tool_calls():
    a = AgentAction(type=AgentActionType.CONTINUE)
    assert not hasattr(a, "tool_calls")
