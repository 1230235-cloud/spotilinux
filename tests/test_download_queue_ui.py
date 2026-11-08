#!/usr/bin/env python3
"""
Pruebas de interfaz para download_queue_ui.py usando el backend offscreen de Qt.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Configurar backend headless para Qt antes de importar QApplication
os.environ["QT_QPA_PLATFORM"] = "offscreen"

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PyQt6.QtWidgets import QApplication

from download_queue import DownloadQueue, QueueRunner
from download_queue_ui import DownloadQueuePanel, QueueSignals

# Asegurar una instancia de QApplication para pruebas
app = QApplication.instance() or QApplication([])


class TestDownloadQueueUI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "ui_test.sqlite"
        self.queue = DownloadQueue(self.db_path)
        self.signals = QueueSignals()

        self.mock_downloader = MagicMock()
        self.runner = QueueRunner(
            queue=self.queue,
            downloader_factory=lambda: self.mock_downloader,
            on_event=lambda ev: self.signals.summary_changed.emit(),
        )

        self.panel = DownloadQueuePanel(
            queue=self.queue,
            runner=self.runner,
            signals=self.signals,
        )

    def tearDown(self):
        self.runner.stop()
        self.panel.close()
        self.temp_dir.cleanup()

    def test_panel_populates_table(self):
        self.queue.add(["Track A", "Track B", "Track C"])
        self.panel._refresh_table()

        self.assertEqual(self.panel.table.rowCount(), 3)
        self.assertEqual(self.panel.table.item(0, 1).text(), "Track A")
        self.assertEqual(self.panel.table.item(1, 1).text(), "Track B")
        self.assertEqual(self.panel.table.item(2, 1).text(), "Track C")

    def test_retry_failed_action(self):
        self.queue.add(["Failed Song"], target_playlist="Rock Clasico")
        item = self.queue.get(1)
        self.queue.mark_error(item.id, "Simulated Error", is_transient=False)

        self.panel._refresh_table()
        self.assertEqual(self.panel.table.item(0, 2).text(), "🎵 Rock Clasico")
        self.assertEqual(self.panel.table.item(0, 3).text(), "❌ Error")

        # Ejecutar retry failed desde el panel
        self.panel._retry_failed()
        self.assertEqual(self.panel.table.item(0, 3).text(), "⏳ En cola")
        self.assertEqual(self.queue.get(1).status, "pending")

    def test_clear_finished_action(self):
        self.queue.add(["Done Song", "Pending Song"])
        self.queue.mark_done(1, "Done Song", {})

        self.panel._refresh_table()
        self.assertEqual(self.panel.table.rowCount(), 2)

        self.panel._clear_finished()
        self.assertEqual(self.panel.table.rowCount(), 1)
        self.assertEqual(self.panel.table.item(0, 1).text(), "Pending Song")

    def test_concurrency_change(self):
        self.panel.cmb_concurrency.setCurrentText("3")
        self.assertEqual(self.runner.max_parallel, 3)
        self.assertEqual(self.queue.get_meta("max_parallel"), "3")


if __name__ == "__main__":
    unittest.main()
