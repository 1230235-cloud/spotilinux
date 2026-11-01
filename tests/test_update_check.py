#!/usr/bin/env python3
"""Pruebas de la comprobación de actualizaciones de escritorio."""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PyQt6.QtWidgets import QApplication

import main as app_main
from app_version import APP_VERSION_CODE

_app = QApplication.instance() or QApplication([])


def _fake_response(payload: dict, status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestUpdateCheck(unittest.TestCase):
    def _run_worker(self, urlopen_mock):
        events = []
        w = app_main.GitHubDesktopUpdateWorker()
        w.update_available.connect(lambda d: events.append(("new", d["versionCode"])))
        w.up_to_date.connect(lambda d: events.append(("same", d["versionCode"])))
        w.check_failed.connect(lambda e: events.append(("fail", e)))
        with patch("main.urllib.request.urlopen", urlopen_mock):
            w.run()  # síncrono: señales directas en el mismo hilo
        return events

    def test_newer_remote_version_is_reported(self):
        ev = self._run_worker(MagicMock(return_value=_fake_response({"versionCode": APP_VERSION_CODE + 1})))
        self.assertEqual(ev, [("new", APP_VERSION_CODE + 1)])

    def test_same_version_is_up_to_date(self):
        ev = self._run_worker(MagicMock(return_value=_fake_response({"versionCode": APP_VERSION_CODE})))
        self.assertEqual(ev, [("same", APP_VERSION_CODE)])

    def test_network_error_is_reported_not_swallowed(self):
        ev = self._run_worker(MagicMock(side_effect=OSError("sin red")))
        self.assertEqual(ev[0][0], "fail")
        self.assertIn("sin red", ev[0][1])

    def test_invalid_payload_is_failure(self):
        ev = self._run_worker(MagicMock(return_value=_fake_response({"foo": 1})))
        self.assertEqual(ev[0][0], "fail")

    def test_local_and_published_versions_match(self):
        published = json.loads((BASE_DIR / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(published["versionCode"], APP_VERSION_CODE)


if __name__ == "__main__":
    unittest.main()
