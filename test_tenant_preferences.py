from __future__ import annotations

import base64
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import tenant_bootstrap
import tenant_context
import tenant_registry


class TenantPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": self.temp.name,
            "CANVAS_BASE_DOMAIN": "example.com",
            "CANVAS_COOKIE_SECURE": "0",
            "CANVAS_MASTER_KEY": "base64:" + base64.urlsafe_b64encode(
                b"abcdef0123456789abcdef0123456789"
            ).decode("ascii"),
        }
        self.patch = patch.dict(os.environ, self.env)
        self.patch.start()
        tenant_bootstrap.create_tenant("alpha", name="Alpha")
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        self.tenant_id = tenant["id"]

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_database_merge_preserves_unrelated_preferences(self):
        tenant_registry.update_tenant_preferences(
            self.tenant_id,
            {"canvasBackgroundMode": "dots"},
        )
        tenant_registry.update_tenant_preferences(
            self.tenant_id,
            {"theme": "forest"},
        )
        self.assertEqual(
            tenant_registry.get_tenant_preferences(self.tenant_id),
            {"canvasBackgroundMode": "dots", "theme": "forest"},
        )

    def test_concurrent_preference_writes_do_not_lose_updates(self):
        for index in range(20):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(
                        tenant_registry.update_tenant_preferences,
                        self.tenant_id,
                        {"theme": index % 2 == 0 and "copper" or "forest"},
                    ),
                    pool.submit(
                        tenant_registry.update_tenant_preferences,
                        self.tenant_id,
                        {"canvasBackgroundMode": index % 2 == 0 and "lines" or "dots"},
                    ),
                ]
                errors = [
                    future.exception()
                    for future in futures
                    if future.exception() is not None
                ]
                self.assertEqual(errors, [])

        preferences = tenant_registry.get_tenant_preferences(self.tenant_id)
        self.assertIn(preferences.get("theme"), {"forest", "copper"})
        self.assertIn(
            preferences.get("canvasBackgroundMode"),
            {"dots", "lines"},
        )


if __name__ == "__main__":
    unittest.main()
