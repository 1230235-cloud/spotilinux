#!/usr/bin/env python3
"""
Pruebas unitarias para el almacenamiento SQLite y JSON de LibraryStore.
"""

import sys
import unittest
import tempfile
import shutil
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from storage import LibraryStore, Playlist
from metadata import TrackMetadata


class TestLibraryStoreSqlite(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.store = LibraryStore(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sqlite_file_created(self):
        self.assertTrue(self.store.sqlite_path.exists())

    def test_save_and_sync_to_sqlite(self):
        # Crear datos de prueba
        track = TrackMetadata(
            id="test_t1",
            filepath="gdrive://12345/song.mp3",
            title="Song 1",
            artist="Artist 1",
            album="Album 1",
            genre="Pop",
            year="2026",
            duration=180.0,
            cover_path="",
            dominant_color=(30, 215, 96),
        )
        self.store.tracks["test_t1"] = track
        self.store.liked_ids.append("test_t1")
        pl = self.store.create_playlist("Mi Lista", "Descripción")
        self.store.add_track_to_playlist(pl.id, "test_t1")

        # Verificar que SQLite tenga la información
        with sqlite3.connect(self.store.sqlite_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, title, artist, is_liked FROM tracks WHERE id = ?", ("test_t1",))
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "test_t1")
            self.assertEqual(row[1], "Song 1")
            self.assertEqual(row[2], "Artist 1")
            self.assertEqual(row[3], 1)

            cur.execute("SELECT id, name, track_ids FROM playlists WHERE id = ?", (pl.id,))
            p_row = cur.fetchone()
            self.assertIsNotNone(p_row)
            self.assertEqual(p_row[1], "Mi Lista")
            self.assertIn("test_t1", p_row[2])


if __name__ == "__main__":
    unittest.main()
