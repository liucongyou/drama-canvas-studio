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


def test_main_generation_uses_single_context_and_single_model_call(monkeypatch) -> None:
    source = runtime.__file__
    text = __import__("pathlib").Path(source).read_text(encoding="utf-8")
    assert '("get_project_context", {"nodeLimit": 160})' in text
    assert "for chunk in chat_stream(" in text
    assert "execute_tool_loop(\n                system_prompt" not in text
    assert "模型调用 1 次" in text


def test_system_prompt_injects_runtime_quality_contract(tmp_path) -> None:
    root = tmp_path / "skill"
    entry_root = root / "short-drama-director"
    (entry_root / "references").mkdir(parents=True)
    (entry_root / "SKILL.md").write_text("# skill", encoding="utf-8")
    (entry_root / "references" / "runtime-quality-contract.md").write_text(
        "逐镜 15 项 fail-closed 自检",
        encoding="utf-8",
    )
    (entry_root / "references" / "short-video-platform-and-hook-playbook.md").write_text(
        "前 3 秒必须给出观看理由",
        encoding="utf-8",
    )
    (entry_root / "references" / "agent-cinematic-grammar-contract.md").write_text(
        "镜头四问：信息还是反应；画左画右与视线落点必须写死",
        encoding="utf-8",
    )
    (entry_root / "references" / "agent-aicg3d-recipe-contract.md").write_text(
        "每段最多一个主配方，配方不得覆盖正文 @ 引用",
        encoding="utf-8",
    )
    prompt = runtime._system_prompt(
        {
            "id": "skill",
            "version": "7.4.35",
            "rootPath": str(root),
            "manifest": {"entry": "short-drama-director/SKILL.md"},
        },
        recipe_bindings=[{
            "id": "handoff-ensemble-focus",
            "version": "1.0.0",
            "role": "primary",
            "segmentNo": 1,
            "slots": {},
        }],
    )
    assert "逐镜 15 项 fail-closed 自检" in prompt
    assert "镜头四问：信息还是反应" in prompt
    assert "前 3 秒必须给出观看理由" in prompt
    assert "每段最多一个主配方" in prompt
    assert "handoff-ensemble-focus" in prompt
    assert "不要请求额外工具" in prompt
