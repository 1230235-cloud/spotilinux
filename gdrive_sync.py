#!/usr/bin/env python3
"""
Conector de Nube Privada y Base de Datos de Cuentas en Google Drive para SpotiLinux.
Carpeta raíz oficial compartida:
  https://drive.google.com/drive/folders/1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT?usp=sharing
  (ID: 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT)

Dentro de esta carpeta raíz:
  1. Se almacena la base de datos de cuentas, playlists y catálogo (`spotilinux_database.json` y `spotilinux_cloud_playlists.json`).
  2. Se ubica la subcarpeta `Musica` donde residen las canciones para escuchar en modo Online (Streaming)
     o Descargarlas (Offline con verificación de duplicados), y donde se pueden subir más canciones.
"""

import hashlib
import html
import json
import mimetypes
import re
import time
import unicodedata
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
import requests
from PyQt6.QtCore import QThread, pyqtSignal

from metadata import (
    SUPPORTED_EXTENSIONS,
    TrackMetadata,
    compute_dominant_color,
    extract_metadata,
    generate_fallback_cover,
)

AUDIO_EXT_TUPLE = tuple(SUPPORTED_EXTENSIONS)

DEFAULT_ROOT_DRIVE_FOLDER_ID = "1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT"
DEFAULT_ROOT_DRIVE_FOLDER_URL = (
    "https://drive.google.com/drive/folders/1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT?usp=sharing"
)
DEFAULT_MUSICA_FOLDER_ID = "1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T"
DEFAULT_DRIVE_SERVER_URL = "https://script.google.com/macros/s/AKfycbzwOO_eEw7ldBwGUPwX_MqYZfgRA6-N9meQ_z8k43V-0KBc0cmj43ap2qV6vT0ynO4dIQ/exec"
DEFAULT_CLOUD_SERVER_URL = "https://api.restful-api.dev/objects/ff808181a09d98f701a16e7026147e2a"
DEFAULT_CLOUD_OBJECT_NAME = "spotilinux_database_1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT"
CLOUD_DB_FILENAME = "spotilinux_database.json"
LEGACY_PLAYLISTS_FILENAME = "spotilinux_cloud_playlists.json"


def extract_drive_id_from_url(text: str) -> str:
    """Extrae el ID de carpeta o archivo desde una URL de Google Drive o devuelve el ID limpio."""
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    m = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    return text


def canonical_drive_track_id(file_id: str) -> str:
    """ID canónico unificado entre Linux y Android para cualquier pista de Google Drive."""
    clean = extract_drive_id_from_url(file_id)
    return f"gd_{clean}"


def clean_track_title(name: str) -> str:
    """Limpia el título de la canción eliminando prefijos de y2mate, sufijos ruidosos y extensiones."""
    t = str(name or "").strip()
    t = re.sub(r"\.(mp3|flac|wav|m4a|ogg|opus|aac|webm|mp4)$", "", t, flags=re.I)
    t = re.sub(r"^(y2mate(\.com|\.is)?|y2meta(\.com)?|snaptik(\.[a-z]+)?|yt1s(\.com)?)\s*[-_–—:]\s*", "", t, flags=re.I)
    t = re.sub(r"\by2mate(\.com)?\s*[-_–—:]*\s*", "", t, flags=re.I)
    t = t.replace("⧸", " / ").replace("_", " ")
    t = re.sub(r"^[「\[]Nightcore[」\]]\s*[➼♡–—\-]*\s*", "Nightcore - ", t, flags=re.I)
    t = re.sub(r"\s*/\s*(lyrics|letras)\b", "", t, flags=re.I)
    t = re.sub(r"\s*[\(\[]\s*(video\s*oficial|official\s*(music\s*)?video|video|lyrics|letras|letra|official|audio\s*oficial|audio|amv)\s*[\)\]]", "", t, flags=re.I)
    t = re.sub(r"\s+(lyrics|letras)\b.*$", "", t, flags=re.I)
    t = re.sub(r"\s*#[a-zA-Z0-9_-]+", "", t)
    t = re.sub(r"^[💀⚔️♣️◼💖💞「」『』➼♬°•★☆✦✧\s–—-]+", "", t)
    t = re.sub(r"[💀⚔️♣️◼💖💞「」『』➼♬°•★☆✦✧\s–—-]+$", "", t)
    t = re.sub(r"[◼💖💞]+", "", t)
    t = re.sub(r"\s*°•[^\s•]+•°\s*", " ", t)
    t = re.sub(r"\s*[\(\[]\s*[\)\]]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t or str(name or "Canción").strip()


def normalize_track_key(text: str) -> str:
    """Normaliza un título o nombre de archivo para la verificación de duplicados de título completo."""
    clean = clean_track_title(text)
    if clean.startswith("gd_") and "__" in clean:
        clean = clean.split("__", 1)[1]
    elif clean.startswith("gd_"):
        clean = clean[3:]
    s = "".join(
        c for c in unicodedata.normalize("NFD", clean.lower()) if unicodedata.category(c) != "Mn"
    )
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def hash_password(password: str, salt: str = "spotilinux_v2") -> str:
    """Hash SHA-256 determinístico compatible entre Linux y Android."""
    raw = f"{salt}:{password.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class GoogleDriveConfig:
    def __init__(
        self,
        access_token: str = "",
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        api_key: str = "",
        folder_id: str = DEFAULT_ROOT_DRIVE_FOLDER_ID,
        musica_folder_id: str = "",
        check_duplicates: bool = True,
    ) -> None:
        self.access_token = access_token.strip()
        self.refresh_token = refresh_token.strip()
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.api_key = api_key.strip()
        extracted = extract_drive_id_from_url(folder_id)
        self.folder_id = extracted if extracted else DEFAULT_ROOT_DRIVE_FOLDER_ID
        self.musica_folder_id = extract_drive_id_from_url(musica_folder_id)
        self.check_duplicates = bool(check_duplicates)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "api_key": self.api_key,
            "folder_id": self.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID,
            "musica_folder_id": self.musica_folder_id,
            "check_duplicates": self.check_duplicates,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoogleDriveConfig":
        raw_folder = str(data.get("folder_id", "")).strip()
        return cls(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            client_id=str(data.get("client_id", "")),
            client_secret=str(data.get("client_secret", "")),
            api_key=str(data.get("api_key", "")),
            folder_id=raw_folder if raw_folder else DEFAULT_ROOT_DRIVE_FOLDER_ID,
            musica_folder_id=str(data.get("musica_folder_id", "")),
            check_duplicates=bool(data.get("check_duplicates", True)),
        )


def list_public_drive_folder_items(folder_id: str) -> list[dict]:
    """
    Lee los elementos de una carpeta pública/compartida de Google Drive (como 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT)
    usando la vista embebida pública de Google Drive cuando aún no se ha configurado un Token OAuth2.
    Retorna lista de dicts: {'id': ..., 'name': ..., 'is_folder': bool}
    """
    if not folder_id:
        return []
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
    items: list[dict] = []
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) SpotiLinux/2.0"},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        text = resp.text
        # Cada entrada en embeddedfolderview tiene id="entry-<ID>" y class="flip-entry-title"
        pattern = re.compile(
            r'id="entry-([a-zA-Z0-9_-]{10,})"[^>]*>.*?href="([^"]+)"[^>]*>.*?<div class="flip-entry-title">([^<]+)</div>',
            re.DOTALL,
        )
        for m in pattern.finditer(text):
            eid = m.group(1).strip()
            href = m.group(2).strip()
            title = html.unescape(m.group(3).strip())
            is_folder = "/folders/" in href or "folder" in href.lower()
            items.append({"id": eid, "name": title, "is_folder": is_folder})
    except Exception:
        pass
    return items


