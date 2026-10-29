from __future__ import annotations

import json
import unittest
from unittest import mock

import imgbb_storage


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ImgbbStorageTests(unittest.TestCase):
    def test_upload_uses_base64_form_and_returns_public_url(self):
        config = {
            "enabled": True,
            "provider": "imgbb",
            "imgbb": {
                "api_key": "imgbb-key",
                "auto_persist_images": True,
            },
        }
        response = FakeResponse({
            "success": True,
            "data": {
                "id": "img-1",
                "url": "https://i.ibb.co/img-1/test.png",
                "display_url": "https://ibb.co/img-1",
                "delete_url": "https://ibb.co/delete/img-1",
                "thumb": {"url": "https://i.ibb.co/thumb.png"},
            },
        })
        with (
            mock.patch.object(imgbb_storage.provider_catalog, "storage_config", return_value=config),
            mock.patch.object(imgbb_storage.urllib.request, "urlopen", return_value=response) as urlopen,
        ):
            result = imgbb_storage.upload_bytes(
                b"\x89PNG\r\n\x1a\npayload",
                mime="image/png",
                name="shot-1",
                source={"taskId": "task-1"},
            )

        request = urlopen.call_args.args[0]
        self.assertIn("key=imgbb-key", request.full_url)
        self.assertIn(b"image=iVBORw0KG", request.data)
        self.assertEqual(result["url"], "https://i.ibb.co/img-1/test.png")
        self.assertEqual(result["id"], "img-1")
        self.assertEqual(result["source"]["taskId"], "task-1")

    def test_selected_tos_disables_imgbb_mirroring(self):
        config = {
            "enabled": True,
            "provider": "tos",
            "imgbb": {"api_key": "still-saved"},
        }
        with mock.patch.object(imgbb_storage.provider_catalog, "storage_config", return_value=config):
            self.assertFalse(imgbb_storage.is_ready())

    def test_selected_imgbb_enables_imgbb_mirroring(self):
        config = {
            "enabled": True,
            "provider": "imgbb",
            "imgbb": {"api_key": "saved", "auto_persist_images": True},
        }
        with mock.patch.object(imgbb_storage.provider_catalog, "storage_config", return_value=config):
            self.assertTrue(imgbb_storage.is_ready())


if __name__ == "__main__":
    unittest.main()
