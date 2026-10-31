#!/usr/bin/env python3
"""
Pruebas unitarias para media_sync_worker.py
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from media_sync_worker import MediaDownloaderWorker, ProcessedMedia


class TestMediaSyncWorker(unittest.TestCase):
    @patch("shutil.which")
    def test_worker_initialization(self, mock_which):
        mock_which.side_effect = lambda b: f"/usr/bin/{b}"
        worker = MediaDownloaderWorker(
            rclone_video_remote="remoto:videos",
            rclone_audio_remote="remoto:musica",
        )
        self.assertEqual(worker.rclone_video_remote, "remoto:videos")
        self.assertEqual(worker.rclone_audio_remote, "remoto:musica")

    @patch("subprocess.run")
    def test_rclone_sync_call(self, mock_subproc):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("shutil.which", return_value="/usr/bin/rclone"):
            worker = MediaDownloaderWorker(
                rclone_video_remote="gdrive:videos",
                rclone_audio_remote="gdrive:musica",
            )
            media = ProcessedMedia(
                query="Test Song",
                video_title="Test Artist - Test Song (Official Video)",
                clean_title="Test Artist - Test Song",
                artist="Test Artist",
                video_path=Path("/tmp/test_video.mp4"),
                audio_path=Path("/tmp/test_audio.mp3"),
                thumbnail_path=None,
                duration=200.0,
                video_id="test1234",
            )
            res = worker.sync_to_cloud(media)
            self.assertTrue(res["video_synced"])
            self.assertTrue(res["audio_synced"])
            self.assertEqual(res["method"], "rclone")


if __name__ == "__main__":
    unittest.main()
