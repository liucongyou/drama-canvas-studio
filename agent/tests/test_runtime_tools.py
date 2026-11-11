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
    monkeypatch.setattr(runtime, "chat_tool_round_stream", lambda *args, **kwargs: next(rounds))
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


def test_tool_loop_streams_only_final_round(monkeypatch) -> None:
    rounds = iter(
        [
            ToolRound(
                content="先核验项目上下文",
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
    streamed: list[str] = []

    def fake_stream(*_args, on_content=None, **_kwargs):
        round_ = next(rounds)
        if on_content and not round_.tool_calls:
            on_content(round_.content)
        return round_

    monkeypatch.setattr(runtime, "chat_tool_round_stream", fake_stream)
    content, calls = runtime.execute_tool_loop(
        "system",
        [{"role": "user", "content": "hello"}],
        FakeTools(),
        {"projectId": "p1"},
        on_content=streamed.append,
    )
    assert content == '{"title":"x"}'
    assert calls[0]["name"] == "get_project_index"
    assert streamed == ["\n\n", '{"title":"x"}']


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

    monkeypatch.setattr(runtime, "chat_tool_round_stream", always_tool)
    with pytest.raises(runtime.AgentToolLimitError):
        runtime.execute_tool_loop(
            "system",
            [{"role": "user", "content": "hello"}],
            FakeTools(),
            {"projectId": "p1"},
            model_id="m1",
            max_steps=2,
        )


def test_tool_loop_can_finish_after_twelve_steps(monkeypatch) -> None:
    calls_seen = 0

    def many_tools(*_args, **_kwargs):
        nonlocal calls_seen
        calls_seen += 1
        if calls_seen <= 20:
            return ToolRound(
                content="",
                tool_calls=[
                    {
                        "id": f"call-{calls_seen}",
                        "type": "function",
                        "function": {
                            "name": "get_project_index",
                            "arguments": (
                                f'{{"projectId":"p{calls_seen}"}}'
                            ),
                        },
                    }
                ],
                native=True,
            )
        return ToolRound(content='{"title":"finished"}', tool_calls=[], native=True)

    monkeypatch.setattr(runtime, "chat_tool_round_stream", many_tools)
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: type("Settings", (), {"agent_max_tool_steps": 30})(),
    )

    content, calls = runtime.execute_tool_loop(
        "system",
        [{"role": "user", "content": "hello"}],
        FakeTools(),
        {"projectId": "p1"},
    )

    assert content == '{"title":"finished"}'
    assert len(calls) == 20


def test_tool_loop_stops_repeated_identical_calls(monkeypatch) -> None:
    call_number = 0

    def repeated(*_args, **_kwargs):
        nonlocal call_number
        call_number += 1
        return ToolRound(
            content="",
            tool_calls=[
                {
                    "id": f"call-{call_number}",
                    "type": "function",
                    "function": {
                        "name": "get_project_index",
                        "arguments": "{}",
                    },
                }
            ],
            native=True,
        )

    monkeypatch.setattr(runtime, "chat_tool_round_stream", repeated)
    with pytest.raises(runtime.AgentToolLimitError, match="相同参数重复调用"):
        runtime.execute_tool_loop(
            "system",
            [{"role": "user", "content": "hello"}],
            FakeTools(),
            {"projectId": "p1"},
            max_steps=30,
        )


def test_system_prompt_injects_runtime_quality_contract(tmp_path) -> None:
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# skill", encoding="utf-8")
    (root / "references" / "runtime-quality-contract.md").write_text(
        "逐镜 15 项 fail-closed 自检",
        encoding="utf-8",
    )
    prompt = runtime._system_prompt(
        {
            "id": "skill",
            "version": "7.4.30",
            "rootPath": str(root),
            "manifest": {"entry": "SKILL.md"},
        }
    )
    assert "逐镜 15 项 fail-closed 自检" in prompt
    assert "read_skill_section" in prompt
