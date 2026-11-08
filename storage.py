#!/usr/bin/env python3
"""
Almacenamiento persistente de la biblioteca musical, playlists, favoritos,
cuentas de usuario multi-dispositivo, historial de reproducción y ecualizador.
"""

import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
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
    cover_url: str = ""
    cover_drive_id: str = ""
    cover_path: str = ""
    is_cloud: bool = False

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
            cover_url=data.get("cover_url", data.get("coverUrl", "")),
            cover_drive_id=data.get("cover_drive_id", data.get("coverDriveId", "")),
            cover_path=data.get("cover_path", data.get("coverPath", "")),
            is_cloud=bool(data.get("is_cloud", data.get("isCloud", False))),
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
        self.sqlite_path = self.data_dir / "library.sqlite"
        self.accounts_db_path = self.data_dir / "accounts_db.json"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.gdrive_cache_dir.mkdir(parents=True, exist_ok=True)
        self.music_library_dir.mkdir(parents=True, exist_ok=True)

        self.tracks: dict[str, TrackMetadata] = {}
        self.liked_ids: list[str] = []
        self.playlists: dict[str, Playlist] = {}
        self.deleted_gdrive_ids: list[str] = []
        self.recent_ids: list[str] = []
        self.watched_folders: list[str] = []
        self.gdrive_config = GoogleDriveConfig()
        self.welcome_shown: bool = False
        self.google_user: dict = {}
        # Cuenta SpotiLinux vinculada a Drive (username, email, password_hash)
        self.user_account: dict = {}
        self.hidden_track_ids: list[str] = []

        # Estado del reproductor y ecualizador
        self.volume: int = 80
        self.shuffle: bool = False
        self.repeat_mode: str = "off"  # "off" | "all" | "one"
        self.eq_enabled: bool = True
        self.eq_preset: str = "Plano"
        self.eq_gains: list[float] = [0.0, 0.0, 0.0, 0.0, 0.0]  # 60Hz, 230Hz, 910Hz, 3.6kHz, 14kHz
        self.visualizer_mode: str = "bars"  # "bars" | "wave" | "radial"

        self._init_sqlite()
        self.load()

    def _init_sqlite(self) -> None:
        """Inicializa el esquema SQLite para almacenamiento ACID indexado."""
        try:
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tracks (
                        id TEXT PRIMARY KEY,
                        filepath TEXT NOT NULL,
                        title TEXT,
                        artist TEXT,
                        album TEXT,
                        genre TEXT,
                        year TEXT,
                        duration REAL,
                        cover_path TEXT,
                        dominant_color TEXT,
                        is_liked INTEGER DEFAULT 0,
                        updated_at REAL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS playlists (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        track_ids TEXT,
                        created_at REAL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS kv_state (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
        except Exception:
            pass

    def _sync_to_sqlite(self) -> None:
        """Sincroniza el estado actual en memoria hacia SQLite de forma segura."""
        try:
            with sqlite3.connect(self.sqlite_path) as conn:
                # Actualizar pistas
                now = time.time()
                track_rows = [
                    (
                        t.id,
                        t.filepath,
                        t.title,
                        t.artist,
                        t.album,
                        t.genre,
                        t.year,
                        float(t.duration),
                        t.cover_path,
                        json.dumps(t.dominant_color),
                        1 if t.id in self.liked_ids else 0,
                        now,
                    )
                    for t in self.tracks.values()
                ]
                conn.execute("DELETE FROM tracks")
                conn.executemany("""
                    INSERT OR REPLACE INTO tracks (
                        id, filepath, title, artist, album, genre, year,
                        duration, cover_path, dominant_color, is_liked, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, track_rows)

                # Actualizar playlists
                pl_rows = [
                    (
                        pl.id,
                        pl.name,
                        pl.description,
                        json.dumps(pl.track_ids),
                        pl.created_at,
                    )
                    for pl in self.playlists.values()
                ]
                conn.execute("DELETE FROM playlists")
                conn.executemany("""
                    INSERT OR REPLACE INTO playlists (id, name, description, track_ids, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, pl_rows)

                # Guardar configuraciones y listas de IDs
                state_items = [
                    ("liked_ids", json.dumps(self.liked_ids)),
                    ("recent_ids", json.dumps(self.recent_ids[:30])),
                    ("watched_folders", json.dumps(self.watched_folders)),
                    ("gdrive_config", json.dumps(self.gdrive_config.to_dict())),
                    ("user_account", json.dumps(self.user_account)),
                    ("hidden_track_ids", json.dumps(self.hidden_track_ids)),
                    ("player_state", json.dumps({
                        "volume": self.volume,
                        "shuffle": self.shuffle,
                        "repeat_mode": self.repeat_mode,
                        "eq_enabled": self.eq_enabled,
                        "eq_preset": self.eq_preset,
                        "eq_gains": self.eq_gains,
                        "visualizer_mode": self.visualizer_mode,
                    })),
                ]
                conn.executemany("INSERT OR REPLACE INTO kv_state (key, value) VALUES (?, ?)", state_items)
        except Exception:
            pass

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
            self.deleted_gdrive_ids = list(raw.get("deleted_gdrive_ids", []))
            self.watched_folders = list(raw.get("watched_folders", []))
            if str(self.music_library_dir.resolve()) not in self.watched_folders:
                self.watched_folders.append(str(self.music_library_dir.resolve()))
            self.gdrive_config = GoogleDriveConfig.from_dict(raw.get("gdrive_config", {}))
            if not self.gdrive_config.folder_id:
                self.gdrive_config.folder_id = DEFAULT_ROOT_DRIVE_FOLDER_ID
            self.welcome_shown = bool(raw.get("welcome_shown", False))
            self.google_user = dict(raw.get("google_user", {}))
            self.user_account = dict(raw.get("user_account", {}))
            self.hidden_track_ids = list(raw.get("hidden_track_ids", []))
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
            "deleted_gdrive_ids": self.deleted_gdrive_ids,
            "recent_ids": self.recent_ids[:30],
            "watched_folders": self.watched_folders,
            "gdrive_config": self.gdrive_config.to_dict(),
            "welcome_shown": self.welcome_shown,
            "google_user": self.google_user,
            "user_account": self.user_account,
            "hidden_track_ids": self.hidden_track_ids,
            "volume": self.volume,
            "shuffle": self.shuffle,
            "repeat_mode": self.repeat_mode,
            "eq_enabled": self.eq_enabled,
            "eq_preset": self.eq_preset,
            "eq_gains": self.eq_gains,
            "visualizer_mode": self.visualizer_mode,
        }
        self.db_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self._sync_to_sqlite()

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

        for hid in acc_record.get("hidden_track_ids", []):
            if hid not in self.hidden_track_ids:
                self.hidden_track_ids.append(hid)

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

    def hide_track(self, track_id: str) -> None:
        """Oculta una canción para que no se muestre en la cuenta."""
        if track_id not in self.hidden_track_ids:
            self.hidden_track_ids.append(track_id)
        if self.user_account:
            hidden = self.user_account.setdefault("hidden_track_ids", [])
            if track_id not in hidden:
                hidden.append(track_id)
        self.save()

    def unhide_track(self, track_id: str) -> None:
        """Restaura una canción previamente oculta."""
        if track_id in self.hidden_track_ids:
            self.hidden_track_ids.remove(track_id)
        if self.user_account and "hidden_track_ids" in self.user_account:
            if track_id in self.user_account["hidden_track_ids"]:
                self.user_account["hidden_track_ids"].remove(track_id)
        self.save()

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

    def get_or_create_playlist_by_name(
        self,
        name: str,
        description: str = "",
        is_cloud: bool = False,
    ) -> Playlist:
        clean_name = name.strip() or "Nueva Playlist"
        low = clean_name.lower()
        for pl in self.playlists.values():
            if pl.name.strip().lower() == low:
                if is_cloud and not pl.is_cloud:
                    pl.is_cloud = True
                    self.save()
                return pl
        pl = self.create_playlist(
            clean_name,
            description or ("Playlist en la Nube (importada desde .txt)" if is_cloud else "Playlist creada en SpotiLinux"),
        )
        pl.is_cloud = is_cloud
        from gdrive_sync import get_random_drive_cover_path
        used_covers = {p.cover_path for p in self.playlists.values() if getattr(p, "cover_path", "") and p.id != pl.id}
        rnd_cover = get_random_drive_cover_path(self.covers_dir, exclude_paths=used_covers)
        if rnd_cover:
            pl.cover_path = rnd_cover
            pl.cover_drive_id = Path(rnd_cover).stem
            pl.cover_url = f"https://lh3.googleusercontent.com/d/{pl.cover_drive_id}=w600"
        self.save()
        return pl

    def populate_cloud_playlist_from_queries(
        self,
        playlist_name: str,
        queries: list[str],
        description: str = "",
    ) -> tuple[Playlist, int, int]:
        """
        Crea o actualiza una playlist de la NUBE a partir de una lista de nombres de canciones.
        Agrega las canciones de inmediato: si ya existen localmente las vincula; si no,
        crea pistas en la nube 'cloud://pending' para que sean visibles inmediatamente
        en la sección de Playlists de la Nube con su opción de descarga.
        Retorna (playlist, vinculadas_existentes, pendientes_en_nube).
        """
        pl = self.get_or_create_playlist_by_name(
            playlist_name,
            description=description or "Playlist importada desde lista .txt (en la Nube)",
            is_cloud=True,
        )
        linked_local = 0
        pending_cloud = 0

        for raw_q in queries:
            q = raw_q.strip()
            if not q or q.startswith("#"):
                continue

            matched = self.find_track_by_query_or_filename(query=q, prefer_real=True)
            if matched and not matched.filepath.startswith("cloud://pending"):
                # Si existía un placeholder previo para esta query, reemplazarlo por la pista real
                for tid in list(pl.track_ids):
                    old_t = self.tracks.get(tid)
                    if old_t and old_t.filepath.startswith("cloud://pending") and (
                        normalize_track_key(old_t.title) == normalize_track_key(matched.title)
                        or normalize_track_key(old_t.title) == normalize_track_key(q)
                    ):
                        self.reconcile_playlist_track_ids(tid, matched.id)
                        if tid in self.tracks:
                            del self.tracks[tid]
                if matched.id not in pl.track_ids:
                    pl.track_ids.append(matched.id)
                linked_local += 1
            else:
                tid = f"cloud_txt_{hashlib.md5(q.encode('utf-8')).hexdigest()[:12]}"
                if tid not in self.tracks:
                    track = TrackMetadata(
                        id=tid,
                        filepath=f"cloud://pending/{tid}",
                        title=q,
                        artist="En la Nube (Pendiente de descarga)",
                        album=pl.name,
                        genre="Cloud",
                        year="2026",
                        duration=0.0,
                        cover_path=getattr(pl, "cover_path", "") or str(self.covers_dir / "cloud_default.jpg"),
                        dominant_color=(30, 215, 96),
                    )
                    self.tracks[tid] = track
                if tid not in pl.track_ids:
                    pl.track_ids.append(tid)
                pending_cloud += 1

        self.save()
        return pl, linked_local, pending_cloud

    def find_track_by_query_or_filename(
        self, audio_filename: str = "", title: str = "", query: str = "", prefer_real: bool = True
    ) -> Optional[TrackMetadata]:
        if audio_filename:
            target_name = Path(audio_filename).name.lower()
            for t in self.tracks.values():
                if Path(t.filepath).name.lower() == target_name:
                    return t

        candidates = []
        norm_title = normalize_track_key(title or query)
        if norm_title:
            for t in self.tracks.values():
                if normalize_track_key(t.title) == norm_title:
                    candidates.append(t)

        if not candidates and query:
            q_low = query.strip().lower()
            for t in self.tracks.values():
                if q_low in t.title.lower() or t.title.lower() in q_low:
                    candidates.append(t)

        if candidates:
            if prefer_real:
                real = [c for c in candidates if not c.filepath.startswith("cloud://pending")]
                if real:
                    return real[0]
            return candidates[0]
        return None

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
        if not pl:
            return
        track = self.tracks.get(track_id)
        target_ids = {track_id}
        if track:
            if track.id: target_ids.add(track.id)
            if hasattr(track, "gdrive_id") and track.gdrive_id:
                target_ids.add(track.gdrive_id)
                target_ids.add(f"gd_{track.gdrive_id}")
            fn = Path(track.filepath).name
            gd_from_fn = extract_gd_id_from_filename(fn)
            if gd_from_fn:
                target_ids.add(gd_from_fn)
        pl.track_ids = [tid for tid in pl.track_ids if tid not in target_ids]
        self.save()

    def get_track_playlists(self, track: TrackMetadata) -> list[str]:
        """Devuelve los nombres de las playlists a las que pertenece una pista."""
        if not track:
            return []
        norm_key = normalize_track_key(track.title) or normalize_track_key(Path(track.filepath).name)
        target_ids = {track.id}
        if hasattr(track, "gdrive_id") and track.gdrive_id:
            target_ids.add(track.gdrive_id)
            target_ids.add(f"gd_{track.gdrive_id}")
        fn = Path(track.filepath).name
        gd_from_fn = extract_gd_id_from_filename(fn)
        if gd_from_fn:
            target_ids.add(gd_from_fn)

        result: list[str] = []
        for pl in self.playlists.values():
            matched = False
            for tid in pl.track_ids:
                if tid in target_ids:
                    matched = True
                    break
                pl_track = self.tracks.get(tid)
                if pl_track:
                    pl_key = normalize_track_key(pl_track.title) or normalize_track_key(Path(pl_track.filepath).name)
                    if norm_key and pl_key and norm_key == pl_key:
                        matched = True
                        break
            if matched and pl.name not in result:
                result.append(pl.name)
        return result

    def reconcile_playlist_track_ids(self, old_id: str, new_id: str) -> int:
        """Actualiza referencias de old_id a new_id en playlists y favoritos sin duplicados."""
        if not old_id or not new_id or old_id == new_id:
            return 0
        changed = 0
        for pl in self.playlists.values():
            new_list: list[str] = []
            for tid in pl.track_ids:
                mapped = new_id if tid == old_id else tid
                if mapped not in new_list:
                    new_list.append(mapped)
            if new_list != pl.track_ids:
                pl.track_ids = new_list
                changed += 1

        new_liked: list[str] = []
        for lid in self.liked_ids:
            mapped = new_id if lid == old_id else lid
            if mapped not in new_liked:
                new_liked.append(mapped)
        self.liked_ids = new_liked
        if changed > 0:
            self.save()
        return changed

    def delete_track_from_app(self, track_id: str, delete_local_file: bool = True) -> bool:
        """
        Elimina completamente una canción de la aplicación: catálogo tracks,
        playlists, favoritos e historial, y opcionalmente borra el archivo físico.
        Registra el ID en deleted_gdrive_ids para evitar re-descargas automáticas de Drive.
        """
        track = self.tracks.get(track_id)
        if not track:
            return False

        # Guardar identificador para evitar re-sincronizaciones no deseadas
        fn = Path(track.filepath).name
        gd_id = extract_gd_id_from_filename(fn)
        if not gd_id and track.filepath.startswith("gdrive://"):
            gd_id = track.filepath.replace("gdrive://", "").split("?")[0]
        if gd_id and gd_id not in self.deleted_gdrive_ids:
            self.deleted_gdrive_ids.append(gd_id)
        if track_id.startswith("gd_") and track_id not in self.deleted_gdrive_ids:
            self.deleted_gdrive_ids.append(track_id)

        # Borrar archivo físico si se solicitó y existe localmente
        if delete_local_file and not track.filepath.startswith("gdrive://"):
            try:
                local_path = Path(track.filepath)
                if local_path.exists():
                    local_path.unlink(missing_ok=True)
                # Borrar portada asociada si está en covers_cache
                if track.cover_path and "covers_cache" in track.cover_path:
                    cov_p = Path(track.cover_path)
                    if cov_p.exists():
                        cov_p.unlink(missing_ok=True)
            except Exception:
                pass

        # Remover de catálogo, playlists, favoritos e historial
        if track_id in self.tracks:
            del self.tracks[track_id]
        self.liked_ids = [lid for lid in self.liked_ids if lid != track_id]
        self.recent_ids = [rid for rid in self.recent_ids if rid != track_id]
        for pl in self.playlists.values():
            pl.track_ids = [tid for tid in pl.track_ids if tid != track_id]

        self.save()
        return True

    def record_play(self, track_id: str) -> None:
        if track_id in self.recent_ids:
            self.recent_ids.remove(track_id)
        self.recent_ids.insert(0, track_id)
        self.recent_ids = self.recent_ids[:30]
        self.save()