def resolve_musica_subfolder_id(config: GoogleDriveConfig) -> tuple[str, str]:
    """
    Busca la subcarpeta 'Musica' (o 'Música' / 'Music') dentro de la carpeta raíz
    (por defecto 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT).
    Retorna (musica_folder_id, nombre_carpeta). Si no la encuentra, devuelve (root_folder_id, 'Raíz Drive').
    """
    root_id = config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID

    # 1. Vía Google Drive API v3 si hay Token o API Key
    if config.access_token or config.api_key:
        headers = {"Authorization": f"Bearer {config.access_token}"} if config.access_token else {}
        q = (
            f"'{root_id}' in parents and trashed = false and "
            "mimeType = 'application/vnd.google-apps.folder'"
        )
        params: dict[str, str] = {
            "q": q,
            "pageSize": "50",
            "fields": "files(id, name)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if config.api_key and not config.access_token:
            params["key"] = config.api_key
        try:
            r = requests.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=headers,
                params=params,
                timeout=12,
            )
            if r.status_code == 200:
                folders = r.json().get("files", [])
                for f in folders:
                    fname = f.get("name", "")
                    norm = normalize_track_key(fname)
                    if norm in ("musica", "music") or "musica" in norm or "music" in norm:
                        config.musica_folder_id = f["id"]
                        return f["id"], fname
                # Si no existe aún la carpeta Musica y tenemos token de edición, crearla dentro de root_id
                if config.access_token and not folders:
                    create_res = requests.post(
                        "https://www.googleapis.com/drive/v3/files?supportsAllDrives=true",
                        headers={
                            "Authorization": f"Bearer {config.access_token}",
                            "Content-Type": "application/json; charset=UTF-8",
                        },
                        data=json.dumps(
                            {
                                "name": "Musica",
                                "mimeType": "application/vnd.google-apps.folder",
                                "parents": [root_id],
                            }
                        ).encode("utf-8"),
                        timeout=12,
                    )
                    if create_res.status_code in (200, 201):
                        new_id = create_res.json().get("id", "")
                        if new_id:
                            config.musica_folder_id = new_id
                            return new_id, "Musica"
        except Exception:
            pass

    # 2. Respaldo público por vista embebida de la carpeta compartida 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT
    pub_items = list_public_drive_folder_items(root_id)
    for item in pub_items:
        if item.get("is_folder"):
            norm = normalize_track_key(item.get("name", ""))
            if norm in ("musica", "music") or "musica" in norm or "music" in norm:
                config.musica_folder_id = item["id"]
                return item["id"], item.get("name", "Musica")

    if config.musica_folder_id:
        return config.musica_folder_id, "Musica"
    return DEFAULT_MUSICA_FOLDER_ID, "Musica"


def resolve_videos_subfolder_id(config: GoogleDriveConfig) -> Optional[tuple[str, str]]:
    """
    Busca la subcarpeta 'videos' (o 'video' / 'Videos') dentro de la carpeta raíz de SpotiLinux en Drive.
    Retorna (videos_folder_id, nombre_carpeta) o None.
    """
    root_id = config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID
    if config.access_token or config.api_key:
        headers = {"Authorization": f"Bearer {config.access_token}"} if config.access_token else {}
        q = (
            f"'{root_id}' in parents and trashed = false and "
            "mimeType = 'application/vnd.google-apps.folder'"
        )
        params: dict[str, str] = {
            "q": q,
            "pageSize": "50",
            "fields": "files(id, name)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if config.api_key and not config.access_token:
            params["key"] = config.api_key
        try:
            r = requests.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=headers,
                params=params,
                timeout=10,
            )
            if r.status_code == 200:
                folders = r.json().get("files", [])
                for f in folders:
                    fname = f.get("name", "").lower()
                    if "video" in fname:
                        return f["id"], f["name"]
        except Exception:
            pass
    return None


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
                        "<h1>✔ SpotiLinux conectado a Google Drive</h1>"
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
                "scope": "https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive.readonly",
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


