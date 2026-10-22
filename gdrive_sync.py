#!/usr/bin/env python3
"""
Conector de Nube Privada con Google Drive para SpotiLinux.
Soporta:
  1. OAuth 2.0 (Token de acceso / Refresh Token / Flujo de navegador con credentials.json)
     para listar y hacer streaming de toda la música privada del Drive del usuario.
  2. ID o enlace de Carpeta de Google Drive + API Key / Token OAuth.
  3. Enlaces directos de archivos de Google Drive (https://drive.google.com/file/d/...).
  4. Caché inteligente bajo demanda en data/gdrive_cache/ para reproducción instantánea
     y compatibilidad con el Ecualizador de 5 bandas y el visualizador FFT + Maomao.
"""

import json
import re
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
import requests
from PyQt6.QtCore import QThread, pyqtSignal

from metadata import SUPPORTED_EXTENSIONS, TrackMetadata, extract_metadata, generate_fallback_cover, compute_dominant_color


AUDIO_EXT_TUPLE = tuple(SUPPORTED_EXTENSIONS)


def extract_drive_id_from_url(text: str) -> str:
    """Extrae el ID de carpeta o archivo desde una URL de Google Drive o devuelve el ID limpio."""
    text = text.strip()
    if not text:
        return ""
    # Formato /folders/<ID>
    m = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    # Formato /file/d/<ID>
    m = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    # Formato ?id=<ID>
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    return text


class GoogleDriveConfig:
    def __init__(
        self,
        access_token: str = "",
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        api_key: str = "",
        folder_id: str = "",
    ) -> None:
        self.access_token = access_token.strip()
        self.refresh_token = refresh_token.strip()
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.api_key = api_key.strip()
        self.folder_id = extract_drive_id_from_url(folder_id)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "api_key": self.api_key,
            "folder_id": self.folder_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoogleDriveConfig":
        return cls(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            client_id=str(data.get("client_id", "")),
            client_secret=str(data.get("client_secret", "")),
            api_key=str(data.get("api_key", "")),
            folder_id=str(data.get("folder_id", "")),
        )


class GDriveOAuthLoopbackWorker(QThread):
    """Ejecuta el flujo OAuth 2.0 oficial en el navegador local y captura el token automáticamente."""

    oauth_completed = pyqtSignal(str, str)  # access_token, refresh_token
    oauth_error = pyqtSignal(str)

    def __init__(self, client_id: str, client_secret: str, port: int = 8765) -> None:
        super().__init__()
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.port = port

    def run(self) -> None:
        redirect_uri = f"http://127.0.0.1:{self.port}/callback"
        auth_code_holder: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self_handler):
                parsed = urllib.parse.urlparse(self_handler.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    auth_code_holder["code"] = params["code"][0]
                    body = (
                        "<html><body style='background:#121212;color:#1DB954;font-family:sans-serif;"
                        "text-align:center;padding-top:80px;'>"
                        "<h1>✔ SpotiLinux conectado a tu Google Drive</h1>"
                        "<p style='color:#fff;'>Ya puedes cerrar esta pestaña y volver al reproductor.</p>"
                        "</body></html>"
                    ).encode("utf-8")
                else:
                    auth_code_holder["error"] = params.get("error", ["cancelled"])[0]
                    body = b"<html><body>Error de autenticacion.</body></html>"

                self_handler.send_response(200)
                self_handler.send_header("Content-Type", "text/html; charset=utf-8")
                self_handler.end_headers()
                self_handler.wfile.write(body)

            def log_message(self_handler, format, *args):
                pass

        try:
            server = HTTPServer(("127.0.0.1", self.port), CallbackHandler)
            server.timeout = 120

            auth_params = {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "https://www.googleapis.com/auth/drive.readonly",
                "access_type": "offline",
                "prompt": "consent",
            }
            auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)
            webbrowser.open(auth_url)

            server.handle_request()
            server.server_close()

            if "code" not in auth_code_holder:
                self.oauth_error.emit(auth_code_holder.get("error", "Tiempo de espera agotado"))
                return

            token_resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": auth_code_holder["code"],
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=15,
            )
            if token_resp.status_code == 200:
                tdata = token_resp.json()
                self.oauth_completed.emit(
                    tdata.get("access_token", ""),
                    tdata.get("refresh_token", ""),
                )
            else:
                self.oauth_error.emit(f"Error al canjear token ({token_resp.status_code}): {token_resp.text[:160]}")
        except Exception as exc:
            self.oauth_error.emit(str(exc))


