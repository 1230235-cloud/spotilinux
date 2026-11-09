#!/usr/bin/env python3
"""
Pruebas unitarias para las funcionalidades de siguiente nivel:
- Shuffle con Pilas (LIFO) y Colas (FIFO).
- Carátulas personalizadas de Playlist y resolución de carpeta 'imagenes'.
- Modos visuales (Light / Dark / Free).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
_APP = QApplication.instance() or QApplication(sys.argv)

from audio_engine import AudioEngine
from metadata import TrackMetadata
from storage import Playlist
from styles import get_stylesheet
import gdrive_sync


class TestSiguienteNivel(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.engine = AudioEngine(Path(self.tmp_dir.name))
        # Creamos una lista de pistas de prueba
        self.tracks = [
            TrackMetadata(
                id=f"t_{i}",
                filepath=f"/fake/{i}.mp3",
                title=f"Track {i}",
                artist="Artist",
                album="Album",
                genre="Pop",
                year=2026,
                duration=180,
                cover_path="",
                dominant_color="#1DB954",
            )
            for i in range(5)
        ]
        self.engine.context_tracks = list(self.tracks)
        self.engine.shuffle = True

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_shuffle_stack_and_queue_flow(self):
        """Verifica que el aleatorio con cola FIFO y pila LIFO no repita y permita retroceso fiel."""
        first = self.tracks[0]
        self.engine.play_track(first, None)
        self.assertEqual(self.engine.current_track.id, first.id)
        self.assertEqual(self.engine._shuffle_history_stack, [first.id])

        # Avanzar en aleatorio (next)
        played_ids = [first.id]
        for _ in range(4):
            self.engine.next_track()
            cur_id = self.engine.current_track.id
            self.assertIn(cur_id, [t.id for t in self.tracks])
            played_ids.append(cur_id)

        # En 5 reproducciones del ciclo aleatorio, no debe haber repeticiones consecutivas
        for i in range(len(played_ids) - 1):
            self.assertNotEqual(played_ids[i], played_ids[i + 1])

        # Probar retroceso exacto (prev) mediante la pila de historial LIFO
        last_id = played_ids[-1]
        prev_id = played_ids[-2]
        self.engine.previous_track()
        self.assertEqual(self.engine.current_track.id, prev_id)
        self.assertEqual(self.engine._shuffle_forward_stack, [last_id])

        # Probar re-avance (next) tomando de la pila de re-avance
        self.engine.next_track()
        self.assertEqual(self.engine.current_track.id, last_id)
        self.assertEqual(len(self.engine._shuffle_forward_stack), 0)

    def test_playlist_cover_serialization(self):
        """Verifica que Playlist soporte y serialice cover_url y cover_drive_id."""
        pl = Playlist(
            id="pl_123",
            name="MaoMao Beats",
            description="Lo-fi medicinal",
            cover_url="https://drive.usercontent.google.com/download?id=img_999",
            cover_drive_id="img_999",
        )
        data = pl.to_dict()
        self.assertEqual(data["cover_url"], "https://drive.usercontent.google.com/download?id=img_999")
        self.assertEqual(data["cover_drive_id"], "img_999")

        restored = Playlist.from_dict(data)
        self.assertEqual(restored.cover_url, pl.cover_url)
        self.assertEqual(restored.cover_drive_id, pl.cover_drive_id)

    def test_visual_modes_dark_and_free(self):
        """Verifica que el estilo mantenga diseño de píldora y el visualizador soporte modo libre."""
        from visualizer_widget import AudioVisualizerWidget
        dark = get_stylesheet("dark")
        self.assertIn("#09090b", dark)
        self.assertIn("border-radius: 9999px", dark)
        vis = AudioVisualizerWidget()
        self.assertFalse(vis.free_stage_mode)
        vis.set_free_stage_mode(True)
        self.assertTrue(vis.free_stage_mode)

    def test_circular_download_button_paint_does_not_crash(self):
        """Verifica que CircularDownloadButton pinte correctamente sin excepciones ni dependencias no definidas (p. ej. QPen)."""
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QPixmap
        from main import CircularDownloadButton

        _ = QApplication.instance() or QApplication(sys.argv)
        btn = CircularDownloadButton(size=34)
        for state in ("idle", "downloading", "completed"):
            btn.set_state(state)
            btn.set_progress(45, 100)
            pix = QPixmap(34, 34)
            btn.render(pix)
            self.assertFalse(pix.isNull())

    def test_resolve_imagenes_subfolder(self):
        """Verifica que resolve_imagenes_subfolder_id detecte carpetas de imágenes."""
        cfg = gdrive_sync.GoogleDriveConfig(folder_id="root_123")
        fake_items = [
            {"id": "img_fld", "name": "imagenes", "is_folder": True},
            {"id": "aud_fld", "name": "Musica", "is_folder": True},
        ]
        gdrive_sync.list_public_drive_folder_items = MagicMock(return_value=fake_items)
        res = gdrive_sync.resolve_imagenes_subfolder_id(cfg)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "img_fld")


if __name__ == "__main__":
    unittest.main()
