#!/usr/bin/env python3
"""
Pruebas unitarias para download_queue.py y la integración de cola en media_sync_worker.py.
Sin dependencias de red ni PyQt6.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from download_queue import DownloadQueue, QueueItem, QueueRunner, parse_playlist_txt
from storage import LibraryStore
from media_sync_worker import MediaDownloaderWorker, classify_error


class TestDownloadQueue(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_queue.sqlite"
        self.queue = DownloadQueue(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_and_deduplication(self):
        queries = [
            "  ",
            "# Comentario",
            "Canción 1 - Artista",
            "Canción 1 - Artista",  # Duplicado exacto
            "cancion 1 artista",    # Mismo normalize_track_key
            "Canción 2 - Banda",
        ]
        added, skipped = self.queue.add(queries)
        self.assertEqual(added, 2)
        self.assertEqual(skipped, 2)

        items = self.queue.list()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].query, "Canción 1 - Artista")
        self.assertEqual(items[0].status, "pending")
        self.assertEqual(items[1].query, "Canción 2 - Banda")

    def test_persistence_between_instances(self):
        self.queue.add(["Track Alpha", "Track Beta"])
        # Abrir segunda instancia conectada al mismo archivo
        queue2 = DownloadQueue(self.db_path)
        items2 = queue2.list()
        self.assertEqual(len(items2), 2)
        self.assertEqual(items2[0].query, "Track Alpha")
        self.assertEqual(items2[1].query, "Track Beta")

    def test_state_flow_and_next_runnable(self):
        self.queue.add(["Track One"])
        item = self.queue.next_runnable(now=100.0)
        self.assertIsNotNone(item)
        self.assertEqual(item.query, "Track One")

        # Marcar como running
        self.queue.mark_running(item.id, now=101.0)
        item_running = self.queue.get(item.id)
        self.assertEqual(item_running.status, "running")
        self.assertEqual(item_running.attempts, 1)

        # En running ya no debe ser runnable
        self.assertIsNone(self.queue.next_runnable(now=102.0))

        # Actualizar progreso
        self.queue.update_progress(item.id, stage="download", progress=45.5)
        item_prog = self.queue.get(item.id)
        self.assertEqual(item_prog.stage, "download")
        self.assertAlmostEqual(item_prog.progress, 45.5)

        # Marcar done
        self.queue.mark_done(item.id, title="Track One (Clean)", result_data={"audio_file": "one.mp3"}, now=105.0)
        item_done = self.queue.get(item.id)
        self.assertEqual(item_done.status, "done")
        self.assertEqual(item_done.progress, 100.0)
        self.assertEqual(item_done.title, "Track One (Clean)")

    def test_transient_error_backoff(self):
        self.queue.add(["Flaky Track"])
        item = self.queue.next_runnable(now=100.0)
        self.queue.mark_running(item.id, now=100.0)

        # Fallo 1 transitorio -> debe esperar 15 segundos
        self.queue.mark_error(item.id, "HTTP 429 Too Many Requests", is_transient=True, now=100.0)
        item_err1 = self.queue.get(item.id)
        self.assertEqual(item_err1.status, "pending")
        self.assertAlmostEqual(item_err1.next_retry_at, 115.0)

        # A los 110s no debe ser runnable todavía
        self.assertIsNone(self.queue.next_runnable(now=110.0))
        # A los 115s sí
        self.assertIsNotNone(self.queue.next_runnable(now=115.0))

        # Intento 2
        self.queue.mark_running(item.id, now=115.0)
        self.queue.mark_error(item.id, "Connection reset by peer", is_transient=True, now=115.0)
        item_err2 = self.queue.get(item.id)
        self.assertEqual(item_err2.status, "pending")
        self.assertAlmostEqual(item_err2.next_retry_at, 115.0 + 60.0)

        # Intento 3 (max_attempts = 3)
        self.queue.mark_running(item.id, now=175.0)
        self.queue.mark_error(item.id, "Timeout again", is_transient=True, now=175.0)
        item_err3 = self.queue.get(item.id)
        # Se agotaron los intentos -> status error permanente
        self.assertEqual(item_err3.status, "error")

    def test_permanent_error_immediately_fails(self):
        self.queue.add(["Private Track"])
        item = self.queue.next_runnable(now=100.0)
        self.queue.mark_running(item.id, now=100.0)
        self.queue.mark_error(item.id, "Video unavailable / Private video", is_transient=False, now=101.0)
        item_err = self.queue.get(item.id)
        self.assertEqual(item_err.status, "error")

    def test_recover_after_crash(self):
        self.queue.add(["Crashed Track"])
        item = self.queue.next_runnable()
        self.queue.mark_running(item.id)
        self.assertEqual(self.queue.get(item.id).status, "running")

        recovered = self.queue.recover_after_crash()
        self.assertEqual(recovered, 1)
        self.assertEqual(self.queue.get(item.id).status, "pending")

    def test_retry_failed_and_clear_finished(self):
        self.queue.add(["Track 1", "Track 2", "Track 3"])
        i1 = self.queue.get(1)
        i2 = self.queue.get(2)
        i3 = self.queue.get(3)

        self.queue.mark_done(i1.id, "Track 1", {})
        self.queue.mark_error(i2.id, "Error fatal", is_transient=False)
        self.queue.mark_cancelled(i3.id)

        # Retry failed debe revivir i2 e i3
        retried = self.queue.retry_failed()
        self.assertEqual(retried, 2)
        self.assertEqual(self.queue.get(i2.id).status, "pending")
        self.assertEqual(self.queue.get(i3.id).status, "pending")

        # Clear finished debe eliminar solo i1 (que es done)
        cleared = self.queue.clear_finished()
        self.assertEqual(cleared, 1)
        self.assertIsNone(self.queue.get(i1.id))
        self.assertIsNotNone(self.queue.get(i2.id))


class TestQueueRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_runner.sqlite"
        self.queue = DownloadQueue(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_runner_executes_and_reports_events(self):
        self.queue.add(["Simple Song"])
        events = []

        mock_downloader = MagicMock()
        mock_downloader.process_query.return_value = {
            "status": "success",
            "title": "Simple Song",
            "artist": "Mock Artist",
            "duration": 180.0,
            "video_file": "vid.mp4",
            "audio_file": "aud.mp3",
            "sync": {"video_synced": True, "audio_synced": True},
        }

        runner = QueueRunner(
            queue=self.queue,
            downloader_factory=lambda: mock_downloader,
            on_event=lambda ev: events.append(ev),
            max_parallel=1,
        )

        runner.start()
        # Esperar hasta que se complete
        for _ in range(50):
            item = self.queue.get(1)
            if item and item.status == "done":
                break
            time.sleep(0.05)
        runner.stop()

        item = self.queue.get(1)
        self.assertEqual(item.status, "done")
        self.assertEqual(item.title, "Simple Song")

        event_types = [e.get("type") for e in events]
        self.assertIn("item_started", event_types)
        self.assertIn("item_done", event_types)

    def test_runner_pause_and_resume(self):
        self.queue.add(["Song Paused"])
        mock_downloader = MagicMock()
        mock_downloader.process_query.return_value = {"status": "success", "title": "Done"}

        runner = QueueRunner(
            queue=self.queue,
            downloader_factory=lambda: mock_downloader,
            max_parallel=1,
        )

        runner.pause()
        self.assertTrue(runner.is_paused)
        runner.start()

        # Mientras está en pausa no debe arrancar la tarea
        time.sleep(0.2)
        item = self.queue.get(1)
        self.assertEqual(item.status, "pending")

        # Al reanudar se debe procesar
        runner.resume()
        self.assertFalse(runner.is_paused)
        for _ in range(50):
            item = self.queue.get(1)
            if item and item.status == "done":
                break
            time.sleep(0.05)
        runner.stop()

        self.assertEqual(self.queue.get(1).status, "done")

    def test_runner_cancellation(self):
        self.queue.add(["Long Song"])
        cancelled_flag = threading.Event()

        def slow_process(query, keep_local=True, progress_cb=None, cancel_event=None):
            for _ in range(100):
                if cancel_event and cancel_event.is_set():
                    cancelled_flag.set()
                    return {"status": "cancelled", "error": "Cancelado"}
                time.sleep(0.02)
            return {"status": "success", "title": "Never Reached"}

        mock_downloader = MagicMock()
        mock_downloader.process_query.side_effect = slow_process

        runner = QueueRunner(
            queue=self.queue,
            downloader_factory=lambda: mock_downloader,
            max_parallel=1,
        )
        runner.start()

        # Esperar a que pase a running
        for _ in range(50):
            item = self.queue.get(1)
            if item and item.status == "running":
                break
            time.sleep(0.02)

        runner.cancel_item(1)

        for _ in range(50):
            item = self.queue.get(1)
            if item and item.status == "cancelled":
                break
            time.sleep(0.02)
        runner.stop()

        item = self.queue.get(1)
        self.assertEqual(item.status, "cancelled")
        self.assertTrue(cancelled_flag.is_set())

    def test_parse_playlist_txt_default_and_sections(self):
        sample_txt = """
