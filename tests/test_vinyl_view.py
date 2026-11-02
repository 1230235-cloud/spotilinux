#!/usr/bin/env python3
"""Pruebas unitarias para el componente tocadiscos de vinilo y video de fondo (vinyl_view)."""

import unittest
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from vinyl_view import clean_str_key, VinylDiscWidget, VinylStandbyOverlay

_app = QApplication.instance() or QApplication([])


class TestVinylView(unittest.TestCase):
    def test_clean_str_key(self):
        self.assertEqual(clean_str_key("01 - Song Name (Official Video).mp4"), "01songnameofficialvideomp4")
        self.assertEqual(clean_str_key("Canción Con Tildes & Símbolos!"), "cancioncontildessimbolos")
        self.assertEqual(clean_str_key(""), "")

    def test_vinyl_disc_state(self):
        disc = VinylDiscWidget()
        disc.set_playing(True)
        self.assertTrue(disc._is_playing)
        self.assertEqual(disc._target_arm_angle, 24.0)

        disc.set_playing(False)
        self.assertFalse(disc._is_playing)
        self.assertEqual(disc._target_arm_angle, 0.0)

    def test_vinyl_overlay_lifecycle(self):
        overlay = VinylStandbyOverlay(Path(".").resolve())
        overlay.update_state("Test Title", "Test Artist", None, "test.mp3", False)
        self.assertEqual(overlay.lbl_track_title.text(), "Test Title")
        self.assertEqual(overlay.lbl_track_artist.text(), "Test Artist")
        self.assertFalse(overlay.btn_play.icon().isNull())

        overlay.update_state("Test Title", "Test Artist", None, "test.mp3", True)
        self.assertFalse(overlay.btn_play.icon().isNull())

        overlay.close_overlay()
        self.assertFalse(overlay.isVisible())


if __name__ == "__main__":
    unittest.main()
