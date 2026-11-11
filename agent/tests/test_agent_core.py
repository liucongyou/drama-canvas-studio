from types import SimpleNamespace

import pytest

from app import llm
from app.llm import (
    ToolRound,
    _chat_payload,
    _effective_output_tokens,
    _merge_stream_tool_call,
    extract_json,
)
from app.skills import SkillError, _normalize_manifest


def test_extract_json_from_fence() -> None:
    assert extract_json('xxx\n`json\n{"a": 1}\n`\nyyy') == {"a": 1}


def test_extract_json_from_raw_object() -> None:
    assert extract_json('{"title": "x"}')["title"] == "x"


def test_chat_payload_requests_json_object_mode() -> None:
    payload = _chat_payload("system", "user", "test-model", 12_000)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 12_000
    assert payload["messages"][0] == {"role": "system", "content": "system"}
    assert payload["messages"][1] == {"role": "user", "content": "user"}


def test_stream_tool_call_deltas_are_assembled_by_index() -> None:
    calls: dict[int, dict] = {}
    _merge_stream_tool_call(calls, {
        "index": 0,
        "id": "call-1",
        "type": "function",
        "function": {"name": "get_", "arguments": '{"x":'},
    })
    _merge_stream_tool_call(calls, {
        "index": 0,
        "function": {"name": "project", "arguments": '1}'},
    })
    assert calls[0]["id"] == "call-1"
    assert calls[0]["function"]["name"] == "get_project"
    assert calls[0]["function"]["arguments"] == '{"x":1}'


def test_chat_tool_round_stream_emits_content_and_assembles_tools(monkeypatch) -> None:
    sse = [
        'data: {"choices":[{"delta":{"content":"正在"}}]}',
        'data: {"choices":[{"delta":{"content":"写入"}}]}',
        (
            'data: {"choices":[{"delta":{"tool_calls":['
            '{"index":0,"id":"call-1","type":"function",'
            '"function":{"name":"get_project_","arguments":"{\\"x\\":"}}]}}]}'
        ),
        (
            'data: {"choices":[{"delta":{"tool_calls":['
            '{"index":0,"function":{"name":"index","arguments":"1}"}}]}}]}'
        ),
        "data: [DONE]",
    ]

    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            return iter(sse)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, headers, json):
            assert method == "POST"
            assert json["stream"] is True
            return FakeResponse()

    chunks: list[str] = []
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        llm,
        "_target",
        lambda *args, **kwargs: (
            "https://example.test/v1",
            "test-key",
            "test-model",
            12_000,
        ),
    )
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(
            llm_mode="openai",
            request_timeout_seconds=1,
        ),
    )

    result = llm.chat_tool_round_stream(
        "system",
        [{"role": "user", "content": "generate"}],
        [],
        on_content=chunks.append,
    )

    assert chunks == ["正在", "写入"]
    assert result.content == "正在写入"
    assert result.tool_calls == [{
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "get_project_index",
            "arguments": '{"x":1}',
        },
    }]


def test_chat_falls_back_when_gateway_rejects_json_mode(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int, body: dict, text: str = ""):
            self.status_code = status_code
            self._body = body
            self.text = text

        def json(self):
            return self._body

    class FakeClient:
        calls: list[dict] = []

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            self.calls.append(dict(json))
            if "response_format" in json:
                return FakeResponse(
                    400,
                    {},
                    "response_format is not supported",
                )
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": '{"ok":true}'}}]},
            )

    FakeClient.calls = []
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)
    monkeypatch.setattr(llm, "resolve_text_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(
            llm_mode="openai",
            llm_base_url="https://example.test/v1",
            llm_api_key="test-key",
            llm_model="test-model",
            request_timeout_seconds=1,
            agent_min_output_tokens=12_000,
        ),
    )

    assert llm.chat("system", "user") == '{"ok":true}'
    assert "response_format" in FakeClient.calls[0]
    assert "response_format" not in FakeClient.calls[1]


def test_agent_output_budget_uses_model_limits(monkeypatch) -> None:
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(agent_min_output_tokens=12_000),
    )
    assert _effective_output_tokens({"maxTokens": 3000, "maxOutputTokens": 32_000}) == 12_000
    assert _effective_output_tokens({"maxTokens": 16_000, "maxOutputTokens": 32_000}) == 16_000
    assert _effective_output_tokens({"maxTokens": 30_000, "maxOutputTokens": 32_000}) == 30_000
    assert _effective_output_tokens({"maxTokens": 30_000, "maxOutputTokens": 8_000}) == 8_000


def test_tool_loop_forwards_streamed_content(monkeypatch) -> None:
    from app import runtime

    chunks: list[str] = []

    def fake_stream(*args, on_content=None, **kwargs):
        on_content("正在写入")
        on_content("投喂稿")
        return ToolRound(content="正在写入投喂稿", tool_calls=[], native=True)

    monkeypatch.setattr(runtime, "chat_tool_round_stream", fake_stream)
    content, trace = runtime.execute_tool_loop(
        "system",
        [{"role": "user", "content": "generate"}],
        object(),
        {"tenantId": ""},
        on_content=chunks.append,
    )
    assert content == "正在写入投喂稿"
    assert chunks == ["正在写入", "投喂稿"]
    assert trace == []


