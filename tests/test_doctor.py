#!/usr/bin/env python3
"""
Unit tests for doctor.py environment diagnostic.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import doctor


class TestDoctor(unittest.TestCase):
    def test_environment_check_all_ok(self):
        results = doctor.check_environment(online=False)
        self.assertTrue(len(results) >= 8)
        required_results = [r for r in results if r.required]
        for r in required_results:
            self.assertTrue(r.ok, f"Required check {r.name} failed: {r.detail}")

    def test_missing_binary_reporting(self):
        with patch("doctor.resolve_binary") as mock_res:
            mock_res.side_effect = lambda b: None if b == "ffmpeg" else "/usr/bin/yt-dlp"
            results = doctor.check_environment(online=False)
            ffmpeg_check = next((r for r in results if "ffmpeg" in r.name.lower()), None)
            self.assertIsNotNone(ffmpeg_check)
            self.assertFalse(ffmpeg_check.ok)
            self.assertTrue(ffmpeg_check.required)
            self.assertTrue(len(ffmpeg_check.hint) > 0)

    def test_missing_optional_binary(self):
        with patch("doctor.resolve_binary") as mock_res:
            mock_res.side_effect = lambda b: None if b == "rclone" else f"/usr/bin/{b}"
            results = doctor.check_environment(online=False)
            rclone_check = next((r for r in results if "rclone" in r.name.lower()), None)
            self.assertIsNotNone(rclone_check)
            self.assertFalse(rclone_check.ok)
            self.assertFalse(rclone_check.required)


if __name__ == "__main__":
    unittest.main()
