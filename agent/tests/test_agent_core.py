from app.llm import extract_json
import pytest

from app.skills import SkillError, _normalize_manifest


def test_extract_json_from_fence() -> None:
    assert extract_json('xxx\n`json\n{"a": 1}\n`\nyyy') == {"a": 1}


def test_extract_json_from_raw_object() -> None:
    assert extract_json('{"title": "x"}')["title"] == "x"


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