def test_legacy_manifest_falls_back_to_package() -> None:
    normalized = _normalize_manifest(
        {
            "package": "manju-laoli-skill",
            "version": "7.4.29",
            "canvasSchema": "drama-canvas-feed/v2",
            "entry": "short-drama-director/SKILL.md",
        },
        "pkg.zip",
    )
    assert normalized["id"] == "manju-laoli-skill"
    assert normalized["entry"] == "short-drama-director/SKILL.md"
    assert normalized["contract"]["schema"] == "drama-canvas-feed/v2"


def test_manifest_rejects_a_second_canvas_contract() -> None:
    with pytest.raises(SkillError):
        _normalize_manifest(
            {
                "id": "bad-skill",
                "version": "1.0.0",
                "entry": "SKILL.md",
                "contract": {"schema": "other-canvas/v9"},
            },
            "bad.zip",
        )


def test_normalize_feed_fixes_codes_generator_and_audio() -> None:
    from app.feed_normalizer import normalize_feed

    feed = {
        "title": "x",
        "canvas_contract": {"schema": "drama-canvas-feed/v2"},
        "asset_prompts": [{"code": "01", "name": "主角", "raw": "x"}],
        "voice_prompts": [{"name": "主角", "prompt": "calm"}],
        "segments": [{"no": 1, "name": "段1", "raw": "主角（01）"}],
    }
    out = normalize_feed(feed, skill_id="manju-laoli-skill", skill_version="7.4.29")
    assert out["asset_prompts"][0]["code"] == "图1"
    assert out["voice_prompts"][0]["code"] == "图1"
    assert out["voice_prompts"][0]["format"] == "audio-prompt-v1"
    assert "clean voice" in out["voice_prompts"][0]["prompt"]
    assert out["canvas_contract"]["generator"]["name"] == "manju-laoli-skill"
    assert "图1" in out["segments"][0]["raw"]


def test_normalize_feed_adds_voice_reference_and_string_age() -> None:
    from app.feed_normalizer import normalize_feed

    out = normalize_feed({
        "asset_prompts": [{"code": "01", "name": "林晚", "raw": "x"}],
        "voice_prompts": [{"name": "林晚", "age": 28, "gender": "female", "dialect": "standard", "prompt": "calm"}],
        "segments": [{"no": 1, "raw": "台词：林晚（低声）：\"你打错了。\""}],
    }, skill_id="s", skill_version="1")
    assert out["voice_prompts"][0]["age"] == "28"
    assert "【音色引用】@音频｜林晚（图1）" in out["segments"][0]["raw"]


def test_normalize_feed_rewrites_incomplete_voice_reference() -> None:
    from app.feed_normalizer import normalize_feed

    out = normalize_feed({
        "asset_prompts": [
            {"code": "图1", "name": "赵铁柱", "raw": "男性，34岁"},
            {"code": "图2", "name": "钱万金", "raw": "男性，52岁"},
        ],
        "voice_prompts": [
            {"code": "图1", "name": "赵铁柱", "age": 34, "gender": "male", "dialect": "standard", "prompt": "calm"},
            {"code": "图2", "name": "钱万金", "age": 52, "gender": "male", "dialect": "standard", "prompt": "sharp"},
        ],
        "segments": [{
            "no": 1,
            "raw": (
                "【音色引用】@音频｜赵铁柱（图1）\n"
                '台词：钱万金（扬声）："签字。"\n'
                '台词：赵铁柱（沉声）："先念协议。"'
            ),
        }],
    })
    raw = out["segments"][0]["raw"]
    assert raw.count("【音色引用】") == 1
    assert "@音频｜钱万金（图2）" in raw
    assert "@音频｜赵铁柱（图1）" in raw


def test_normalize_voice_name_from_role_prefix() -> None:
    from app.feed_normalizer import normalize_feed

    out = normalize_feed({
        "asset_prompts": [{"code": "图1", "name": "主角｜林深", "raw": "角色设定板"}],
        "voice_prompts": [{"code": "图1", "name": "主角｜林深", "age": 28, "gender": "male", "dialect": "standard", "prompt": "calm"}],
        "segments": [{"no": 1, "raw": "台词：主角（低声）：\"喂？\""}],
    })
    assert out["voice_prompts"][0]["name"] == "主角"


def test_heal_voice_prompts_uses_character_asset() -> None:
    from app.feed_normalizer import heal_voice_prompts, normalize_feed

    feed = {
        "asset_prompts": [{"code": "图1", "name": "贾富贵（角色立绘）", "raw": "男性，42岁，角色设定板"}],
        "voice_prompts": [],
        "segments": [{"no": 1, "raw": "台词：贾富贵（低声）：\"走吧。\""}],
    }
    healed = heal_voice_prompts(feed)
    assert healed["voice_prompts"][0]["name"] == "贾富贵"
    assert healed["voice_prompts"][0]["gender"] == "male"
    normalized = normalize_feed(healed)
    assert "【音色引用】@音频｜贾富贵（图1）" in normalized["segments"][0]["raw"]
