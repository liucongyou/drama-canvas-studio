import unittest
from unittest import mock

import server


class VideoThumbnailTests(unittest.TestCase):
    def test_rejects_missing_or_nonlocal_video(self):
        self.assertEqual(server.ensure_video_thumbnail("https://example.com/v.mp4"), "")
        self.assertEqual(server.ensure_video_thumbnail("/uploads/missing.mp4"), "")

    def test_returns_existing_cover_without_running_ffmpeg(self):
        with mock.patch.object(server.os.path, "isfile", return_value=True), \
             mock.patch.object(server.os.path, "getsize", return_value=2048), \
             mock.patch.object(server.provider_client, "_ffmpeg_executable") as ffmpeg:
            result = server.ensure_video_thumbnail("/uploads/test.mp4")
        self.assertEqual(result, "/uploads/test-cover.jpg")
        ffmpeg.assert_not_called()

    def test_repairs_stale_current_cover_and_each_local_history_version(self):
        stale = "/uploads/stale-cover.jpg"
        usable = {
            "content": "/uploads/current.mp4",
            "params": {
                "thumbUrl": stale,
                "activeVersionId": "v2",
                "mediaVersions": [
                    {"versionId": "v1", "url": "/uploads/v1.mp4"},
                    {"versionId": "v2", "url": "/uploads/current.mp4", "thumbUrl": stale},
                    {"versionId": "remote", "url": "https://example.com/v.mp4"},
                ],
            },
        }

        def isfile(path):
            return path.endswith(("-cover.jpg", ".mp4")) and "stale" not in path

        def thumbnail(url):
            return f"{url[:-4]}-cover.jpg" if url.startswith("/uploads/") else ""

        with mock.patch.object(server.os.path, "isfile", side_effect=isfile), \
             mock.patch.object(server.os.path, "getsize", return_value=2048), \
             mock.patch.object(server, "ensure_video_thumbnail", side_effect=thumbnail):
            patch = server.rebuild_video_thumbnail_metadata(usable)

        self.assertEqual(patch["thumbUrl"], "/uploads/current-cover.jpg")
        versions = patch["mediaVersions"]
        self.assertEqual(versions[0]["thumbUrl"], "/uploads/v1-cover.jpg")
        self.assertEqual(versions[1]["thumbUrl"], "/uploads/current-cover.jpg")
        self.assertNotIn("thumbUrl", versions[2])

    def test_leaves_an_existing_health_checked_cover_untouched(self):
        meta = {
            "content": "/uploads/current.mp4",
            "params": {
                "thumbUrl": "/uploads/current-cover.jpg",
                "mediaVersions": [{
                    "versionId": "v1",
                    "url": "/uploads/current.mp4",
                    "thumbUrl": "/uploads/current-cover.jpg",
                }],
            },
        }
        with mock.patch.object(server.os.path, "isfile", return_value=True), \
             mock.patch.object(server.os.path, "getsize", return_value=2048), \
             mock.patch.object(server, "ensure_video_thumbnail") as ensure:
            patch = server.rebuild_video_thumbnail_metadata(meta)
        self.assertEqual(patch, {})
        ensure.assert_not_called()


class ImageThumbnailTests(unittest.TestCase):
    def test_returns_existing_image_thumbnail_without_running_ffmpeg(self):
        with mock.patch.object(server.os.path, "isfile", return_value=True), \
             mock.patch.object(server.os.path, "getsize", return_value=2048), \
             mock.patch.object(server.provider_client, "_ffmpeg_executable") as ffmpeg:
            result = server.ensure_image_thumbnail("/uploads/test.png")
        self.assertEqual(result, "/uploads/test-thumb.jpg")
        ffmpeg.assert_not_called()

    def test_rebuilds_each_local_image_version_and_active_thumbnail(self):
        stale = "/uploads/stale-thumb.jpg"
        meta = {
            "content": "/uploads/current.png",
            "params": {
                "thumbUrl": stale,
                "activeVersionId": "v2",
                "mediaVersions": [
                    {"versionId": "v1", "url": "/uploads/v1.png"},
                    {"versionId": "v2", "url": "/uploads/current.png", "thumbUrl": stale},
                    {"versionId": "remote", "url": "https://example.com/v.png"},
                ],
            },
        }

        def isfile(path):
            return path.endswith(("-thumb.jpg", ".png")) and "stale" not in path

        def thumbnail(url):
            return f"{url[:-4]}-thumb.jpg" if url.startswith("/uploads/") else ""

        with mock.patch.object(server.os.path, "isfile", side_effect=isfile), \
             mock.patch.object(server.os.path, "getsize", return_value=2048), \
             mock.patch.object(server, "ensure_image_thumbnail", side_effect=thumbnail):
            patch = server.rebuild_image_thumbnail_metadata(meta)

        self.assertEqual(patch["thumbUrl"], "/uploads/current-thumb.jpg")
        versions = patch["mediaVersions"]
        self.assertEqual(versions[0]["thumbUrl"], "/uploads/v1-thumb.jpg")
        self.assertEqual(versions[1]["thumbUrl"], "/uploads/current-thumb.jpg")
        self.assertNotIn("thumbUrl", versions[2])


if __name__ == "__main__":
    unittest.main()
