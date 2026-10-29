# -*- coding: utf-8 -*-
import base64
import copy
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import provider_catalog
import provider_client


class ProviderCatalogTests(unittest.TestCase):
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

    def test_legacy_remote_ids_resolve_without_mutating_nodes(self):
        provider, model, _ = provider_catalog.resolve_model(
            None, "doubao-seedance-2-0-mini-260615", "video"
        )
        self.assertEqual(provider["id"], "ark")
        self.assertEqual(model["id"], "doubao-seedance-2-0-mini")
        self.assertEqual(model["remoteModel"], "doubao-seedance-2-0-mini-260615")

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

    def test_newapi_video_adapter_is_supported(self):
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
                "adapter": "newapi_video",
                "remoteModel": "relay-video",
            })
        model = next(item for item in catalog["models"] if item["id"] == "relay-video")
        self.assertEqual(model["adapter"], "newapi_video")

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
                "duration": {"min": 4, "max": 30},
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
        sleep.assert_called_once_with(5)
        self.assertEqual(calls[1][1], calls[2][1])


if __name__ == "__main__":
    unittest.main()
