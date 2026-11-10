from app.feed_compiler import compile_feed_draft


def test_compiler_owns_contract_identity_and_mechanical_fields() -> None:
    compiled = compile_feed_draft(
        {
            "title": "第01集",
            "canvas_contract": {
                "schema": "drama-canvas-feed/v2",
                "generator": {
                    "name": "model-invented",
                    "skillVersion": "0",
                    "packageRevision": "bad",
                },
            },
            "asset_prompts": [
                {
                    "code": "CHR-001",
                    "name": "贾富贵",
                    "raw": "男性，42岁，角色设定板，旧厂工装。",
                }
            ],
            "voice_prompts": [],
            "segments": [
                {
                    "no": 1,
                    "name": "段1",
                    "raw": (
                        "@角色｜贾富贵（CHR-001）站在厂门口。\n"
                        "台词：贾富贵（压低声音）：\"走吧。\""
                    ),
                }
            ],
        },
        skill_id="manju-laoli-skill",
        skill_version="7.4.30",
        package_revision="20261111-r15",
    )

    assert compiled.ok, compiled.errors
    assert compiled.feed["asset_prompts"][0]["code"] == "图1"
    assert compiled.feed["voice_prompts"][0]["name"] == "贾富贵"
    assert compiled.feed["voice_prompts"][0]["age"] == "42"
    assert "@角色｜贾富贵（图1）" in compiled.feed["segments"][0]["raw"]
    assert "@音频｜贾富贵（图1）" in compiled.feed["segments"][0]["raw"]
    generator = compiled.feed["canvas_contract"]["generator"]
    assert generator["name"] == "manju-laoli-skill"
    assert generator["skillVersion"] == "7.4.30"
    assert generator["packageRevision"] == "20261111-r15"
    assert len(generator["schemaSha256"]) == 64
    assert len(compiled.feed_sha256) == 64


def test_compiler_reports_storyboard_gate_errors_without_mutating_story() -> None:
    compiled = compile_feed_draft(
        {
            "title": "第02集",
            "asset_prompts": [],
            "voice_prompts": [],
            "segments": [
                {
                    "no": 1,
                    "name": "段1",
                    "raw": "台词：陈大强（低声）：\"账本呢？\"",
                }
            ],
        },
        skill_id="manju-laoli-skill",
        skill_version="7.4.30",
        package_revision="20261111-r15",
    )

    assert not compiled.ok
    assert any(item["code"] == "VOICE_PROMPT_MISSING" for item in compiled.errors)
    assert "账本呢" in compiled.feed["segments"][0]["raw"]


def test_compiler_hash_is_stable_for_equivalent_input() -> None:
    draft = {
        "title": "第03集",
        "asset_prompts": [
            {"code": "图01", "name": "赵铁柱", "raw": "男性，38岁，旧棉袄。"}
        ],
        "voice_prompts": [
            {
                "name": "赵铁柱",
                "age": 38,
                "gender": "male",
                "dialect": "northeastern",
                "prompt": "low steady baritone",
            }
        ],
        "segments": [
            {
                "no": 1,
                "name": "段1",
                "raw": "@角色｜赵铁柱（图01）抬头。\n台词：赵铁柱（沉声）：\"走。\"",
            }
        ],
    }
    first = compile_feed_draft(draft, skill_id="s", skill_version="1")
    second = compile_feed_draft(draft, skill_id="s", skill_version="1")
    assert first.feed_sha256 == second.feed_sha256
