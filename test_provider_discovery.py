from __future__ import annotations

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

    def discover(self, payload: str):
        with (
            tenant_context.use_tenant("alpha", "alpha"),
            patch.object(provider_catalog, "load_config", return_value=self.config),
            patch.object(provider_catalog, "_request_status", return_value=(200, payload)),
        ):
            return provider_catalog.discover_provider_models("demo")

    def test_discovers_models_and_infers_capabilities(self):
        result = self.discover(
            '{"data":[{"id":"demo-video-15s"},{"id":"demo-image-pro"},{"id":"demo-tts"}]}'
        )
        self.assertEqual(result["count"], 3)
        self.assertEqual(
            [item["capability"] for item in result["models"]],
            ["video", "image", "audio"],
        )
        self.assertIn("duration", result["models"][0]["params"])
        self.assertIn("ratio", result["models"][1]["defaultParams"])

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
            self.discover('{"data":[{"name":"no-id"}]}')

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
            if url.endswith("/models?limit=100&offset=0"):
                raise provider_catalog.ProviderRequestTimeoutError("timeout")
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
            [
                "https://api.example.com/v1/models?limit=100&offset=0",
                "https://api.example.com/v1/models",
            ],
        )
        self.assertTrue(all(kwargs["timeout"] == 30 for _, kwargs in calls))

    def test_endpoint_types_drive_discovered_capability(self):
        result = self.discover(
            '{"data":[{"id":"relay-video","supported_endpoint_types":["openai_video"]}]}'
        )
        self.assertEqual(result["models"][0]["capability"], "video")
        self.assertEqual(result["models"][0]["adapter"], "newapi_video")


if __name__ == "__main__":
    unittest.main()
