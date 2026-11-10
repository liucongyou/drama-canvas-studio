import pytest

from app import runtime
from app.llm import ToolRound


class FakeTools:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments, context):
        self.calls.append((name, arguments, context))
        return {"ok": True, "tool": name}


def test_tool_loop_executes_tool_then_final(monkeypatch) -> None:
    rounds = iter(
        [
            ToolRound(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "get_project_index",
                            "arguments": "{}",
                        },
                    }
                ],
                native=True,
            ),
            ToolRound(content='{"title":"x"}', tool_calls=[], native=True),
        ]
    )
    monkeypatch.setattr(runtime, "chat_tool_round", lambda *args, **kwargs: next(rounds))
    tools = FakeTools()
    content, calls = runtime.execute_tool_loop(
        "system",
        [{"role": "user", "content": "hello"}],
        tools,
        {"projectId": "p1"},
        model_id="m1",
        max_steps=12,
    )
    assert content == '{"title":"x"}'
    assert calls[0]["name"] == "get_project_index"
    assert tools.calls[0][0] == "get_project_index"


def test_tool_loop_stops_at_limit(monkeypatch) -> None:
    def always_tool(*_args, **_kwargs):
        return ToolRound(
            content="",
            tool_calls=[
                {
                    "id": "call-x",
                    "type": "function",
                    "function": {
                        "name": "get_project_index",
                        "arguments": "{}",
                    },
                }
            ],
            native=True,
        )

    monkeypatch.setattr(runtime, "chat_tool_round", always_tool)
    with pytest.raises(runtime.AgentToolLimitError):
        runtime.execute_tool_loop(
            "system",
            [{"role": "user", "content": "hello"}],
            FakeTools(),
            {"projectId": "p1"},
            model_id="m1",
            max_steps=2,
        )
