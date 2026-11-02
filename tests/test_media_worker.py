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

    @patch("requests.post")
    def test_serverless_proxy_sync_call(self, mock_post):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"status": "ok", "file_id": "test_drive_id_123"}
        mock_post.return_value = mock_resp

        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}" if b in ("yt-dlp", "ffmpeg") else None):
            worker = MediaDownloaderWorker(server_url="https://mock.script.google.com/exec")
            media = ProcessedMedia(
                query="Test Song",
                video_title="Artist - Song",
                clean_title="Artist - Song",
                artist="Artist",
                video_path=Path("/tmp/test_video.mp4"),
                audio_path=Path("/tmp/test_audio.mp3"),
                thumbnail_path=None,
                duration=180.0,
                video_id="id123",
            )
            with patch("pathlib.Path.read_bytes", return_value=b"fake_media_data"), \
                 patch("pathlib.Path.stat") as mock_stat, \
                 patch.object(Path, "exists", return_value=True):
                mock_stat.return_value = MagicMock(st_size=5 * 1024 * 1024, st_mode=0o100644)
                res = worker.sync_to_cloud(media)
                self.assertTrue(res["video_synced"])
                self.assertTrue(res["audio_synced"])
                self.assertEqual(res["method"], "serverless_proxy")
                self.assertEqual(res.get("audio_file_id"), "test_drive_id_123")

    def test_local_keep_saves_audio_video_and_cover(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            work_dir = tmp_path / "work"
            work_dir.mkdir()
            dest_dir = tmp_path / "music_library"

            dummy_audio = work_dir / "track.mp3"
            dummy_audio.write_bytes(b"dummy_mp3_data_here")
            dummy_video = work_dir / "track.mp4"
            dummy_video.write_bytes(b"dummy_mp4_data_here")
            dummy_thumb = work_dir / "track.jpg"
            dummy_thumb.write_bytes(b"dummy_jpg_cover_data")

            media = ProcessedMedia(
                query="Song",
                video_title="Song",
                clean_title="Song",
                artist="Artist",
                video_path=dummy_video,
                audio_path=dummy_audio,
                thumbnail_path=dummy_thumb,
                duration=120.0,
                video_id="xyz",
            )

            worker = MediaDownloaderWorker()
            with patch.object(worker, "fetch_and_extract", return_value=media), \
                 patch.object(worker, "sync_to_cloud", return_value={"status": "local_only"}), \
                 patch.object(worker, "missing_binaries", return_value=[]):
                res = worker.process_query("Song", keep_local=True, local_destination=dest_dir)
                self.assertEqual(res["status"], "success")

                # Verify audio is saved
                self.assertTrue((dest_dir / "track.mp3").exists())
                # Verify video is saved
                self.assertTrue((dest_dir / "videos" / "track.mp4").exists())
                # Verify cover art is saved alongside
                self.assertTrue((dest_dir / "track.jpg").exists())


if __name__ == "__main__":
    unittest.main()