def find_existing_local_duplicate(
    file_id: str,
    fname: str,
    existing_tracks: dict[str, TrackMetadata],
    search_dirs: list[Path],
) -> Optional[Path]:
    """
    Verifica si una canción de Google Drive ya está descargada localmente (por ID de Drive o por nombre/título normalizado).
    Devuelve la ruta local existente si se detecta duplicado, o None si no está descargada.
    """
    tid = canonical_drive_track_id(file_id)
    target_key = normalize_track_key(fname)

    # 1. Revisar pistas ya registradas en la biblioteca
    for t in existing_tracks.values():
        if t.filepath.startswith("gdrive://"):
            continue
        p = Path(t.filepath)
        if not p.exists() or p.stat().st_size < 1024:
            continue
        if t.id == tid or f"gd_{file_id}" in p.name:
            return p
        if target_key and (normalize_track_key(t.title) == target_key or normalize_track_key(p.name) == target_key):
            return p

    # 2. Revisar archivos físicos en las carpetas de caché y music_library
    for d in search_dirs:
        if not d or not d.exists():
            continue
        for f in d.iterdir():
            if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTENSIONS or f.stat().st_size < 1024:
                continue
            if f.name.startswith(f"gd_{file_id}") or (target_key and normalize_track_key(f.name) == target_key):
                return f

    return None