# Lista de canciones favoritas
Song 1 - Artist 1
Song 2 - Artist 2

# Playlist: Rock Classics
Rock 1 - Band A
Rock 2 - Band B

[Anime Openings]
Opening 1 - LiSA
Opening 2 - Aimer
"""
        entries = parse_playlist_txt(sample_txt, default_playlist_name="Gym Tracks")
        self.assertEqual(len(entries), 6)
        self.assertEqual(entries[0], ("Song 1 - Artist 1", "Gym Tracks"))
        self.assertEqual(entries[1], ("Song 2 - Artist 2", "Gym Tracks"))
        self.assertEqual(entries[2], ("Rock 1 - Band A", "Rock Classics"))
        self.assertEqual(entries[3], ("Rock 2 - Band B", "Rock Classics"))
        self.assertEqual(entries[4], ("Opening 1 - LiSA", "Anime Openings"))
        self.assertEqual(entries[5], ("Opening 2 - Aimer", "Anime Openings"))

    def test_queue_persists_target_playlist_and_migrates(self):
        added, skipped = self.queue.add(["Phonk 1", "Phonk 2"], target_playlist="Drift Phonk")
        self.assertEqual(added, 2)
        items = self.queue.list()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].target_playlist, "Drift Phonk")
        self.assertEqual(items[1].target_playlist, "Drift Phonk")

        # Verificar persistencia reabriendo la base de datos
        queue2 = DownloadQueue(self.db_path)
        item1 = queue2.get(items[0].id)
        self.assertIsNotNone(item1)
        self.assertEqual(item1.target_playlist, "Drift Phonk")

    def test_storage_get_or_create_playlist_and_find_track(self):
        store_path = Path(self.temp_dir.name) / "store.json"
        store = LibraryStore(store_path)
        pl1 = store.get_or_create_playlist_by_name("Lo-Fi Beats", "Musica relajante")
        self.assertEqual(pl1.name, "Lo-Fi Beats")
        self.assertEqual(pl1.description, "Musica relajante")

        # Re-obtener la misma sin duplicar
        pl2 = store.get_or_create_playlist_by_name("lo-fi beats")
        self.assertEqual(pl1.id, pl2.id)
        self.assertEqual(len(store.playlists), 1)

        # Buscar pista simulada
        t = store.find_track_by_query_or_filename(title="NonExistentTrack")
        self.assertIsNone(t)


class TestErrorClassificationAndWorker(unittest.TestCase):
    def test_classify_error(self):
        self.assertEqual(classify_error("HTTP Error 429: Too Many Requests"), "transient")
        self.assertEqual(classify_error("RemoteDisconnected: Connection reset"), "transient")
        self.assertEqual(classify_error("Connection timed out"), "transient")
        self.assertEqual(classify_error("503 Server Error: Service Unavailable"), "transient")
        self.assertEqual(classify_error("Temporary failure in name resolution"), "transient")

        self.assertEqual(classify_error("ERROR: Video unavailable"), "permanent")
        self.assertEqual(classify_error("Private video"), "permanent")
        self.assertEqual(classify_error("ffmpeg: syntax error or invalid argument"), "permanent")
        self.assertEqual(classify_error(""), "permanent")

    def test_missing_binaries(self):
        worker = MediaDownloaderWorker()
        with patch("media_sync_worker.resolve_binary", return_value=None):
            missing = worker.missing_binaries()
            self.assertIn("yt-dlp", missing)
            self.assertIn("ffmpeg", missing)


if __name__ == "__main__":
    unittest.main()
