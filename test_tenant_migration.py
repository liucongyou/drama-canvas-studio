from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import provider_catalog
import tenant_bootstrap
import tenant_context
import tenant_registry


class TenantMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        (self.app / "data" / "uploads").mkdir(parents=True)
        self.old_base = tenant_context.BASE_DIR
        self.old_config = provider_catalog.CONFIG_PATH
        self.old_local = provider_catalog.LOCAL_CONFIG_PATH
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "CANVAS_MULTI_TENANT",
                "CANVAS_TENANT_DATA_ROOT",
                "CANVAS_MASTER_KEY",
                "CANVAS_OWNER_SLUG",
            )
        }
        tenant_context.BASE_DIR = self.app
        provider_catalog.CONFIG_PATH = str(self.app / "config.json")
        provider_catalog.LOCAL_CONFIG_PATH = str(self.app / "config.local.json")
        (self.app / "config.json").write_text('{"providers":[],"models":[],"storage":{}}', encoding="utf-8")
        (self.app / "config.local.json").write_text(
            '{"providers":[{"id":"ark","api_key":"legacy-secret"}]}',
            encoding="utf-8",
        )
        connection = sqlite3.connect(self.app / "data" / "canvas.db")
        connection.execute("CREATE TABLE legacy_marker(value TEXT)")
        connection.execute("INSERT INTO legacy_marker(value) VALUES('kept')")
        connection.commit()
        connection.close()
        (self.app / "data" / "uploads" / "legacy.txt").write_text("legacy", encoding="utf-8")
        os.environ["CANVAS_MULTI_TENANT"] = "1"
        os.environ["CANVAS_TENANT_DATA_ROOT"] = str(self.root / "tenant-data")
        os.environ["CANVAS_OWNER_SLUG"] = "owner"
        os.environ["CANVAS_MASTER_KEY"] = "base64:" + base64.urlsafe_b64encode(
            b"migration-key-0123456789abcdef01"
        ).decode("ascii")

    def tearDown(self):
        tenant_context.BASE_DIR = self.old_base
        provider_catalog.CONFIG_PATH = self.old_config
        provider_catalog.LOCAL_CONFIG_PATH = self.old_local
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def test_legacy_database_and_uploads_copy_to_owner_once(self):
        owner = tenant_bootstrap.ensure_owner_tenant()
        context = tenant_context.make_context(owner["id"], owner["slug"])
        self.assertTrue(context.db_path.exists())
        self.assertTrue((context.upload_dir / "legacy.txt").exists())
        self.assertTrue((context.data_root / ".legacy-migrated").exists())
        connection = sqlite3.connect(context.db_path)
        value = connection.execute("SELECT value FROM legacy_marker").fetchone()[0]
        connection.close()
        self.assertEqual(value, "kept")
        saved = context.config_path.read_text(encoding="utf-8")
        self.assertNotIn("legacy-secret", saved)
        self.assertIn("enc:v1:", saved)
        self.assertEqual(
            (self.app / "config.local.json").read_text(encoding="utf-8"),
            '{"providers":[{"id":"ark","api_key":"legacy-secret"}]}',
        )

    def test_migration_is_idempotent(self):
        tenant_bootstrap.ensure_owner_tenant()
        owner = tenant_registry.get_tenant_by_slug("owner")
        self.assertFalse(
            tenant_bootstrap.migrate_legacy_to_owner(owner["id"], owner["slug"])
        )


if __name__ == "__main__":
    unittest.main()

