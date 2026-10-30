from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import canvas_store
import provider_catalog
import provider_client
import server
import tenant_bootstrap
import tenant_context
import tenant_registry


class TenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "CANVAS_MULTI_TENANT",
                "CANVAS_TENANT_DATA_ROOT",
                "CANVAS_BASE_DOMAIN",
                "CANVAS_MASTER_KEY",
                "CANVAS_COOKIE_SECURE",
            )
        }
        os.environ["CANVAS_MULTI_TENANT"] = "1"
        os.environ["CANVAS_TENANT_DATA_ROOT"] = self.temp.name
        os.environ["CANVAS_BASE_DOMAIN"] = "example.com"
        os.environ["CANVAS_COOKIE_SECURE"] = "0"
        os.environ["CANVAS_MASTER_KEY"] = "base64:" + base64.urlsafe_b64encode(
            b"0123456789abcdef0123456789abcdef"
        ).decode("ascii")
        tenant_bootstrap.create_tenant("alpha", name="Alpha")
        tenant_bootstrap.create_tenant("beta", name="Beta")
        with server.GENERATION_QUEUE_CONDITION:
            self.old_global = server.GENERATION_QUEUE_ACTIVE_GLOBAL
            self.old_providers = dict(server.GENERATION_QUEUE_ACTIVE_PROVIDERS)
            self.old_tenants = dict(server.GENERATION_QUEUE_ACTIVE_TENANTS)
            server.GENERATION_QUEUE_ACTIVE_GLOBAL = 0
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.clear()
            server.GENERATION_QUEUE_ACTIVE_TENANTS.clear()

    def tearDown(self):
        with server.GENERATION_QUEUE_CONDITION:
            server.GENERATION_QUEUE_ACTIVE_GLOBAL = self.old_global
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.clear()
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.update(self.old_providers)
            server.GENERATION_QUEUE_ACTIVE_TENANTS.clear()
            server.GENERATION_QUEUE_ACTIVE_TENANTS.update(self.old_tenants)
            server.GENERATION_QUEUE_CONDITION.notify_all()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def test_project_rows_are_isolated_by_tenant_database(self):
        with tenant_context.use_tenant("alpha", "alpha"):
            alpha_project = canvas_store.create_project("Alpha project")
        with tenant_context.use_tenant("beta", "beta"):
            beta_project = canvas_store.create_project("Beta project")

        with tenant_context.use_tenant("alpha", "alpha"):
            self.assertEqual([row["id"] for row in canvas_store.list_projects()], [alpha_project])
        with tenant_context.use_tenant("beta", "beta"):
            self.assertEqual([row["id"] for row in canvas_store.list_projects()], [beta_project])
        self.assertNotEqual(alpha_project, beta_project)

    def test_upload_bytes_are_written_inside_current_tenant(self):
        with tenant_context.use_tenant("alpha", "alpha"):
            url = provider_client.save_generated_bytes(b"alpha", "image/png", ".png")
            alpha_path = Path(provider_client._upload_dir()) / url.split("/")[-1]
            self.assertTrue(alpha_path.is_file())
        with tenant_context.use_tenant("beta", "beta"):
            self.assertNotEqual(Path(provider_client._upload_dir()), alpha_path.parent)

    def test_generated_download_is_streamed_to_atomic_file(self):
        class FakeResponse:
            headers = {
                "Content-Type": "video/mp4",
                "Content-Length": "11",
            }

            def __init__(self):
                self.chunks = [b"video", b"-bytes"]

            def read(self, amount=-1):
                return self.chunks.pop(0) if self.chunks else b""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        with tenant_context.use_tenant("alpha", "alpha"):
            with mock.patch.object(
                provider_client.network_security,
                "safe_urlopen",
                return_value=FakeResponse(),
            ):
                url = provider_client.save_generated_url(
                    "https://provider.example/video.mp4",
                    ".mp4",
                )
            path = Path(provider_client._upload_dir()) / url.split("/")[-1]
            payload = path.read_bytes()
            leftovers = list(path.parent.glob("*.part-*"))

        self.assertEqual(payload, b"video-bytes")
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            hashlib.sha256(b"video-bytes").hexdigest(),
        )
        self.assertEqual(leftovers, [])

    def test_truncated_generated_download_keeps_no_partial_final_file(self):
        class TruncatedResponse:
            headers = {
                "Content-Type": "video/mp4",
                "Content-Length": "100",
            }

            def __init__(self):
                self.chunks = [b"partial"]

            def read(self, amount=-1):
                return self.chunks.pop(0) if self.chunks else b""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        with tenant_context.use_tenant("alpha", "alpha"):
            upload_dir = Path(provider_client._upload_dir())
            before = set(upload_dir.glob("*"))
            with (
                mock.patch.object(
                    provider_client.network_security,
                    "safe_urlopen",
                    return_value=TruncatedResponse(),
                ),
                self.assertRaisesRegex(RuntimeError, "下载不完整"),
            ):
                provider_client.save_generated_url(
                    "https://provider.example/truncated.mp4",
                    ".mp4",
                )
            after = set(upload_dir.glob("*"))

        self.assertEqual(after, before)
        self.assertFalse(list(upload_dir.glob("*.part-*")))

    def test_tenant_secrets_are_encrypted_and_not_shared(self):
        with tenant_context.use_tenant("alpha", "alpha"):
            cfg = provider_catalog.load_config()
            next(item for item in cfg["providers"] if item["id"] == "ark")["api_key"] = "alpha-secret"
            provider_catalog.save_config(cfg)
            alpha_config = tenant_context.current().config_path.read_text(encoding="utf-8")
            self.assertNotIn("alpha-secret", alpha_config)
            self.assertEqual(
                next(item for item in provider_catalog.load_config()["providers"] if item["id"] == "ark")["api_key"],
                "alpha-secret",
            )
        with tenant_context.use_tenant("beta", "beta"):
            beta_key = next(
                item for item in provider_catalog.load_config()["providers"] if item["id"] == "ark"
            )["api_key"]
            self.assertEqual(beta_key, "")

    def test_legacy_flat_secret_fields_are_not_written_back(self):
        with tenant_context.use_tenant("alpha", "alpha"):
            cfg = provider_catalog.load_config(migrate=False)
            cfg["ark_api_key"] = "legacy-flat-secret"
            next(item for item in cfg["providers"] if item["id"] == "ark")["api_key"] = "provider-secret"
            provider_catalog.save_config(cfg)
            saved = tenant_context.current().config_path.read_text(encoding="utf-8")
            self.assertNotIn("legacy-flat-secret", saved)
            self.assertNotIn("provider-secret", saved)
            self.assertNotIn("ark_api_key", saved)

    def test_same_provider_slots_are_not_shared_between_tenants(self):
        with tenant_context.use_tenant("alpha", "alpha"):
            alpha_slot = server.acquire_generation_slot(None, "ark")
        with tenant_context.use_tenant("beta", "beta"):
            beta_slot = server.acquire_generation_slot(None, "ark")
        self.assertIsNotNone(alpha_slot)
        self.assertIsNotNone(beta_slot)
        server.release_generation_slot(alpha_slot)
        server.release_generation_slot(beta_slot)

    def test_invite_is_single_use_and_session_is_host_scoped(self):
        alpha = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(alpha["id"], ttl_seconds=3600)
        self.assertIsNotNone(tenant_registry.consume_invite("alpha", invite["token"]))
        self.assertIsNone(tenant_registry.consume_invite("alpha", invite["token"]))
        session = tenant_registry.create_session(alpha["id"], ttl_seconds=3600)
        self.assertEqual(tenant_registry.get_session(session["token"])["tenant_slug"], "alpha")
        self.assertIsNone(tenant_registry.get_session("not-a-session"))

    def test_host_classification_blocks_main_domain_and_accepts_subdomain(self):
        self.assertEqual(tenant_context.classify_host("example.com"), ("base", ""))
        self.assertEqual(tenant_context.classify_host("alpha.example.com:443"), ("tenant", "alpha"))
        self.assertEqual(tenant_context.classify_host("example.net"), ("invalid", ""))

    def test_tos_bucket_cannot_be_reused_by_another_tenant(self):
        storage = {
            "enabled": True,
            "bucket": "shared-bucket",
            "region": "cn-beijing",
            "endpoint": "tos-cn-beijing.volces.com",
            "internal_endpoint": "tos-cn-beijing.ivolces.com",
            "prefix": "alpha/",
            "access_key": "AK-alpha",
            "secret_key": "SK-alpha",
        }
        with tenant_context.use_tenant("alpha", "alpha"):
            provider_catalog.save_storage_config(storage)
        with tenant_context.use_tenant("beta", "beta"):
            with self.assertRaises(ValueError):
                provider_catalog.save_storage_config(
                    {**storage, "access_key": "AK-beta", "secret_key": "SK-beta"}
                )


if __name__ == "__main__":
    unittest.main()

