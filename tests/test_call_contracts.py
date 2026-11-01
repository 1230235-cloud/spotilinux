#!/usr/bin/env python3
"""Guardarraíl: toda llamada a clases/funciones del proyecto debe coincidir con su firma.

Evita regresiones como pasar kwargs inexistentes a un worker (TypeError dentro de un slot
de PyQt6 aborta la aplicación).
"""

import ast
import importlib
import inspect
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

MODULES = ("gdrive_sync", "storage", "download_queue", "media_sync_worker", "audio_engine", "metadata", "download_queue_ui")
SCANNED_FILES = ("main.py", "download_queue_ui.py", "gdrive_sync.py", "media_sync_worker.py")


def _collect_callables() -> dict:
    found = {}
    for name in MODULES:
        mod = importlib.import_module(name)
        for attr, obj in vars(mod).items():
            if (inspect.isclass(obj) or inspect.isfunction(obj)) and getattr(obj, "__module__", None) == name:
                found[attr] = obj
    return found


class TestCallContracts(unittest.TestCase):
    def test_all_project_calls_match_signatures(self):
        callables = _collect_callables()
        problems = []
        for fname in SCANNED_FILES:
            tree = ast.parse((BASE_DIR / fname).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                    continue
                obj = callables.get(node.func.id)
                if obj is None:
                    continue
                if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                    continue
                try:
                    sig = inspect.signature(obj)
                    sig.bind(*[None] * len(node.args), **{k.arg: None for k in node.keywords})
                except TypeError as exc:
                    problems.append(f"{fname}:{node.lineno} {node.func.id}: {exc}")
                except ValueError:
                    continue
        self.assertEqual(problems, [], "Firmas incompatibles:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
