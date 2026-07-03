from __future__ import annotations

from agent.agent import AgentLoop
from agent.events import AgentState
from agent.messages import Completion
from agent.providers.base import Provider
from agent.tools.approval import (
    ApprovalChoice,
    ApprovalLevel,
    ApprovalManager,
    ApprovalRequirement,
)
from agent.tools.base import Tool


class ApprovalProvider(Provider):
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, model, options=None):
        self.calls += 1
        if self.calls == 1:
            return Completion(
                message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "mutate",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            )
        return Completion(
            message={"role": "assistant", "content": "finished"}
        )


def approval_requirement(arguments):
    return ApprovalRequirement(
        ApprovalLevel.ASK, "mutate state", "test mutation", "mutation:key"
    )


def test_agent_emits_approval_event_before_mutation(tmp_path) -> None:
    executed: list[bool] = []
    tool = Tool(
        "mutate",
        "Mutate test state.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda: executed.append(True) or "mutated",
        approval_requirement,
    )
    approvals = ApprovalManager(user_home=tmp_path)
    approvals.set_callback(
        lambda request, timeout: ApprovalChoice.ONCE
    )
    loop = AgentLoop(
        provider=ApprovalProvider(),
        model="model",
        tools=(tool,),
        approval_manager=approvals,
    )

    events = list(loop.stream_events([], "change it"))

    assert executed == [True]
    awaiting = [
        event for event in events
        if event.state == AgentState.AWAITING_APPROVAL
    ]
    assert len(awaiting) == 1
    assert awaiting[0].reason == "test mutation"
    assert awaiting[0].choices == ("once", "session", "always", "deny")


def test_denied_operation_has_no_side_effect(tmp_path) -> None:
    executed: list[bool] = []
    tool = Tool(
        "mutate",
        "Mutate test state.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda: executed.append(True) or "mutated",
        approval_requirement,
    )
    loop = AgentLoop(
        provider=ApprovalProvider(),
        model="model",
        tools=(tool,),
        approval_manager=ApprovalManager(user_home=tmp_path),
    )
    messages = []

    assert loop.run_turn(messages, "change it") == "finished"
    assert executed == []
    assert "Operation was not executed" in messages[2]["content"]
