#!/usr/bin/env python3
"""Validate drama-canvas-feed payload declarations before canvas import."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from canvas_feed_contract import analyze_declared_feed


def validate_file(path: Path) -> tuple[int, list[str], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return 0, [f"FEED_PARSE_ERROR: {exc}"], []
    if not isinstance(payload, dict):
        return 0, ["FEED_PARSE_ERROR: 根节点必须是 JSON 对象"], []
    result = analyze_declared_feed(payload)
    errors = [
        f"{issue.code}: {issue.message}"
        + (f" [{issue.path}]" if issue.path else "")
        for issue in result.errors
    ]
    warnings = [
        f"{issue.code}: {issue.message}"
        + (f" [{issue.path}]" if issue.path else "")
        for issue in result.warnings
    ]
    return len(payload.get("voice_prompts") or []), errors, warnings


def self_test() -> int:
    payload = {
        "canvas_contract": {"schema": "drama-canvas-feed/v2"},
        "asset_prompts": [{"code": "图9", "name": "Lin Xia（角色立绘）", "raw": "asset"}],
        "voice_prompts": [{
            "code": "图9",
            "name": "Lin Xia",
            "format": "audio-prompt-v1",
            "prompt": (
                "clear restrained female voice. A female voice, clear and restrained. "
                "A female aged 27, speaking standard Mandarin, short drama dubbing "
                "reference clip, 4.5 seconds clean voice, no background noise."
            ),
            "label": "clear restrained female voice",
            "description": "A female voice, clear and restrained.",
            "age": "27",
            "gender": "female",
            "dialect": "standard",
        }],
        "segments": [{
            "no": 1,
            "raw": (
                "【画幅风格】9:16 竖屏\n"
                "### 素材引用\n"
                "【人物】\n"
                "<Lin Xia> 对应@角色｜Lin Xia（图9），仅采用该人物的外貌、发型和服装。\n"
                "【场景】\n"
                "<街口> 对应@场景｜街口（图1），仅采用空间布局、建筑、陈设和光线，不采用图中人物。\n"
                "【声音】\n"
                "<Lin Xia>的音色对应@音频｜Lin Xia（图9），仅用于该角色发声，不得分配给其他角色。\n"
                "【场景资产】@场景｜街口（图1）\n"
                "【核心人物】@角色｜Lin Xia（图9）\n"
                "【音色引用】@音频｜Lin Xia（图9）\n"
                "台词：Lin Xia（calm）：\"Start.\""
            ),
        }],
    }
    assert not analyze_declared_feed(payload).errors
    invalid = json.loads(json.dumps(payload))
    invalid["voice_prompts"][0]["prompt"] = invalid["voice_prompts"][0]["prompt"].replace(
        "A female aged 27, ", "A 27-year-old female, "
    )
    assert any(
        str(issue.code) == "VOICE_PROMPT_INCOMPLETE"
        for issue in analyze_declared_feed(invalid).errors
    )
    print("[+] validate_canvas_feed self-test PASSED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.json_path:
        parser.print_help()
        return 2
    path = Path(args.json_path)
    if not path.exists():
        print(f"[X] JSON 不存在：{path}")
        return 1
    voice_count, errors, warnings = validate_file(path)
    for warning in warnings:
        print(f"[WARN] {warning}")
    if errors:
        print(f"[X] 画布 JSON 校验失败：{len(errors)} 项")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"[+] 画布 JSON 校验通过：{path} · voice_prompts={voice_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
