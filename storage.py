#!/usr/bin/env python3
"""
Almacenamiento persistente de la biblioteca musical, playlists, favoritos,
cuentas de usuario multi-dispositivo, historial de reproducción y ecualizador.
"""

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from metadata import (
    TrackMetadata,
    SUPPORTED_EXTENSIONS,
    extract_metadata,
    generate_fallback_cover,
    compute_dominant_color,
)
from gdrive_sync import (
    GoogleDriveConfig,
    DEFAULT_ROOT_DRIVE_FOLDER_ID,
    normalize_track_key,
    canonical_drive_track_id,
)


@dataclass
class Playlist:
    id: str
    name: str
    description: str
    track_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Playlist":
        return cls(
            id=data["id"],
            name=data.get("name", "Mi Playlist"),
            description=data.get("description", ""),
            track_ids=list(data.get("track_ids", [])),
            created_at=float(data.get("created_at", time.time())),
        )


def extract_gd_id_from_filename(filename: str) -> str:
    """Si el archivo local proviene de Drive (gd_<fileId>_nombre.mp3), extrae su ID canónico gd_<fileId>."""
    if not filename.startswith("gd_"):
        return ""
    # Formato nuevo: gd_<fileId>__<nombre> o formato clásico: gd_<10_chars>_<nombre>
    if "__" in filename:
        prefix = filename.split("__", 1)[0]
        if len(prefix) > 5:
            return prefix
    m = re.match(r"^(gd_[A-Za-z0-9_-]{8,})_", filename)
    if m:
        return m.group(1)
    return ""


class LibraryStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.data_dir = base_dir / "data"
        self.covers_dir = self.data_dir / "covers_cache"
        self.gdrive_cache_dir = self.data_dir / "gdrive_cache"
        self.music_library_dir = base_dir / "music_library"
        self.db_path = self.data_dir / "library.json"
        self.accounts_db_path = self.data_dir / "accounts_db.json"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.gdrive_cache_dir.mkdir(parents=True, exist_ok=True)
        self.music_library_dir.mkdir(parents=True, exist_ok=True)

        self.tracks: dict[str, TrackMetadata] = {}
        self.liked_ids: list[str] = []
        self.playlists: dict[str, Playlist] = {}
        self.recent_ids: list[str] = []
        self.watched_folders: list[str] = []
        self.gdrive_config = GoogleDriveConfig()
        self.welcome_shown: bool = False
        self.google_user: dict = {}
        # Cuenta SpotiLinux vinculada a Drive (username, email, password_hash)
        self.user_account: dict = {}

        # Estado del reproductor y ecualizador
        self.volume: int = 80
        self.shuffle: bool = False
        self.repeat_mode: str = "off"  # "off" | "all" | "one"
        self.eq_enabled: bool = True
        self.eq_preset: str = "Plano"
        self.eq_gains: list[float] = [0.0, 0.0, 0.0, 0.0, 0.0]  # 60Hz, 230Hz, 910Hz, 3.6kHz, 14kHz
        self.visualizer_mode: str = "bars"  # "bars" | "wave" | "radial"

        self.load()

    def load(self) -> None:
        if not self.db_path.exists():
            self._init_default_folders()
            return

        try:
            raw = json.loads(self.db_path.read_text(encoding="utf-8"))
            for tid, tdata in raw.get("tracks", {}).items():
                fp = str(tdata.get("filepath", ""))
                if fp.startswith("gdrive://") or Path(fp).exists():
                    self.tracks[tid] = TrackMetadata.from_dict(tdata)

            self.liked_ids = [tid for tid in raw.get("liked_ids", []) if tid in self.tracks]
            for pid, pdata in raw.get("playlists", {}).items():
                pl = Playlist.from_dict(pdata)
                pl.track_ids = [tid for tid in pl.track_ids if tid in self.tracks]
                self.playlists[pid] = pl

            self.recent_ids = [tid for tid in raw.get("recent_ids", []) if tid in self.tracks][:30]
            self.watched_folders = list(raw.get("watched_folders", []))
            if str(self.music_library_dir.resolve()) not in self.watched_folders:
                self.watched_folders.append(str(self.music_library_dir.resolve()))
            self.gdrive_config = GoogleDriveConfig.from_dict(raw.get("gdrive_config", {}))
            if not self.gdrive_config.folder_id:
                self.gdrive_config.folder_id = DEFAULT_ROOT_DRIVE_FOLDER_ID
            self.welcome_shown = bool(raw.get("welcome_shown", False))
            self.google_user = dict(raw.get("google_user", {}))
            self.user_account = dict(raw.get("user_account", {}))
            self.volume = int(raw.get("volume", 80))
            self.shuffle = bool(raw.get("shuffle", False))
            self.repeat_mode = str(raw.get("repeat_mode", "off"))
            self.eq_enabled = bool(raw.get("eq_enabled", True))
            self.eq_preset = str(raw.get("eq_preset", "Plano"))
            gains = raw.get("eq_gains", [0.0, 0.0, 0.0, 0.0, 0.0])
            if len(gains) == 5:
                self.eq_gains = [float(g) for g in gains]
            self.visualizer_mode = str(raw.get("visualizer_mode", "bars"))
            self.deduplicate_library()
        except Exception:
            self._init_default_folders()

    def _init_default_folders(self) -> None:
        demo_dir = self.base_dir / "demo_tracks"
        candidates = [
            self.music_library_dir,
            demo_dir,
            Path.home() / "Music",
            Path.home() / "Música",
        ]
        for folder in candidates:
            if folder.exists() and str(folder.resolve()) not in self.watched_folders:
                self.watched_folders.append(str(folder.resolve()))

    def save(self) -> None:
        payload = {
            "tracks": {tid: t.to_dict() for tid, t in self.tracks.items()},
            "liked_ids": self.liked_ids,
            "playlists": {pid: pl.to_dict() for pid, pl in self.playlists.items()},
            "recent_ids": self.recent_ids[:30],
            "watched_folders": self.watched_folders,
            "gdrive_config": self.gdrive_config.to_dict(),
            "welcome_shown": self.welcome_shown,
            "google_user": self.google_user,
            "user_account": self.user_account,
            "volume": self.volume,
            "shuffle": self.shuffle,
            "repeat_mode": self.repeat_mode,
            "eq_enabled": self.eq_enabled,
            "eq_preset": self.eq_preset,
            "eq_gains": self.eq_gains,
            "visualizer_mode": self.visualizer_mode,
        }
        self.db_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def deduplicate_library(self) -> int:
        """
        Fusiona pistas duplicadas (por ejemplo, una pista online gdrive://<id> y su
        archivo descargado en music_library/) preservando las referencias en playlists y favoritos.
        """
        if not self.tracks:
            return 0

        by_norm_key: dict[str, str] = {}
        remap: dict[str, str] = {}

        # Primero priorizar pistas locales descargadas sobre stubs gdrive://
        sorted_items = sorted(
            self.tracks.items(),
            key=lambda kv: (kv[1].filepath.startswith("gdrive://"), kv[0]),
        )
        for tid, track in sorted_items:
            key = normalize_track_key(track.title) or normalize_track_key(Path(track.filepath).name)
            if not key:
                continue
            if key in by_norm_key:
                keep_id = by_norm_key[key]
                if keep_id != tid:
                    keep_track = self.tracks.get(keep_id)
                    # Si uno de los dos tiene ID canónico gd_..., conservar ese ID si ambos son de Drive
                    if tid.startswith("gd_") and keep_track and not keep_track.filepath.startswith("gdrive://"):
                        keep_track.id = tid
                        self.tracks[tid] = keep_track
                        del self.tracks[keep_id]
                        by_norm_key[key] = tid
                        remap[keep_id] = tid
                    else:
                        remap[tid] = keep_id
            else:
                by_norm_key[key] = tid

        if not remap:
            return 0

        for old_id, new_id in remap.items():
            if old_id in self.tracks and old_id != new_id:
                del self.tracks[old_id]

        # Actualizar playlists y liked_ids
        new_liked: list[str] = []
        for lid in self.liked_ids:
            mapped = remap.get(lid, lid)
            if mapped in self.tracks and mapped not in new_liked:
                new_liked.append(mapped)
        self.liked_ids = new_liked

        for pl in self.playlists.values():
            updated_ids: list[str] = []
            for tid in pl.track_ids:
                mapped = remap.get(tid, tid)
                if mapped in self.tracks and mapped not in updated_ids:
                    updated_ids.append(mapped)
            pl.track_ids = updated_ids

        return len(remap)

    def apply_account_record(self, acc_record: dict) -> tuple[int, int]:
        """
        Restaura las canciones de Drive y las playlists guardadas en una cuenta SpotiLinux
        para que se puedan usar en varios dispositivos con la misma cuenta.
        Devuelve (num_playlists_restauradas, num_canciones_drive_vinculadas).
        """
        if not acc_record:
            return (0, 0)

        drive_added = 0
        for dt in acc_record.get("drive_tracks", []):
            file_id = str(dt.get("file_id", "")).strip()
            tid = str(dt.get("id", "")).strip() or (canonical_drive_track_id(file_id) if file_id else "")
            if not tid:
                continue
            if tid not in self.tracks:
                title = str(dt.get("title", "Canción de Drive"))
                artist = str(dt.get("artist", "Google Drive Cloud"))
                album = str(dt.get("album", "Google Drive (Online)"))
                genre = str(dt.get("genre", "Cloud Music"))
                duration = float(dt.get("duration", 180.0))
                cover_path = self.covers_dir / f"cover_{tid[:16]}.png"
                if not cover_path.exists():
                    generate_fallback_cover(title, artist, cover_path, tid)
                dom_rgb = compute_dominant_color(cover_path)
                self.tracks[tid] = TrackMetadata(
                    id=tid,
                    filepath=f"gdrive://{file_id}" if file_id else str(dt.get("filepath", "")),
                    title=title,
                    artist=artist,
                    album=album,
                    genre=genre,
                    year="Cloud",
                    duration=duration,
                    cover_path=str(cover_path),
                    dominant_color=dom_rgb,
                    added_at=time.time(),
                )
                drive_added += 1

        for lid in acc_record.get("liked_ids", []):
            if lid in self.tracks and lid not in self.liked_ids:
                self.liked_ids.append(lid)

        pl_restored = 0
        cloud_playlists = acc_record.get("playlists", {})
        if isinstance(cloud_playlists, dict):
            iterable = cloud_playlists.items()
        elif isinstance(cloud_playlists, list):
            iterable = [(p.get("id", f"pl_{i}"), p) for i, p in enumerate(cloud_playlists) if isinstance(p, dict)]
        else:
            iterable = []

        for pid, pdata in iterable:
            if not isinstance(pdata, dict):
                continue
            valid_tids = [t for t in pdata.get("track_ids", []) if t in self.tracks]
            if pid in self.playlists:
                existing = self.playlists[pid]
                existing.name = pdata.get("name", existing.name)
                existing.description = pdata.get("description", existing.description)
                for t in valid_tids:
                    if t not in existing.track_ids:
                        existing.track_ids.append(t)
            else:
                self.playlists[pid] = Playlist(
                    id=pid,
                    name=pdata.get("name", "Playlist Sincronizada"),
                    description=pdata.get("description", "Sincronizada desde tu cuenta SpotiLinux"),
                    track_ids=valid_tids,
                    created_at=float(pdata.get("created_at", time.time())),
                )
            pl_restored += 1

        self.deduplicate_library()
        self.save()
        return (pl_restored, drive_added)

    def scan_folder(
        self,
        folder_path: Path,
        max_files: int = 500,
        copy_to_app_folder: bool = False,
    ) -> list[TrackMetadata]:
        """Escanea recursivamente una carpeta buscando archivos de audio compatibles y opcionalmente los guarda en music_library/."""
        folder_path = folder_path.resolve()
        if not folder_path.exists():
            return []

        if str(folder_path) not in self.watched_folders:
            self.watched_folders.append(str(folder_path))

        added: list[TrackMetadata] = []
        count = 0
        for root, _dirs, files in os.walk(folder_path):
            # Evitar escanear la carpeta de caché o venv o android-sdk
            if any(skip in root for skip in ("venv", "covers_cache", ".git", ".android-sdk", "eq_cache")):
                continue
            for fname in sorted(files):
                ext = Path(fname).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    fpath = Path(root) / fname
                    track = self.import_file(fpath, save_immediately=False, copy_to_app_folder=copy_to_app_folder)
                    if track:
                        added.append(track)
                    count += 1
                    if count >= max_files:
                        break
            if count >= max_files:
                break

        self.deduplicate_library()
        self._ensure_starter_playlists()
        self.save()
        return added

    def auto_scan_device_to_app_folder(self) -> list[TrackMetadata]:
        """Rastrea automáticamente las carpetas comunes del sistema (Music, Música, Downloads) y guarda las pistas en music_library/."""
        search_dirs = [
            Path.home() / "Music",
            Path.home() / "Música",
            Path.home() / "Downloads",
            Path.home() / "Descargas",
        ]
        discovered: list[TrackMetadata] = []
        for d in search_dirs:
            if d.exists():
                discovered.extend(self.scan_folder(d, max_files=250, copy_to_app_folder=True))
        return discovered

    def import_file(
        self,
        filepath: Path,
        save_immediately: bool = True,
        copy_to_app_folder: bool = False,
        forced_id: str = "",
    ) -> Optional[TrackMetadata]:
        filepath = filepath.resolve()
        if not filepath.exists() or filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None

        if copy_to_app_folder and filepath.parent != self.music_library_dir.resolve() and "demo_tracks" not in str(filepath):
            try:
                self.music_library_dir.mkdir(parents=True, exist_ok=True)
                dest = self.music_library_dir / filepath.name
                if not dest.exists():
                    shutil.copy2(filepath, dest)
                filepath = dest.resolve()
            except Exception:
                pass

        # Si ya existe en la biblioteca y su portada existe, devolver rápido
        for existing in self.tracks.values():
            if existing.filepath == str(filepath) and Path(existing.cover_path).exists():
                return existing

        meta = extract_metadata(filepath, self.covers_dir)
        gd_canonical = forced_id or extract_gd_id_from_filename(filepath.name)
        if gd_canonical:
            meta.id = gd_canonical

        # Verificación de duplicados por título normalizado si ya existe como gdrive://
        norm_title = normalize_track_key(meta.title)
        for existing_id, existing_track in list(self.tracks.items()):
            if existing_id != meta.id and normalize_track_key(existing_track.title) == norm_title and norm_title:
                if existing_track.filepath.startswith("gdrive://"):
                    meta.id = existing_id
                    break

        self.tracks[meta.id] = meta
        if save_immediately:
            self.save()
        return meta

    def _ensure_starter_playlists(self) -> None:
        """Crea playlists iniciales de muestra si el usuario aún no tiene ninguna."""
        if self.playlists or not self.tracks:
            return

        all_tracks = list(self.tracks.values())
        synth_ids = [t.id for t in all_tracks if any(k in t.genre.lower() for k in ("synth", "electro", "house"))]
        chill_ids = [t.id for t in all_tracks if any(k in t.genre.lower() for k in ("lo-fi", "ambient", "chill", "indie"))]

        if synth_ids:
            p1 = Playlist(
                id="pl_night_drive",
                name="Night Drive & Synthwave",
                description="Sintetizadores retro, bajos profundos y energía nocturna.",
                track_ids=synth_ids,
            )
            self.playlists[p1.id] = p1

        if chill_ids:
            p2 = Playlist(
                id="pl_focus_chill",
                name="Focus & Late Night Coding",
                description="Beats relajantes y atmósferas envolventes para programar en Linux.",
                track_ids=chill_ids,
            )
            self.playlists[p2.id] = p2

        # Marcar las 2 primeras pistas como favoritas por defecto en el primer arranque
        if not self.liked_ids and len(all_tracks) >= 2:
            self.liked_ids = [all_tracks[0].id, all_tracks[1].id]

    def toggle_like(self, track_id: str) -> bool:
        if track_id in self.liked_ids:
            self.liked_ids.remove(track_id)
            liked = False
        else:
            self.liked_ids.insert(0, track_id)
            liked = True
        self.save()
        return liked

    def is_liked(self, track_id: str) -> bool:
        return track_id in self.liked_ids

    def create_playlist(self, name: str, description: str = "") -> Playlist:
        pid = f"pl_{uuid.uuid4().hex[:8]}"
        pl = Playlist(id=pid, name=name.strip() or "Nueva Playlist", description=description.strip())
        self.playlists[pid] = pl
        self.save()
        return pl

    def rename_playlist(self, playlist_id: str, new_name: str, new_desc: str = "") -> None:
        if playlist_id in self.playlists:
            self.playlists[playlist_id].name = new_name.strip() or self.playlists[playlist_id].name
            self.playlists[playlist_id].description = new_desc.strip()
            self.save()

    def delete_playlist(self, playlist_id: str) -> None:
        if playlist_id in self.playlists:
            del self.playlists[playlist_id]
            self.save()

    def add_track_to_playlist(self, playlist_id: str, track_id: str) -> bool:
        pl = self.playlists.get(playlist_id)
        if not pl or track_id not in self.tracks:
            return False
        if track_id not in pl.track_ids:
            pl.track_ids.append(track_id)
            self.save()
            return True
        return False

    def add_multiple_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        pl = self.playlists.get(playlist_id)
        if not pl:
            return 0
        added_count = 0
        for tid in track_ids:
            if tid in self.tracks and tid not in pl.track_ids:
                pl.track_ids.append(tid)
                added_count += 1
        if added_count > 0:
            self.save()
        return added_count

    def remove_track_from_playlist(self, playlist_id: str, track_id: str) -> None:
        pl = self.playlists.get(playlist_id)
        if pl and track_id in pl.track_ids:
            pl.track_ids.remove(track_id)
            self.save()

    def record_play(self, track_id: str) -> None:
        if track_id in self.recent_ids:
            self.recent_ids.remove(track_id)
        self.recent_ids.insert(0, track_id)
        self.recent_ids = self.recent_ids[:30]
        self.save()
