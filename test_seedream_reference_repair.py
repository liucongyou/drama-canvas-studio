import tempfile
import unittest
from pathlib import Path
from unittest import mock

import canvas_store
import provider_catalog
import server
import tenant_context
import tos_storage


class SeedreamReferenceRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = mock.patch.object(
            tenant_context,
            "data_root",
            return_value=self.root,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.context = tenant_context.make_context(
            "reference-repair",
            "reference-repair",
        )
        tenant_context.ensure_context_dirs(self.context)

    def test_repairs_expiring_seedream_image_from_local_original(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("seedream-repair")
            local = self.root / "tenants" / "reference-repair" / "uploads"
            local.mkdir(parents=True, exist_ok=True)
            image = local / "role.jpg"
            image.write_bytes(b"image")
            node = {
                "id": "role-image",
                "type": "image",
                "metadata": {
                    "status": "done",
                    "content": "/uploads/role.jpg",
                    "model": "doubao-seedream-5-0-pro-260628",
                    "providerResultUrl": "https://ark.example/expired.jpg",
                    "params": {
                        "mediaVersionIndex": 0,
                        "mediaVersions": [{
                            "url": "/uploads/role.jpg",
                            "model": "doubao-seedream-5-0-pro-260628",
                        }],
                    },
                },
            }
            canvas_store.update_project(
                project_id,
                payload={"nodes": [node], "connections": []},
            )
            descriptor = {
                "status": "stored",
                "provider": "tos",
                "bucket": "bucket",
                "key": "image/role.jpg",
                "region": "cn-beijing",
            }
            with (
                mock.patch.object(
                    tos_storage,
                    "is_verified",
                    return_value=True,
                ),
                mock.patch.object(
                    provider_catalog,
                    "storage_profile_config",
                    return_value={"provider": "tos"},
                ),
                mock.patch.object(
                    tos_storage,
                    "ensure_local_image",
                    return_value=descriptor,
                ),
                mock.patch.object(
                    tos_storage,
                    "_client",
                    return_value=mock.Mock(
                        head_object=mock.Mock(return_value=object())
                    ),
                ),
            ):
                repaired = server.repair_expiring_seedream_references()
            saved = canvas_store.get_project(project_id)

        self.assertEqual(repaired, 1)
        meta = saved["payload"]["nodes"][0]["metadata"]
        self.assertEqual(meta["storage"]["key"], "image/role.jpg")
        self.assertEqual(
            meta["params"]["mediaVersions"][0]["storage"]["key"],
            "image/role.jpg",
        )


if __name__ == "__main__":
    unittest.main()
