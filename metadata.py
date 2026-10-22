#!/usr/bin/env python3
"""
Extractor de metadatos ID3 / Vorbis / FLAC / MP4 y carátulas incrustadas (APIC).
Incluye generación automática de portadas de respaldo y cálculo de color dominante
para el encabezado dinámico al estilo Spotify Desktop.
"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".opus", ".aac", ".wma"}

FALLBACK_PALETTES = [
    ((20, 30, 48), (36, 59, 85), (29, 185, 84)),
    ((44, 18, 62), (106, 48, 147), (160, 68, 255)),
    ((15, 32, 39), (32, 58, 67), (44, 83, 100)),
    ((58, 28, 38), (142, 45, 86), (255, 94, 98)),
    ((19, 42, 34), (29, 151, 108), (147, 249, 185)),
    ((35, 37, 38), (65, 67, 69), (242, 153, 74)),
]


@dataclass
class TrackMetadata:
    id: str
    filepath: str
    title: str
    artist: str
    album: str
    genre: str
    year: str
    duration: float
    cover_path: str
    dominant_color: tuple[int, int, int]
    added_at: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["dominant_color"] = list(self.dominant_color)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "TrackMetadata":
        color = data.get("dominant_color", [29, 185, 84])
        return cls(
            id=data["id"],
            filepath=data["filepath"],
            title=data.get("title", "Desconocido"),
            artist=data.get("artist", "Artista desconocido"),
            album=data.get("album", "Álbum desconocido"),
            genre=data.get("genre", ""),
            year=data.get("year", ""),
            duration=float(data.get("duration", 0.0)),
            cover_path=data.get("cover_path", ""),
            dominant_color=(int(color[0]), int(color[1]), int(color[2])),
            added_at=float(data.get("added_at", 0.0)),
        )


def compute_track_id(filepath: Path) -> str:
    resolved = str(filepath.resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def compute_dominant_color(image_path: Path) -> tuple[int, int, int]:
    """Calcula un color vibrante representativo de la carátula para el fondo degradado estilo Spotify."""
    try:
        with Image.open(image_path) as img:
            small = img.convert("RGB").resize((36, 36))
            arr = np.asarray(small, dtype=np.float32)
            pixels = arr.reshape(-1, 3)

            # Filtrar píxeles demasiado oscuros o demasiado blancos para lograr un degradado rico
            brightness = pixels.mean(axis=1)
            max_c = pixels.max(axis=1)
            min_c = pixels.min(axis=1)
            saturation = (max_c - min_c) / (max_c + 1e-5)

            mask = (brightness > 28) & (brightness < 225) & (saturation > 0.18)
            valid = pixels[mask] if np.any(mask) else pixels
            mean_rgb = np.mean(valid, axis=0)

            # Ajustar intensidad para que luzca elegante con texto blanco encima
            r, g, b = [int(np.clip(c * 0.82, 24, 195)) for c in mean_rgb]
            return (r, g, b)
    except Exception:
        return (32, 88, 64)


def generate_fallback_cover(title: str, artist: str, output_path: Path, seed_str: str) -> None:
    """Genera una carátula minimalista estilo Spotify cuando el archivo de audio no tiene imagen incrustada."""
    size = 400
    h = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16)
    c1, c2, c3 = FALLBACK_PALETTES[h % len(FALLBACK_PALETTES)]

    y_idx, x_idx = np.mgrid[0:size, 0:size]
    t = (x_idx * 0.55 + y_idx * 0.45) / size
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    for ch in range(3):
        arr[:, :, ch] = np.clip(c1[ch] * (1 - t) + c2[ch] * t, 0, 255).astype(np.uint8)

    img = Image.fromarray(arr, mode="RGB")
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2 - 20
    r = 86
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=c3, width=4)
    draw.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), fill=c3)

    initials = "".join([w[0].upper() for w in title.split()[:2] if w]) or "♪"
    try:
        font_big = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 36)
        font_sub = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans.ttf", 18)
    except Exception:
        font_big = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    short_title = title if len(title) <= 22 else title[:20] + "…"
    short_artist = artist if len(artist) <= 28 else artist[:26] + "…"
    draw.text((28, size - 85), short_title, fill=(255, 255, 255), font=font_big)
    draw.text((28, size - 42), short_artist, fill=(200, 210, 220), font=font_sub)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="JPEG", quality=90)


def extract_metadata(filepath: Path, covers_cache_dir: Path) -> TrackMetadata:
    """Extrae etiquetas ID3/Vorbis/MP4 y portada incrustada mediante ffprobe y ffmpeg."""
    filepath = filepath.resolve()
    track_id = compute_track_id(filepath)
    covers_cache_dir.mkdir(parents=True, exist_ok=True)
    cached_cover = covers_cache_dir / f"{track_id}.jpg"

    title = filepath.stem.replace("_", " ").strip()
    artist = "Artista desconocido"
    album = "Archivos locales"
    genre = ""
    year = ""
    duration = 0.0
    has_embedded_cover = False

    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(filepath),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        if res.returncode == 0 and res.stdout:
            info = json.loads(res.stdout)
            fmt = info.get("format", {})
            duration = float(fmt.get("duration", 0.0) or 0.0)

            # Recolectar tags de format y de streams (case-insensitive)
            raw_tags: dict[str, str] = {}
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video" or stream.get("disposition", {}).get("attached_pic") == 1:
                    has_embedded_cover = True
                for k, v in (stream.get("tags") or {}).items():
                    raw_tags[k.lower()] = str(v)

            for k, v in (fmt.get("tags") or {}).items():
                raw_tags[k.lower()] = str(v)

            title = raw_tags.get("title") or title
            artist = raw_tags.get("artist") or raw_tags.get("album_artist") or raw_tags.get("performer") or artist
            album = raw_tags.get("album") or album
            genre = raw_tags.get("genre") or ""
            year = (raw_tags.get("date") or raw_tags.get("year") or "")[:4]
    except Exception:
        pass

    # Extraer carátula incrustada si existe y aún no está en caché
    if not cached_cover.exists():
        extracted = False
        if has_embedded_cover:
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i", str(filepath),
                        "-an",
                        "-vcodec", "mjpeg",
                        "-vframes", "1",
                        str(cached_cover),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=6,
                )
                extracted = cached_cover.exists() and cached_cover.stat().st_size > 200
            except Exception:
                extracted = False

        # Buscar imagen en la misma carpeta (cover.jpg, folder.jpg, etc.)
        if not extracted:
            for candidate_name in ("cover.jpg", "cover.png", "folder.jpg", "album.jpg", f"{filepath.stem}.jpg"):
                candidate = filepath.parent / candidate_name
                if candidate.exists():
                    try:
                        with Image.open(candidate) as im:
                            im.convert("RGB").resize((400, 400)).save(cached_cover, format="JPEG", quality=90)
                        extracted = True
                        break
                    except Exception:
                        pass

        if not extracted:
            generate_fallback_cover(title, artist, cached_cover, track_id)

    dominant_color = compute_dominant_color(cached_cover)
    try:
        mtime = filepath.stat().st_mtime
    except Exception:
        mtime = 0.0

    return TrackMetadata(
        id=track_id,
        filepath=str(filepath),
        title=title,
        artist=artist,
        album=album,
        genre=genre,
        year=year,
        duration=duration,
        cover_path=str(cached_cover),
        dominant_color=dominant_color,
        added_at=mtime,
    )


def format_duration(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "0:00"
    total_sec = int(round(seconds))
    mins, secs = divmod(total_sec, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"
