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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Integración con el ecosistema MaoMusic
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from gdrive_sync import (
    DEFAULT_ROOT_DRIVE_FOLDER_ID,
    DEFAULT_MUSICA_FOLDER_ID,
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
        temp_work_dir: Optional[Path] = None,
    ) -> None:
        self.config = config or GoogleDriveConfig()
        self.rclone_video_remote = rclone_video_remote or os.environ.get("RCLONE_VIDEO_REMOTE")
        self.rclone_audio_remote = rclone_audio_remote or os.environ.get("RCLONE_AUDIO_REMOTE")
        self.temp_work_dir = temp_work_dir or Path(tempfile.gettempdir()) / "maomusic_worker"
        self.temp_work_dir.mkdir(parents=True, exist_ok=True)

        self._check_system_binaries()

    def _check_system_binaries(self) -> None:
        """Verifica dependencias fundamentales del sistema."""
        for binary in ("yt-dlp", "ffmpeg"):
            if not shutil.which(binary):
                logger.error(f"Binario requerido no encontrado en PATH: {binary}")
                raise RuntimeError(f"Falta herramienta de sistema: {binary}")
        if not shutil.which("rclone"):
            logger.info("Aviso: 'rclone' no está en PATH; se utilizará Google Drive API directa como fallback.")

    def fetch_and_extract(self, query: str) -> ProcessedMedia:
        """
        Descarga el video de YouTube en contenedor MP4 y deriva el audio en MP3 320kbps,
        minimizando I/O y evitando descargas dobles.
        """
        task_id = re.sub(r"[^a-zA-Z0-9_-]", "_", query)[:32] + f"_{os.getpid()}"
        work_dir = self.temp_work_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=True)

        search_query = query if query.startswith("http://") or query.startswith("https://") else f"ytsearch1:{query}"
        logger.info(f"Iniciando búsqueda y descarga para consulta: '{query}'")

        info_cmd = [
            "yt-dlp",
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

        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", clean_title).strip() or video_id
        video_output_template = str(work_dir / f"{safe_filename}.mp4")

        # 1. Descarga del video en MP4 (h264/avc1 + aac preferido para compatibilidad total con HTML5 y dispositivos)
        download_cmd = [
            "yt-dlp",
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--write-thumbnail",
            "--convert-thumbnails", "jpg",
            "-o", video_output_template,
            search_query,
        ]

        logger.info("Descargando contenedor de video en máxima calidad compatible...")
        dl_res = subprocess.run(download_cmd, capture_output=True, text=True)
        if dl_res.returncode != 0:
            logger.error(f"Error durante descarga de video yt-dlp: {dl_res.stderr}")
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"Error en yt-dlp al procesar {query}")

        video_path = Path(video_output_template)
        if not video_path.exists():
            # Buscar si yt-dlp añadió alguna extensión ligeramente distinta
            found = list(work_dir.glob(f"{safe_filename}*.mp4"))
            if found:
                video_path = found[0]
            else:
                shutil.rmtree(work_dir, ignore_errors=True)
                raise FileNotFoundError(f"No se generó el archivo de video esperado para {query}")

        thumb_candidates = list(work_dir.glob("*.jpg"))
        thumbnail_path = thumb_candidates[0] if thumb_candidates else None

        # 2. Extracción de audio en alta fidelidad (MP3 320k) directamente desde el video descargado
        audio_path = work_dir / f"{safe_filename}.mp3"
        logger.info("Extrayendo stream de audio a MP3 320kbps con metadatos incrustados...")

        ffmpeg_cmd = [
            "ffmpeg",
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
            raise RuntimeError(f"Error en ffmpeg al extraer audio para {query}")

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

    def sync_to_cloud(self, media: ProcessedMedia) -> dict:
        """
        Sincroniza video y audio a sus respectivas carpetas remotas en Google Drive.
        Usa rclone si está configurado, o la API de Google Drive / backend de MaoMusic.
        """
        results = {"video_synced": False, "audio_synced": False, "method": ""}

        # Intentar vía Rclone si los remotos están definidos
        if self.rclone_video_remote and self.rclone_audio_remote and shutil.which("rclone"):
            logger.info("Sincronizando mediante rclone a remotos configurados...")
            try:
                # 1. Subir video
                v_res = subprocess.run(
                    ["rclone", "copyto", str(media.video_path), f"{self.rclone_video_remote}/{media.video_path.name}"],
                    capture_output=True,
                    text=True,
                )
                if v_res.returncode == 0:
                    results["video_synced"] = True
                    logger.info(f"Video sincronizado exitosamente con rclone: {media.video_path.name}")
                else:
                    logger.warning(f"Rclone video error: {v_res.stderr}")

                # 2. Subir audio
                a_res = subprocess.run(
                    ["rclone", "copyto", str(media.audio_path), f"{self.rclone_audio_remote}/{media.audio_path.name}"],
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
            except Exception as exc:
                logger.warning(f"Excepción en rclone: {exc}. Evaluando fallback nativo.")

        # Fallback: API de Google Drive usando gdrive_sync
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

            results["audio_synced"] = _upload_file_to_drive(media.audio_path, musica_folder_id, "audio/mpeg")
            results["video_synced"] = _upload_file_to_drive(media.video_path, videos_folder_id, "video/mp4")
            results["method"] = "gdrive_api"
            return results

        logger.warning("No se cuenta con remotos de rclone ni Access Token OAuth para subida remota inmediata.")
        results["method"] = "local_only"
        return results

    def process_query(self, query: str, keep_local: bool = False, local_destination: Optional[Path] = None) -> dict:
        """
        Ejecuta el ciclo completo: Descarga -> Extracción -> Sincronización -> Limpieza.
        """
        media = None
        work_dir = None
        try:
            media = self.fetch_and_extract(query)
            work_dir = media.video_path.parent

            sync_status = self.sync_to_cloud(media)

            if keep_local:
                dest = local_destination or BASE_DIR / "music_library"
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(media.audio_path, dest / media.audio_path.name)
                logger.info(f"Copia local guardada en: {dest / media.audio_path.name}")

            return {
                "status": "success",
                "title": media.clean_title,
                "artist": media.artist,
                "duration": media.duration,
                "video_file": media.video_path.name,
                "audio_file": media.audio_path.name,
                "sync": sync_status,
            }
        except Exception as exc:
            logger.error(f"Fallo en procesamiento de '{query}': {exc}")
            return {"status": "error", "query": query, "error": str(exc)}
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
    parser.add_argument("--json", action="store_true", help="Salida estructurada en JSON")

    args = parser.parse_args()

    # Cargar token o config existente si existe en data/library.json
    store = LibraryStore(BASE_DIR)
    gdrive_cfg = store.gdrive_config

    worker = MediaDownloaderWorker(
        config=gdrive_cfg,
        rclone_video_remote=args.rclone_video,
        rclone_audio_remote=args.rclone_audio,
    )

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