class GDriveSyncWorker(QThread):
    """Consulta Google Drive API v3 para listar pistas de audio y registrarlas en la biblioteca."""

    sync_progress = pyqtSignal(str)
    sync_finished = pyqtSignal(list, str)  # list[TrackMetadata], status_msg

    def __init__(
        self,
        config: GoogleDriveConfig,
        cache_dir: Path,
        covers_dir: Path,
        direct_links: Optional[list[str]] = None,
        download_immediately: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.cache_dir = cache_dir
        self.covers_dir = covers_dir
        self.direct_links = direct_links or []
        self.download_immediately = download_immediately

    def _refresh_access_token_if_needed(self) -> None:
        if self.config.refresh_token and self.config.client_id and self.config.client_secret:
            try:
                resp = requests.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": self.config.client_id,
                        "client_secret": self.config.client_secret,
                        "refresh_token": self.config.refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    new_tok = resp.json().get("access_token")
                    if new_tok:
                        self.config.access_token = new_tok
            except Exception:
                pass

    def run(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self._refresh_access_token_if_needed()

        discovered: list[TrackMetadata] = []

        # 1. Procesar enlaces directos de archivos si el usuario pegó URLs individuales
        for link in self.direct_links:
            fid = extract_drive_id_from_url(link)
            if not fid:
                continue
            self.sync_progress.emit(f"Descargando pista de Drive ({fid[:8]})...")
            meta = download_drive_file_to_cache(
                file_id=fid,
                filename_hint=f"drive_{fid[:8]}.mp3",
                config=self.config,
                cache_dir=self.cache_dir,
                covers_dir=self.covers_dir,
            )
            if meta:
                discovered.append(meta)

        # 2. Consultar Google Drive API v3 (con OAuth2 Token o API Key)
        if self.config.access_token or self.config.api_key:
            self.sync_progress.emit("Conectando con Google Drive API v3...")
            headers = {}
            params: dict[str, str] = {
                "pageSize": "100",
                "fields": "files(id, name, mimeType, size, videoMediaMetadata)",
                "orderBy": "name",
            }

            if self.config.access_token:
                headers["Authorization"] = f"Bearer {self.config.access_token}"
            elif self.config.api_key:
                params["key"] = self.config.api_key

            q_parts = ["trashed = false"]
            target_folder_id = self.config.folder_id
            if not target_folder_id:
                # Buscar automáticamente carpeta 'Music' / 'Música' / 'Musica' en el Drive del usuario
                self.sync_progress.emit("Buscando automáticamente carpeta 'Música' / 'Music' en tu Google Drive...")
                try:
                    folder_q = (
                        "trashed = false and mimeType = 'application/vnd.google-apps.folder' and "
                        "(name contains 'Music' or name contains 'Musica' or name contains 'Música' or name contains 'music')"
                    )
                    f_params = dict(params)
                    f_params["q"] = folder_q
                    f_params["pageSize"] = "10"
                    f_params["fields"] = "files(id, name)"
                    f_resp = requests.get(
                        "https://www.googleapis.com/drive/v3/files",
                        headers=headers,
                        params=f_params,
                        timeout=12,
                    )
                    if f_resp.status_code == 200:
                        folders = f_resp.json().get("files", [])
                        if folders:
                            target_folder_id = folders[0]["id"]
                            folder_name = folders[0].get("name", "Music")
                            self.config.folder_id = target_folder_id
                            self.sync_progress.emit(f"Carpeta encontrada en Drive: «{folder_name}». Importando canciones...")
                except Exception:
                    pass

            if target_folder_id:
                q_parts.append(
                    f"('{target_folder_id}' in parents or mimeType contains 'audio/' or name contains '.mp3' or name contains '.flac' or name contains '.wav' or name contains '.m4a' or name contains '.ogg')"
                )
            else:
                q_parts.append("(mimeType contains 'audio/' or name contains '.mp3' or name contains '.flac' or name contains '.wav' or name contains '.m4a' or name contains '.ogg')")

            params["q"] = " and ".join(q_parts)

            try:
                resp = requests.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers=headers,
                    params=params,
                    timeout=15,
                )
                if resp.status_code == 200:
                    files = resp.json().get("files", [])
                    audio_files = [
                        f for f in files
                        if f.get("mimeType", "").startswith("audio/")
                        or Path(f.get("name", "")).suffix.lower() in SUPPORTED_EXTENSIONS
                    ]
                    self.sync_progress.emit(f"Se encontraron {len(audio_files)} canciones en tu Google Drive.")

                    for idx, fitem in enumerate(audio_files):
                        fid = fitem["id"]
                        fname = fitem.get("name", f"{fid}.mp3")
                        ext = Path(fname).suffix.lower() or ".mp3"
                        if ext not in SUPPORTED_EXTENSIONS:
                            ext = ".mp3"
                        local_target = self.cache_dir / f"gd_{fid}{ext}"

                        if self.download_immediately or local_target.exists():
                            self.sync_progress.emit(f"Sincronizando ({idx + 1}/{len(audio_files)}): {fname}")
                            meta = download_drive_file_to_cache(
                                file_id=fid,
                                filename_hint=fname,
                                config=self.config,
                                cache_dir=self.cache_dir,
                                covers_dir=self.covers_dir,
                            )
                            if meta:
                                discovered.append(meta)
                        else:
                            # Registrar entrada en la nube lista para streaming bajo demanda
                            tid = f"gd_{fid[:14]}"
                            cover_file = self.covers_dir / f"{tid}.jpg"
                            clean_title = Path(fname).stem.replace("_", " ").replace("-", " - ")
                            artist_guess = "Google Drive Cloud ☁️"
                            if " - " in clean_title:
                                parts = clean_title.split(" - ", 1)
                                artist_guess = f"{parts[0].strip()} ☁️"
                                clean_title = parts[1].strip()

                            if not cover_file.exists():
                                generate_fallback_cover(clean_title, artist_guess, cover_file, tid)
                            dom_rgb = compute_dominant_color(cover_file)

                            discovered.append(
                                TrackMetadata(
                                    id=tid,
                                    filepath=f"gdrive://{fid}/{fname}",
                                    title=clean_title,
                                    artist=artist_guess,
                                    album="Mi Google Drive Privado ☁️",
                                    genre="Cloud Stream",
                                    year="",
                                    duration=0.0,
                                    cover_path=str(cover_file),
                                    dominant_color=dom_rgb,
                                )
                            )
                else:
                    err_detail = resp.text[:180]
                    self.sync_finished.emit(
                        discovered,
                        f"Aviso de Google Drive API ({resp.status_code}): {err_detail}",
                    )
                    return
            except Exception as exc:
                self.sync_finished.emit(discovered, f"Error de conexión a Google Drive: {exc}")
                return

        self.sync_finished.emit(
            discovered,
            f"✔ Sincronización completada: {len(discovered)} pistas de Google Drive listas.",
        )


class GDriveStreamDownloadWorker(QThread):
    """Descarga/hace streaming de una pista gdrive://<id>/<name> a caché local justo al darle Play."""

    stream_ready = pyqtSignal(str, object)  # old_track_id, updated TrackMetadata
    stream_failed = pyqtSignal(str, str)    # track_id, error_msg

    def __init__(
        self,
        track: TrackMetadata,
        config: GoogleDriveConfig,
        cache_dir: Path,
        covers_dir: Path,
    ) -> None:
        super().__init__()
        self.track = track
        self.config = config
        self.cache_dir = cache_dir
        self.covers_dir = covers_dir

    def run(self) -> None:
        # Formato gdrive://<file_id>/<filename>
        raw = self.track.filepath[len("gdrive://") :]
        parts = raw.split("/", 1)
        file_id = parts[0]
        fname = parts[1] if len(parts) > 1 else f"{file_id}.mp3"

        meta = download_drive_file_to_cache(
            file_id=file_id,
            filename_hint=fname,
            config=self.config,
            cache_dir=self.cache_dir,
            covers_dir=self.covers_dir,
            preserve_id=self.track.id,
        )
        if meta:
            self.stream_ready.emit(self.track.id, meta)
        else:
            self.stream_failed.emit(
                self.track.id,
                "No se pudo transmitir el archivo desde Google Drive. Verifica el token OAuth o permisos del archivo.",
            )


def download_drive_file_to_cache(
    file_id: str,
    filename_hint: str,
    config: GoogleDriveConfig,
    cache_dir: Path,
    covers_dir: Path,
    preserve_id: Optional[str] = None,
) -> Optional[TrackMetadata]:
    """Descarga un archivo de audio desde Google Drive (vía API v3 o enlace directo) y extrae sus tags ID3."""
    ext = Path(filename_hint).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        ext = ".mp3"
    target_path = cache_dir / f"gd_{file_id}{ext}"

    if not target_path.exists() or target_path.stat().st_size < 1024:
        headers = {}
        if config.access_token:
            headers["Authorization"] = f"Bearer {config.access_token}"

        # 1. Intentar mediante endpoint oficial Drive API v3 alt=media
        urls_to_try = []
        api_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        if config.api_key and not config.access_token:
            api_url += f"&key={config.api_key}"
        urls_to_try.append((api_url, headers))

        # 2. Respaldo mediante endpoint de descarga directa uc?export=download
        urls_to_try.append((f"https://drive.google.com/uc?export=download&id={file_id}", {}))

        downloaded = False
        for url, req_headers in urls_to_try:
            try:
                with requests.get(url, headers=req_headers, stream=True, timeout=25) as r:
                    if r.status_code == 200 and "text/html" not in r.headers.get("Content-Type", ""):
                        with open(target_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
                        if target_path.exists() and target_path.stat().st_size > 1024:
                            downloaded = True
                            break
            except Exception:
                continue

        if not downloaded:
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            return None

    meta = extract_metadata(target_path, covers_dir)
    if preserve_id:
        meta.id = preserve_id
    if meta.album == "Archivos locales":
        meta.album = "Mi Google Drive Privado ☁️"
    if not meta.artist or meta.artist == "Artista desconocido":
        meta.artist = "Google Drive Cloud ☁️"
    return meta
