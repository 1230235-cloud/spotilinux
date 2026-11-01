#!/usr/bin/env python3
"""
Módulo de servicio desacoplado para operaciones con Google Drive (MaoMusic):
- Streaming al vuelo y descargas individuales sin bloquear la UI
- Descargas por lotes de playlists completas en segundo plano con reporte de progreso
- Sincronización de biblioteca y escaneo de carpetas de Drive
- Subida de archivos locales a la nube
- Sincronización segura de base de datos de cuentas y playlists
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from gdrive_sync import (
    GoogleDriveConfig,
    GDriveSyncWorker,
    GDriveStreamDownloadWorker,
    GDriveUploadWorker,
    GDriveCloudPlaylistSyncWorker,
    download_drive_file_to_cache,
    find_existing_local_duplicate,
    canonical_drive_track_id,
)
from metadata import TrackMetadata, extract_metadata

logger = logging.getLogger("maomusic.cloud")


class BatchPlaylistDownloader(QThread):
    """Descarga una lista de canciones de Google Drive de forma no bloqueante."""

    progress = pyqtSignal(int, int, str)               # (actual, total, titulo_cancion)
    track_ready = pyqtSignal(str, object, bool)        # (old_id, updated_track, was_duplicate)
    batch_finished = pyqtSignal(int, int, int)         # (descargadas, duplicadas, errores)

    def __init__(
        self,
        tracks: list[TrackMetadata],
        config: GoogleDriveConfig,
        cache_dir: Path,
        covers_dir: Path,
        existing_tracks: Optional[dict[str, TrackMetadata]] = None,
        extra_search_dirs: Optional[list[Path]] = None,
    ) -> None:
        super().__init__()
        self.tracks = list(tracks)
        self.config = config
        self.cache_dir = cache_dir
        self.covers_dir = covers_dir
        self.existing_tracks = dict(existing_tracks or {})
        self.extra_search_dirs = list(extra_search_dirs or [])
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        downloaded = 0
        duplicates = 0
        errors = 0
        total = len(self.tracks)

        search_dirs = [self.cache_dir] + self.extra_search_dirs

        for idx, track in enumerate(self.tracks, start=1):
            if self._is_cancelled or self.isInterruptionRequested():
                break

            self.progress.emit(idx, total, track.title)

            if track.filepath.startswith("gdrive://"):
                raw = track.filepath[len("gdrive://") :]
                parts = raw.split("/", 1)
                file_id = parts[0]
                fname = parts[1] if len(parts) > 1 else f"{file_id}.mp3"
            else:
                file_id = track.id.replace("gd_", "")
                fname = Path(track.filepath).name

            tid = canonical_drive_track_id(file_id)

            # 1. Comprobar duplicado existente en local
            if getattr(self.config, "check_duplicates", True):
                dup = find_existing_local_duplicate(file_id, fname, self.existing_tracks, search_dirs)
                if dup is not None and dup.exists() and dup.stat().st_size > 1024:
                    meta = extract_metadata(dup, self.covers_dir)
                    meta.id = track.id or tid
                    if meta.album == "Archivos locales":
                        meta.album = "Carpeta Musica • Google Drive ☁️"
                    duplicates += 1
                    self.track_ready.emit(track.id, meta, True)
                    continue

            # 2. Descargar archivo
            try:
                meta = download_drive_file_to_cache(
                    file_id=file_id,
                    filename_hint=fname,
                    config=self.config,
                    cache_dir=self.cache_dir,
                    covers_dir=self.covers_dir,
                    preserve_id=track.id or tid,
                    existing_tracks=self.existing_tracks,
                    extra_search_dirs=self.extra_search_dirs,
                )
                if meta:
                    downloaded += 1
                    self.track_ready.emit(track.id, meta, False)
                else:
                    errors += 1
            except Exception as exc:
                logger.warning(f"Error descargando pista {track.title}: {exc}")
                errors += 1

        self.batch_finished.emit(downloaded, duplicates, errors)


class CloudSyncManager(QObject):
    """
    Gestor de servicios en la nube para MaoMusic.
    Aísla las operaciones de Google Drive (streaming, descargas por lotes,
    subidas y sincronización de base de datos) del hilo de la interfaz de usuario.
    """

    # Señales públicas para la UI
    stream_progress = pyqtSignal(str)
    stream_ready = pyqtSignal(str, object, bool)      # old_id, updated_track, was_duplicate
    stream_failed = pyqtSignal(str, str)              # track_id, error_message

    sync_progress = pyqtSignal(str)
    sync_finished = pyqtSignal(list, str)             # tracks, status_message

    batch_progress = pyqtSignal(int, int, str)        # current, total, track_title
    batch_track_saved = pyqtSignal(str, object, bool) # old_id, updated_track, was_duplicate
    batch_finished = pyqtSignal(int, int, int)        # downloaded, duplicates, errors

    upload_progress = pyqtSignal(str)
    upload_finished = pyqtSignal(list, list)          # uploaded_files, error_files

    cloud_db_sync_completed = pyqtSignal(dict)        # merged_db
    cloud_db_sync_failed = pyqtSignal(str)            # error_message

    def __init__(
        self,
        config: GoogleDriveConfig,
        music_library_dir: Path,
        gdrive_cache_dir: Path,
        covers_dir: Path,
        accounts_db_path: Path,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.music_library_dir = music_library_dir
        self.gdrive_cache_dir = gdrive_cache_dir
        self.covers_dir = covers_dir
        self.accounts_db_path = accounts_db_path

        self._stream_worker: Optional[GDriveStreamDownloadWorker] = None
        self._batch_worker: Optional[BatchPlaylistDownloader] = None
        self._sync_worker: Optional[GDriveSyncWorker] = None
        self._upload_worker: Optional[GDriveUploadWorker] = None
        self._cloud_db_worker: Optional[GDriveCloudPlaylistSyncWorker] = None

        self._retired_workers: list[QThread] = []

    def _retire(self, attr_name: str) -> None:
        worker: Optional[QThread] = getattr(self, attr_name, None)
        if worker is None:
            return
        setattr(self, attr_name, None)
        try:
            if worker.isRunning():
                worker.requestInterruption()
                worker.quit()
                if not worker.wait(1200):
                    self._retired_workers.append(worker)
                    worker.finished.connect(
                        lambda w=worker: self._retired_workers.remove(w)
                        if w in self._retired_workers
                        else None
                    )
        except RuntimeError:
            pass

    def stream_track(
        self,
        track: TrackMetadata,
        existing_tracks: Optional[dict[str, TrackMetadata]] = None,
    ) -> None:
        """Inicia el streaming / descarga al vuelo de una pista de Google Drive."""
        self.stream_progress.emit(f"☁️ Transmitiendo: {track.title}...")
        self._retire("_stream_worker")
        self._stream_worker = GDriveStreamDownloadWorker(
            track=track,
            config=self.config,
            cache_dir=self.gdrive_cache_dir,
            covers_dir=self.covers_dir,
            existing_tracks=existing_tracks,
            extra_search_dirs=[self.music_library_dir, self.gdrive_cache_dir],
            verify_duplicates=getattr(self.config, "verify_duplicates", True),
        )
        self._stream_worker.stream_ready.connect(self.stream_ready)
        self._stream_worker.stream_failed.connect(self.stream_failed)
        self._stream_worker.start()

    def download_track_to_library(
        self,
        track: TrackMetadata,
        existing_tracks: Optional[dict[str, TrackMetadata]] = None,
    ) -> None:
        """Descarga explícitamente una pista a music_library/ en segundo plano."""
        self.stream_progress.emit(f"⬇️ Guardando en biblioteca: {track.title}...")
        self._retire("_stream_worker")
        self._stream_worker = GDriveStreamDownloadWorker(
            track=track,
            config=self.config,
            cache_dir=self.music_library_dir,
            covers_dir=self.covers_dir,
            existing_tracks=existing_tracks,
            extra_search_dirs=[self.music_library_dir, self.gdrive_cache_dir],
            verify_duplicates=getattr(self.config, "verify_duplicates", True),
        )
        self._stream_worker.stream_ready.connect(self.stream_ready)
        self._stream_worker.stream_failed.connect(self.stream_failed)
        self._stream_worker.start()

    def download_playlist_batch(
        self,
        tracks: list[TrackMetadata],
        existing_tracks: Optional[dict[str, TrackMetadata]] = None,
    ) -> None:
        """Descarga una lista de canciones en lote sin bloquear la interfaz."""
        self._retire("_batch_worker")
        self._batch_worker = BatchPlaylistDownloader(
            tracks=tracks,
            config=self.config,
            cache_dir=self.music_library_dir,
            covers_dir=self.covers_dir,
            existing_tracks=existing_tracks,
            extra_search_dirs=[self.music_library_dir, self.gdrive_cache_dir],
        )
        self._batch_worker.progress.connect(self.batch_progress)
        self._batch_worker.track_ready.connect(self.batch_track_saved)
        self._batch_worker.batch_finished.connect(self.batch_finished)
        self._batch_worker.start()

    def sync_library(
        self,
        download_immediately: bool = False,
        direct_links: bool = True,
        extra_search_dirs: Optional[list[Path]] = None,
    ) -> None:
        """Sincroniza la lista de canciones desde la carpeta de Google Drive."""
        target_cache = self.music_library_dir if download_immediately else self.gdrive_cache_dir
        dirs = list(extra_search_dirs or [self.music_library_dir, self.gdrive_cache_dir])
        self._retire("_sync_worker")
        self._sync_worker = GDriveSyncWorker(
            config=self.config,
            cache_dir=target_cache,
            covers_dir=self.covers_dir,
            direct_links=direct_links,
            download_immediately=download_immediately,
            extra_search_dirs=dirs,
        )
        self._sync_worker.sync_progress.connect(self.sync_progress)
        self._sync_worker.sync_finished.connect(self.sync_finished)
        self._sync_worker.start()

    def upload_files(self, file_paths: list[Path]) -> None:
        """Sube archivos locales a la carpeta de Google Drive en segundo plano."""
        self._retire("_upload_worker")
        self._upload_worker = GDriveUploadWorker(
            filepaths=file_paths,
            config=self.config,
            covers_dir=self.covers_dir,
        )
        self._upload_worker.upload_progress.connect(self.upload_progress)
        self._upload_worker.upload_finished.connect(self.upload_finished)
        self._upload_worker.start()

    def sync_cloud_db(self, payload: dict) -> None:
        """Sincroniza la base de datos de usuarios y playlists con Google Drive."""
        self._retire("_cloud_db_worker")
        self._cloud_db_worker = GDriveCloudPlaylistSyncWorker(
            access_token=self.config.access_token,
            local_payload=payload,
            config=self.config,
            local_db_path=self.accounts_db_path,
        )
        self._cloud_db_worker.sync_completed.connect(self.cloud_db_sync_completed)
        self._cloud_db_worker.sync_failed.connect(self.cloud_db_sync_failed)
        self._cloud_db_worker.start()

    def shutdown(self) -> None:
        """Detiene de forma segura todos los workers activos."""
        names = ("_stream_worker", "_batch_worker", "_sync_worker", "_upload_worker", "_cloud_db_worker")
        threads = [getattr(self, n, None) for n in names] + list(self._retired_workers)
        for th in threads:
            try:
                if th is not None and th.isRunning():
                    th.requestInterruption()
                    th.quit()
                    th.wait(1500)
            except RuntimeError:
                pass
