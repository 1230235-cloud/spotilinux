#!/usr/bin/env python3
"""Pruebas del ciclo de vida de QThread en la ventana principal (sin abortar al reemplazar workers)."""

import os
import sys
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

import main as app_main

_app = QApplication.instance() or QApplication([])


class _SlowWorker(QThread):
    def run(self) -> None:
        for _ in range(30):
            if self.isInterruptionRequested():
                return
            self.msleep(20)


class _FakeWindow:
    _retire_worker = app_main.SpotiLinuxWindow._retire_worker
    _stop_all_workers = app_main.SpotiLinuxWindow._stop_all_workers

    def __init__(self) -> None:
        self._retired_workers = []
        self._stream_worker = None


class TestWorkerLifecycle(unittest.TestCase):
    def test_replacing_running_worker_does_not_abort(self):
        win = _FakeWindow()
        for _ in range(3):
            win._retire_worker("_stream_worker")
            win._stream_worker = _SlowWorker()
            win._stream_worker.start()
        win._stop_all_workers()
        self.assertFalse(win._stream_worker.isRunning())

    def test_retire_none_is_noop(self):
        win = _FakeWindow()
        win._retire_worker("_stream_worker")
        win._retire_worker("_missing_attr")


if __name__ == "__main__":
    unittest.main()
