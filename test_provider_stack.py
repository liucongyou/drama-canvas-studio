# -*- coding: utf-8 -*-
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import patch

import provider_catalog
import provider_client


class ProviderCatalogTests(unittest.TestCase):
    def test_fourstoken_image_with_references_uses_edits_multipart(self):
        provider = {
            "id": "4stoken",
            "name": "4SToken",
            "base_url": "https://api.4stoken.cn/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "seedream-image",
            "remoteModel": "seedream-image",
            "adapter": "fourstoken_image",
            "profile": "fourstoken-image",
            "params": {},
        }
        calls = []

        def fake_multipart(url, key, fields, files, *, timeout):
            calls.append((url, key, fields, files))
            return {
                "task_id": "image-task-1",
                "data": [{"url": "https://example.com/image.png"}],
            }

        with (
            patch.object(provider_client, "_newapi_image_files", return_value=[
                ("reference.png", "image/png", b"image"),
            ]),
            patch.object(
                provider_client,
                "_fourstoken_multipart",
                side_effect=fake_multipart,
            ),
        ):
            result = provider_client.generate_image(
                provider,
                model,
                "参考角色生成街景",
                {"size": "1024x1024", "imageSize": "2K"},
                [{
                    "kind": "image",
                    "url": "https://example.com/reference.png",
                    "label": "角色",
                }],
            )

        self.assertEqual(result["url"], "https://example.com/image.png")
        self.assertEqual(result["taskId"], "image-task-1")
        self.assertEqual(calls[0][0], "https://api.4stoken.cn/v1/images/edits")
        self.assertEqual(calls[0][2][0], ("model", "seedream-image"))
        self.assertTrue(any(
            name == "image_size" and value == "2K"
            for name, value in calls[0][2]
        ))

    def test_fourstoken_video_persists_task_before_polling(self):
        provider = {
            "id": "4stoken",
            "name": "4SToken",
            "base_url": "https://api.4stoken.cn/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "4sdance431",
            "remoteModel": "4sdance431",
            "adapter": "fourstoken_video",
            "profile": "fourstoken-video",
            "params": {},
        }
        calls = []
        created = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {
                    "task_id": "video-task-1",
                    "taskId": "video-task-1",
                    "status": "pending",
                }
            return {
                "task_id": "video-task-1",
                "taskId": "video-task-1",
                "status": "succeeded",
                "data": [{"video_url": "https://example.com/video.mp4"}],
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(provider_client.time, "sleep"),
        ):
            result = provider_client.generate_video(
                provider,
                model,
                "generate",
                {"duration": 5, "ratio": "9:16", "resolution": "720P"},
                [],
                [],
                on_task_created=created.append,
            )

        self.assertEqual(created[0]["taskId"], "video-task-1")
        self.assertEqual(calls[0][1], "https://api.4stoken.cn/v1/videos")
        self.assertEqual(
            calls[0][2],
            {
                "model": "4sdance431",
                "prompt": "generate",
                "duration": 5,
                "ratio": "9:16",
                "resolution": "720p",
            },
        )
        self.assertEqual(calls[1][1], "https://api.4stoken.cn/v1/videos/video-task-1")
        self.assertEqual(result["url"], "https://example.com/video.mp4")

    def test_fourstoken_resumed_task_uses_the_original_url_field(self):
        provider = {
            "id": "4stoken",
            "name": "4SToken",
            "base_url": "https://api.4stoken.cn/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "4sdance431",
            "remoteModel": "4sdance431",
            "adapter": "fourstoken_newapi_video",
            "profile": "fourstoken-newapi-video",
            "params": {},
        }

        with patch.object(
            provider_client,
            "_poll_json",
            return_value={
                "task_id": "task-1",
                "status": "completed",
                "url": "https://example.com/newapi-video.mp4",
            },
        ) as poll:
            result = provider_client.check_video_task(provider, model, "task-1")

        self.assertEqual(
            poll.call_args.args[0],
            "https://api.4stoken.cn/v1/video/generations/task-1",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["url"], "https://example.com/newapi-video.mp4")

    def test_json_request_uses_single_http_transport_with_timeout(self):
        response = unittest.mock.MagicMock()
        response.read.return_value = b'{"id":"task-1"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch.object(
            provider_client.network_security,
            "safe_urlopen",
            return_value=response,
        ) as open_url:
            result = provider_client._request_json(
                "POST",
                "https://ark.example/api/v3/images/generations",
                "test-key",
                {"prompt": "test"},
                timeout=37,
            )

        self.assertEqual(result["id"], "task-1")
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.kwargs["timeout"], 37)


    def test_aistars_image_polls_task_endpoint_and_downloads_protected_artifact(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }
        calls = []
        downloads = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-aistars", "status": "queued"}
            return {
                "id": "task-aistars",
                "status": "completed",
                "artifacts": [{
                    "content_url": "/v1/tasks/task-aistars/artifacts/image-0/content",
                }],
            }

        def fake_download(url, key, **kwargs):
            downloads.append((url, key))
            return b"image-bytes", "image/png"

        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client, "_download_bytes_with_key", side_effect=fake_download), \
                patch.object(
                    provider_client,
                    "_persist_auth_url",
                    side_effect=lambda url, base, key, _ext: (
                        fake_download(
                            url if str(url).startswith("http") else base + url,
                            key,
                        ),
                        "/uploads/result.png",
                    )[1],
                ), \
                patch.object(provider_client.time, "sleep"):
            result = provider_client.generate_image(
                provider,
                model,
                "test",
                {"ratio": "16:9", "resolution": "2K"},
                [],
            )

        self.assertEqual(result["taskId"], "task-aistars")
        self.assertEqual(result["url"], "/uploads/result.png")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "https://api.canvasproai.com/aistars/v1/images/generations")
        self.assertEqual(calls[1][0], "GET")
        self.assertEqual(calls[1][1], "https://api.canvasproai.com/aistars/v1/tasks/task-aistars")
        self.assertEqual(
            downloads,
            [(
                "https://api.canvasproai.com/v1/tasks/task-aistars/artifacts/image-0/content",
                "test-key",
            )],
        )

    def test_aistars_image_persists_task_id_before_first_poll(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }
        created = []

        def fake_request(method, url, key, body=None, **kwargs):
            if method == "POST":
                return {"id": "task-persisted", "status": "queued"}
            return {
                "id": "task-persisted",
                "status": "completed",
                "artifacts": [{
                    "content_url": "/v1/tasks/task-persisted/artifacts/image-0/content",
                }],
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(provider_client, "_persist_auth_url", return_value="/uploads/persisted.png"),
            patch.object(provider_client.time, "sleep"),
        ):
            provider_client.generate_image(
                provider,
                model,
                "test",
                {"ratio": "16:9", "resolution": "2K"},
                [],
                on_task_created=created.append,
            )

        self.assertEqual(created[0]["taskId"], "task-persisted")

    def test_task_creation_callback_failure_stops_before_polling(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url))
            if method == "POST":
                return {"id": "task-db-failure", "status": "queued"}
            raise AssertionError("polling must not start when task id persistence fails")

        def fail_to_persist(_task):
            raise RuntimeError("database is locked")

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            with self.assertRaises(provider_client.ProviderOutcomeUncertain) as caught:
                provider_client.generate_image(
                    provider,
                    model,
                    "test",
                    {"ratio": "16:9", "resolution": "2K"},
                    [],
                    on_task_created=fail_to_persist,
                )

        self.assertEqual([method for method, _url in calls], ["POST"])
        self.assertIn("task-db-failure", str(caught.exception))
        self.assertEqual(
            caught.exception.response.get("taskId"),
            "task-db-failure",
        )

    def test_aistars_image_ambiguous_timeout_keeps_recovery_state(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }

        def fake_request(method, url, key, body=None, **kwargs):
            raise provider_client.ProviderOutcomeUncertain(
                "供应商请求已发出，但未收到可确认结果",
                submitted_at="2026-10-30T00:26:03+00:00",
            )

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            with self.assertRaises(provider_client.ProviderOutcomeUncertain) as caught:
                provider_client.generate_image(
                    provider,
                    model,
                    "test",
                    {"ratio": "16:9", "resolution": "2K"},
                    [],
                )

        self.assertIn("未收到可确认结果", str(caught.exception))
        self.assertEqual(
            caught.exception.submitted_at,
            "2026-10-30T00:26:03+00:00",
        )

    def test_aistars_image_503_without_task_id_is_uncertain_and_not_retried(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            raise provider_client.ProviderHTTPError(
                503,
                '{"error":{"code":"server_error","message":"Task request failed"}}',
            )

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            with self.assertRaisesRegex(
                provider_client.ProviderOutcomeUncertain,
                "没有返回任务号",
            ):
                provider_client.generate_image(
                    provider,
                    model,
                    "test",
                    {"ratio": "9:16", "clarity": "2K"},
                    [],
                )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(
            calls[0][1],
            "https://api.canvasproai.com/aistars/v1/images/generations",
        )

    def test_aistars_image_429_without_task_id_is_uncertain_and_not_retried(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            raise provider_client.ProviderHTTPError(
                429,
                '{"error":{"code":"rate_limit","message":"busy"}}',
            )

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            with self.assertRaisesRegex(
                provider_client.ProviderOutcomeUncertain,
                "没有返回任务号",
            ):
                provider_client.generate_image(
                    provider,
                    model,
                    "test",
                    {"ratio": "9:16", "clarity": "2K"},
                    [],
                )

        self.assertEqual(len(calls), 1)

    def test_submit_http_500_is_not_automatically_repeated(self):
        calls = []

        def fail():
            calls.append("POST")
            raise provider_client.ProviderHTTPError(500, "busy")

        with self.assertRaises(provider_client.ProviderHTTPError):
            provider_client._submit_with_retry(fail)
        self.assertEqual(calls, ["POST"])

    def test_canvaspro_video_503_without_task_id_is_uncertain_not_failed(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            raise provider_client.ProviderHTTPError(
                503,
                '{"error":{"code":"server_error","message":"Task request failed"}}',
            )

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            with self.assertRaisesRegex(
                provider_client.ProviderOutcomeUncertain,
                "没有返回任务号",
            ):
                provider_client.generate_video(
                    provider,
                    model,
                    "test",
                    {"ratio": "9:16", "duration": 12},
                    [],
                    [],
                )

        self.assertEqual(len(calls), 1)

    def test_aistars_image_503_with_task_id_recovers_by_polling_same_task(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                raise provider_client.ProviderHTTPError(
                    503,
                    '{"error":{"code":"server_error","message":"Task request failed"},'
                    '"id":"image-task-503"}',
                )
            return {
                "id": "image-task-503",
                "status": "succeeded",
                "artifacts": [{
                    "content_url": "/v1/tasks/image-task-503/artifacts/image-0/content",
                }],
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(
                provider_client,
                "_persist_auth_url",
                return_value="/uploads/recovered.png",
            ),
            patch.object(provider_client.time, "sleep"),
        ):
            result = provider_client.generate_image(
                provider,
                model,
                "test",
                {"ratio": "9:16", "clarity": "2K"},
                [],
            )

        self.assertEqual(result["taskId"], "image-task-503")
        self.assertEqual(result["url"], "/uploads/recovered.png")
        self.assertEqual([item[0] for item in calls], ["POST", "GET"])
        self.assertEqual(
            calls[1][1],
            "https://api.canvasproai.com/aistars/v1/tasks/image-task-503",
        )

    def test_newapi_video_adapter_dispatches_aistars_model_protocol(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "47-seedance-2.5",
            "remoteModel": "即梦-47-seedance-2.5",
            "adapter": "canvaspro_video",
            "profile": "aistars-video",
            "params": {},
        }
        calls = []
        downloads = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "video-task-1", "status": "queued"}
            return {
                "id": "video-task-1",
                "status": "completed",
                "artifacts": [{
                    "key": "video-0",
                    "type": "video",
                    "content_url": "/v1/tasks/video-task-1/artifacts/video-0/content",
                }],
            }

        def fake_download(url, key, **kwargs):
            downloads.append((url, key))
            return b"video-bytes", "video/mp4"

        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client, "_download_bytes_with_key", side_effect=fake_download), \
                patch.object(
                    provider_client,
                    "_persist_auth_url",
                    side_effect=lambda url, base, key, _ext: (
                        fake_download(
                            url if str(url).startswith("http") else base + url,
                            key,
                        ),
                        "/uploads/result.mp4",
                    )[1],
                ), \
                patch.object(
                    provider_client,
                    "save_generated_url",
                    side_effect=lambda url, *args, **kwargs: (
                        fake_download(url, kwargs.get("key", "")),
                        "/uploads/result.mp4",
                    )[1],
                ), \
                patch.object(provider_client.time, "sleep"):
            result = provider_client.generate_video(
                provider,
                model,
                "参考图一的人物沿运河行走",
                {
                    "ratio": "16:9",
                    "resolution": "480P",
                    "duration": 5,
                    "mode": "image2video",
                },
                [{"kind": "image", "url": "https://example.com/ref.png", "label": "角色"}],
                [],
            )

        self.assertEqual(result["taskId"], "video-task-1")
        self.assertEqual(result["url"], "/uploads/result.mp4")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "https://api.canvasproai.com/aistars/v1/videos")
        self.assertEqual(calls[1][0], "GET")
        self.assertEqual(calls[1][1], "https://api.canvasproai.com/aistars/v1/tasks/video-task-1")
        self.assertEqual(
            downloads,
            [(
                "https://api.canvasproai.com/v1/tasks/video-task-1/artifacts/video-0/content",
                "test-key",
            )],
        )

    def test_aistars_video_renumbers_canvas_image_labels_in_prompt(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "50-seedance-2.0-fast",
            "remoteModel": "即梦-50-seedance-2.0-fast",
            "adapter": "canvaspro_video",
            "profile": "aistars-video",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "video-task-2", "status": "queued"}
            return {
                "id": "video-task-2",
                "status": "completed",
                "artifacts": [{"content_url": "/result.mp4"}],
            }

        refs = [
            {
                "kind": "image",
                "url": "https://example.com/scene.png",
                "label": "场景｜铁西厂大门口·母版（图6）",
            },
            {
                "kind": "image",
                "url": "https://example.com/prop.png",
                "label": "道具｜买断协议（图7）",
            },
            {
                "kind": "image",
                "url": "https://example.com/chen.png",
                "label": "角色｜陈大强（图4）",
            },
            {
                "kind": "image",
                "url": "https://example.com/zhao.png",
                "label": "角色｜赵铁柱（图1）",
            },
        ]
        prompt = (
            "@角色｜陈大强（图4）盯着@角色｜赵铁柱（图1），"
            "手里攥着@道具｜买断协议（图7）。"
        )
        with patch.object(
            provider_client,
            "_request_json",
            side_effect=fake_request,
        ), patch.object(
            provider_client,
            "save_generated_url",
            return_value="/uploads/result.mp4",
        ), patch.object(
            provider_client.time,
            "sleep",
        ):
            provider_client.generate_video(
                provider,
                model,
                prompt,
                {
                    "ratio": "9:16",
                    "resolution": "720P",
                    "duration": 15,
                    "mode": "auto",
                },
                refs,
                [],
            )

        body = calls[0][2]
        sent_prompt = body["prompt"]
        self.assertEqual(
            prompt,
            "@角色｜陈大强（图4）盯着@角色｜赵铁柱（图1），"
            "手里攥着@道具｜买断协议（图7）。",
        )
        self.assertEqual(
            body["images"],
            [
                "https://example.com/scene.png",
                "https://example.com/prop.png",
                "https://example.com/chen.png",
                "https://example.com/zhao.png",
            ],
        )
        self.assertIn("第3张参考图", sent_prompt)
        self.assertIn("陈大强", sent_prompt)
        self.assertIn("第4张参考图", sent_prompt)
        self.assertIn("赵铁柱", sent_prompt)
        self.assertIn("第2张参考图", sent_prompt)
        self.assertIn("买断协议", sent_prompt)
        self.assertIn("第3张参考图：角色｜陈大强", sent_prompt)
        self.assertIn("第4张参考图：角色｜赵铁柱", sent_prompt)
        self.assertNotIn("（图4）", sent_prompt)
        self.assertNotIn("（图1）", sent_prompt)

    def test_aistars_video_numbers_and_images_share_one_order(self):
        """Prompt numbering and the `images` array must never diverge.

        Regression: prompt labels were renumbered from images+video refs while
        the images array kept the original interleaved order. The supplier then
        bound the wrong person to "第N张参考图", producing swapped roles.
        """
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "50-seedance-2.0-fast",
            "remoteModel": "即梦-50-seedance-2.0-fast",
            "adapter": "canvaspro_video",
            "profile": "aistars-video",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "video-task-order", "status": "queued"}
            return {
                "id": "video-task-order",
                "status": "completed",
                "artifacts": [{"content_url": "/result.mp4"}],
            }

        # Deliberately interleave a video ref ahead of the character images.
        refs = [
            {
                "kind": "video",
                "url": "https://example.com/ref.mp4",
                "label": "参考视频｜上一段",
            },
            {
                "kind": "image",
                "url": "https://example.com/open.png",
                "label": "场景｜开场（图5）",
            },
            {
                "kind": "image",
                "url": "https://example.com/chen.png",
                "label": "角色｜陈大强（图4）",
            },
            {
                "kind": "image",
                "url": "https://example.com/zhao.png",
                "label": "角色｜赵铁柱（图1）",
            },
        ]
        prompt = "@角色｜陈大强（图4）盯着@角色｜赵铁柱（图1）"
        with patch.object(
            provider_client,
            "_request_json",
            side_effect=fake_request,
        ), patch.object(
            provider_client,
            "save_generated_url",
            return_value="/uploads/result.mp4",
        ), patch.object(
            provider_client.time,
            "sleep",
        ):
            provider_client.generate_video(
                provider,
                model,
                prompt,
                {
                    "ratio": "9:16",
                    "resolution": "720P",
                    "duration": 5,
                    "mode": "auto",
                },
                refs,
                [],
            )

        body = calls[0][2]
        sent_prompt = body["prompt"]
        # Images array must follow the same images-first grouping used by the
        # numbering: scene=1, chen=2, zhao=3.
        self.assertEqual(
            body["images"],
            [
                "https://example.com/open.png",
                "https://example.com/chen.png",
                "https://example.com/zhao.png",
            ],
        )
        self.assertIn("陈大强（第2张参考图）", sent_prompt)
        self.assertIn("赵铁柱（第3张参考图）", sent_prompt)
        self.assertIn("第1张参考图：场景｜开场", sent_prompt)
        self.assertIn("第2张参考图：角色｜陈大强", sent_prompt)
        self.assertIn("第3张参考图：角色｜赵铁柱", sent_prompt)
        # The canvas asset number must not survive into the supplier prompt:
        # leaving "（图4）" next to "第3张参考图" lets the model bind the
        # character to slot 4 (the other person) and swap roles.
        self.assertNotIn("（图5）", sent_prompt)
        self.assertNotIn("（图4）", sent_prompt)
        self.assertNotIn("（图1）", sent_prompt)

    def test_check_image_task_recovers_completed_newapi_image(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }

        def fake_request(method, url, key, body=None, **kwargs):
            self.assertEqual(method, "GET")
            self.assertEqual(
                url,
                "https://api.canvasproai.com/aistars/v1/tasks/task-recover",
            )
            return {
                "id": "task-recover",
                "status": "completed",
                "artifacts": [{
                    "content_url": "/v1/tasks/task-recover/artifacts/image-0/content",
                }],
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(
                provider_client,
                "_persist_auth_url",
                return_value="/uploads/recovered.png",
            ),
        ):
            state = provider_client.check_image_task(
                provider,
                model,
                "task-recover",
            )

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["url"], "/uploads/recovered.png")
        self.assertEqual(state["taskId"], "task-recover")

    def test_newapi_image_submit_returns_task_without_second_poll(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "65-seedream-5-pro",
            "remoteModel": "即梦-65-seedream-5-pro",
            "adapter": "newapi_image",
            "profile": "aistars-image",
            "params": {},
        }
        calls = []
        created = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            return {
                "id": "task-single-submit",
                "status": "queued",
            }

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            result = provider_client.submit_image_task(
                provider,
                model,
                "角色站在院门口",
                {"ratio": "9:16", "clarity": "2K"},
                [],
                on_task_created=created.append,
            )

        self.assertEqual(result["taskId"], "task-single-submit")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "https://api.canvasproai.com/aistars/v1/images/generations")
        self.assertEqual(created[0]["taskId"], "task-single-submit")

    def test_high_quality_sd20_uses_documented_suanli_contract(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "high-quality-sd20",
            "remoteModel": "高质量sd2.0/(全参933原生过真人速度快720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-suanli", "status": "queued"}
            return {
                "id": "task-suanli",
                "status": "completed",
                "content_url": "/v1/tasks/task-suanli/artifacts/video/content",
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(provider_client.time, "sleep"),
        ):
            result = provider_client.generate_video(
                provider,
                model,
                "人物走进旧工厂",
                {
                    "ratio": "16:9",
                    "resolution": "720P",
                    "duration": 15,
                },
                [
                    {
                        "kind": "image",
                        "url": "https://example.com/reference.png",
                        "label": "角色",
                    },
                ],
                [
                    {
                        "url": "https://example.com/reference.mp3",
                        "label": "音色",
                    },
                ],
            )

        self.assertEqual(result["taskId"], "task-suanli")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(
            calls[0][1],
            "https://api.canvasproai.com/suanli/v1/videos",
        )
        body = calls[0][2]
        self.assertEqual(body["seconds"], 15)
        self.assertEqual(body["resolution"], "720p")
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertNotIn("duration", body)
        self.assertNotIn("ratio", body)
        self.assertEqual(calls[1][0], "GET")
        self.assertEqual(
            calls[1][1],
            "https://api.canvasproai.com/suanli/v1/videos/task-suanli",
        )

    def test_002_sd20_12s_720p_rejects_stale_video_defaults_before_submit(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {
                "resolution": {"type": "select", "options": ["720P"]},
                "duration": {"type": "range", "min": 12, "max": 12, "step": 1},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            return {"id": "must-not-submit", "status": "queued"}

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            self.assertRaisesRegex(RuntimeError, "只支持 \\[12\\] 秒"),
        ):
            provider_client.generate_video(
                provider,
                model,
                "人物回头",
                {
                    "ratio": "9:16",
                    "resolution": "480P",
                    "duration": 5,
                },
                [],
                [],
            )

        self.assertEqual(calls, [])

    def test_002_sd20_12s_720p_submits_exact_documented_video_contract(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {
                "resolution": {"type": "select", "options": ["720P"]},
                "duration": {"type": "range", "min": 12, "max": 12, "step": 1},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-002", "status": "queued"}
            return {
                "id": "task-002",
                "status": "completed",
                "content_url": "/suanli/v1/tasks/task-002/artifacts/video/content",
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(provider_client.time, "sleep"),
        ):
            result = provider_client.generate_video(
                provider,
                model,
                "九张参考图与三段音色生成十二秒人物镜头",
                {
                    "ratio": "9:16",
                    "resolution": "720P",
                    "duration": 12,
                },
                [
                    {
                        "kind": "image",
                        "url": f"https://cdn.example/ref-{index}.png",
                        "label": f"参考图 {index}",
                    }
                    for index in range(1, 10)
                ],
                [
                    {
                        "kind": "audio",
                        "url": f"https://cdn.example/voice-{index}.mp3",
                        "label": f"音色 {index}",
                    }
                    for index in range(1, 4)
                ],
            )

        self.assertEqual(result["taskId"], "task-002")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(
            calls[0][1],
            "https://api.canvasproai.com/suanli/v1/videos",
        )
        body = calls[0][2]
        self.assertEqual(body["model"], model["remoteModel"])
        self.assertEqual(body["resolution"], "720p")
        self.assertEqual(body["seconds"], 12)
        self.assertEqual(body["aspect_ratio"], "9:16")
        self.assertEqual(
            body["images"],
            [f"https://cdn.example/ref-{index}.png" for index in range(1, 10)],
        )
        self.assertEqual(
            body["audio_refs"],
            [f"https://cdn.example/voice-{index}.mp3" for index in range(1, 4)],
        )

    def test_002_sd20_local_images_use_data_uri_when_storage_not_ready(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {
                "resolution": {"type": "select", "options": ["720P"]},
                "duration": {"type": "number", "default": 12, "min": 12, "max": 12},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-public", "status": "queued"}
            return {
                "id": "task-public",
                "status": "completed",
                "content_url": "https://cdn.example/result.mp4",
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(provider_client.time, "sleep"),
            patch.object(
                provider_client.media_storage,
                "is_ready",
                return_value=False,
            ),
            patch.object(
                provider_client,
                "_canvaspro_inline_image",
                side_effect=lambda url: "[image omitted]",
            ),
        ):
            provider_client.generate_video(
                provider,
                model,
                "公共素材测试",
                {"ratio": "9:16", "resolution": "720P", "duration": 12},
                [
                    {
                        "kind": "image",
                        "url": f"/uploads/generated-{index}.png",
                        "label": f"图{index}",
                    }
                    for index in range(4)
                ],
                [
                    {
                        "kind": "audio",
                        "url": f"https://cdn.example/voice-{index}.mp3",
                        "label": f"音{index}",
                    }
                    for index in range(2)
                ],
            )

        body = calls[0][2]
        self.assertTrue(
            all(
                url.startswith("[image omitted]")
                for url in body["images"]
            )
        )
        self.assertTrue(
            all(
                url.startswith("https://cdn.example/")
                for url in body["audio_refs"]
            )
        )
        self.assertLess(len(json.dumps(body, ensure_ascii=False).encode("utf-8")), 100_000)

    def test_canvaspro_local_audio_is_inlined_when_no_public_storage_exists(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "generated-voice.mp3")
            with open(path, "wb") as handle:
                handle.write(b"ID3-audio")
            old = provider_client.UPLOAD_DIR
            provider_client.UPLOAD_DIR = root
            try:
                value = provider_client._canvaspro_asset_url(
                    "/uploads/generated-voice.mp3"
                )
            finally:
                provider_client.UPLOAD_DIR = old
        self.assertTrue(value.startswith("data:audio/mpeg;base64,"))

    def test_oversized_base64_video_body_is_rejected_before_submit(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {},
        }
        calls = []
        with (
            patch.object(provider_client, "_request_json", side_effect=lambda *args, **kwargs: calls.append(args)),
            patch.object(provider_client, "_canvaspro_asset_url", return_value="data:image/png;base64," + "A" * (9 * 1024 * 1024)),
            self.assertRaisesRegex(RuntimeError, "超过安全上限"),
        ):
            provider_client.generate_video(
                provider,
                model,
                "超大请求体测试",
                {"ratio": "9:16", "resolution": "720P", "duration": 12},
                [{"kind": "image", "url": "/uploads/fake.png", "label": "图1"}],
                [],
            )
        self.assertEqual(calls, [])

    def test_002_sd20_local_images_use_storage_public_urls_when_ready(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {
                "resolution": {"type": "select", "options": ["720P"]},
                "duration": {"type": "number", "default": 12, "min": 12, "max": 12},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-imgbb", "status": "queued"}
            return {
                "id": "task-imgbb",
                "status": "completed",
                "content_url": "https://cdn.example/result.mp4",
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(provider_client.time, "sleep"),
            patch.object(
                provider_client.media_storage,
                "is_ready",
                return_value=True,
            ),
            patch.object(
                provider_client.media_storage,
                "ensure_local",
                side_effect=lambda url, **kwargs: {
                    "provider": "aliyun_oss",
                    "key": url.rsplit("/", 1)[-1],
                },
            ),
            patch.object(
                provider_client.media_storage,
                "public_url",
                side_effect=lambda descriptor: "https://cdn.example/" + descriptor["key"],
            ),
        ):
            provider_client.generate_video(
                provider,
                model,
                "ImgBB 公共素材测试",
                {"ratio": "9:16", "resolution": "720P", "duration": 12},
                [
                    {
                        "kind": "image",
                        "url": f"/uploads/generated-{index}.png",
                        "label": f"图{index}",
                    }
                    for index in range(4)
                ],
                [],
            )
        body = calls[0][2]
        self.assertTrue(
            all(url.startswith("https://cdn.example/") for url in body["images"])
        )
        self.assertLess(len(json.dumps(body, ensure_ascii=False).encode("utf-8")), 100_000)

    def test_newapi_video_submit_timeout_is_uncertain_and_not_reposted(self):
        provider = {
            "id": "new-apl",
            "name": "New APl",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "002-sd2.0-9-3-12-720p",
            "remoteModel": "002系列/sd2.0(9图3音频12秒不卡脸720P)",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
            "params": {
                "resolution": {"type": "select", "options": ["720P"]},
                "duration": {"type": "range", "min": 12, "max": 12, "step": 1},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            raise provider_client.ProviderOutcomeUncertain(
                "供应商请求已发出，但未收到明确确认结果",
            )

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            self.assertRaises(provider_client.ProviderOutcomeUncertain),
        ):
            provider_client.generate_video(
                provider,
                model,
                "人物回头",
                {
                    "ratio": "9:16",
                    "resolution": "720P",
                    "duration": 12,
                },
                [],
                [],
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "POST")

    def test_newapi_invalid_token_is_reported_as_auth_action(self):
        with (
            patch.object(
                provider_client,
                "_request_json",
                side_effect=provider_client.ProviderHTTPError(
                    401,
                    '{"error":{"message":"Invalid token"}}',
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "供应商鉴权或权限失败"),
        ):
            provider_client._submit_json(
                "POST",
                "https://api.canvasproai.com/suanli/v1/videos",
                "invalid-token",
                {"model": "002系列/sd2.0(9图3音频12秒不卡脸720P)"},
                retry_window=0,
            )

    def test_overdue_provider_response_is_reported_as_balance_failure(self):
        detail = (
            '{"error":{"code":"AccountOverdueError",'
            '"message":"The request failed because your account has an overdue balance."}}'
        )
        mapped = provider_client._provider_submit_error(
            provider_client.ProviderHTTPError(403, detail)
        )

        self.assertIsInstance(mapped, provider_client.ProviderHTTPError)
        self.assertEqual(mapped.status_code, 403)
        self.assertIn("供应商账户余额不足或已欠费", str(mapped))
        self.assertIn("AccountOverdueError", str(mapped))
        self.assertNotIn("供应商接口 HTTP 403：供应商账户", str(mapped))

    def test_quota_error_is_reported_as_balance_failure_even_on_429(self):
        detail = (
            '{"error":{"code":"insufficient_quota",'
            '"message":"You exceeded your current quota, please check your plan and billing details."}}'
        )
        mapped = provider_client._provider_submit_error(
            provider_client.ProviderHTTPError(429, detail)
        )

        self.assertIsInstance(mapped, provider_client.ProviderHTTPError)
        self.assertEqual(mapped.status_code, 429)
        self.assertIn("供应商账户余额不足或已欠费", str(mapped))
        self.assertIn("insufficient_quota", str(mapped))

    def test_ark_image_submit_uses_the_shared_provider_error_mapping(self):
        provider = {
            "id": "ark",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "doubao-seedream-5-0-pro",
            "remoteModel": "doubao-seedream-5-0-pro-260628",
            "adapter": "ark_image",
        }
        detail = (
            '{"error":{"code":"AccountOverdueError",'
            '"message":"The request failed because your account has an overdue balance."}}'
        )
        with patch.object(
            provider_client,
            "_request_json",
            side_effect=provider_client.ProviderHTTPError(403, detail),
        ):
            with self.assertRaises(provider_client.ProviderHTTPError) as caught:
                provider_client.generate_image(
                    provider,
                    model,
                    "极简蓝色圆点",
                    {"size": "1536x1536"},
                    [],
                )

        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("供应商账户余额不足或已欠费", str(caught.exception))
        self.assertIn("AccountOverdueError", str(caught.exception))

    def test_newapi_video_result_fields_are_provider_specific(self):
        self.assertEqual(
            provider_client._newapi_video_url_from_task({
                "data": [{"url": "https://cdn.example/data-url.mp4"}],
            }),
            "https://cdn.example/data-url.mp4",
        )
        self.assertEqual(
            provider_client._newapi_video_url_from_task({
                "data": [{"content_url": "https://cdn.example/content.mp4"}],
            }),
            "https://cdn.example/content.mp4",
        )
        self.assertEqual(
            provider_client._newapi_video_url_from_task({
                "artifacts": [{"content_url": "https://cdn.example/artifact.mp4"}],
            }),
            "https://cdn.example/artifact.mp4",
        )

    def test_resumed_newapi_video_returns_provider_specific_url(self):
        provider = {
            "id": "canvaspro",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "special-video",
            "remoteModel": "特价视频-v1",
            "adapter": "canvaspro_video",
            "profile": "suanli-video",
        }
        with patch.object(
            provider_client,
            "_request_json",
            return_value={
                "status": "completed",
                "data": [{"content_url": "https://cdn.example/result.mp4"}],
            },
        ):
            result = provider_client.check_video_task(provider, model, "task-1")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["url"], "https://cdn.example/result.mp4")

    def test_provider_submit_timeout_is_short_enough_for_interactive_retry(self):
        self.assertLessEqual(provider_client.SUBMIT_TIMEOUT_SECONDS, 60)

    def test_exhausted_video_queue_is_reported_as_not_created(self):
        with (
            patch.object(
                provider_client,
                "_request_json",
                side_effect=provider_client.ProviderHTTPError(
                    503,
                    '{"code":"video_queue_full","message":"视频队列已满，请稍后重试"}',
                ),
            ),
            patch.object(provider_client.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "本次未创建任务"):
                provider_client._submit_json(
                    "POST",
                    "https://provider.example/videos",
                    "test-key",
                    {"model": "m"},
                    retry_window=0,
                )

    def test_sync_image_media_uses_long_response_timeout(self):
        self.assertGreaterEqual(provider_client.SYNC_MEDIA_TIMEOUT_SECONDS, 600)

    def test_submit_runtime_does_not_retry_provider_busy(self):
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            raise provider_client.ProviderHTTPError(503, '{"code":"queue_full"}')

        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "不会自动重提"):
                provider_client._submit_json(
                    "POST",
                    "https://provider.example/videos",
                    "test-key",
                    {"model": "m"},
                )
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_submit_runtime_does_not_retry_ambiguous_timeout(self):
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url))
            raise RuntimeError("The write operation timed out")

        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                provider_client._submit_json(
                    "POST",
                    "https://provider.example/videos",
                    "test-key",
                    {"model": "m"},
                )
        self.assertEqual(len(calls), 1)

    def test_required_models_are_registered(self):
        catalog = provider_catalog.public_catalog()
        ids = {model["id"] for model in catalog["models"]}
        self.assertTrue({
            "doubao-seedream-5-0-pro",
            "doubao-seedance-2-0",
            "doubao-seedance-2-0-mini",
            "doubao-seedance-2-0-fast",
            "doubao-seedance-2-5",
            "agnes-image-2.5-flash",
            "agnes-video-2.5-flash",
            "deepseek-flash-1017",
            "cosyvoice-v3-5-flash",
        }.issubset(ids))
        tts = next(model for model in catalog["models"] if model["id"] == "cosyvoice-v3-5-flash")
        self.assertEqual(tts["defaultParams"]["outputFormat"], "mp3")
        seedance = next(model for model in catalog["models"] if model["id"] == "doubao-seedance-2-0")
        self.assertEqual(seedance["pricing"]["video"]["rates"]["1080P"], 2.478)

    def test_audio_trim_uses_bundled_ffmpeg_when_system_binary_is_missing(self):
        with tempfile.TemporaryDirectory() as root:
            bundled = os.path.join(root, "ffmpeg")
            with open(bundled, "wb") as handle:
                handle.write(b"binary")
            os.chmod(bundled, 0o755)
            module = types.SimpleNamespace(get_ffmpeg_exe=lambda: bundled)
            with patch.object(provider_client.shutil, "which", return_value=None), \
                    patch.dict(sys.modules, {"imageio_ffmpeg": module}):
                self.assertEqual(provider_client._ffmpeg_executable(), bundled)

    def test_remote_model_ids_are_exact_catalog_fields(self):
        provider, model, _ = provider_catalog.resolve_model(
            None, "doubao-seedance-2-0-mini-260615", "video"
        )
        self.assertEqual(provider["id"], "ark")
        self.assertEqual(model["id"], "doubao-seedance-2-0-mini")
        self.assertEqual(model["remoteModel"], "doubao-seedance-2-0-mini-260615")

    def test_catalog_id_and_remote_model_id_are_distinct_exact_fields(self):
        provider, model, _ = provider_catalog.resolve_model(
            "ark",
            "doubao-seedream-5-0-pro",
            "image",
        )
        self.assertEqual(provider["id"], "ark")
        self.assertEqual(model["id"], "doubao-seedream-5-0-pro")
        self.assertEqual(
            model["remoteModel"],
            "doubao-seedream-5-0-pro-260628",
        )

    def test_missing_model_uses_default_for_each_capability(self):
        expected = {
            "image": "doubao-seedream-5-0-pro",
            "video": "doubao-seedance-2-0",
            "text": "deepseek-flash-1017",
            "audio": "cosyvoice-v3-5-flash",
        }
        for capability, model_id in expected.items():
            with self.subTest(capability=capability):
                _provider, model, _cfg = provider_catalog.resolve_model(None, None, capability)
                self.assertEqual(model["id"], model_id)

    def test_unknown_or_disabled_model_is_not_silently_replaced(self):
        with self.assertRaisesRegex(RuntimeError, "不可用"):
            provider_catalog.resolve_model(None, "removed-custom-model", "video")

    def test_unknown_seedream_is_not_replaced_by_first_ark_image_model(self):
        with self.assertRaisesRegex(RuntimeError, "不可用"):
            provider_catalog.resolve_model(
                "ark",
                "doubao-seedream-5-0-260128",
                "image",
            )

    def test_unknown_seedance_is_not_replaced_by_first_ark_video_model(self):
        with self.assertRaisesRegex(RuntimeError, "不可用"):
            provider_catalog.resolve_model(
                "ark",
                "doubao-seedance-9-9-unknown",
                "video",
            )

    def test_exact_dated_remote_model_wins_over_legacy_alias(self):
        cfg = {
            "providers": [{
                "id": "ark",
                "name": "火山方舟",
                "protocol": "ark",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "api_key": "ark-test",
                "enabled": True,
            }],
            "models": [{
            "id": "doubao-seedream-5-0-pro",
                "providerId": "ark",
                "capability": "image",
                "remoteModel": "doubao-seedream-5-0-pro-260628",
                "label": "doubao-seedream-5-0-pro-260628",
                "adapter": "ark_image",
                "enabled": True,
            }],
        }
        with patch.object(
            provider_catalog,
            "load_config",
            return_value=copy.deepcopy(cfg),
        ):
            provider, model, _ = provider_catalog.resolve_model(
                "ark",
                "doubao-seedream-5-0-pro-260628",
                "image",
            )
        self.assertEqual(provider["id"], "ark")
        self.assertEqual(model["id"], "doubao-seedream-5-0-pro")
        self.assertEqual(
            model["remoteModel"],
            "doubao-seedream-5-0-pro-260628",
        )

    def test_adapter_and_capability_must_match(self):
        cfg = {
            "providers": [{"id": "custom", "name": "Custom", "api_key": "x"}],
            "models": [],
        }
        with patch.object(provider_catalog, "load_config", return_value=copy.deepcopy(cfg)), \
                patch.object(provider_catalog, "save_config"):
            with self.assertRaisesRegex(ValueError, "只能用于"):
                provider_catalog.upsert_model({
                    "id": "wrong-adapter",
                    "providerId": "custom",
                    "capability": "text",
                    "adapter": "ark_image",
                    "remoteModel": "wrong-model",
                })

    def test_provider_icon_is_optional_and_round_trips(self):
        cfg = {
            "providers": [{
                "id": "custom",
                "name": "Custom",
                "protocol": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "x",
                "enabled": True,
            }],
            "models": [],
        }
        with patch.object(provider_catalog, "load_config", return_value=copy.deepcopy(cfg)), \
                patch.object(provider_catalog, "save_config") as save:
            catalog = provider_catalog.upsert_provider({
                "id": "custom",
                "name": "Custom",
                "protocol": "openai",
                "base_url": "https://api.example.com/v1",
                "icon": "https://cdn.example/custom.png",
            })
        saved_provider = next(
            item for item in catalog["providers"]
            if item["id"] == "custom"
        )
        self.assertEqual(saved_provider["icon"], "https://cdn.example/custom.png")
        persisted = save.call_args.args[0]["providers"][0]
        self.assertEqual(persisted["icon"], "https://cdn.example/custom.png")

    def test_provider_icon_can_be_cleared(self):
        cfg = {
            "providers": [{
                "id": "custom",
                "name": "Custom",
                "protocol": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "x",
                "icon": "https://cdn.example/old.png",
            }],
            "models": [],
        }
        with patch.object(provider_catalog, "load_config", return_value=copy.deepcopy(cfg)), \
                patch.object(provider_catalog, "save_config"):
            catalog = provider_catalog.upsert_provider({
                "id": "custom",
                "name": "Custom",
                "protocol": "openai",
                "base_url": "https://api.example.com/v1",
                "icon": "",
            })
        saved_provider = next(
            item for item in catalog["providers"]
            if item["id"] == "custom"
        )
        self.assertEqual(saved_provider["icon"], "")

    def test_canvaspro_video_adapter_is_supported(self):
        cfg = {
            "providers": [{"id": "relay", "name": "Relay", "api_key": "x"}],
            "models": [],
        }
        with patch.object(provider_catalog, "load_config", return_value=cfg), \
                patch.object(provider_catalog, "save_config"):
            catalog = provider_catalog.upsert_model({
                "id": "relay-video",
                "providerId": "relay",
                "capability": "video",
                "adapter": "canvaspro_video",
                "remoteModel": "relay-video",
            })
        model = next(item for item in catalog["models"] if item["id"] == "relay-video")
        self.assertEqual(model["adapter"], "canvaspro_video")

    def test_newapi_image_adapter_is_supported(self):
        cfg = {
            "providers": [{"id": "relay", "name": "Relay", "api_key": "x"}],
            "models": [],
        }
        with patch.object(provider_catalog, "load_config", return_value=cfg), \
                patch.object(provider_catalog, "save_config"):
            catalog = provider_catalog.upsert_model({
                "id": "relay-image",
                "providerId": "relay",
                "capability": "image",
                "adapter": "newapi_image",
                "remoteModel": "gpt-image-2",
            })
        model = next(item for item in catalog["models"] if item["id"] == "relay-image")
        self.assertEqual(model["adapter"], "newapi_image")

    def test_newapi_aistars_image_save_migrates_video_style_resolution(self):
        cfg = {
            "providers": [{"id": "new-apl", "name": "New APl", "api_key": "x"}],
            "models": [],
        }
        with patch.object(provider_catalog, "load_config", return_value=cfg), \
                patch.object(provider_catalog, "save_config") as save:
            catalog = provider_catalog.upsert_model({
                "id": "65-seedream-5-pro",
                "providerId": "new-apl",
                "capability": "image",
                "adapter": "newapi_image",
                "remoteModel": "即梦-65-seedream-5-pro",
                "defaultParams": {
                    "ratio": "9:16",
                    "resolution": "480P",
                    "duration": 5,
                    "watermark": False,
                },
                "params": {
                    "ratio": {"type": "select", "options": ["1:1", "16:9", "9:16"]},
                    "resolution": {"type": "select", "options": ["480P", "720P", "1080P"]},
                    "duration": {"type": "range", "min": 1, "max": 30},
                },
            })

        model = next(item for item in catalog["models"] if item["id"] == "65-seedream-5-pro")
        self.assertEqual(model["defaultParams"]["ratio"], "9:16")
        self.assertEqual(model["defaultParams"]["clarity"], "2K")
        self.assertNotIn("resolution", model["defaultParams"])
        self.assertNotIn("duration", model["defaultParams"])
        self.assertEqual(model["params"]["clarity"]["options"], ["1K", "2K"])
        self.assertNotIn("resolution", model["params"])
        save.assert_called_once()

    def test_relay_video_uses_videos_endpoint(self):
        requests = []

        def fake_request(method, url, key, body=None, **kwargs):
            requests.append((method, url, body))
            if method == "POST":
                return {"id": "video-task-1", "status": "queued"}
            return {
                "id": "video-task-1",
                "status": "completed",
                "content": {"video_url": "https://cdn.example/video.mp4"},
            }

        provider = {
            "id": "relay",
            "name": "Relay",
            "base_url": "https://api.example.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "relay-video",
            "remoteModel": "relay-video",
            "capability": "video",
            "adapter": "openai_video",
            "profile": "openai-video",
            "params": {},
        }
        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep"):
            result = provider_client.generate_video(
                provider,
                model,
                "测试视频",
                {"duration": 5, "resolution": "720P"},
                [{"kind": "image", "url": "https://cdn.example/ref.png"}],
                [],
            )
        self.assertEqual(result["taskId"], "video-task-1")
        self.assertEqual(requests[0][:2], ("POST", "https://api.example.com/v1/videos"))
        self.assertEqual(requests[1][:2], ("GET", "https://api.example.com/v1/videos/video-task-1"))
        self.assertEqual(requests[0][2]["images"], ["https://cdn.example/ref.png"])
        self.assertEqual(requests[0][2]["resolution"], "720P")

    def test_provider_request_rejects_private_dns_targets(self):
        private_result = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with patch(
            "network_security.socket.getaddrinfo",
            return_value=private_result,
        ):
            with self.assertRaisesRegex(RuntimeError, "内网|保留"):
                provider_catalog._request_status(
                    "http://localhost:8890/models",
                    "audit-key",
                )

    def test_model_reference_limits_are_normalized(self):
        limits = provider_catalog._normalize_model_limits({
            "maxRefs": "8",
            "maxImages": 5.9,
            "maxVideos": -2,
            "maxAudios": "3",
            "maxTextRefs": "not-a-number",
            "maxConcurrency": "6",
            "customLimit": "kept",
        })
        self.assertEqual(limits["maxRefs"], 8)
        self.assertEqual(limits["maxImages"], 5)
        self.assertEqual(limits["maxVideos"], 0)
        self.assertEqual(limits["maxAudios"], 3)
        self.assertEqual(limits["maxConcurrency"], 6)
        self.assertNotIn("maxTextRefs", limits)
        self.assertEqual(limits["customLimit"], "kept")
        recomputed = provider_catalog._normalize_model_limits({
            "maxRefs": 30,
            "maxImages": 30,
            "maxVideos": 10,
            "maxAudios": 10,
        })
        self.assertEqual(recomputed["maxRefs"], 50)
        cfg = {
            "providers": [{"id": "custom", "name": "Custom", "api_key": "x"}],
            "models": [{
                "id": "video-total",
                "providerId": "custom",
                "capability": "video",
                "adapter": "agnes_video",
                "remoteModel": "video-total",
                "enabled": True,
                "limits": {
                    "maxRefs": 30,
                    "maxImages": 30,
                    "maxVideos": 10,
                    "maxAudios": 10,
                },
            }],
        }
        with patch.object(provider_catalog, "load_config", return_value=copy.deepcopy(cfg)):
            catalog = provider_catalog.public_catalog()
        public_model = next(item for item in catalog["models"] if item["id"] == "video-total")
        self.assertEqual(public_model["limits"]["maxRefs"], 50)

    def test_public_catalog_never_contains_api_keys(self):
        catalog = provider_catalog.public_catalog()
        rendered = repr(catalog)
        self.assertNotIn("sk-teamo-", rendered)
        self.assertNotIn("ark-d0", rendered)
        self.assertNotIn("wk-", rendered)
        self.assertTrue(all("api_key" not in provider for provider in catalog["providers"]))

    def test_model_pricing_round_trips_through_catalog(self):
        cfg = {
            "providers": [{"id": "custom", "name": "Custom", "api_key": "x"}],
            "models": [],
        }
        pricing = {
            "currency": "CNY",
            "video": {
                "unit": "second",
                "rates": {"480P": 0.9, "720P": 1.8},
            },
        }
        with patch.object(provider_catalog, "load_config", return_value=copy.deepcopy(cfg)), \
                patch.object(provider_catalog, "save_config") as save_config:
            provider_catalog.upsert_model({
                "id": "priced-video",
                "providerId": "custom",
                "capability": "video",
                "adapter": "agnes_video",
                "remoteModel": "priced-video",
                "pricing": pricing,
            })
        saved = save_config.call_args.args[0]
        saved_model = next(item for item in saved["models"] if item["id"] == "priced-video")
        self.assertEqual(saved_model["pricing"], pricing)


class ProviderPayloadTests(unittest.TestCase):
    def test_newapi_gpt_image_uses_kr_async_endpoint(self):
        requests = []

        def fake_request(method, url, key, body=None, **kwargs):
            requests.append((method, url, body))
            if method == "POST":
                return {"task_id": "image-task-1", "status": "queued"}
            return {
                "task_id": "image-task-1",
                "status": "succeeded",
                "data": [{"url": "https://cdn.example/result.png"}],
            }

        provider = {
            "id": "new-api",
            "name": "New API",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "gpt-image-2",
            "remoteModel": "gpt-image-2",
            "capability": "image",
            "adapter": "newapi_image",
            "profile": "kr-image",
            "params": {},
        }
        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep"):
            result = provider_client.generate_image(
                provider,
                model,
                "测试图片",
                {"ratio": "1:1", "clarity": "2K"},
                [],
            )
        self.assertEqual(result["url"], "https://cdn.example/result.png")
        self.assertEqual(result["taskId"], "image-task-1")
        self.assertEqual(
            requests[0][:2],
            ("POST", "https://api.canvasproai.com/kr/v1/images/generations"),
        )
        self.assertEqual(
            requests[1][:2],
            ("GET", "https://api.canvasproai.com/kr/v1/images/tasks/image-task-1"),
        )
        self.assertEqual(requests[0][2]["size"], "1:1")
        self.assertEqual(requests[0][2]["image_size"], "2K")
        self.assertFalse(any(
            item[1] == "https://api.canvasproai.com/v1/images/generations"
            for item in requests
        ))

    def test_newapi_gpt_image_reference_uses_edit_endpoint(self):
        requests = []

        def fake_request(method, url, key, body=None, **kwargs):
            requests.append((method, url, body))
            if method == "POST":
                return {"task_id": "image-task-2", "status": "queued"}
            return {
                "task_id": "image-task-2",
                "status": "succeeded",
                "data": [{"url": "https://cdn.example/edited.png"}],
            }

        provider = {
            "id": "new-api",
            "name": "New API",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "gpt-image-2",
            "remoteModel": "gpt-image-2",
            "capability": "image",
            "adapter": "newapi_image",
            "profile": "kr-image",
            "params": {},
        }
        with patch.object(provider_client, "_request_multipart", return_value={
            "task_id": "image-task-2",
            "status": "queued",
        }) as multipart, \
                patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep"):
            result = provider_client.generate_image(
                provider,
                model,
                "保留人物，改成清晨运河",
                {"ratio": "16:9", "clarity": "1K"},
                [{"kind": "image", "url": "https://cdn.example/ref.png"}],
            )
        self.assertEqual(result["url"], "https://cdn.example/edited.png")
        self.assertEqual(
            multipart.call_args.args[:3],
            (
                "POST",
                "https://api.canvasproai.com/kr/v1/images/edits",
                "test-key",
            ),
        )
        fields = dict(multipart.call_args.args[3])
        self.assertEqual(fields["image_url[]"], "https://cdn.example/ref.png")
        self.assertEqual(
            requests[0][:2],
            ("GET", "https://api.canvasproai.com/kr/v1/images/tasks/image-task-2"),
        )

    def test_newapi_gpt_image_local_reference_is_uploaded_as_file(self):
        fields_seen = []

        def fake_multipart(method, url, key, fields, **kwargs):
            fields_seen.extend(fields)
            return {"task_id": "image-task-3", "status": "queued"}

        def fake_request(method, url, key, body=None, **kwargs):
            return {
                "task_id": "image-task-3",
                "status": "succeeded",
                "data": [{"url": "https://cdn.example/local-edit.png"}],
            }

        provider = {
            "id": "new-api",
            "name": "New API",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "gpt-image-2",
            "remoteModel": "gpt-image-2",
            "capability": "image",
            "adapter": "newapi_image",
            "profile": "kr-image",
            "params": {},
        }
        with patch.object(provider_client, "_asset_url_for_provider", return_value="data:image/png;base64,YWJj"), \
                patch.object(provider_client, "_request_multipart", side_effect=fake_multipart), \
                patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep"):
            result = provider_client.generate_image(
                provider,
                model,
                "本地参考图编辑",
                {"size": "1024x1024"},
                [{"kind": "image", "url": "/uploads/local.png"}],
            )
        self.assertEqual(result["url"], "https://cdn.example/local-edit.png")
        image_fields = [item for item in fields_seen if item[0] == "image[]"]
        self.assertEqual(len(image_fields), 1)
        self.assertEqual(image_fields[0][1][0], "reference.png")
        self.assertEqual(image_fields[0][1][1], "image/png")
        self.assertEqual(image_fields[0][1][2], b"abc")

    def test_newapi_gpt_image_protected_reference_is_downloaded_with_key(self):
        fields_seen = []
        downloads = []

        def fake_multipart(method, url, key, fields, **kwargs):
            fields_seen.extend(fields)
            return {"task_id": "image-task-4", "status": "queued"}

        def fake_download(url, key, **kwargs):
            downloads.append((url, key))
            return b"image-bytes", "image/png"

        def fake_request(method, url, key, body=None, **kwargs):
            return {
                "task_id": "image-task-4",
                "status": "succeeded",
                "data": [{"url": "https://cdn.example/protected-edit.png"}],
            }

        provider = {
            "id": "new-api",
            "name": "New API",
            "base_url": "https://api.canvasproai.com/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "gpt-image-2",
            "remoteModel": "gpt-image-2",
            "capability": "image",
            "adapter": "newapi_image",
            "profile": "kr-image",
            "params": {},
        }
        with patch.object(provider_client, "_download_bytes_with_key", side_effect=fake_download), \
                patch.object(provider_client, "_request_multipart", side_effect=fake_multipart), \
                patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep"):
            provider_client.generate_image(
                provider,
                model,
                "继续编辑",
                {"size": "1024x1024"},
                [{
                    "kind": "image",
                    "url": "https://api.canvasproai.com/v1/tasks/old-task/artifacts/image-0/content",
                }],
            )
        self.assertEqual(
            downloads,
            [(
                "https://api.canvasproai.com/v1/tasks/old-task/artifacts/image-0/content",
                "test-key",
            )],
        )
        image_fields = [item for item in fields_seen if item[0] == "image[]"]
        self.assertEqual(image_fields[0][1][2], b"image-bytes")

    def test_ark_video_uses_task_endpoint_and_ratio_field(self):
        provider = {
            "id": "ark",
            "name": "火山方舟",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "seedance",
            "remoteModel": "doubao-seedance-2-5-260628",
            "capabilities": "video",
            "adapter": "ark_video",
            "params": {
                "duration": {"min": 4, "max": 30, "allowSmart": True},
                "ratio": {"options": ["9:16"]},
                "resolution": {"options": ["480P", "720P"]},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, key, body))
            if method == "POST":
                return {"id": "task-1"}
            return {"status": "succeeded", "content": {"video_url": "https://video.example/result.mp4"}}

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            result = provider_client.generate_video(
                provider,
                model,
                "镜头动作",
                {"duration": 30, "ratio": "9:16", "resolution": "720P"},
                [
                    {"kind": "image", "url": "https://img.example/a.png", "label": "角色｜甲"},
                    {"kind": "text", "text": "保持动作", "label": "动作说明"},
                ],
                [{"kind": "audio", "url": "https://audio.example/a.wav", "label": "音色｜甲"}],
            )
        self.assertEqual(result["url"], "https://video.example/result.mp4")
        self.assertEqual(calls[0][1], "https://ark.example/api/v3/contents/generations/tasks")
        body = calls[0][3]
        self.assertEqual(body["ratio"], "9:16")
        self.assertEqual(body["resolution"], "720p")
        self.assertEqual(body["duration"], 30)
        self.assertNotIn("rate", body)
        kinds = [item["type"] for item in body["content"]]
        self.assertIn("image_url", kinds)
        self.assertIn("audio_url", kinds)
        self.assertEqual(calls[1][1], "https://ark.example/api/v3/contents/generations/tasks/task-1")

    def test_ark_video_preserves_user_reference_slot_order(self):
        provider = {
            "id": "ark",
            "name": "火山方舟",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "seedance",
            "remoteModel": "doubao-seedance-2-0-mini-260615",
            "adapter": "ark_video",
            "params": {
                "duration": {"min": 4, "max": 30},
                "ratio": {"options": ["9:16"]},
                "resolution": {"options": ["480P"]},
            },
        }
        refs = [
            {
                "kind": "image",
                "url": "https://image.example/role-4.jpg",
                "label": "角色｜陈大强（图4）",
            },
            {
                "kind": "image",
                "url": "https://image.example/scene-10.jpg",
                "label": "场景｜厂区后街·母版（图10）",
            },
            {
                "kind": "image",
                "url": "https://image.example/role-1.jpg",
                "label": "角色｜赵铁柱（图1）",
            },
            {
                "kind": "image",
                "url": "https://image.example/scene-12.jpg",
                "label": "场景｜塌方矿道·母版（图12）",
            },
            {
                "kind": "image",
                "url": "https://image.example/role-3.jpg",
                "label": "角色｜贾富贵（图3）",
            },
            {
                "kind": "image",
                "url": "https://image.example/prop-7.jpg",
                "label": "道具｜合同（图7）",
            },
            {
                "kind": "image",
                "url": "https://image.example/crowd-16.jpg",
                "label": "群像｜街坊群（图16）",
            },
        ]
        calls = []

        def fake_request(method, url, key, body=None, **kwargs):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-order"}
            return {
                "id": "task-order",
                "status": "succeeded",
                "content": {"video_url": "https://video.example/result.mp4"},
            }

        with (
            patch.object(
                provider_client,
                "_request_json",
                side_effect=fake_request,
            ),
            patch.object(
                provider_client,
                "_ark_reference_url",
                side_effect=lambda ref: ref["url"],
            ),
        ):
            provider_client.generate_video(
                provider,
                model,
                "段3测试",
                {"duration": 4, "ratio": "9:16", "resolution": "480P"},
                refs,
                [],
            )

        body = calls[0][2]
        image_urls = [
            item["image_url"]["url"]
            for item in body["content"]
            if item.get("type") == "image_url"
        ]
        text = body["content"][0]["text"]
        self.assertEqual(image_urls, [
            "https://image.example/role-4.jpg",
            "https://image.example/scene-10.jpg",
            "https://image.example/role-1.jpg",
            "https://image.example/scene-12.jpg",
            "https://image.example/role-3.jpg",
            "https://image.example/prop-7.jpg",
            "https://image.example/crowd-16.jpg",
        ])
        self.assertIn("将图片 1 中的人物定义为<角色｜陈大强>", text)
        self.assertIn("将图片 2 中的场景定义为<场景｜厂区后街·母版>", text)
        self.assertIn("将图片 3 中的人物定义为<角色｜赵铁柱>", text)
        self.assertIn("将图片 4 中的场景定义为<场景｜塌方矿道·母版>", text)
        self.assertIn("将图片 5 中的人物定义为<角色｜贾富贵>", text)
        self.assertIn("将图片 6 中的道具定义为<道具｜合同>", text)
        self.assertIn("将图片 7 中的群体定义为<群像｜街坊群>", text)
        self.assertIn("【群像】", text)
        self.assertIn("不采用图中人物", text)

    def test_ark_video_replaces_existing_material_contract_with_actual_slots(self):
        refs = [
            {
                "kind": "image",
                "url": "https://image.example/scene-10.jpg",
                "label": "场景｜厂区后街·母版（图10）",
            },
            {
                "kind": "image",
                "url": "https://image.example/role-4.jpg",
                "label": "角色｜陈大强（图4）",
            },
        ]
        prompt = (
            "陈大强探出半个身子说话。\n\n"
            "### 素材引用\n"
            "【人物】\n"
            "<角色｜陈大强（图4）> 对应参考图 99，旧编号"
        )

        text = provider_client._ark_prompt_with_material_reference_contract(
            prompt,
            refs,
            [],
        )

        self.assertEqual(text.count("### 素材引用"), 1)
        self.assertNotIn("参考图 99", text)
        self.assertIn("将图片 1 中的场景定义为<场景｜厂区后街·母版>", text)
        self.assertIn("将图片 2 中的人物定义为<角色｜陈大强>", text)

    def test_ark_provider_prompt_uses_only_actual_reference_slots(self):
        refs = [
            {
                "kind": "image",
                "url": "https://image.example/accountant.jpg",
                "label": "角色｜老会计（图16）",
            },
            {
                "kind": "image",
                "url": "https://image.example/jia.jpg",
                "label": "角色｜贾富贵（图3）",
            },
        ]
        prompt = (
            "【核心人物】@角色｜老会计（图16）；@角色｜贾富贵（图3）\n"
            "@角色｜贾富贵（图3）抱着布包跑进夜色。"
        )

        text = provider_client._ark_prompt_with_material_reference_contract(
            prompt,
            refs,
            [],
        )

        self.assertIn("<角色｜老会计>；", text)
        self.assertIn("<角色｜贾富贵>抱着布包", text)
        self.assertNotIn("@角色｜贾富贵（图3）", text)
        self.assertNotIn("（图16）", text)
        self.assertIn("将图片 1 中的人物定义为<角色｜老会计>", text)
        self.assertIn("将图片 2 中的人物定义为<角色｜贾富贵>", text)

    def test_ark_reference_guard_rejects_old_figure_code(self):
        refs = [
            {
                "kind": "image",
                "url": "https://image.example/role.jpg",
                "label": "角色｜贾富贵（图3）",
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "未绑定"):
            provider_client._ark_validate_reference_contract(
                "将图片 1 中的人物定义为<角色｜贾富贵>\n"
                "@角色｜贾富贵（图3）正在奔跑。",
                refs,
                [],
                ["https://image.example/role.jpg"],
                [],
            )

    def test_ark_reference_guard_rejects_undefined_body_tag(self):
        refs = [
            {
                "kind": "image",
                "url": "https://image.example/accountant.jpg",
                "label": "角色｜老会计（图16）",
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "未定义身份标签"):
            provider_client._ark_validate_reference_contract(
                "将图片 1 中的人物定义为<角色｜老会计>\n"
                "<角色｜贾富贵>抱着布包跑进夜色。",
                refs,
                [],
                ["https://image.example/accountant.jpg"],
                [],
            )

    def test_ark_reference_guard_rejects_slot_count_mismatch(self):
        refs = [
            {
                "kind": "image",
                "url": "https://image.example/role-1.jpg",
                "label": "角色｜老会计（图16）",
            },
            {
                "kind": "image",
                "url": "https://image.example/role-2.jpg",
                "label": "角色｜贾富贵（图3）",
            },
        ]
        text = (
            "将图片 1 中的人物定义为<角色｜老会计>\n"
            "将图片 2 中的人物定义为<角色｜贾富贵>"
        )
        with self.assertRaisesRegex(RuntimeError, "数量"):
            provider_client._ark_validate_reference_contract(
                text,
                refs,
                [],
                ["https://image.example/role-1.jpg"],
                [],
            )

    def test_ark_video_rejects_below_four_seconds_and_maps_expired_error(self):
        provider = {
            "id": "ark",
            "name": "火山方舟",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "seedance",
            "remoteModel": "doubao-seedance-2-5-260628",
            "adapter": "ark_video",
            "params": {
                "duration": {"min": 4, "max": 30},
                "ratio": {"options": ["9:16"]},
                "resolution": {"options": ["720P"]},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-expired"}
            return {
                "id": "task-expired",
                "status": "expired",
                "error": {
                    "code": "TaskExpired",
                    "message": "任务超过执行期限",
                },
            }

        with (
            patch.object(provider_client, "_request_json", side_effect=fake_request),
            patch.object(provider_client.time, "sleep"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "TaskExpired.*任务超过执行期限",
            ):
                provider_client.generate_video(
                    provider,
                    model,
                    "test",
                    {"duration": 1, "ratio": "9:16", "resolution": "720P"},
                    [],
                    [],
                )

        self.assertEqual(calls[0][2]["duration"], 4)
        self.assertEqual(calls[0][2]["resolution"], "720p")
        self.assertEqual(calls[0][2]["ratio"], "9:16")
        self.assertEqual(calls[0][2]["generate_audio"], True)

    def test_ark_video_payload_uses_official_seedance_fields(self):
        provider = {
            "id": "ark",
            "name": "火山方舟",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "seedance",
            "remoteModel": "doubao-seedance-2-5-260628",
            "adapter": "ark_video",
            "params": {
                "duration": {"min": 4, "max": 30},
                "ratio": {"options": ["16:9", "9:16", "adaptive"]},
                "resolution": {"options": ["480P", "720P", "1080P"]},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "cgt-test"}
            return {
                "id": "cgt-test",
                "status": "succeeded",
                "content": {
                    "video_url": "https://video.example/result.mp4",
                },
                "duration": 4,
                "ratio": "16:9",
                "resolution": "720p",
                "generate_audio": True,
            }

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            result = provider_client.generate_video(
                provider,
                model,
                "镜头运动",
                {
                    "duration": 4,
                    "ratio": "16:9",
                    "resolution": "720P",
                    "generateAudio": True,
                    "watermark": False,
                },
                [],
                [],
            )

        body = calls[0][2]
        self.assertEqual(
            calls[0][1],
            "https://ark.example/api/v3/contents/generations/tasks",
        )
        self.assertEqual(
            calls[1][1],
            "https://ark.example/api/v3/contents/generations/tasks/cgt-test",
        )
        self.assertEqual(body["model"], "doubao-seedance-2-5-260628")
        self.assertEqual(body["duration"], 4)
        self.assertEqual(body["ratio"], "16:9")
        self.assertEqual(body["resolution"], "720p")
        self.assertIs(body["generate_audio"], True)
        self.assertIs(body["watermark"], False)
        self.assertEqual(body["content"][0]["type"], "text")
        self.assertEqual(result["url"], "https://video.example/result.mp4")

    def test_local_upload_is_inlined_for_provider(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "a.png")
            with open(path, "wb") as fh:
                fh.write(b"abc")
            old = provider_client.UPLOAD_DIR
            provider_client.UPLOAD_DIR = root
            try:
                url = provider_client._asset_url_for_provider("/uploads/a.png")
            finally:
                provider_client.UPLOAD_DIR = old
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), b"abc")

    def test_ark_image_preserves_reference_order_and_parameters(self):
        provider = {
            "id": "ark",
            "name": "火山方舟",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "seedream",
            "remoteModel": "doubao-seedream-5-0-pro-260628",
            "adapter": "ark_image",
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            return {"data": [{"url": "https://image.example/result.png"}]}

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            result = provider_client.generate_image(
                provider,
                model,
                "角色定妆",
                {"size": "2048x1152", "guidance": "高", "watermark": False},
                [
                    {"kind": "image", "url": "https://img.example/char.png", "label": "角色｜甲"},
                    {"kind": "text", "text": "保持旧工厂冷色光影", "label": "风格说明"},
                ],
            )
        self.assertEqual(result["url"], "https://image.example/result.png")
        body = calls[0][2]
        self.assertEqual(body["size"], "2048x1152")
        self.assertEqual(body["image"], "https://img.example/char.png")
        self.assertIn("角色｜甲", body["prompt"])
        self.assertIn("保持旧工厂冷色光影", body["prompt"])
        self.assertIn("high detail", body["prompt"])

    def test_ark_image_sends_exact_model_id_and_resolution_tier(self):
        provider = {
            "id": "ark",
            "name": "火山方舟",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "doubao-seedream-5-0-pro",
            "remoteModel": "doubao-seedream-5-0-pro-260628",
            "adapter": "ark_image",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            return {"data": [{"url": "https://image.example/result.png"}]}

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            provider_client.generate_image(
                provider,
                model,
                "test",
                {"clarity": "1.5K", "ratio": "16:9"},
                [],
            )

        self.assertEqual(
            calls[0][1],
            "https://ark.example/api/v3/images/generations",
        )
        self.assertEqual(calls[0][2]["model"], "doubao-seedream-5-0-pro-260628")
        self.assertEqual(calls[0][2]["size"], "1.5K")

    def test_seedream_invalid_small_size_is_replaced_before_request(self):
        provider = {
            "id": "ark",
            "name": "火山方舟",
            "base_url": "https://ark.example/api/v3",
            "api_key": "test-key",
        }
        model = {
            "id": "seedream",
            "remoteModel": "doubao-seedream-5-0-pro-260628",
            "adapter": "ark_image",
            "params": {},
        }
        captured = {}

        def fake_request(method, url, key, body=None, timeout=120):
            captured["body"] = body
            return {"data": [{"url": "https://image.example/result.png"}]}

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            provider_client.generate_image(
                provider,
                model,
                "test",
                {"size": "1024x576", "ratio": "16:9"},
                [],
            )
        self.assertEqual(captured["body"]["size"], "1536x864")

    def test_dashscope_speech_payload_and_local_audio_result(self):
        provider = {
            "id": "aliyun_bailian",
            "name": "阿里云百炼 TTS",
            "base_url": "https://workspace.example/api/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "cosyvoice",
            "remoteModel": "cosyvoice-v3.5-flash",
            "adapter": "dashscope_tts",
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            return {"output": {"audio": {"data": base64.b64encode(b"RIFF-test").decode("ascii")}}}

        with tempfile.TemporaryDirectory() as root:
            old = provider_client.UPLOAD_DIR
            provider_client.UPLOAD_DIR = root
            try:
                with patch.object(provider_client, "_request_json", side_effect=fake_request):
                    result = provider_client.generate_audio(
                        provider,
                        model,
                        "台词",
                        {"ttsMode": "speech", "voiceId": "longxiaochun", "outputFormat": "wav", "sampleRate": 24000},
                        title="音色｜甲",
                    )
                self.assertTrue(os.path.isfile(os.path.join(root, result["url"].removeprefix("/uploads/"))))
            finally:
                provider_client.UPLOAD_DIR = old
        self.assertEqual(calls[0][1], "https://workspace.example/api/v1/services/audio/tts/SpeechSynthesizer")
        self.assertEqual(calls[0][2]["input"]["voice"], "longxiaochun")
        self.assertEqual(calls[0][2]["input"]["text"], "台词")

    def test_dashscope_voice_design_pads_short_preview_text(self):
        provider = {
            "id": "aliyun_bailian",
            "name": "阿里云百炼 TTS",
            "base_url": "https://workspace.example/api/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "cosyvoice",
            "remoteModel": "cosyvoice-v3.5-flash",
            "adapter": "dashscope_tts",
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            return {"output": {"audio": {"data": base64.b64encode(b"RIFF-test").decode("ascii")}}}

        with tempfile.TemporaryDirectory() as root:
            old = provider_client.UPLOAD_DIR
            provider_client.UPLOAD_DIR = root
            try:
                with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                        patch.object(provider_client, "_trim_audio_bytes", return_value=b"RIFF-test"):
                    provider_client.generate_audio(
                        provider,
                        model,
                        "low steady northeastern baritone",
                        {
                            "ttsMode": "design",
                            "voicePrompt": "low steady northeastern baritone",
                            "outputFormat": "wav",
                            "sampleRate": 24000,
                        },
                        title="音频｜赵铁柱（图1）",
                    )
            finally:
                provider_client.UPLOAD_DIR = old

        preview_text = calls[0][2]["input"]["preview_text"]
        self.assertGreaterEqual(len(preview_text), 15)
        self.assertIn("赵铁柱", preview_text)
        self.assertLessEqual(len(preview_text), 200)
        self.assertLessEqual(len(calls[0][2]["input"]["voice_prompt"]), 500)
        self.assertEqual(calls[0][2]["input"]["target_model"], "cosyvoice-v3.5-flash")
        self.assertLessEqual(len(calls[0][2]["input"]["prefix"]), 10)
        self.assertEqual(calls[0][2]["input"]["language_hints"], ["zh"])

    def test_voice_design_mp3_result_is_trimmed_to_requested_duration(self):
        provider = {
            "id": "aliyun_bailian",
            "name": "阿里云百炼 TTS",
            "base_url": "https://workspace.example/api/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "cosyvoice",
            "remoteModel": "cosyvoice-v3.5-flash",
            "adapter": "dashscope_tts",
        }

        def fake_request(method, url, key, body=None, timeout=120):
            return {
                "output": {
                    "preview_audio": {
                        "data": base64.b64encode(b"fake-mp3-bytes").decode("ascii"),
                    }
                }
            }

        with tempfile.TemporaryDirectory() as root:
            old = provider_client.UPLOAD_DIR
            provider_client.UPLOAD_DIR = root
            try:
                with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                        patch.object(provider_client, "_trim_audio_bytes", return_value=b"trimmed") as trim:
                    provider_client.generate_audio(
                        provider,
                        model,
                        "沉稳男性旁白",
                        {
                            "ttsMode": "design",
                            "voicePrompt": "沉稳男性旁白",
                            "previewText": "这是一段用于短剧对白的角色音色试听。",
                            "previewDuration": 9,
                            "outputFormat": "mp3",
                            "sampleRate": 24000,
                        },
                        title="音频｜赵铁柱",
                    )
            finally:
                provider_client.UPLOAD_DIR = old

        trim.assert_called_once_with(b"fake-mp3-bytes", ".mp3", 4.5)

    def test_custom_schema_api_paths_are_forwarded(self):
        captured = {}

        def fake_request(method, url, key, body=None, timeout=120):
            captured["body"] = body
            return {"choices": [{"message": {"content": "OK"}}]}

        provider = {
            "id": "custom",
            "name": "Custom",
            "base_url": "https://custom.example/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "custom-chat",
            "remoteModel": "custom-model",
            "adapter": "openai_chat",
            "params": {
                "presencePenalty": {"type": "range", "api": "presence_penalty"},
                "reasoning": {"type": "text", "api": "reasoning.effort"},
            },
        }
        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            provider_client.generate_text(
                provider,
                model,
                "test",
                {"presencePenalty": 0.4, "reasoning": "high"},
                [],
            )
        self.assertEqual(captured["body"]["presence_penalty"], 0.4)
        self.assertEqual(captured["body"]["reasoning"]["effort"], "high")

    def test_agnes_image_uses_tier_size_and_nested_reference_images(self):
        provider = {
            "id": "agnes-ai",
            "name": "Agnes AI",
            "base_url": "https://api.agnes-ai.cn/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "agnes-image-2.5-flash",
            "remoteModel": "agnes-image-2.5-flash",
            "adapter": "agnes_image",
            "params": {
                "responseFormat": {"api": "extra_body.response_format"},
            },
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, key, body, timeout))
            return {"data": [{"url": "https://image.example/agnes.png"}]}

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            result = provider_client.generate_image(
                provider,
                model,
                "保持角色一致，改成雨夜街头",
                {
                    "ratio": "16:9",
                    "clarity": "3K",
                    "size": "2048x1152",
                    "responseFormat": "url",
                },
                [
                    {"kind": "image", "url": "https://img.example/char.png", "label": "角色｜甲"},
                    {"kind": "text", "text": "保持冷色电影光", "label": "风格"},
                ],
            )

        self.assertEqual(result["url"], "https://image.example/agnes.png")
        self.assertEqual(calls[0][1], "https://api.agnes-ai.cn/v1/images/generations")
        body = calls[0][3]
        self.assertEqual(body["size"], "3K")
        self.assertEqual(body["ratio"], "16:9")
        self.assertEqual(body["extra_body"]["image"], ["https://img.example/char.png"])
        self.assertEqual(body["extra_body"]["response_format"], "url")
        self.assertIn("<Picture 1>", body["prompt"])
        self.assertIn("保持冷色电影光", body["prompt"])

    def test_agnes_video_uses_videos_endpoint_and_agnesapi_polling(self):
        provider = {
            "id": "agnes-ai",
            "name": "Agnes AI",
            "base_url": "https://api.agnes-ai.cn/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "agnes-video-2.5-flash",
            "remoteModel": "agnes-video-2.5-flash",
            "adapter": "agnes_video",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, key, body, timeout))
            if method == "POST":
                return {"id": "task-1", "video_id": "video-1", "status": "queued"}
            return {
                "id": "task-1",
                "status": "completed",
                "progress": 100,
                "url": "https://video.example/agnes.mp4",
            }

        with patch.object(provider_client, "_request_json", side_effect=fake_request):
            result = provider_client.generate_video(
                provider,
                model,
                "人物转身走向镜头",
                {
                    "mode": "auto",
                    "ratio": "9:16",
                    "resolution": "1080P",
                    "duration": 8,
                    "seed": 101,
                },
                [
                    {"kind": "image", "url": "https://img.example/char.png", "label": "角色｜甲"},
                    {"kind": "text", "text": "固定镜头", "label": "镜头说明"},
                ],
                [{"kind": "audio", "url": "https://audio.example/voice.wav", "label": "音色｜甲"}],
            )

        self.assertEqual(result["url"], "https://video.example/agnes.mp4")
        self.assertEqual(result["taskId"], "video-1")
        self.assertEqual(calls[0][1], "https://api.agnes-ai.cn/v1/videos")
        body = calls[0][3]
        self.assertEqual(body["model"], "agnes-video-2.5-flash")
        self.assertEqual(body["seconds"], "8")
        self.assertEqual(body["mode"], "reference")
        self.assertEqual(body["size"], "720P")
        self.assertEqual(body["aspect_ratio"], "9:16")
        self.assertEqual(body["images"], ["https://img.example/char.png"])
        self.assertEqual(body["audios"], ["https://audio.example/voice.wav"])
        self.assertEqual(body["seed"], 101)
        self.assertNotIn("videos", body)
        self.assertNotIn("generate_audio", body)
        self.assertNotIn("watermark", body)
        self.assertIn("<Picture 1>", body["prompt"])
        self.assertIn("<Audio 1>", body["prompt"])
        self.assertIn("固定镜头", body["prompt"])
        self.assertTrue(calls[1][1].startswith("https://api.agnes-ai.cn/agnesapi?"))
        self.assertIn("video_id=video-1", calls[1][1])
        self.assertIn("model_name=agnes-video-2.5-flash", calls[1][1])

    def test_agnes_video_retries_polling_429_without_duplicate_create(self):
        provider = {
            "id": "agnes-ai",
            "name": "Agnes AI",
            "base_url": "https://api.agnes-ai.cn/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "agnes-video-2.5-flash",
            "remoteModel": "agnes-video-2.5-flash",
            "adapter": "agnes_video",
            "params": {},
        }
        calls = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body))
            if method == "POST":
                return {"id": "task-1", "video_id": "video-1", "status": "queued"}
            if len(calls) == 2:
                raise provider_client.ProviderHTTPError(
                    429,
                    '{"error":{"code":429,"message":"查询视频任务过于频繁，请稍后重试"}}',
                )
            return {
                "id": "task-1",
                "status": "completed",
                "progress": 100,
                "url": "https://video.example/retried.mp4",
            }

        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep") as sleep:
            result = provider_client.generate_video(
                provider,
                model,
                "人物转身",
                {"mode": "text", "ratio": "16:9", "duration": 5},
                [],
                [],
            )

        self.assertEqual(result["url"], "https://video.example/retried.mp4")
        self.assertEqual([call[0] for call in calls], ["POST", "GET", "GET"])
        # 轮询节奏已改为自适应：前 60 秒按 1.5s 打，退避基准随之变成 1.5s。
        # 关键断言是"没有重新 POST"，不是具体秒数。
        sleep.assert_called_once_with(1.5)
        self.assertEqual(calls[1][1], calls[2][1])

    def test_agnes_video_does_not_repeat_queue_full_submission(self):
        provider = {
            "id": "agnes-ai",
            "name": "Agnes AI",
            "base_url": "https://api.agnes-ai.cn/v1",
            "api_key": "test-key",
        }
        model = {
            "id": "agnes-video-2.5-flash",
            "remoteModel": "agnes-video-2.5-flash",
            "adapter": "agnes_video",
            "params": {},
        }
        calls = []
        events = []

        def fake_request(method, url, key, body=None, timeout=120):
            calls.append((method, url, body, timeout))
            if method == "POST":
                raise provider_client.ProviderHTTPError(
                    503,
                    '{"code":"video_queue_full","message":"视频队列已满，请稍后重试"}',
                )
            return {
                "id": "task-full",
                "status": "completed",
                "progress": 100,
                "url": "https://video.example/after-queue.mp4",
            }

        with patch.object(provider_client, "_request_json", side_effect=fake_request), \
                patch.object(provider_client.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "不会自动重提"):
                provider_client.generate_video(
                    provider,
                    model,
                    "人物转身",
                    {"mode": "text", "ratio": "16:9", "duration": 5},
                    [],
                    [],
                    on_task_update=lambda event: events.append(event),
                )

        self.assertEqual([item[0] for item in calls], ["POST"])
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
