# -*- coding: utf-8 -*-
import copy
import json
import unittest
from pathlib import Path

import canvas_feed_contract


FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "canvas-feed-valid"


class CanvasFeedSchemaRuntimeTests(unittest.TestCase):
    def valid_feed(self):
        return json.loads(
            (FIXTURES / "generic.json").read_text(encoding="utf-8")
        )

    def schema_issues(self, payload):
        return [
            issue
            for issue in canvas_feed_contract.validate_feed_schema(payload)
            if issue.code == canvas_feed_contract.ErrorCode.SCHEMA_VALIDATION_FAILED
        ]

    def test_valid_fixture_passes_runtime_schema(self):
        self.assertEqual(self.schema_issues(self.valid_feed()), [])

    def test_rejects_non_object_root(self):
        issues = self.schema_issues([])
        self.assertTrue(issues)
        self.assertEqual(issues[0].path, "$")

    def test_rejects_wrong_segment_type_with_path(self):
        feed = self.valid_feed()
        feed["segments"] = {"raw": "not-an-array"}
        issues = self.schema_issues(feed)
        self.assertTrue(any(issue.path == "segments" for issue in issues))

    def test_rejects_missing_required_voice_fields(self):
        feed = self.valid_feed()
        feed["voice_prompts"][0].pop("format")
        issues = self.schema_issues(feed)
        self.assertTrue(
            any(issue.path == "voice_prompts[0].format" for issue in issues)
        )

    def test_rejects_invalid_const_and_enum(self):
        feed = self.valid_feed()
        feed["canvas_contract"]["schema"] = "drama-canvas-feed/v1"
        feed["voice_prompts"][0]["gender"] = "unknown"
        issues = self.schema_issues(feed)
        paths = {issue.path for issue in issues}
        self.assertIn("canvas_contract.schema", paths)
        self.assertIn("voice_prompts[0].gender", paths)

    def test_rejects_duplicate_unique_array_values(self):
        feed = self.valid_feed()
        feed["segments"][0]["canvas"] = {
            "asset_codes": ["图9", "图9"],
        }
        issues = self.schema_issues(feed)
        self.assertTrue(
            any(issue.path == "segments[0].canvas.asset_codes" for issue in issues)
        )

    def test_analyze_feed_uses_runtime_schema_before_semantics(self):
        feed = self.valid_feed()
        feed["segments"] = "broken"
        result = canvas_feed_contract.analyze_declared_feed(feed)
        self.assertIn(
            canvas_feed_contract.ErrorCode.SCHEMA_VALIDATION_FAILED,
            {issue.code for issue in result.errors},
        )


if __name__ == "__main__":
    unittest.main()
