#!/usr/bin/env python3
"""
Módulo Autónomo de Descarga y Sincronización Multimedia (YouTube -> Google Drive).
Parte del ecosistema MaoMusic / SpotiLinux.

Funcionalidad:
1. Resuelve consultas vía YouTube (ytsearch1:).
2. Descarga video en contenedor MP4 optimizado.
3. Extrae stream de audio de alta fidelidad (MP3 320kbps) incrustando metadatos y portada vía ffmpeg.
4. Sincroniza video y audio hacia Google Drive mediante:
   - 'rclone' (si está configurado con remotos dedicados).
   - O API oficial de Google Drive v3 / OAuth2 token configurado en gdrive_sync.
5. Limpieza garantizada de archivos temporales en almacenamiento local sin residuos.
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Integración con el ecosistema MaoMusic
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from binaries import resolve_binary
from gdrive_sync import (
    DEFAULT_ROOT_DRIVE_FOLDER_ID,
    DEFAULT_MUSICA_FOLDER_ID,
    DEFAULT_DRIVE_SERVER_URL,
    GoogleDriveConfig,
    clean_track_title,
    normalize_track_key,
    resolve_musica_subfolder_id,
    resolve_videos_subfolder_id,
)
from storage import LibraryStore

# Configuración de Logging Estructurado
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [MediaWorker] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("MediaWorker")


def classify_error(err_str: str) -> str:
    """
    Clasifica un error como 'transient' (reintentable con backoff)
    o 'permanent' (fallo definitivo).
    """
    if not err_str:
        return "permanent"
    s = err_str.lower()
    transient_indicators = [
        "429",
        "too many requests",
        "500",
        "502",
        "503",
        "504",
        "timed out",
        "timeout",
        "unable to download webpage",
        "connection reset",
        "connection refused",
        "remotedisconnected",
        "network is unreachable",
        "temporary failure in name resolution",
        "name or service not known",
        "proxy error",
        "server disconnected",
        "try again",
    ]
    for ind in transient_indicators:
        if ind in s:
            return "transient"
    return "permanent"


@dataclass
class ProcessedMedia:
    query: str
    video_title: str
    clean_title: str
    artist: str
    video_path: Path
    audio_path: Path
    thumbnail_path: Optional[Path]
    duration: float
    video_id: str


class MediaDownloaderWorker:
    """
    Gestiona la búsqueda, descarga optimizada de video, extracción de audio y
    sincronización con Google Drive (Rclone o Drive API).
    """

    def __init__(
        self,
        config: Optional[GoogleDriveConfig] = None,
        rclone_video_remote: Optional[str] = None,
        rclone_audio_remote: Optional[str] = None,
        server_url: Optional[str] = None,
        temp_work_dir: Optional[Path] = None,
    ) -> None:
        self.config = config or GoogleDriveConfig()
        self.rclone_video_remote = rclone_video_remote or os.environ.get("RCLONE_VIDEO_REMOTE")
        self.rclone_audio_remote = rclone_audio_remote or os.environ.get("RCLONE_AUDIO_REMOTE")
        self.server_url = server_url or os.environ.get("MAOMUSIC_DRIVE_SERVER_URL") or DEFAULT_DRIVE_SERVER_URL
        self.temp_work_dir = temp_work_dir or Path(tempfile.gettempdir()) / "maomusic_worker"
        self.temp_work_dir.mkdir(parents=True, exist_ok=True)

        self._check_system_binaries()

    def missing_binaries(self) -> list[str]:
        """Devuelve una lista de binarios obligatorios que no están presentes en el sistema."""
        missing = []
        if not resolve_binary("yt-dlp"):
            missing.append("yt-dlp")
        if not resolve_binary("ffmpeg"):
            missing.append("ffmpeg")
        return missing

    def _check_system_binaries(self) -> None:
        """Verifica dependencias fundamentales del sistema sin abortar la construcción."""
        self.ytdlp_bin = resolve_binary("yt-dlp")
        self.ffmpeg_bin = resolve_binary("ffmpeg")
        self.rclone_bin = resolve_binary("rclone")

        if not self.ytdlp_bin:
            logger.warning("Binario no encontrado al inicializar: yt-dlp")
        if not self.ffmpeg_bin:
            logger.warning("Binario no encontrado al inicializar: ffmpeg")
        if not self.rclone_bin:
            logger.info("Aviso: 'rclone' no está en PATH; se utilizará el Proxy Serverless de Google Drive como método principal.")

    def fetch_and_extract(
        self,
        query: str,
        progress_cb: Optional[Callable[[str, float, str], None]] = None,
        cancel_event: Optional[Any] = None,
    ) -> ProcessedMedia:
        """
        Descarga el video de YouTube en contenedor MP4 y deriva el audio en MP3 320kbps,
        minimizando I/O y reportando progreso granular con soporte de cancelación inmediata.
        """
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Operación cancelada por el usuario")

        task_id = re.sub(r"[^a-zA-Z0-9_-]", "_", query)[:32] + f"_{os.getpid()}_{int(time.time()*1000)}"
        work_dir = self.temp_work_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=True)

        if progress_cb:
            progress_cb("search", 2.0, f"Buscando en YouTube: '{query}'")

        search_query = query if query.startswith("http://") or query.startswith("https://") else f"ytsearch1:{query}"
        logger.info(f"Iniciando búsqueda y descarga para consulta: '{query}'")

        info_cmd = [
            self.ytdlp_bin or "yt-dlp",
            "--dump-json",
            "--default-search", "ytsearch",
            "--no-playlist",
            search_query,
        ]

        try:
            res = subprocess.run(info_cmd, capture_output=True, text=True, check=True)
            meta = json.loads(res.stdout.strip().split("\n")[0])
        except Exception as exc:
            logger.error(f"Error al resolver metadata para '{query}': {exc}")
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"Fallo en búsqueda de YouTube para: {query}") from exc

        if cancel_event and cancel_event.is_set():
            shutil.rmtree(work_dir, ignore_errors=True)
            raise InterruptedError("Operación cancelada por el usuario")

        raw_title = meta.get("title", query)
        video_id = meta.get("id", "video")
        duration = float(meta.get("duration", 0.0))
        channel = meta.get("uploader", "") or meta.get("channel", "")

        clean_title = clean_track_title(raw_title)
        artist_guess = channel
        final_track_title = clean_title
        if " - " in clean_title:
            parts = clean_title.split(" - ", 1)
            artist_guess = parts[0].strip()
            final_track_title = parts[1].strip()

        logger.info(f"Resolución de pista: '{final_track_title}' por '{artist_guess}' (Duración: {duration}s)")
        if progress_cb:
            progress_cb("download", 10.0, f"Descargando video: {clean_title}")

        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", clean_title).strip() or video_id
        video_output_template = str(work_dir / f"{safe_filename}.mp4")

        # 1. Descarga del video en MP4 (h264/avc1 + aac preferido para compatibilidad total con HTML5 y dispositivos)
        download_cmd = [
            self.ytdlp_bin or "yt-dlp",
            "--format", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--write-thumbnail",
            "--convert-thumbnails", "jpg",
            "--newline",
            "--progress-template", "download:%(progress._percent_str)s",
            "-o", video_output_template,
            search_query,
        ]

        logger.info("Descargando contenedor de video en máxima calidad compatible...")
        proc = subprocess.Popen(
            download_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        dl_output = []
        try:
            if proc.stdout:
                for line in proc.stdout:
                    dl_output.append(line)
                    if cancel_event and cancel_event.is_set():
                        proc.terminate()
                        try:
                            proc.wait(timeout=3.0)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        shutil.rmtree(work_dir, ignore_errors=True)
                        raise InterruptedError("Operación cancelada por el usuario durante la descarga")

                    m = re.search(r"download:\s*([0-9.]+)%", line)
                    if m and progress_cb:
                        try:
                            pct_val = float(m.group(1))
                            scaled = 10.0 + (pct_val * 0.60)
                            progress_cb("download", scaled, f"Descargando: {pct_val:.0f}%")
                        except ValueError:
                            pass
            proc.wait()
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            raise

        if proc.returncode != 0:
            err_tail = "".join(dl_output[-10:])
            logger.error(f"Error durante descarga de video yt-dlp: {err_tail}")
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"Error en yt-dlp al procesar {query}: {err_tail}")

        video_path = Path(video_output_template)
        if not video_path.exists():
            found = list(work_dir.glob(f"{safe_filename}*.mp4"))
            if found:
                video_path = found[0]
            else:
                shutil.rmtree(work_dir, ignore_errors=True)
                raise FileNotFoundError(f"No se generó el archivo de video esperado para {query}")

        if cancel_event and cancel_event.is_set():
            shutil.rmtree(work_dir, ignore_errors=True)
            raise InterruptedError("Operación cancelada por el usuario")

        # Optimización preventiva si el video supera los 35MB para streaming móvil y límites de carga
        if video_path.stat().st_size > 35 * 1024 * 1024:
            size_mb = video_path.stat().st_size / (1024 * 1024)
            logger.info(f"Video descargado ({size_mb:.1f}MB) excede 35MB; optimizando a 720p H.264...")
            opt_video_path = work_dir / f"opt_{safe_filename}.mp4"
            opt_cmd = [
                self.ffmpeg_bin or "ffmpeg", "-y", "-i", str(video_path),
                "-vf", "scale='min(1280,iw)':-2",
                "-c:v", "libx264", "-crf", "26", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k",
                str(opt_video_path),
            ]
            opt_res = subprocess.run(opt_cmd, capture_output=True, text=True)
            if opt_res.returncode == 0 and opt_video_path.exists():
                video_path.unlink(missing_ok=True)
                video_path = opt_video_path
                logger.info(f"✔ Video optimizado con éxito a {video_path.stat().st_size / (1024*1024):.1f}MB")

        thumb_candidates = (
            list(work_dir.glob("*.jpg"))
            + list(work_dir.glob("*.jpeg"))
            + list(work_dir.glob("*.png"))
            + list(work_dir.glob("*.webp"))
        )
        thumbnail_path = None
        if thumb_candidates:
            raw_thumb = thumb_candidates[0]
            if raw_thumb.suffix.lower() == ".jpg":
                thumbnail_path = raw_thumb
            else:
                converted_jpg = work_dir / f"{safe_filename}_cover.jpg"
                try:
                    from PIL import Image
                    with Image.open(raw_thumb) as im:
                        im.convert("RGB").save(converted_jpg, format="JPEG", quality=92)
                    thumbnail_path = converted_jpg
                except Exception:
                    thumbnail_path = raw_thumb

        # Incrustar carátula en el contenedor de video MP4 si está disponible
        if thumbnail_path and thumbnail_path.exists():
            cov_video = work_dir / f"cov_{safe_filename}.mp4"
            embed_v_cmd = [
                self.ffmpeg_bin or "ffmpeg",
                "-y",
                "-i", str(video_path),
                "-i", str(thumbnail_path),
                "-map", "0",
                "-map", "1",
                "-c", "copy",
                "-disposition:v:1", "attached_pic",
                str(cov_video),
            ]
            v_res = subprocess.run(embed_v_cmd, capture_output=True, text=True)
            if v_res.returncode == 0 and cov_video.exists():
                video_path.unlink(missing_ok=True)
                video_path = cov_video
                logger.info("✔ Carátula incrustada con éxito en el contenedor de video MP4.")

        # 2. Extracción de audio en alta fidelidad (MP3 320k) directamente desde el video descargado con carátula incrustada
        if progress_cb:
            progress_cb("convert", 72.0, "Extrayendo audio MP3 320kbps con carátula y metadatos incrustados...")

        audio_path = work_dir / f"{safe_filename}.mp3"
        logger.info("Extrayendo stream de audio a MP3 320kbps con carátula y metadatos incrustados...")

        if thumbnail_path and thumbnail_path.exists():
            ffmpeg_cmd = [
                self.ffmpeg_bin or "ffmpeg",
                "-y",
                "-i", str(video_path),
                "-i", str(thumbnail_path),
                "-map", "0:a",
                "-map", "1:0",
                "-c:a", "libmp3lame",
                "-b:a", "320k",
                "-c:v", "mjpeg",
                "-id3v2_version", "3",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)",
                "-metadata", f"title={final_track_title}",
                "-metadata", f"artist={artist_guess}",
                "-metadata", "album=MaoMusic Cloud Library",
                "-metadata", "comment=MaoMusic Media Sync Worker",
                str(audio_path),
            ]
        else:
            ffmpeg_cmd = [
                self.ffmpeg_bin or "ffmpeg",
                "-y",
                "-i", str(video_path),
                "-vn",
                "-acodec", "libmp3lame",
                "-b:a", "320k",
                "-metadata", f"title={final_track_title}",
                "-metadata", f"artist={artist_guess}",
                "-metadata", "album=MaoMusic Cloud Library",
                "-metadata", "comment=MaoMusic Media Sync Worker",
                str(audio_path),
            ]

        ff_res = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if ff_res.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size < 1024:
            logger.error(f"Fallo al extraer audio vía ffmpeg: {ff_res.stderr}")
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"Error en ffmpeg al extraer audio para {query}: {ff_res.stderr}")

        if progress_cb:
            progress_cb("convert", 84.0, "Audio MP3 con carátula incrustada extraído con éxito")

        return ProcessedMedia(
            query=query,
            video_title=raw_title,
            clean_title=clean_title,
            artist=artist_guess,
            video_path=video_path,
            audio_path=audio_path,
            thumbnail_path=thumbnail_path,
            duration=duration,
            video_id=video_id,
        )

    def _upload_to_serverless_proxy(self, file_path: Path, folder_type: str, mime_type: str) -> Optional[dict]:
        """Sube un archivo directamente a Google Drive a través del backend Google Apps Script sin requerir OAuth."""
        if not self.server_url or not file_path.exists():
            return None
        import base64
        import requests

        try:
            size_mb = file_path.stat().st_size / (1024 * 1024)
            logger.info(f"Subiendo {file_path.name} ({size_mb:.2f}MB) a Google Drive ({folder_type}) vía proxy serverless...")
            encoded_data = base64.b64encode(file_path.read_bytes()).decode("utf-8")
            payload = {
                "action": "upload_file",
                "folder_type": folder_type,
                "filename": file_path.name,
                "mime_type": mime_type,
                "file_data": encoded_data,
            }
            resp = requests.post(
                self.server_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=180,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok":
                    logger.info(f"✔ {file_path.name} sincronizado exitosamente en Google Drive: ID={data.get('file_id')}")
                    return data
                else:
                    logger.warning(f"Respuesta del proxy serverless: {data}")
            else:
                logger.warning(f"Error HTTP {resp.status_code} desde proxy serverless: {resp.text[:200]}")
        except Exception as exc:
            logger.warning(f"Excepción al subir mediante proxy serverless: {exc}")
        return None

    def sync_to_cloud(
        self,
        media: ProcessedMedia,
        progress_cb: Optional[Callable[[str, float, str], None]] = None,
        cancel_event: Optional[Any] = None,
    ) -> dict:
        """
        Sincroniza video y audio a sus respectivas carpetas remotas en Google Drive.
        Usa rclone si está configurado, o el Proxy Serverless de Google Apps Script (cero configuración),
        o la API oficial de Google Drive v3 con OAuth.
        """
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Operación cancelada por el usuario")

        results = {"video_synced": False, "audio_synced": False, "method": ""}

        # 1. Intentar vía Rclone si los remotos están definidos
        rclone_exe = self.rclone_bin or resolve_binary("rclone")
        if self.rclone_video_remote and self.rclone_audio_remote and rclone_exe:
            logger.info("Sincronizando mediante rclone a remotos configurados...")
            try:
                if progress_cb:
                    progress_cb("upload", 86.0, "Sincronizando video con rclone...")
                # Subir video
                v_res = subprocess.run(
                    [rclone_exe, "copyto", str(media.video_path), f"{self.rclone_video_remote}/{media.video_path.name}"],
                    capture_output=True,
                    text=True,
                )
                if v_res.returncode == 0:
                    results["video_synced"] = True
                    logger.info(f"Video sincronizado exitosamente con rclone: {media.video_path.name}")
                else:
                    logger.warning(f"Rclone video error: {v_res.stderr}")

                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Operación cancelada por el usuario")

                if progress_cb:
                    progress_cb("upload", 94.0, "Sincronizando audio con rclone...")
                # Subir audio
                a_res = subprocess.run(
                    [rclone_exe, "copyto", str(media.audio_path), f"{self.rclone_audio_remote}/{media.audio_path.name}"],
                    capture_output=True,
                    text=True,
                )
                if a_res.returncode == 0:
                    results["audio_synced"] = True
                    logger.info(f"Audio sincronizado exitosamente con rclone: {media.audio_path.name}")
                else:
                    logger.warning(f"Rclone audio error: {a_res.stderr}")

                if results["video_synced"] or results["audio_synced"]:
                    results["method"] = "rclone"
                    return results
            except InterruptedError:
                raise
            except Exception as exc:
                logger.warning(f"Excepción en rclone: {exc}. Evaluando fallback.")

        # 2. Intentar vía Proxy Serverless (Google Apps Script - Cero configuración)
        if self.server_url:
            logger.info("Sincronizando mediante Proxy Serverless de Google Drive...")
            if progress_cb:
                progress_cb("upload", 86.0, "Sincronizando audio vía Proxy Serverless...")
            audio_info = self._upload_to_serverless_proxy(media.audio_path, folder_type="audio", mime_type="audio/mpeg")
            if audio_info:
                results["audio_synced"] = True
                results["audio_file_id"] = audio_info.get("file_id")

            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Operación cancelada por el usuario")

            # Video: solo si no excede 38MB para no sobrepasar el límite de payload
            if media.video_path.stat().st_size <= 38 * 1024 * 1024:
                if progress_cb:
                    progress_cb("upload", 94.0, "Sincronizando video vía Proxy Serverless...")
                video_info = self._upload_to_serverless_proxy(media.video_path, folder_type="video", mime_type="video/mp4")
                if video_info:
                    results["video_synced"] = True
                    results["video_file_id"] = video_info.get("file_id")
            else:
                logger.warning(f"Video {media.video_path.name} ({media.video_path.stat().st_size / (1024*1024):.1f}MB) supera 38MB; omitido de subida por proxy serverless.")

            if results["audio_synced"] or results["video_synced"]:
                results["method"] = "serverless_proxy"
                return results

        # 3. Fallback: API oficial de Google Drive v3 usando gdrive_sync
        if self.config.access_token:
            logger.info("Sincronizando mediante API oficial de Google Drive v3...")
            import requests

            musica_folder_id, _ = resolve_musica_subfolder_id(self.config)
            videos_res = resolve_videos_subfolder_id(self.config)
            videos_folder_id = videos_res[0] if videos_res else "1tKyv8TEFVGP0f-r8ohMeDj5lz3OC-bsa"

            headers = {"Authorization": f"Bearer {self.config.access_token}"}

            def _upload_file_to_drive(file_path: Path, parent_id: str, mime: str) -> bool:
                try:
                    boundary = "-------MaoMusicWorkerBoundary"
                    metadata = {"name": file_path.name, "parents": [parent_id]}
                    body = (
                        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                        f"{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
                    ).encode("utf-8") + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")

                    r = requests.post(
                        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
                        headers={"Authorization": f"Bearer {self.config.access_token}", "Content-Type": f"multipart/related; boundary={boundary}"},
                        data=body,
                        timeout=120,
                    )
                    return r.status_code in (200, 201)
                except Exception as e:
                    logger.error(f"Error subiendo {file_path.name} a Drive: {e}")
                    return False

            if progress_cb:
                progress_cb("upload", 86.0, "Subiendo audio a Google Drive...")
            results["audio_synced"] = _upload_file_to_drive(media.audio_path, musica_folder_id, "audio/mpeg")

            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Operación cancelada por el usuario")

            if progress_cb:
                progress_cb("upload", 94.0, "Subiendo video a Google Drive...")
            results["video_synced"] = _upload_file_to_drive(media.video_path, videos_folder_id, "video/mp4")
            results["method"] = "gdrive_api"
            return results

        logger.warning("No se cuenta con remotos de rclone, proxy serverless funcional ni Access Token OAuth para subida remota.")
        results["method"] = "local_only"
        return results

    def process_query(
        self,
        query: str,
        keep_local: bool = False,
        local_destination: Optional[Path] = None,
        progress_cb: Optional[Callable[[str, float, str], None]] = None,
        cancel_event: Optional[Any] = None,
    ) -> dict:
        """
        Ejecuta el ciclo completo: Descarga -> Extracción -> Sincronización -> Limpieza.
        Soporta reporte de progreso continuo y cancelación cooperativa por hilo.
        """
        missing = self.missing_binaries()
        if missing:
            err_msg = f"Faltan dependencias de sistema requeridas: {', '.join(missing)}"
            logger.error(err_msg)
            return {
                "status": "error",
                "query": query,
                "error": err_msg,
                "error_kind": "permanent",
            }

        if cancel_event and cancel_event.is_set():
            return {"status": "cancelled", "query": query, "error": "Cancelado por el usuario"}

        media = None
        work_dir = None
        try:
            media = self.fetch_and_extract(query, progress_cb=progress_cb, cancel_event=cancel_event)
            work_dir = media.video_path.parent

            sync_status = self.sync_to_cloud(media, progress_cb=progress_cb, cancel_event=cancel_event)

            if keep_local:
                dest = local_destination or BASE_DIR / "music_library"
                dest.mkdir(parents=True, exist_ok=True)
                dest_audio = dest / media.audio_path.name
                shutil.copy2(media.audio_path, dest_audio)
                logger.info(f"Copia local de audio guardada en: {dest_audio}")

                # Guardar copia local del video descargado en music_library/videos/ para sincronización con modo vinilo
                if media.video_path and media.video_path.exists():
                    v_dir = dest / "videos"
                    v_dir.mkdir(parents=True, exist_ok=True)
                    dest_video = v_dir / media.video_path.name
                    shutil.copy2(media.video_path, dest_video)
                    logger.info(f"Copia local de video guardada en: {dest_video}")

                # Guardar carátula en archivo .jpg junto a la canción y en la caché de carátulas
                if media.thumbnail_path and media.thumbnail_path.exists():
                    dest_cover = dest / f"{media.audio_path.stem}.jpg"
                    shutil.copy2(media.thumbnail_path, dest_cover)
                    try:
                        from metadata import compute_track_id
                        tid = compute_track_id(dest_audio)
                        covers_cache_dir = BASE_DIR / "data" / "covers_cache"
                        covers_cache_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(media.thumbnail_path, covers_cache_dir / f"{tid}.jpg")
                    except Exception as e:
                        logger.warning(f"No se pudo indexar carátula en caché: {e}")
                    logger.info(f"Carátula guardada en: {dest_cover}")

            if progress_cb:
                progress_cb("done", 100.0, "Descarga y sincronización completadas")

            return {
                "status": "success",
                "title": media.clean_title,
                "artist": media.artist,
                "duration": media.duration,
                "video_file": media.video_path.name,
                "audio_file": media.audio_path.name,
                "sync": sync_status,
            }
        except (InterruptedError, KeyboardInterrupt):
            logger.info(f"Procesamiento cancelado para '{query}'")
            return {"status": "cancelled", "query": query, "error": "Cancelado por el usuario"}
        except Exception as exc:
            logger.error(f"Fallo en procesamiento de '{query}': {exc}")
            err_str = str(exc)
            return {
                "status": "error",
                "query": query,
                "error": err_str,
                "error_kind": classify_error(err_str),
            }
        finally:
            if work_dir and work_dir.exists():
                logger.info("Limpiando archivos temporales locales (Zero-Waste I/O)...")
                shutil.rmtree(work_dir, ignore_errors=True)

    def process_batch_file(self, batch_file: Path) -> list[dict]:
        """Procesa una lista de consultas desde un archivo de texto (una por línea)."""
        if not batch_file.exists():
            raise FileNotFoundError(f"Archivo batch no encontrado: {batch_file}")

        queries = [line.strip() for line in batch_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
        logger.info(f"Iniciando procesamiento por lotes ({len(queries)} consultas)...")
        results = []
        for idx, q in enumerate(queries, 1):
            logger.info(f"[{idx}/{len(queries)}] Procesando: '{q}'")
            res = self.process_query(q)
            results.append(res)
        return results


def main():
    parser = argparse.ArgumentParser(description="MaoMusic Autonomous YouTube-to-Drive Sync Worker")
    parser.add_argument("query", nargs="?", help="Nombre o enlace de la canción / video a procesar")
    parser.add_argument("--batch", "-b", type=Path, help="Ruta a archivo de texto con lista de canciones por lotes")
    parser.add_argument("--keep-local", "-k", action="store_true", help="Mantener una copia en music_library/")
    parser.add_argument("--rclone-video", help="Remoto rclone para videos (ej: gdrive:videos)")
    parser.add_argument("--rclone-audio", help="Remoto rclone para audios (ej: gdrive:Musica)")
    parser.add_argument("--server-url", help="URL del endpoint Web App de Google Apps Script")
    parser.add_argument("--json", action="store_true", help="Salida estructurada en JSON")

    # Comandos de cola persistente (Fase 3)
    parser.add_argument("--queue-add", nargs="+", help="Agregar una o más consultas a la cola de descargas")
    parser.add_argument("--queue-import", type=Path, help="Importar archivo de texto (.txt) a la cola")
    parser.add_argument("--queue-list", action="store_true", help="Mostrar estado actual de la cola de descargas")
    parser.add_argument("--queue-run", action="store_true", help="Ejecutar y procesar la cola en primer plano hasta vaciarla")
    parser.add_argument("--queue-retry-failed", action="store_true", help="Reintentar todas las canciones fallidas en la cola")
    parser.add_argument("--queue-clear", action="store_true", help="Limpiar canciones terminadas o canceladas de la cola")
    parser.add_argument("--parallel", type=int, default=1, choices=[1, 2, 3], help="Descargas simultáneas (1 a 3, default: 1)")

    args = parser.parse_args()

    # Manejo de comandos de cola sin inicializar worker
    if args.queue_add:
        from download_queue import DownloadQueue
        queue = DownloadQueue()
        added, skipped = queue.add(args.queue_add, keep_local=True)
        print(f"✔ Encolados: {added} | Omitidos por duplicado: {skipped}")
        return

    if args.queue_import:
        from download_queue import DownloadQueue
        if not args.queue_import.exists():
            print(f"❌ Archivo no encontrado: {args.queue_import}", file=sys.stderr)
            sys.exit(1)
        lines = args.queue_import.read_text(encoding="utf-8").splitlines()
        queue = DownloadQueue()
        added, skipped = queue.add(lines, keep_local=True)
        print(f"✔ Importados: {added} | Omitidos por duplicado: {skipped}")
        return

    if args.queue_list:
        from download_queue import DownloadQueue
        queue = DownloadQueue()
        items = queue.list()
        if not items:
            print("La cola de descargas está vacía.")
        else:
            print(f"{'ID':<4} {'Estado':<11} {'Etapa':<15} {'Progreso':<10} {'Consulta':<30} {'Error'}")
            print("-" * 80)
            for it in items:
                err = (it.error[:25] + "...") if len(it.error) > 25 else it.error
                print(f"{it.id:<4} {it.status:<11} {it.stage:<15} {it.progress:>5.1f}%     {it.query[:28]:<30} {err}")
        return

    if args.queue_retry_failed:
        from download_queue import DownloadQueue
        queue = DownloadQueue()
        count = queue.retry_failed()
        print(f"✔ Se reencolaron {count} elementos con error o cancelados.")
        return

    if args.queue_clear:
        from download_queue import DownloadQueue
        queue = DownloadQueue()
        count = queue.clear_finished()
        print(f"✔ Se eliminaron {count} elementos terminados o cancelados.")
        return

    # Cargar token o config existente si existe en data/library.json
    store = LibraryStore(BASE_DIR)
    gdrive_cfg = store.gdrive_config

    worker = MediaDownloaderWorker(
        config=gdrive_cfg,
        rclone_video_remote=args.rclone_video,
        rclone_audio_remote=args.rclone_audio,
        server_url=args.server_url,
    )

    if args.queue_run:
        from download_queue import DownloadQueue, QueueRunner
        queue = DownloadQueue()
        print(f"Iniciando ejecutor de cola (concurrencia: {args.parallel}). Presione Ctrl+C para detener.")

        def on_event(ev):
            t = ev.get("type")
            if t == "item_started":
                print(f"▶ [{ev.get('item_id')}] Iniciando: {ev.get('query')}")
            elif t == "item_progress":
                print(f"  [{ev.get('item_id')}] {ev.get('stage')}: {ev.get('progress'):.1f}% - {ev.get('message', '')}")
            elif t == "item_done":
                print(f"✔ [{ev.get('item_id')}] Completado: {ev.get('result', {}).get('title')}")
            elif t == "item_error":
                print(f"❌ [{ev.get('item_id')}] Error (transient={ev.get('transient')}): {ev.get('error')}")
            elif t == "item_cancelled":
                print(f"⏹ [{ev.get('item_id')}] Cancelado")

        runner = QueueRunner(
            queue=queue,
            downloader_factory=lambda: worker,
            on_event=on_event,
            max_parallel=args.parallel,
        )
        runner.start()
        try:
            while True:
                active_pending = [it for it in queue.list() if it.status in ("pending", "running")]
                if not active_pending:
                    print("✔ Cola completada. No hay más tareas pendientes.")
                    break
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nDeteniendo ejecutor de cola...")
        finally:
            runner.stop()
        return

    if args.batch:
        results = worker.process_batch_file(args.batch)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            successes = sum(1 for r in results if r.get("status") == "success")
            print(f"✔ Procesamiento por lotes finalizado: {successes}/{len(results)} exitosos.")
    elif args.query:
        result = worker.process_query(args.query, keep_local=args.keep_local)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            if result.get("status") == "success":
                print(f"✔ Completado exitosamente: {result['title']} | Audio: {result['audio_file']} | Video: {result['video_file']}")
                print(f"  Sincronización Cloud: {result['sync']}")
            else:
                print(f"❌ Error al procesar: {result.get('error')}", file=sys.stderr)
                sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