class GDriveSyncWorker(QThread):
    """
    Consulta la carpeta compartida de Google Drive (1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT -> subcarpeta 'Musica')
    para listar las canciones disponibles en modo Online o Descargarlas con verificación de duplicados.
    """

    sync_progress = pyqtSignal(str)
    sync_finished = pyqtSignal(list, str)  # list[TrackMetadata], status_msg

    def __init__(
        self,
        config: GoogleDriveConfig,
        cache_dir: Path,
        covers_dir: Path,
        direct_links: Optional[list[str]] = None,
        download_immediately: bool = False,
        existing_tracks: Optional[dict[str, TrackMetadata]] = None,
        extra_search_dirs: Optional[list[Path]] = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.cache_dir = cache_dir
        self.covers_dir = covers_dir
        self.direct_links = direct_links or []
        self.download_immediately = download_immediately
        self.existing_tracks = dict(existing_tracks or {})
        self.extra_search_dirs = list(extra_search_dirs or [])

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

    def _build_track_from_drive_item(
        self,
        fid: str,
        fname: str,
        skipped_counter: list[int],
    ) -> Optional[TrackMetadata]:
        tid = canonical_drive_track_id(fid)
        search_dirs = [self.cache_dir] + self.extra_search_dirs

        # Verificación de duplicados antes de descargar
        existing_local = None
        if self.config.check_duplicates:
            existing_local = find_existing_local_duplicate(fid, fname, self.existing_tracks, search_dirs)

        if existing_local is not None:
            skipped_counter[0] += 1
            meta = extract_metadata(existing_local, self.covers_dir)
            meta.id = tid
            if meta.album == "Archivos locales":
                meta.album = "Carpeta Musica • Google Drive ☁️"
            return meta

        if self.download_immediately:
            meta = download_drive_file_to_cache(
                file_id=fid,
                filename_hint=fname,
                config=self.config,
                cache_dir=self.cache_dir,
                covers_dir=self.covers_dir,
                preserve_id=tid,
                existing_tracks=self.existing_tracks,
                extra_search_dirs=self.extra_search_dirs,
            )
            if meta:
                return meta

        # Modo en línea (Streaming bajo demanda)
        cover_file = self.covers_dir / f"{tid}.jpg"
        clean_title = Path(fname).stem.replace("_", " ").replace("-", " - ")
        artist_guess = "Google Drive • Musica ☁️"
        if " - " in clean_title:
            parts = clean_title.split(" - ", 1)
            artist_guess = f"{parts[0].strip()} ☁️"
            clean_title = parts[1].strip()

        if not cover_file.exists():
            generate_fallback_cover(clean_title, artist_guess, cover_file, tid)
        dom_rgb = compute_dominant_color(cover_file)

        return TrackMetadata(
            id=tid,
            filepath=f"gdrive://{fid}/{fname}",
            title=clean_title,
            artist=artist_guess,
            album="Carpeta Musica • Google Drive ☁️",
            genre="Cloud Stream",
            year="",
            duration=0.0,
            cover_path=str(cover_file),
            dominant_color=dom_rgb,
        )

    def run(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self._refresh_access_token_if_needed()

        discovered: list[TrackMetadata] = []
        seen_keys: set[str] = set()
        skipped_duplicates = [0]

        # 1. Procesar enlaces directos de archivos si el usuario pegó URLs individuales
        for link in self.direct_links:
            fid = extract_drive_id_from_url(link)
            if not fid:
                continue
            self.sync_progress.emit(f"Verificando pista de Drive ({fid[:8]})...")
            t = self._build_track_from_drive_item(fid, f"drive_{fid[:8]}.mp3", skipped_duplicates)
            if t and t.id not in seen_keys:
                seen_keys.add(t.id)
                discovered.append(t)

        # 2. Resolver carpeta raíz (1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT) y su subcarpeta 'Musica'
        root_id = self.config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID
        self.sync_progress.emit("Localizando subcarpeta 'Musica' en el Drive compartido...")
        musica_id, musica_name = resolve_musica_subfolder_id(self.config)

        audio_items: list[dict] = []

        # 2A. Listar con Google Drive API v3 si hay Token o API Key
        if self.config.access_token or self.config.api_key:
            headers = {"Authorization": f"Bearer {self.config.access_token}"} if self.config.access_token else {}
            folders_to_query = [musica_id]
            if root_id and root_id != musica_id:
                folders_to_query.append(root_id)

            parent_clause = " or ".join(f"'{fid}' in parents" for fid in folders_to_query if fid)
            q = f"trashed = false and ({parent_clause})"
            params: dict[str, str] = {
                "q": q,
                "pageSize": "200",
                "fields": "files(id, name, mimeType, size)",
                "orderBy": "name",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if self.config.api_key and not self.config.access_token:
                params["key"] = self.config.api_key

            try:
                resp = requests.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers=headers,
                    params=params,
                    timeout=15,
                )
                if resp.status_code == 200:
                    files = resp.json().get("files", [])
                    for f in files:
                        fname = f.get("name", "")
                        mime = f.get("mimeType", "")
                        if mime.startswith("audio/") or Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                            audio_items.append({"id": f["id"], "name": fname})
            except Exception:
                pass

        # 2B. Si no hubo resultados por API (p.ej. sin token OAuth aún), consultar la vista pública de la carpeta 'Musica'
        if not audio_items:
            self.sync_progress.emit(f"Consultando carpeta compartida «{musica_name}» en Google Drive...")
            for folder_target in ([musica_id, root_id] if musica_id != root_id else [root_id]):
                pub_entries = list_public_drive_folder_items(folder_target)
                for entry in pub_entries:
                    if not entry.get("is_folder"):
                        fname = entry.get("name", "")
                        ext = Path(fname).suffix.lower()
                        if ext in SUPPORTED_EXTENSIONS or not ext:
                            if not ext:
                                fname = fname + ".mp3"
                            audio_items.append({"id": entry["id"], "name": fname})

        # Procesar las canciones encontradas evitando duplicados
        total = len(audio_items)
        for idx, fitem in enumerate(audio_items):
            fid = fitem["id"]
            fname = fitem.get("name", f"{fid}.mp3")
            norm_k = normalize_track_key(fname)
            tid = canonical_drive_track_id(fid)
            if tid in seen_keys or (self.config.check_duplicates and norm_k and norm_k in seen_keys):
                skipped_duplicates[0] += 1
                continue

            self.sync_progress.emit(f"Procesando ({idx + 1}/{total}) de «{musica_name}»: {fname}")
            t = self._build_track_from_drive_item(fid, fname, skipped_duplicates)
            if t:
                seen_keys.add(tid)
                if norm_k:
                    seen_keys.add(norm_k)
                discovered.append(t)

        dup_note = (
            f" ({skipped_duplicates[0]} duplicadas verificadas/omitidas)"
            if skipped_duplicates[0] > 0
            else ""
        )
        self.sync_finished.emit(
            discovered,
            f"✔ Carpeta «{musica_name}» sincronizada: {len(discovered)} pistas listas{dup_note}.",
        )


class GDriveStreamDownloadWorker(QThread):
    """Descarga/hace streaming de una pista gdrive://<id>/<name> a caché o music_library con verificación de duplicados."""

    stream_ready = pyqtSignal(str, object, bool)  # old_track_id, updated TrackMetadata, was_duplicate_skipped
    stream_failed = pyqtSignal(str, str)          # track_id, error_msg

    def __init__(
        self,
        track: TrackMetadata,
        config: GoogleDriveConfig,
        cache_dir: Path,
        covers_dir: Path,
        existing_tracks: Optional[dict[str, TrackMetadata]] = None,
        extra_search_dirs: Optional[list[Path]] = None,
    ) -> None:
        super().__init__()
        self.track = track
        self.config = config
        self.cache_dir = cache_dir
        self.covers_dir = covers_dir
        self.existing_tracks = dict(existing_tracks or {})
        self.extra_search_dirs = list(extra_search_dirs or [])

    def run(self) -> None:
        if self.track.filepath.startswith("gdrive://"):
            raw = self.track.filepath[len("gdrive://") :]
            parts = raw.split("/", 1)
            file_id = parts[0]
            fname = parts[1] if len(parts) > 1 else f"{file_id}.mp3"
        else:
            file_id = self.track.id.replace("gd_", "")
            fname = Path(self.track.filepath).name

        tid = canonical_drive_track_id(file_id)
        search_dirs = [self.cache_dir] + self.extra_search_dirs

        # Verificación de duplicados antes de descargar
        if self.config.check_duplicates:
            dup_path = find_existing_local_duplicate(file_id, fname, self.existing_tracks, search_dirs)
            if dup_path is not None and dup_path.exists():
                meta = extract_metadata(dup_path, self.covers_dir)
                meta.id = self.track.id or tid
                if meta.album == "Archivos locales":
                    meta.album = "Carpeta Musica • Google Drive ☁️"
                self.stream_ready.emit(self.track.id, meta, True)
                return

        meta = download_drive_file_to_cache(
            file_id=file_id,
            filename_hint=fname,
            config=self.config,
            cache_dir=self.cache_dir,
            covers_dir=self.covers_dir,
            preserve_id=self.track.id or tid,
            existing_tracks=self.existing_tracks,
            extra_search_dirs=self.extra_search_dirs,
        )
        if meta:
            self.stream_ready.emit(self.track.id, meta, False)
        else:
            self.stream_failed.emit(
                self.track.id,
                "No se pudo descargar/transmitir el archivo desde Google Drive. Verifica la conexión o el token OAuth.",
            )


def download_drive_file_to_cache(
    file_id: str,
    filename_hint: str,
    config: GoogleDriveConfig,
    cache_dir: Path,
    covers_dir: Path,
    preserve_id: Optional[str] = None,
    existing_tracks: Optional[dict[str, TrackMetadata]] = None,
    extra_search_dirs: Optional[list[Path]] = None,
) -> Optional[TrackMetadata]:
    """
    Descarga un archivo de audio desde Google Drive (vía API v3 o enlace público) verificando primero
    que no exista un duplicado ya descargado en la carpeta de la aplicación.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename_hint).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        ext = ".mp3"
    safe_stem = re.sub(r"[^a-zA-Z0-9_\-\s.]", "", Path(filename_hint).stem).strip() or file_id[:10]
    target_path = cache_dir / f"gd_{file_id}_{safe_stem}{ext}"

    if config.check_duplicates:
        dup = find_existing_local_duplicate(
            file_id,
            filename_hint,
            existing_tracks or {},
            [cache_dir] + list(extra_search_dirs or []),
        )
        if dup is not None and dup.exists() and dup.stat().st_size > 1024:
            target_path = dup

    if not target_path.exists() or target_path.stat().st_size < 1024:
        headers = {}
        if config.access_token:
            headers["Authorization"] = f"Bearer {config.access_token}"

        urls_to_try = []
        api_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"
        if config.api_key and not config.access_token:
            api_url += f"&key={config.api_key}"
        urls_to_try.append((api_url, headers))
        urls_to_try.append((f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t", {}))

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
    meta.id = preserve_id or canonical_drive_track_id(file_id)
    if meta.album == "Archivos locales":
        meta.album = "Carpeta Musica • Google Drive ☁️"
    if not meta.artist or meta.artist == "Artista desconocido":
        meta.artist = "Google Drive Cloud ☁️"
    return meta


class GDriveUploadWorker(QThread):
    """
    Sube uno o varios archivos de audio locales directamente a la subcarpeta 'Musica'
    dentro del Drive compartido (1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT), verificando duplicados.
    """

    upload_progress = pyqtSignal(str)
    upload_finished = pyqtSignal(list, str)  # list[TrackMetadata], msg

    def __init__(
        self,
        filepaths: list[Path],
        config: GoogleDriveConfig,
        covers_dir: Path,
    ) -> None:
        super().__init__()
        self.filepaths = filepaths
        self.config = config
        self.covers_dir = covers_dir

    def run(self) -> None:
        if not self.config.access_token:
            self.upload_finished.emit(
                [],
                "⚠️ Para subir canciones a la carpeta «Musica» del Drive se requiere iniciar sesión con Token OAuth 2.0.",
            )
            return

        headers = {"Authorization": f"Bearer {self.config.access_token}"}
        musica_id, musica_name = resolve_musica_subfolder_id(self.config)

        # Consultar archivos existentes en la carpeta Musica para evitar subir duplicados
        existing_remote_keys: dict[str, dict] = {}
        try:
            q = f"'{musica_id}' in parents and trashed = false"
            r = requests.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=headers,
                params={
                    "q": q,
                    "pageSize": "200",
                    "fields": "files(id, name)",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                },
                timeout=12,
            )
            if r.status_code == 200:
                for f in r.json().get("files", []):
                    k = normalize_track_key(f.get("name", ""))
                    if k:
                        existing_remote_keys[k] = f
        except Exception:
            pass

        uploaded_tracks: list[TrackMetadata] = []
        skipped_dups = 0

        for idx, fp in enumerate(self.filepaths):
            if not fp.exists() or fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            norm_k = normalize_track_key(fp.name)
            if self.config.check_duplicates and norm_k in existing_remote_keys:
                skipped_dups += 1
                rem = existing_remote_keys[norm_k]
                meta = extract_metadata(fp, self.covers_dir)
                meta.id = canonical_drive_track_id(rem["id"])
                meta.album = "Carpeta Musica • Google Drive ☁️"
                uploaded_tracks.append(meta)
                continue

            self.upload_progress.emit(f"⬆️ Subiendo ({idx + 1}/{len(self.filepaths)}) a «{musica_name}»: {fp.name}...")
            mime_type = mimetypes.guess_type(fp.name)[0] or "audio/mpeg"
            boundary = "-------SpotiLinuxUploadBoundary" + uuid.uuid4().hex[:12]
            meta_json = json.dumps(
                {
                    "name": fp.name,
                    "parents": [musica_id],
                    "description": "Canción subida desde SpotiLinux a la carpeta Musica",
                }
            )
            try:
                audio_bytes = fp.read_bytes()
                body = (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{meta_json}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

                up_res = requests.post(
                    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id,name",
                    headers={
                        "Authorization": f"Bearer {self.config.access_token}",
                        "Content-Type": f"multipart/related; boundary={boundary}",
                    },
                    data=body,
                    timeout=60,
                )
                if up_res.status_code in (200, 201):
                    new_fid = up_res.json().get("id", "")
                    if new_fid:
                        t_meta = extract_metadata(fp, self.covers_dir)
                        t_meta.id = canonical_drive_track_id(new_fid)
                        t_meta.album = "Carpeta Musica • Google Drive ☁️"
                        uploaded_tracks.append(t_meta)
                        existing_remote_keys[norm_k] = {"id": new_fid, "name": fp.name}
            except Exception:
                continue

        dup_msg = f" ({skipped_dups} duplicadas ya existían en Drive)" if skipped_dups else ""
        self.upload_finished.emit(
            uploaded_tracks,
            f"✔ {len(uploaded_tracks)} canciones disponibles en la carpeta «{musica_name}» de Google Drive{dup_msg}.",
        )


def fetch_google_user_profile(token: str) -> dict:
    """Obtiene los datos del perfil de usuario de Google Drive (nombre, correo, avatar)."""
    if not token:
        return {}
    try:
        r = requests.get(
            "https://www.googleapis.com/drive/v3/about?fields=user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            u = r.json().get("user", {})
            return {
                "name": u.get("displayName", ""),
                "email": u.get("emailAddress", ""),
                "photo": u.get("photoLink", ""),
            }
    except Exception:
        pass
    return {}


# =========================================================================== #
#   BASE DE DATOS DE CUENTAS (USUARIO, CORREO, CONTRASEÑA) EN GOOGLE DRIVE    #
# =========================================================================== #

def _find_cloud_db_file_id(config: GoogleDriveConfig, filename: str = CLOUD_DB_FILENAME) -> Optional[str]:
    """Busca el archivo spotilinux_database.json dentro de la carpeta raíz 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT."""
    root_id = config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID
    if config.access_token or config.api_key:
        headers = {"Authorization": f"Bearer {config.access_token}"} if config.access_token else {}
        for q in (
            f"'{root_id}' in parents and name = '{filename}' and trashed = false",
            f"name = '{filename}' and trashed = false",
        ):
            params: dict[str, str] = {
                "q": q,
                "pageSize": "5",
                "fields": "files(id, name)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if config.api_key and not config.access_token:
                params["key"] = config.api_key
            try:
                r = requests.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers=headers,
                    params=params,
                    timeout=10,
                )
                if r.status_code == 200:
                    files = r.json().get("files", [])
                    if files:
                        return files[0]["id"]
            except Exception:
                pass

    # Respaldo: buscar en la vista pública de la carpeta compartida 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT
    pub_items = list_public_drive_folder_items(root_id)
    for item in pub_items:
        if not item.get("is_folder") and item.get("name") == filename:
            return item["id"]
    return None


def download_cloud_database(config: GoogleDriveConfig, local_db_path: Path) -> tuple[dict, Optional[str]]:
    """Descarga y combina spotilinux_database.json desde la carpeta 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT con la copia local.
    Retorna (base_db, cloud_file_id)."""
    base_db: dict = {
        "app": "SpotiLinux",
        "version": "2.0.0",
        "root_folder_id": config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID,
        "updated_at": time.time(),
        "accounts": {},
        "drive_catalog": [],
    }
    if local_db_path.exists():
        try:
            local_raw = json.loads(local_db_path.read_text(encoding="utf-8"))
            if isinstance(local_raw, dict):
                base_db.update(local_raw)
        except Exception:
            pass

    fid = config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID

    # 1. Consultar el Servidor Oficial de Google Drive (Google Apps Script Web App)
    try:
        r_drive = requests.get(DEFAULT_DRIVE_SERVER_URL, timeout=12)
        if r_drive.status_code == 200:
            drive_db = r_drive.json()
            if isinstance(drive_db, dict) and "accounts" in drive_db:
                rem_accs = drive_db.get("accounts", {})
                loc_accs = base_db.setdefault("accounts", {})
                for acc_k, rem_a in rem_accs.items():
                    if acc_k not in loc_accs:
                        loc_accs[acc_k] = rem_a
                    else:
                        loc_accs[acc_k] = merge_account_records(loc_accs[acc_k], rem_a)
                cat_by_id = {c["id"]: c for c in base_db.get("drive_catalog", []) if isinstance(c, dict) and "id" in c}
                for rc in drive_db.get("drive_catalog", []):
                    if isinstance(rc, dict) and "id" in rc:
                        cat_by_id[rc["id"]] = rc
                base_db["drive_catalog"] = list(cat_by_id.values())
                local_db_path.parent.mkdir(parents=True, exist_ok=True)
                local_db_path.write_text(json.dumps(base_db, indent=2, ensure_ascii=False), encoding="utf-8")
                return base_db, "drive_server"
    except Exception:
        pass

    # 2. Respaldo: Servidor Central REST de la base de datos de Drive
    try:
        r_srv = requests.get(DEFAULT_CLOUD_SERVER_URL, timeout=8)
        if r_srv.status_code == 200:
            srv_resp = r_srv.json()
            srv_db = srv_resp.get("data", srv_resp) if isinstance(srv_resp, dict) else {}
            if isinstance(srv_db, dict) and "accounts" in srv_db:
                rem_accs = srv_db.get("accounts", {})
                loc_accs = base_db.setdefault("accounts", {})
                for acc_k, rem_a in rem_accs.items():
                    if acc_k not in loc_accs:
                        loc_accs[acc_k] = rem_a
                    else:
                        loc_accs[acc_k] = merge_account_records(loc_accs[acc_k], rem_a)
                cat_by_id = {c["id"]: c for c in base_db.get("drive_catalog", []) if isinstance(c, dict) and "id" in c}
                for rc in srv_db.get("drive_catalog", []):
                    if isinstance(rc, dict) and "id" in rc:
                        cat_by_id[rc["id"]] = rc
                base_db["drive_catalog"] = list(cat_by_id.values())
                local_db_path.parent.mkdir(parents=True, exist_ok=True)
                local_db_path.write_text(json.dumps(base_db, indent=2, ensure_ascii=False), encoding="utf-8")
                return base_db, "cloud_server"
    except Exception:
        pass

    file_id = _find_cloud_db_file_id(config, CLOUD_DB_FILENAME)
    if not file_id:
        file_id = _find_cloud_db_file_id(config, "spotilinux_cloud_playlists.json")
    if not file_id:
        return base_db, None

    headers = {"Authorization": f"Bearer {config.access_token}"} if config.access_token else {}
    urls = [
        (f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true", headers),
        (f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t", {}),
    ]
    for url, req_h in urls:
        try:
            r = requests.get(url, headers=req_h, timeout=12)
            if r.status_code == 200:
                remote_db = r.json()
                if isinstance(remote_db, dict):
                    # Combinar cuentas
                    rem_accounts = remote_db.get("accounts", {})
                    loc_accounts = base_db.setdefault("accounts", {})
                    for acc_key, rem_acc in rem_accounts.items():
                        if acc_key not in loc_accounts:
                            loc_accounts[acc_key] = rem_acc
                        else:
                            # Combinar playlists y canciones del Drive de esa cuenta
                            loc_acc = loc_accounts[acc_key]
                            merged_acc = merge_account_records(loc_acc, rem_acc)
                            loc_accounts[acc_key] = merged_acc
                    # Combinar catálogo de canciones de Drive
                    cat_by_id = {c["id"]: c for c in base_db.get("drive_catalog", []) if isinstance(c, dict) and "id" in c}
                    for rc in remote_db.get("drive_catalog", []):
                        if isinstance(rc, dict) and "id" in rc:
                            cat_by_id[rc["id"]] = rc
                    base_db["drive_catalog"] = list(cat_by_id.values())
                    local_db_path.parent.mkdir(parents=True, exist_ok=True)
                    local_db_path.write_text(json.dumps(base_db, indent=2, ensure_ascii=False), encoding="utf-8")
                    break
        except Exception:
            continue
    return base_db, file_id


def merge_account_records(acc_a: dict, acc_b: dict) -> dict:
    """Combina sin pérdida las playlists, favoritos y pistas de Drive de dos instantáneas de una misma cuenta."""
    newer = acc_b if float(acc_b.get("updated_at", 0)) >= float(acc_a.get("updated_at", 0)) else acc_a
    older = acc_a if newer is acc_b else acc_b
    merged = dict(newer)

    # Unir favoritos
    likes = list(dict.fromkeys(list(newer.get("liked_ids", [])) + list(older.get("liked_ids", []))))
    merged["liked_ids"] = likes

    # Unir pistas de Drive asociadas a la cuenta
    dtracks_by_id: dict[str, dict] = {}
    for dt in list(older.get("drive_tracks", [])) + list(newer.get("drive_tracks", [])):
        if isinstance(dt, dict) and dt.get("id"):
            dtracks_by_id[dt["id"]] = dt
    merged["drive_tracks"] = list(dtracks_by_id.values())

    # Unir playlists
    pl_by_id: dict[str, dict] = {}
    pl_by_name: dict[str, dict] = {}
    for p in list(newer.get("playlists", [])) + list(older.get("playlists", [])):
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or f"pl_{uuid.uuid4().hex[:8]}"
        pname_low = p.get("name", "Playlist").strip().lower()
        target = pl_by_id.get(pid) or pl_by_name.get(pname_low)
        tids = list(p.get("track_ids", p.get("trackIds", [])))
        snaps = list(p.get("tracks_snapshot", []))
        if target:
            cur_tids = list(target.get("track_ids", target.get("trackIds", [])))
            for t_id in tids:
                if t_id not in cur_tids:
                    cur_tids.append(t_id)
            target["track_ids"] = cur_tids
            target["trackIds"] = cur_tids
            snap_map = {s["id"]: s for s in target.get("tracks_snapshot", []) if isinstance(s, dict) and "id" in s}
            for s in snaps:
                if isinstance(s, dict) and "id" in s:
                    snap_map[s["id"]] = s
            target["tracks_snapshot"] = list(snap_map.values())
        else:
            new_p = {
                "id": pid,
                "name": p.get("name", "Playlist"),
                "description": p.get("description", p.get("desc", "")),
                "track_ids": tids,
                "trackIds": tids,
                "tracks_snapshot": snaps,
                "created_at": float(p.get("created_at", time.time())),
            }
            pl_by_id[pid] = new_p
            pl_by_name[pname_low] = new_p

    merged["playlists"] = list(pl_by_id.values())
    return merged


def upload_cloud_database(
    config: GoogleDriveConfig,
    local_db_path: Path,
    db_data: dict,
    existing_file_id: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Guarda la base de datos localmente en data/accounts_db.json y la sube/actualiza en la carpeta
    1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT de Google Drive como spotilinux_database.json.
    """
    if isinstance(local_db_path, dict) and isinstance(db_data, Path):
        local_db_path, db_data = db_data, local_db_path

    db_data["updated_at"] = time.time()
    fid = config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID
    db_data["root_folder_id"] = fid
    local_db_path.parent.mkdir(parents=True, exist_ok=True)
    json_bytes = json.dumps(db_data, indent=2, ensure_ascii=False).encode("utf-8")
    local_db_path.write_text(json_bytes.decode("utf-8"), encoding="utf-8")

    # 1. Siempre sincronizar con el Servidor Oficial de Google Drive (Apps Script Web App)
    cloud_synced = False
    try:
        r_drive = requests.post(
            DEFAULT_DRIVE_SERVER_URL,
            json={"action": "sync_db", "db": db_data},
            timeout=15,
        )
        if r_drive.status_code in (200, 201, 302):
            cloud_synced = True
    except Exception:
        pass

    # 2. Sincronizar también con el Servidor Central REST de respaldo
    try:
        wrap = {
            "name": DEFAULT_CLOUD_OBJECT_NAME,
            "data": db_data,
        }
        r_put = requests.put(
            DEFAULT_CLOUD_SERVER_URL,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            json=wrap,
            timeout=10,
        )
        if r_put.status_code in (200, 201):
            cloud_synced = True
    except Exception:
        pass

    if not config.access_token:
        if cloud_synced:
            return True, "✔ Base de datos sincronizada con el servidor de la nube (Drive 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT)."
        return True, "Base de datos guardada localmente (conecta Token OAuth para escribir en Google Drive)."

    root_id = fid
    file_id = existing_file_id if (existing_file_id and existing_file_id != "cloud_server") else _find_cloud_db_file_id(config, CLOUD_DB_FILENAME)
    try:
        if file_id:
            patch_res = requests.patch(
                f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media&supportsAllDrives=true",
                headers={
                    "Authorization": f"Bearer {config.access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                data=json_bytes,
                timeout=15,
            )
            if patch_res.status_code in (200, 201):
                return True, "✔ Base de datos sincronizada en la carpeta de Google Drive."
        # Crear archivo dentro de la carpeta 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT
        boundary = "-------SpotiLinuxDBBoundary314159"
        metadata = {
            "name": CLOUD_DB_FILENAME,
            "mimeType": "application/json",
            "parents": [root_id],
            "description": "Base de datos de cuentas, playlists y canciones de SpotiLinux",
        }
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        ).encode("utf-8") + json_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        post_res = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
            headers={
                "Authorization": f"Bearer {config.access_token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            data=body,
            timeout=15,
        )
        if post_res.status_code in (200, 201):
            return True, "✔ Base de datos creada y sincronizada en la carpeta de Google Drive."
        return False, f"Aviso al guardar en Drive (HTTP {post_res.status_code})"
    except Exception as exc:
        return False, str(exc)


def sync_cloud_playlists_with_drive(
    token: str,
    local_payload: dict,
    config: Optional[GoogleDriveConfig] = None,
    local_db_path: Optional[Path] = None,
) -> tuple[dict, Optional[str]]:
    """
    Sincroniza tanto la cuenta activa en spotilinux_database.json (dentro de 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT)
    como el archivo spotilinux_cloud_playlists.json para compatibilidad total entre dispositivos.
    """
    cfg = config or GoogleDriveConfig(access_token=token, folder_id=DEFAULT_ROOT_DRIVE_FOLDER_ID)
    if token and not cfg.access_token:
        cfg.access_token = token
    db_path = local_db_path or Path("data/accounts_db.json")

    try:
        db = download_cloud_database(cfg, db_path)
        account = local_payload.get("account") or {}
        acc_email = str(account.get("email", "")).strip().lower()
        acc_user = str(account.get("username", "")).strip().lower()
        acc_key = acc_email or acc_user

        drive_tracks = local_payload.get("drive_tracks", [])
        if drive_tracks:
            cat_map = {c["id"]: c for c in db.get("drive_catalog", []) if isinstance(c, dict) and "id" in c}
            for dt in drive_tracks:
                if isinstance(dt, dict) and "id" in dt:
                    cat_map[dt["id"]] = dt
            db["drive_catalog"] = list(cat_map.values())

        if acc_key:
            existing_acc = db.setdefault("accounts", {}).get(acc_key, {})
            incoming_acc = {
                "username": account.get("username", existing_acc.get("username", "")),
                "email": account.get("email", existing_acc.get("email", "")),
                "password_hash": account.get("password_hash", existing_acc.get("password_hash", "")),
                "created_at": existing_acc.get("created_at", time.time()),
                "updated_at": time.time(),
                "liked_ids": local_payload.get("likedIds", []),
                "playlists": local_payload.get("playlists", []),
                "drive_tracks": drive_tracks,
            }
            merged_acc = merge_account_records(existing_acc, incoming_acc) if existing_acc else incoming_acc
            db["accounts"][acc_key] = merged_acc
            local_payload["playlists"] = merged_acc.get("playlists", [])
            local_payload["likedIds"] = merged_acc.get("liked_ids", [])
            local_payload["drive_tracks"] = merged_acc.get("drive_tracks", [])

        ok, msg = upload_cloud_database(cfg, db_path, db)
        return local_payload, (None if ok else msg)
    except Exception as e:
        return local_payload, str(e)


class GDriveCloudPlaylistSyncWorker(QThread):
    sync_completed = pyqtSignal(dict)
    sync_failed = pyqtSignal(str)

    def __init__(
        self,
        token: str = "",
        payload: Optional[dict] = None,
        config: Optional[GoogleDriveConfig] = None,
        local_db_path: Optional[Path] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        raw_token = token or kwargs.get("access_token", "")
        self.token = str(raw_token).strip()
        self.payload = payload if payload is not None else kwargs.get("local_payload", {})
        self.config = config
        self.local_db_path = local_db_path

    def run(self) -> None:
        merged, err = sync_cloud_playlists_with_drive(
            self.token,
            self.payload,
            config=self.config,
            local_db_path=self.local_db_path,
        )
        if err:
            self.sync_failed.emit(err)
        else:
            self.sync_completed.emit(merged)
