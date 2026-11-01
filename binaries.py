#!/usr/bin/env python3
"""
System binary locator and resolver for MaoMusic.
Resolves executables like yt-dlp, ffmpeg, rclone across environments.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def resolve_binary(name: str) -> Optional[str]:
    """
    Finds the absolute path to an executable binary with fallback hierarchy:
    1. Custom environment variable (e.g. MAOMUSIC_FFMPEG or MAOMUSIC_YT_DLP).
    2. Active Python virtualenv bin directory (e.g. venv/bin/yt-dlp).
    3. System PATH via shutil.which().
    4. Optional fallback for ffmpeg via imageio_ffmpeg if installed.
    """
    if not name:
        return None

    # 1. Environment variable override (e.g. MAOMUSIC_YT_DLP, MAOMUSIC_FFMPEG)
    env_key = f"MAOMUSIC_{name.upper().replace('-', '_')}"
    env_override = os.environ.get(env_key)
    if env_override:
        p = Path(env_override).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())

    # 2. Virtualenv or current interpreter bin directory
    try:
        py_dir = Path(sys.executable).parent
        venv_candidate = py_dir / name
        if venv_candidate.is_file() and os.access(venv_candidate, os.X_OK):
            return str(venv_candidate.resolve())
    except Exception:
        pass

    # 3. Standard system PATH search
    system_path = shutil.which(name)
    if system_path:
        return system_path

    # 4. Optional imageio_ffmpeg fallback for ffmpeg only
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            if ffmpeg_exe and Path(ffmpeg_exe).is_file():
                return ffmpeg_exe
        except Exception:
            pass

    return None
