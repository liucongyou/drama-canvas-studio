from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import provider_catalog
import tenant_context


class _FakeResponse:
    status = 200

    def __init__(self):
        self.read_amounts = []

    def read(self, amount=-1):
        self.read_amounts.append(amount)
        return b'{"data":[]}'

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class ProviderDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "providers": [{
                "id": "demo",
                "name": "Demo",
                "protocol": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "test-key",
                "enabled": True,
            }],
            "models": [],
        }

    def test_provider_failure_classification_is_shared_across_providers(self):
        cases = [
            (401, "invalid token", "auth"),
            (403, "invalid api key", "auth"),
            (403, "account overdue balance", "balance"),
            (402, "quota exhausted", "balance"),
            (429, "rate limited", "rate_limit"),
            (503, "upstream unavailable", "upstream"),
            (405, "probe missing", "probe_missing"),
        ]
        for status, detail, expected in cases:
            with self.subTest(status=status, detail=detail):
                self.assertEqual(
                    provider_catalog.classify_provider_failure(status, detail),
                    expected,
                )

    def discover(self, payload: str):
        with (
            tenant_context.use_tenant("alpha", "alpha"),
            patch.object(provider_catalog, "load_config", return_value=self.config),
            patch.object(provider_catalog, "_request_status", return_value=(200, payload)),
        ):
            return provider_catalog.discover_provider_models(
                str(self.config["providers"][0]["id"])
            )

    def test_models_without_declared_types_require_explicit_selection(self):
        result = self.discover(
            '{"data":[{"id":"demo-video-15s"},{"id":"demo-image-pro"},{"id":"demo-tts"}]}'
        )
        self.assertEqual(result["count"], 3)
        self.assertEqual(
            [item["capability"] for item in result["models"]],
            ["unknown", "unknown", "unknown"],
        )
        self.assertTrue(all(item["enabled"] is False for item in result["models"]))

    def test_declared_types_are_used_without_name_guessing(self):
        result = self.discover(
            '{"data":['
            '{"id":"即梦-55-gpt-image-2","supported_endpoint_types":["openai_image"]},'
            '{"id":"gpt-6-astra","supported_endpoint_types":["openai"]},'
            '{"id":"seedance-2.0","supported_endpoint_types":["openai_video"]}'
            ']}'
        )
        self.assertEqual(
            [item["capability"] for item in result["models"]],
            ["image", "unknown", "video"],
        )

    def test_marks_existing_remote_models_without_writing(self):
        self.config["models"] = [{
            "id": "demo-image-pro",
            "providerId": "demo",
            "remoteModel": "demo-image-pro",
        }]
        result = self.discover('{"models":[{"id":"demo-image-pro"}]}')
        self.assertTrue(result["models"][0]["alreadyConfigured"])
        self.assertEqual(self.config["models"], [{
            "id": "demo-image-pro",
            "providerId": "demo",
            "remoteModel": "demo-image-pro",
        }])

    def test_rejects_model_list_without_ids(self):
        with self.assertRaises(RuntimeError):
            self.discover('{"data":[{"description":"no-id"}]}')

    def test_provider_timeout_returns_readable_failure(self):
        with (
            tenant_context.use_tenant("alpha", "alpha"),
            patch.object(provider_catalog, "load_config", return_value=self.config),
            patch.object(
                provider_catalog,
                "_request_status",
                side_effect=provider_catalog.ProviderRequestTimeoutError(
                    "供应商接口响应超时，请检查网络、接口地址或稍后重试"
                ),
            ),
        ):
            result = provider_catalog.test_provider("demo")
        self.assertFalse(result["ok"])
        self.assertTrue(result["timeout"])
        self.assertIn("响应超时", result["message"])

    def test_provider_auth_failure_is_actionable(self):
        with (
            tenant_context.use_tenant("alpha", "alpha"),
            patch.object(provider_catalog, "load_config", return_value=self.config),
            patch.object(
                provider_catalog,
                "_request_status",
                return_value=(401, '{"error":{"message":"无效的令牌"}}'),
            ),
        ):
            result = provider_catalog.test_provider("demo")
        self.assertFalse(result["ok"])
        self.assertTrue(result["authFailed"])
        self.assertIn("鉴权失败", result["message"])
        self.assertIn("更新", result["message"])

    def test_provider_balance_failure_is_actionable(self):
        with (
            tenant_context.use_tenant("alpha", "alpha"),
            patch.object(provider_catalog, "load_config", return_value=self.config),
            patch.object(
                provider_catalog,
                "_request_status",
                return_value=(403, '{"error":{"message":"account overdue balance"}}'),
            ),
        ):
            result = provider_catalog.test_provider("demo")
        self.assertFalse(result["ok"])
        self.assertTrue(result["balanceFailed"])
        self.assertIn("余额", result["message"])

    def test_request_status_uses_bounded_read_when_requested(self):
        response = _FakeResponse()
        with patch.object(provider_catalog.network_security, "safe_urlopen", return_value=response):
            status, text = provider_catalog._request_status(
                "https://api.example.com/models",
                "test-key",
                read_limit=4096,
            )
        self.assertEqual(status, 200)
        self.assertEqual(text, '{"data":[]}')
        self.assertEqual(response.read_amounts, [4096])

    def test_discovery_retries_with_bounded_page_query(self):
        calls = []

        def fake_request(url, key, **kwargs):
            calls.append((url, kwargs))
            return 200, '{"data":[{"id":"demo-video"}]}'

        with (
            tenant_context.use_tenant("alpha", "alpha"),
            patch.object(provider_catalog, "load_config", return_value=self.config),
            patch.object(provider_catalog, "_request_status", side_effect=fake_request),
        ):
            result = provider_catalog.discover_provider_models("demo")
        self.assertEqual(result["count"], 1)
        self.assertEqual(
            [url for url, _ in calls],
            ["https://api.example.com/v1/models"],
        )
        self.assertTrue(all(kwargs["timeout"] == 30 for _, kwargs in calls))
        self.assertTrue(all(kwargs["read_limit"] == 1024 * 1024 for _, kwargs in calls))

    def test_openai_video_defaults_to_relay_adapter(self):
        result = self.discover(
            '{"data":[{"id":"demo-seedance-video","supported_endpoint_types":["openai_video"]}]}'
        )
        self.assertEqual(result["models"][0]["capability"], "video")
        self.assertEqual(result["models"][0]["adapter"], "newapi_video")

    def test_newapi_image_defaults_to_relay_adapter(self):
        self.config["providers"][0]["base_url"] = "https://api.canvasproai.com/v1"
        result = self.discover(
            '{"data":[{"id":"gpt-image-2","supported_endpoint_types":["openai_image"]}]}'
        )
        self.assertEqual(result["models"][0]["capability"], "image")
        self.assertEqual(result["models"][0]["adapter"], "newapi_image")

    def test_documented_video_profiles_are_loaded_by_exact_model_id(self):
        self.assertEqual(
            provider_catalog._documented_model_profile(
                "高质量sd2.0/(全参933原生过真人速度快720P)"
            ),
            "suanli-video",
        )
        self.assertEqual(
            provider_catalog._documented_model_profile(
                "即梦-47-seedance-2.5"
            ),
            "aistars-video",
        )
        self.assertEqual(
            provider_catalog._documented_model_profile("not-in-document"),
            "",
        )

    def test_generic_openai_image_keeps_openai_adapter(self):
        result = self.discover(
            '{"data":[{"id":"custom-gpt-image-model","supported_endpoint_types":["openai_image"]}]}'
        )
        self.assertEqual(result["models"][0]["capability"], "image")
        self.assertEqual(result["models"][0]["adapter"], "openai_image")

    def test_relay_image_migration_repairs_legacy_parameters(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"CANVAS_TENANT_DATA_ROOT": root, "CANVAS_MULTI_TENANT": "1"},
        ):
            path = os.path.join(root, "tenants", "alpha", "config.local.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.config | {
                    "models": [{
                        "id": "relay-gpt-image-test",
                        "providerId": "demo",
                        "remoteModel": "relay-gpt-image-test",
                        "capability": "image",
                        "adapter": "openai_image",
                        "defaultParams": {"temperature": 0.7},
                        "params": {"temperature": {"type": "range"}},
                    }],
                }, handle, ensure_ascii=False)
            with tenant_context.use_tenant("alpha", "alpha"):
                model = next(
                    item
                    for item in provider_catalog.load_config()["models"]
                    if item["id"] == "relay-gpt-image-test"
                )
        self.assertEqual(model["defaultParams"], {"ratio": "1:1", "size": "1024x1024"})
        self.assertIn("ratio", model["params"])
        self.assertNotIn("temperature", model["params"])

    def test_newapi_legacy_image_adapter_is_migrated(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"CANVAS_TENANT_DATA_ROOT": root, "CANVAS_MULTI_TENANT": "1"},
        ):
            path = os.path.join(root, "tenants", "alpha", "config.local.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            config = copy.deepcopy(self.config)
            config["providers"][0].update({
                "id": "new-api",
                "base_url": "https://api.canvasproai.com/v1",
            })
            config["models"] = [{
                "id": "gpt-image-2",
                "providerId": "new-api",
                "remoteModel": "gpt-image-2",
                "capability": "image",
                "adapter": "openai_image",
                "enabled": True,
                "defaultParams": {"ratio": "1:1", "size": "1024x1024"},
                "params": {},
            }]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False)
            with tenant_context.use_tenant("alpha", "alpha"):
                model = next(
                    item
                    for item in provider_catalog.load_config()["models"]
                    if item["id"] == "gpt-image-2"
                )
        self.assertEqual(model["adapter"], "newapi_image")

    def test_discovery_parses_truncated_model_payload(self):
        with (
            tenant_context.use_tenant("alpha", "alpha"),
            patch.object(provider_catalog, "load_config", return_value=self.config),
            patch.object(
                provider_catalog,
                "_request_status",
                return_value=(200, '{"data":[{"id":"demo-video","supported_endpoint_types":["openai_video"]},{"id":"truncated'),
            ),
        ):
            result = provider_catalog.discover_provider_models("demo")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["models"][0]["adapter"], "newapi_video")

    def test_endpoint_types_drive_discovered_capability(self):
        result = self.discover(
            '{"data":[{"id":"relay-video","supported_endpoint_types":["openai_video"]}]}'
        )
        self.assertEqual(result["models"][0]["capability"], "video")
        self.assertEqual(result["models"][0]["adapter"], "newapi_video")

    def test_declared_endpoint_types_win_over_model_name(self):
        result = self.discover(json.dumps({
            "data": [
                {
                    "id": "即梦-47-seedance-2.5",
                    "supported_endpoint_types": ["openai_image"],
                },
                {
                    "id": "gpt-image-2",
                    "supported_endpoint_types": ["openai_video"],
                },
            ],
        }))
        self.assertEqual(
            [item["capability"] for item in result["models"]],
            ["image", "video"],
        )

    def test_ark_task_type_and_output_modalities_drive_capability(self):
        result = self.discover(json.dumps({
            "data": [
                {
                    "id": "ark-video",
                    "task_type": ["TextToVideo"],
                    "modalities": {
                        "input_modalities": ["text"],
                        "output_modalities": ["video"],
                    },
                },
                {
                    "id": "ark-image",
                    "task_type": ["TextToImage"],
                    "modalities": {
                        "input_modalities": ["text"],
                        "output_modalities": ["image"],
                    },
                },
                {
                    "id": "ark-text",
                    "task_type": ["TextGeneration"],
                    "modalities": {
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                },
            ],
        }))
        self.assertEqual(
            [item["capability"] for item in result["models"]],
            ["video", "image", "text"],
        )

    def test_bailian_output_models_are_discovered(self):
        result = self.discover(json.dumps({
            "code": None,
            "message": None,
            "success": True,
            "output": {
                "total": 2,
                "page_no": 1,
                "page_size": 20,
                "models": [
                    {"model": "qwen3.8-max", "name": "Qwen3.8-Max"},
                    {"model": "cosyvoice-v3.5-flash", "name": "CosyVoice v3.5"},
                ],
            },
        }))
        self.assertEqual(
            [item["remoteModel"] for item in result["models"]],
            ["qwen3.8-max", "cosyvoice-v3.5-flash"],
        )
        self.assertEqual(
            [item["capability"] for item in result["models"]],
            ["unknown", "unknown"],
        )

    def test_agnes_documented_catalog_types_are_used(self):
        self.config["providers"][0].update({
            "id": "agnes-ai",
            "name": "Agnes AI",
        })
        result = self.discover(json.dumps({
            "data": [
                {
                    "id": "agnes-2.5-flash",
                    "supported_endpoint_types": ["openai"],
                },
                {
                    "id": "agnes-image-2.5-flash",
                    "supported_endpoint_types": ["openai"],
                },
                {
                    "id": "agnes-video-2.5-flash",
                    "supported_endpoint_types": ["openai"],
                },
            ],
        }))
        self.assertEqual(
            [item["capability"] for item in result["models"]],
            ["text", "image", "video"],
        )

    def test_one_provider_keeps_one_video_adapter_for_all_video_lines(self):
        result = self.discover(json.dumps({
            "data": [
                {"id": "即梦-47-seedance-2.5", "supported_endpoint_types": ["openai_video"]},
                {"id": "官转doubao-seedance-2.0-fast", "supported_endpoint_types": ["openai_video"]},
                {"id": "算力-视频-示例", "supported_endpoint_types": ["openai_video"]},
            ],
        }))
        self.assertEqual(result["count"], 3)
        self.assertEqual(
            {item["adapter"] for item in result["models"]},
            {"newapi_video"},
        )


if __name__ == "__main__":
    unittest.main()
