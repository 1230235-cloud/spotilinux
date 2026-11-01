#!/usr/bin/env python3
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from main import setup_crash_logging


class TestCrashLogging(unittest.TestCase):
    def setUp(self):
        self._orig_sys_hook = sys.excepthook
        self._orig_thread_hook = threading.excepthook

    def tearDown(self):
        sys.excepthook = self._orig_sys_hook
        threading.excepthook = self._orig_thread_hook
        # Desconectar handlers para no interferir con otras pruebas
        root_logger = logging.getLogger()
        for h in list(root_logger.handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
                root_logger.removeHandler(h)

    def test_sys_excepthook_writes_traceback_to_log(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = setup_crash_logging(tmp_dir)
            self.assertTrue(Path(log_path).exists())

            test_message = "PruebaFalloControlada_98765"
            try:
                raise RuntimeError(test_message)
            except RuntimeError:
                exc_info = sys.exc_info()

            with patch("PyQt6.QtWidgets.QMessageBox.critical") as mock_box:
                sys.excepthook(*exc_info)

            # Flush handlers
            for h in logging.getLogger().handlers:
                h.flush()

            content = Path(log_path).read_text(encoding="utf-8")
            self.assertIn(test_message, content)
            self.assertIn("RuntimeError", content)
            self.assertIn("Excepción no capturada en hilo principal", content)

    def test_threading_excepthook_writes_to_log(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = setup_crash_logging(tmp_dir)

            test_msg = "ErrorHiloFondo_43210"
            try:
                raise ValueError(test_msg)
            except ValueError:
                exc_type, exc_val, tb = sys.exc_info()

            args = threading.ExceptHookArgs(
                (exc_type, exc_val, tb, threading.current_thread())
            )

            with patch("PyQt6.QtWidgets.QMessageBox.critical") as mock_box:
                threading.excepthook(args)

            for h in logging.getLogger().handlers:
                h.flush()

            content = Path(log_path).read_text(encoding="utf-8")
            self.assertIn(test_msg, content)
            self.assertIn("ValueError", content)
            self.assertIn("Excepción no capturada en hilo", content)


if __name__ == "__main__":
    unittest.main()
