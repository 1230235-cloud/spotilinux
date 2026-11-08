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

    def test_get_track_playlists_and_reconcile(self):
        track = TrackMetadata(
            id="gd_file123",
            filepath="gdrive://file123/track.mp3",
            title="Drive Track",
            artist="Artist",
            album="Album",
            genre="Pop",
            year="2026",
            duration=200.0,
            cover_path="",
            dominant_color=(30, 215, 96),
        )
        self.store.tracks["gd_file123"] = track
        pl = self.store.create_playlist("Favoritas Verano")
        self.store.add_track_to_playlist(pl.id, "gd_file123")

        # 1. Comprobar que get_track_playlists devuelva la playlist
        pl_names = self.store.get_track_playlists(track)
        self.assertIn("Favoritas Verano", pl_names)

        # 2. Reconciliar a nuevo ID tras descarga local
        new_track = TrackMetadata(
            id="local_hash_456",
            filepath=str(self.test_dir / "music_library" / "gd_file123__Drive Track.mp3"),
            title="Drive Track",
            artist="Artist",
            album="Album",
            genre="Pop",
            year="2026",
            duration=200.0,
            cover_path="",
            dominant_color=(30, 215, 96),
        )
        self.store.tracks["local_hash_456"] = new_track
        del self.store.tracks["gd_file123"]
        self.store.reconcile_playlist_track_ids("gd_file123", "local_hash_456")

        self.assertIn("local_hash_456", pl.track_ids)
        self.assertNotIn("gd_file123", pl.track_ids)
        pl_names_after = self.store.get_track_playlists(new_track)
        self.assertIn("Favoritas Verano", pl_names_after)

    def test_delete_track_from_app_and_remove_from_playlist(self):
        dummy_file = self.test_dir / "music_library" / "to_delete.mp3"
        dummy_file.parent.mkdir(parents=True, exist_ok=True)
        dummy_file.write_bytes(b"dummy audio data")

        track = TrackMetadata(
            id="del_123",
            filepath=str(dummy_file),
            title="To Delete",
            artist="Artist",
            album="Album",
            genre="Pop",
            year="2026",
            duration=120.0,
            cover_path="",
            dominant_color=(30, 215, 96),
        )
        self.store.tracks["del_123"] = track
        self.store.liked_ids.append("del_123")
        pl = self.store.create_playlist("Lista Test")
        self.store.add_track_to_playlist(pl.id, "del_123")

        # Quitar de playlist
        self.store.remove_track_from_playlist(pl.id, "del_123")
        self.assertNotIn("del_123", pl.track_ids)

        # Re-agregar y probar eliminación global de la app
        self.store.add_track_to_playlist(pl.id, "del_123")
        self.assertTrue(dummy_file.exists())
        ok = self.store.delete_track_from_app("del_123", delete_local_file=True)
        self.assertTrue(ok)
        self.assertNotIn("del_123", self.store.tracks)
        self.assertNotIn("del_123", self.store.liked_ids)
        self.assertNotIn("del_123", pl.track_ids)
        self.assertFalse(dummy_file.exists())

    def test_populate_cloud_playlist_from_queries(self):
        """Verifica que las canciones de una lista .txt se agreguen de inmediato a la playlist en la nube."""
        queries = ["Yorushika - That's Why I Gave Up on Music", "Eve - Kaikai Kitan", "RADWIMPS - Sparkle"]
        pl, linked, pending = self.store.populate_cloud_playlist_from_queries(
            "Anime Hits 2026",
            queries,
            description="Playlists importada de prueba",
        )
        self.assertTrue(pl.is_cloud)
        self.assertEqual(len(pl.track_ids), 3)
        self.assertEqual(pending, 3)
        self.assertEqual(linked, 0)

        # Verificar que las pistas existan en store y tengan prefijo cloud://pending
        for tid in pl.track_ids:
            t = self.store.tracks.get(tid)
            self.assertIsNotNone(t)
            self.assertTrue(t.filepath.startswith("cloud://pending/"))

        # Si volvemos a llamar con una ya existente localmente
        existing_t = TrackMetadata(
            id="local_track_99",
            filepath="/tmp/music/local.mp3",
            title="Eve - Kaikai Kitan",
            artist="Eve",
            album="Kitan",
            genre="J-Pop",
            year="2021",
            duration=210.0,
            cover_path="",
            dominant_color=(30, 215, 96),
        )
        self.store.tracks["local_track_99"] = existing_t
        pl2, linked2, pending2 = self.store.populate_cloud_playlist_from_queries(
            "Anime Hits 2026",
            ["Eve - Kaikai Kitan", "Nueva Cancion 123"],
        )
        self.assertIn("local_track_99", pl2.track_ids)
        self.assertGreaterEqual(linked2, 1)


if __name__ == "__main__":
    unittest.main()
