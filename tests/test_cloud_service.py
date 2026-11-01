#!/usr/bin/env python3
"""Pruebas unitarias para cloud_service.py (CloudSyncManager y BatchPlaylistDownloader)."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cloud_service import CloudSyncManager, BatchPlaylistDownloader
from gdrive_sync import GoogleDriveConfig
from metadata import TrackMetadata


class TestCloudService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_manager_initialization_and_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            mgr = CloudSyncManager(
                config=GoogleDriveConfig(),
                music_library_dir=t / "music",
                gdrive_cache_dir=t / "cache",
                covers_dir=t / "covers",
                accounts_db_path=t / "accounts.db",
            )
            self.assertIsNotNone(mgr)
            mgr.shutdown()

    @patch("cloud_service.find_existing_local_duplicate")
    @patch("cloud_service.extract_metadata")
    def test_batch_downloader_detects_duplicates(self, mock_extract, mock_find):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            existing_file = t / "existing.mp3"
            existing_file.write_bytes(b"0" * 2048)

            mock_find.return_value = existing_file
            mock_meta = TrackMetadata(
                id="gd_dup",
                filepath=str(existing_file),
                title="Duplicate Song",
                artist="Artist",
                album="Album",
                genre="Genre",
                year=2026,
                duration=200,
                cover_path="",
                dominant_color="",
            )
            mock_extract.return_value = mock_meta

            track = TrackMetadata(
                id="gd_dup",
                filepath="gdrive://dup_id/dup.mp3",
                title="Duplicate Song",
                artist="Artist",
                album="Cloud",
                genre="Genre",
                year=2026,
                duration=200,
                cover_path="",
                dominant_color="",
            )

            downloader = BatchPlaylistDownloader(
                tracks=[track],
                config=GoogleDriveConfig(),
                cache_dir=t / "music",
                covers_dir=t / "covers",
            )

            ready_results = []
            downloader.track_ready.connect(lambda tid, meta, is_dup: ready_results.append((tid, is_dup)))
            finished_results = []
            downloader.batch_finished.connect(lambda d, dup, err: finished_results.append((d, dup, err)))

            downloader.run()

            self.assertEqual(len(ready_results), 1)
            self.assertTrue(ready_results[0][1])  # is_dup == True
            self.assertEqual(finished_results, [(0, 1, 0)])

    @patch("cloud_service.find_existing_local_duplicate")
    @patch("cloud_service.download_drive_file_to_cache")
    def test_batch_downloader_downloads_new_track(self, mock_download, mock_find):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            mock_find.return_value = None
            mock_meta = TrackMetadata(
                id="gd_new",
                filepath=str(t / "music" / "new.mp3"),
                title="New Song",
                artist="Artist",
                album="Album",
                genre="Genre",
                year=2026,
                duration=210,
                cover_path="",
                dominant_color="",
            )
            mock_download.return_value = mock_meta

            track = TrackMetadata(
                id="gd_new",
                filepath="gdrive://new_id/new.mp3",
                title="New Song",
                artist="Artist",
                album="Cloud",
                genre="Genre",
                year=2026,
                duration=210,
                cover_path="",
                dominant_color="",
            )

            downloader = BatchPlaylistDownloader(
                tracks=[track],
                config=GoogleDriveConfig(),
                cache_dir=t / "music",
                covers_dir=t / "covers",
            )

            finished_results = []
            downloader.batch_finished.connect(lambda d, dup, err: finished_results.append((d, dup, err)))

            downloader.run()
            self.assertEqual(finished_results, [(1, 0, 0)])


if __name__ == "__main__":
    unittest.main()
