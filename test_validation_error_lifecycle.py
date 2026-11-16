from __future__ import annotations

import unittest

import server


class ValidationErrorLifecycleTests(unittest.TestCase):
    def test_edited_prompt_clears_superseded_reference_validation_error(self):
        meta = {
            "status": "failed",
            "lastError": "无法解析引用 @道具｜军绿色自行车",
            "providerTaskStatus": "failed",
            "providerTaskPhase": "生成失败",
            "generationFinishedAt": "2026-11-16T12:00:00Z",
            "params": {},
        }

        server.merge_node_metadata(
            meta,
            {
                "prompt": (
                    "【场景资产】@道具｜军绿色自行车（图40），"
                    "支在画左院墙边，本段不移动。"
                ),
                "params": {"refs": [{"nodeId": "bike"}]},
            },
        )

        self.assertEqual(meta["status"], "idle")
        self.assertEqual(meta["lastError"], "")
        self.assertEqual(meta["providerTaskStatus"], "")
        self.assertEqual(meta["providerTaskPhase"], "")
        self.assertIn("军绿色自行车", meta["prompt"])
        self.assertEqual(meta["params"]["refs"], [{"nodeId": "bike"}])

    def test_edited_prompt_keeps_durable_media_done(self):
        meta = {
            "status": "failed",
            "content": "/uploads/existing.mp4",
            "lastError": "参考素材缺失 1 项：军绿色自行车（源节点尚未生成）",
            "params": {},
        }

        server.merge_node_metadata(meta, {"prompt": "修正后的提示词"})

        self.assertEqual(meta["status"], "done")
        self.assertEqual(meta["lastError"], "")
        self.assertEqual(meta["content"], "/uploads/existing.mp4")

    def test_provider_task_failure_is_not_cleared_by_prompt_edit(self):
        meta = {
            "status": "failed",
            "taskId": "provider-task-1",
            "lastError": "供应商明确返回任务不存在（HTTP 404）",
            "providerTaskStatus": "failed",
            "providerTaskPhase": "查询失败",
            "params": {},
        }

        server.merge_node_metadata(meta, {"prompt": "换一个镜头描述"})

        self.assertEqual(meta["status"], "failed")
        self.assertEqual(meta["taskId"], "provider-task-1")
        self.assertIn("任务不存在", meta["lastError"])
        self.assertEqual(meta["providerTaskPhase"], "查询失败")

    def test_active_generation_is_not_cleared(self):
        meta = {
            "status": "error",
            "lastError": "参考素材缺失：角色图尚未生成",
            "params": {},
        }

        server.merge_node_metadata(
            meta,
            {"prompt": "编辑中的提示词"},
            generation_active=True,
        )

        self.assertEqual(meta["status"], "error")
        self.assertIn("参考素材缺失", meta["lastError"])

    def test_unchanged_error_is_not_hidden_before_request_validation(self):
        meta = {
            "status": "failed",
            "lastError": "无法解析引用 @不存在的角色",
            "params": {},
        }

        server.merge_node_metadata(meta, {"position": {"x": 10, "y": 20}})

        self.assertEqual(meta["status"], "failed")
        self.assertIn("不存在的角色", meta["lastError"])


if __name__ == "__main__":
    unittest.main()
