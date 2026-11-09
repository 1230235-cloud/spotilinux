#!/usr/bin/env python3
"""
Suite de pruebas unitarias para el núcleo de MaoMusic / SpotiLinux.
Valida normalización de títulos, identificadores canónicos de Drive,
hash de contraseñas y lógica de playlists.
"""

import sys
import unittest
from pathlib import Path

# Asegurar que el directorio raíz del proyecto esté en sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from gdrive_sync import (
    clean_track_title,
    normalize_track_key,
    extract_drive_id_from_url,
    canonical_drive_track_id,
    generate_recovery_key,
    hash_password,
    merge_account_records,
    validate_recovery_key,
)
from storage import Playlist, extract_gd_id_from_filename


class TestTitleNormalization(unittest.TestCase):
    def test_clean_track_title_suffixes(self):
        cases = [
            ("Song Name (Official Music Video).mp3", "Song Name"),
            ("y2mate.com - Artist - Title [Lyrics].flac", "Artist - Title"),
            ("Track Title (Video Oficial).wav", "Track Title"),
            ("Song Title 「Official Music Video」", "Song Title"),
            ("Song Title 【Official Audio】", "Song Title"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(clean_track_title(raw), expected)

    def test_clean_track_title_nightcore(self):
        cases = [
            ("Nightcore - We Don't Talk Anymore.mp3", "Nightcore - We Don't Talk Anymore"),
            ("[Nightcore] Treat You Better", "Nightcore - Treat You Better"),
            ("「Nightcore」- Say My Name", "Nightcore - Say My Name"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(clean_track_title(raw), expected)

    def test_normalize_track_key_accent_and_symbols(self):
        key1 = normalize_track_key("Canción de Prueba (Video Oficial)")
        key2 = normalize_track_key("cancion de prueba")
        self.assertEqual(key1, key2)
        self.assertEqual(key1, "cancion de prueba")

    def test_normalize_track_key_cjk_brackets(self):
        key = normalize_track_key("「Official Video」 Que Dolor")
        self.assertIn("que dolor", key)


class TestDriveIdentifiers(unittest.TestCase):
    def test_extract_drive_id(self):
        url = "https://drive.google.com/drive/folders/1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT?usp=sharing"
        self.assertEqual(extract_drive_id_from_url(url), "1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT")
        
        file_url = "https://drive.google.com/file/d/1tKyv8TEFVGP0f-r8ohMeDj5lz3OC-bsa/view"
        self.assertEqual(extract_drive_id_from_url(file_url), "1tKyv8TEFVGP0f-r8ohMeDj5lz3OC-bsa")

        raw_id = "1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T"
        self.assertEqual(extract_drive_id_from_url(raw_id), raw_id)

    def test_canonical_id(self):
        fid = "1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T"
        self.assertEqual(canonical_drive_track_id(fid), f"gd_{fid}")

    def test_extract_gd_id_from_filename(self):
        fn1 = "gd_1234567890__MySong.mp3"
        self.assertEqual(extract_gd_id_from_filename(fn1), "gd_1234567890")

        fn2 = "gd_1234567890_MySong.mp3"
        self.assertEqual(extract_gd_id_from_filename(fn2), "gd_1234567890")


class TestSecurityAndAccounts(unittest.TestCase):
    def test_hash_password_consistency(self):
        h1 = hash_password("secreto123")
        h2 = hash_password("secreto123")
        h3 = hash_password("otro_password")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)
        self.assertEqual(len(h1), 64)  # SHA-256 en hex

    def test_merge_account_records(self):
        acc1 = {
            "username": "usuario1",
            "updated_at": 100.0,
            "recovery_key": "MAO-ABCD-1234",
            "liked_ids": ["t1", "t2"],
            "playlists": [{"id": "p1", "name": "Favoritos", "track_ids": ["t1"]}],
        }
        acc2 = {
            "username": "usuario1",
            "updated_at": 200.0,
            "liked_ids": ["t2", "t3"],
            "playlists": [{"id": "p1", "name": "Favoritos", "track_ids": ["t1", "t3"]}],
        }
        merged = merge_account_records(acc1, acc2)
        self.assertIn("t1", merged["liked_ids"])
        self.assertIn("t2", merged["liked_ids"])
        self.assertIn("t3", merged["liked_ids"])
        self.assertEqual(len(merged["playlists"]), 1)
        self.assertEqual(set(merged["playlists"][0]["track_ids"]), {"t1", "t3"})
        self.assertEqual(merged["recovery_key"], "MAO-ABCD-1234")

    def test_recovery_key_generation_and_validation(self):
        key = generate_recovery_key()
        self.assertTrue(key.startswith("MAO-"))
        parts = key.split("-")
        self.assertEqual(len(parts), 3)
        self.assertEqual(len(parts[1]), 4)
        self.assertEqual(len(parts[2]), 4)

        acc = {"email": "test@maomusic.com", "recovery_key": key}
        self.assertTrue(validate_recovery_key(acc, key))
        self.assertTrue(validate_recovery_key(acc, key.lower()))
        self.assertTrue(validate_recovery_key(acc, f"  {key}  "))
        self.assertFalse(validate_recovery_key(acc, "MAO-WRON-GKEY"))
        self.assertFalse(validate_recovery_key(acc, ""))

        # Fallback determinista para cuenta legacy (y rechazo del correo plano como clave)
        import hashlib
        legacy_acc = {"email": "legacy@maomusic.com"}
        expected_legacy_hash = hashlib.sha256(b"recovery:legacy@maomusic.com").hexdigest()[:8].upper()
        self.assertTrue(validate_recovery_key(legacy_acc, f"MAO-{expected_legacy_hash}"))
        self.assertTrue(validate_recovery_key(legacy_acc, expected_legacy_hash))
        self.assertFalse(validate_recovery_key(legacy_acc, "legacy@maomusic.com"))
        self.assertFalse(validate_recovery_key(legacy_acc, "LEGACY@MAOMUSIC.COM"))


class TestPlaylistModel(unittest.TestCase):
    def test_playlist_serialization(self):
        pl = Playlist(id="pl_test", name="Synthwave", description="Retro", track_ids=["s1", "s2"])
        d = pl.to_dict()
        self.assertEqual(d["name"], "Synthwave")
        restored = Playlist.from_dict(d)
        self.assertEqual(restored.id, pl.id)
        self.assertEqual(restored.track_ids, ["s1", "s2"])


if __name__ == "__main__":
    unittest.main()


class TestLinkingRobustness(unittest.TestCase):
    def test_find_free_port_skips_busy_port(self):
        import socket
        from gdrive_sync import find_free_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind(("127.0.0.1", 0))
            busy_port = busy.getsockname()[1]
            busy.listen(1)
            free = find_free_port(busy_port, busy_port + 5)
            self.assertNotEqual(free, busy_port)

    def test_upload_reports_failure_when_cloud_unreachable(self):
        import tempfile
        from unittest.mock import patch
        from gdrive_sync import GoogleDriveConfig, upload_cloud_database

        with tempfile.TemporaryDirectory() as tmp, patch("gdrive_sync.requests") as req:
            req.post.side_effect = Exception("offline")
            req.put.side_effect = Exception("offline")
            ok, _msg = upload_cloud_database(GoogleDriveConfig(), Path(tmp) / "db.json", {"accounts": {}})
            self.assertFalse(ok)
            self.assertTrue((Path(tmp) / "db.json").exists())


class TestPlaybackAndBatchDownload(unittest.TestCase):
    def test_audio_engine_delegates_gdrive_track(self):
        import tempfile
        from PyQt6.QtWidgets import QApplication
        from audio_engine import AudioEngine
        from metadata import TrackMetadata

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            engine = AudioEngine(Path(tmp))
            delegated = []
            engine.track_stream_requested.connect(lambda t, ctx: delegated.append((t, ctx)))

            cloud_track = TrackMetadata(
                id="gd_12345",
                filepath="gdrive://12345",
                title="Cloud Song",
                artist="Cloud Artist",
                album="Cloud Album",
                genre="Pop",
                year=2026,
                duration=180,
                cover_path="",
                dominant_color="",
            )
            engine.play_track(cloud_track)
            self.assertEqual(len(delegated), 1)
            self.assertEqual(delegated[0][0].id, "gd_12345")


class TestCrossDeviceLinking(unittest.TestCase):
    def test_store_device_id_persists(self):
        import tempfile
        from storage import LibraryStore

        with tempfile.TemporaryDirectory() as tmp:
            store1 = LibraryStore(Path(tmp))
            dev_id = store1.device_id
            self.assertTrue(dev_id.startswith("sp_desktop_"))
            store1.save()

            store2 = LibraryStore(Path(tmp))
            self.assertEqual(store2.device_id, dev_id)

    def test_session_expiration_logic(self):
        import time

        now_ms = time.time() * 1000
        # Sesión reciente (hace 2 minutos)
        recent_session = {
            "deviceId": "sp_dev_phone1",
            "title": "Cancion Prueba",
            "posSec": 45.0,
            "updatedAt": now_ms - (2 * 60 * 1000)
        }
        # Sesión expirada (hace 14 horas)
        expired_session = {
            "deviceId": "sp_dev_phone1",
            "title": "Cancion Vieja",
            "posSec": 10.0,
            "updatedAt": now_ms - (14 * 3600 * 1000)
        }

        ttl_limit = 12 * 3600 * 1000
        self.assertTrue(now_ms - recent_session["updatedAt"] <= ttl_limit)
        self.assertFalse(now_ms - expired_session["updatedAt"] <= ttl_limit)
