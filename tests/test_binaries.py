#!/usr/bin/env python3
"""
Unit tests for binaries.py.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from binaries import resolve_binary


class TestBinariesResolver(unittest.TestCase):
    def test_nonexistent_binary(self):
        self.assertIsNone(resolve_binary("nonexistent_binary_xyz_12345"))

    def test_environment_variable_override(self):
        # Point MAOMUSIC_TEST_BIN to python executable itself
        with patch.dict(os.environ, {"MAOMUSIC_TEST_BIN": sys.executable}):
            resolved = resolve_binary("test-bin")
            self.assertEqual(resolved, str(Path(sys.executable).resolve()))

    def test_virtualenv_directory_resolution(self):
        py_bin_name = Path(sys.executable).name
        resolved = resolve_binary(py_bin_name)
        self.assertIsNotNone(resolved)
        self.assertTrue(Path(resolved).is_file())


if __name__ == "__main__":
    unittest.main()
