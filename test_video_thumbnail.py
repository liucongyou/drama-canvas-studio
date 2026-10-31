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


if __name__ == "__main__":
    unittest.main()
