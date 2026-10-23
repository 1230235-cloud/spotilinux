#!/usr/bin/env python3
"""
Motor de audio con PyQt6.QtMultimedia (QMediaPlayer + QAudioOutput),
análisis FFT y forma de onda en tiempo real con NumPy + FFmpeg,
ecualizador paramétrico de 5 bandas y gestión de cola / Shuffle / Repeat.
"""

import hashlib
import random
import subprocess
import time
from pathlib import Path
from typing import Optional
import numpy as np
from PyQt6.QtCore import QObject, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from metadata import TrackMetadata
from rhythm import SpectrumAnalyzer, analyze_rhythm, beat_position

EQ_BANDS = [
    {"freq": 60, "label": "60 Hz", "width": 50},
    {"freq": 230, "label": "230 Hz", "width": 150},
    {"freq": 910, "label": "910 Hz", "width": 500},
    {"freq": 3600, "label": "3.6 kHz", "width": 1800},
    {"freq": 14000, "label": "14 kHz", "width": 5000},
]

EQ_PRESETS: dict[str, list[float]] = {
    "Plano": [0.0, 0.0, 0.0, 0.0, 0.0],
    "Bass Boost": [7.5, 5.0, 0.0, -1.0, 1.5],
    "Electrónica": [6.0, 3.5, -1.5, 3.0, 5.5],
    "Rock": [5.0, 3.0, -2.0, 3.5, 5.0],
    "Vocal / Podcast": [-2.5, 1.5, 5.5, 4.0, 1.0],
    "Acústico": [3.0, 2.0, 1.0, 3.5, 4.0],
    "Lo-Fi Nocturno": [4.5, 3.0, 1.0, -4.5, -7.5],
}


class PCMAnalyzerWorker(QThread):
    """Decodifica el archivo de audio a PCM mono ligero (11025 Hz) en segundo plano para FFT exacta."""

    finished_pcm = pyqtSignal(str, object, int, object)  # id, samples, sample_rate, rhythm(dict)

    def __init__(self, track_id: str, filepath: str, sample_rate: int = 22050) -> None:
        super().__init__()
        self.track_id = track_id
        self.filepath = filepath
        self.sample_rate = sample_rate

    def run(self) -> None:
        try:
            cmd = [
                "ffmpeg",
                "-v", "quiet",
                "-i", self.filepath,
                "-vn",
                "-ac", "1",
                "-ar", str(self.sample_rate),
                "-f", "s16le",
                "-",
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=25)
            if res.returncode == 0 and res.stdout:
                samples = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
                try:
                    rhythm = analyze_rhythm(samples, self.sample_rate)
                except Exception:
                    rhythm = {"bpm": 0.0, "confidence": 0.0, "beat_times": np.zeros(0), "env": np.zeros(1), "fps": 86.0}
                self.finished_pcm.emit(self.track_id, samples, self.sample_rate, rhythm)
        except Exception:
            pass


class EQRenderWorker(QThread):
    """Aplica el filtro ecualizador de 5 bandas con FFmpeg en segundo plano sin bloquear la UI."""

    finished_eq = pyqtSignal(str, str, int)

    def __init__(self, track_id: str, src_path: str, out_path: str, gains: list[float], resume_pos_ms: int) -> None:
        super().__init__()
        self.track_id = track_id
        self.src_path = src_path
        self.out_path = out_path
        self.gains = gains
        self.resume_pos_ms = resume_pos_ms

    def run(self) -> None:
        try:
            filters = []
            for band, gain in zip(EQ_BANDS, self.gains):
                if abs(gain) >= 0.2:
                    filters.append(
                        f"equalizer=f={band['freq']}:width_type=h:width={band['width']}:g={gain:.1f}"
                    )
            if not filters:
                self.finished_eq.emit(self.track_id, self.src_path, self.resume_pos_ms)
                return

            af_chain = ",".join(filters)
            cmd = [
                "ffmpeg",
                "-y",
                "-v", "quiet",
                "-i", self.src_path,
                "-vn",
                "-af", af_chain,
                "-c:a", "libmp3lame",
                "-q:a", "3",
                self.out_path,
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True)
            if Path(self.out_path).exists():
                self.finished_eq.emit(self.track_id, self.out_path, self.resume_pos_ms)
        except Exception:
            pass


class AudioEngine(QObject):
    track_changed = pyqtSignal(object)          # TrackMetadata | None
    state_changed = pyqtSignal(bool)            # True = playing, False = paused/stopped
    position_changed = pyqtSignal(int, int)     # pos_ms, duration_ms
    queue_changed = pyqtSignal()
    spectrum_updated = pyqtSignal(object, object)  # (spectrum_32_bars, waveform_128_pts)
    beat_info = pyqtSignal(float, float, float, float)  # (bpm, beat_pos, confianza, onset 0..1)
    eq_status_changed = pyqtSignal(str)

    def __init__(self, eq_cache_dir: Path) -> None:
        super().__init__()
        self.eq_cache_dir = eq_cache_dir
        self.eq_cache_dir.mkdir(parents=True, exist_ok=True)

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        self.current_track: Optional[TrackMetadata] = None
        self.context_tracks: list[TrackMetadata] = []
        self.user_queue: list[TrackMetadata] = []
        self.history: list[TrackMetadata] = []

        self.shuffle: bool = False
        self.repeat_mode: str = "off"  # "off" | "all" | "one"

        self.eq_enabled: bool = True
        self.eq_gains: list[float] = [0.0, 0.0, 0.0, 0.0, 0.0]

        # Datos PCM en memoria para análisis FFT real a 60 FPS
        self._pcm_samples: Optional[np.ndarray] = None
        self._pcm_sr: int = 22050
        self._smooth_bars = np.zeros(32, dtype=np.float32)
        self._analyzer = SpectrumAnalyzer(32)
        self._rhythm: Optional[dict] = None
        self._onset_smooth: float = 0.0
        # Posición interpolada (QMediaPlayer actualiza la posición a saltos de ~50-100 ms)
        self._pos_ref_ms: float = 0.0
        self._pos_ref_wall: float = 0.0
        self._last_raw_pos: int = -1
        self.sync_offset_ms: float = 45.0  # compensa la latencia de salida de audio
        self._pcm_worker: Optional[PCMAnalyzerWorker] = None
        self._eq_worker: Optional[EQRenderWorker] = None
        self._pending_seek_ms: int = -1

        # Conectar señales de QMediaPlayer
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        # Timer de 60 FPS para el visualizador de espectro y onda
        self.vis_timer = QTimer(self)
        self.vis_timer.setInterval(22)
        self.vis_timer.timeout.connect(self._update_visualizer_frame)
        self.vis_timer.start()

    # -------------------- Controles de reproducción -------------------- #

    def play_track(
        self,
        track: TrackMetadata,
        context_list: Optional[list[TrackMetadata]] = None,
        record_history: bool = True,
    ) -> None:
        if self.current_track and record_history and self.current_track.id != track.id:
            self.history.append(self.current_track)
            if len(self.history) > 50:
                self.history.pop(0)

        if context_list is not None:
            self.context_tracks = list(context_list)

        self.current_track = track
        self._pcm_samples = None
        self._rhythm = None
        self._analyzer.reset()
        self._last_raw_pos = -1
        self._pending_seek_ms = -1

        # Iniciar decodificación PCM en segundo plano para el visualizador FFT
        self._pcm_worker = PCMAnalyzerWorker(track.id, track.filepath)
        self._pcm_worker.finished_pcm.connect(self._on_pcm_ready)
        self._pcm_worker.start()

        # Reproducir archivo original inmediatamente y si hay EQ activo renderizar versión filtrada
        play_path = self._get_or_schedule_eq_file(track, resume_pos_ms=0)
        self.player.setSource(QUrl.fromLocalFile(play_path))
        self.player.play()

        self.track_changed.emit(track)
        self.queue_changed.emit()

    def toggle_play_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.current_track is None and self.context_tracks:
                self.play_track(self.context_tracks[0], self.context_tracks)
            else:
                self.player.play()

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def seek(self, position_ms: int) -> None:
        self.player.setPosition(max(0, int(position_ms)))

    def set_volume(self, volume_percent: int) -> None:
        vol = max(0, min(100, int(volume_percent)))
        # Curva perceptual suave
        self.audio_output.setVolume((vol / 100.0) ** 1.5)

    def next_track(self, auto_triggered: bool = False) -> None:
        if auto_triggered and self.repeat_mode == "one" and self.current_track:
            self.player.setPosition(0)
            self.player.play()
            return

        # 1. Priorizar cola manual del usuario
        if self.user_queue:
            nxt = self.user_queue.pop(0)
            self.play_track(nxt, None)
            return

        if not self.context_tracks:
            if auto_triggered:
                self.player.stop()
            return

        if self.shuffle and len(self.context_tracks) > 1:
            candidates = [t for t in self.context_tracks if not self.current_track or t.id != self.current_track.id]
            nxt = random.choice(candidates) if candidates else self.context_tracks[0]
            self.play_track(nxt, None)
            return

        # Orden secuencial
        idx = -1
        if self.current_track:
            for i, t in enumerate(self.context_tracks):
                if t.id == self.current_track.id:
                    idx = i
                    break

        next_idx = idx + 1
        if next_idx < len(self.context_tracks):
            self.play_track(self.context_tracks[next_idx], None)
        elif self.repeat_mode == "all" and self.context_tracks:
            self.play_track(self.context_tracks[0], None)
        elif not auto_triggered and self.context_tracks:
            self.play_track(self.context_tracks[0], None)

    def previous_track(self) -> None:
        # Si lleva más de 3 segundos, reiniciar la canción actual
        if self.player.position() > 3200:
            self.player.setPosition(0)
            return

        if self.history:
            ctx_ids = {t.id for t in self.context_tracks}
            # Descartar entradas del historial que no pertenecen a la playlist/cola activa
            while self.history and ctx_ids and self.history[-1].id not in ctx_ids:
                self.history.pop()
        if self.history:
            prev = self.history.pop()
            self.play_track(prev, None, record_history=False)
            return

        if self.context_tracks and self.current_track:
            idx = 0
            for i, t in enumerate(self.context_tracks):
                if t.id == self.current_track.id:
                    idx = i
                    break
            prev_idx = (idx - 1) % len(self.context_tracks)
            self.play_track(self.context_tracks[prev_idx], None, record_history=False)

    # -------------------- Gestión de Cola (Queue) -------------------- #

    def add_to_queue(self, track: TrackMetadata) -> None:
        self.user_queue.append(track)
        self.queue_changed.emit()

    def remove_from_queue(self, index: int) -> None:
        if 0 <= index < len(self.user_queue):
            self.user_queue.pop(index)
            self.queue_changed.emit()

    def clear_queue(self) -> None:
        self.user_queue.clear()
        self.queue_changed.emit()

    def get_upcoming_tracks(self, limit: int = 15) -> list[tuple[str, TrackMetadata]]:
        """Devuelve [(origen, track)] donde origen es 'user' (en cola) o 'context' (siguiente de la lista)."""
        result: list[tuple[str, TrackMetadata]] = [("user", t) for t in self.user_queue]
        if len(result) >= limit or not self.context_tracks:
            return result[:limit]

        idx = -1
        if self.current_track:
            for i, t in enumerate(self.context_tracks):
                if t.id == self.current_track.id:
                    idx = i
                    break

        for i in range(idx + 1, len(self.context_tracks)):
            result.append(("context", self.context_tracks[i]))
            if len(result) >= limit:
                break
        return result

    # -------------------- Ecualizador de 5 Bandas -------------------- #

    def set_equalizer(self, enabled: bool, gains: list[float]) -> None:
        self.eq_enabled = enabled
        self.eq_gains = [float(g) for g in gains[:5]]

        if not self.current_track:
            return

        pos_ms = self.player.position()
        was_playing = self.is_playing()
        if not enabled or all(abs(g) < 0.2 for g in self.eq_gains):
            # Volver al archivo original sin filtro
            current_src = self.player.source().toLocalFile()
            if current_src != self.current_track.filepath:
                self._pending_seek_ms = pos_ms
                self.player.setSource(QUrl.fromLocalFile(self.current_track.filepath))
                if was_playing:
                    self.player.play()
            self.eq_status_changed.emit("EQ: Plano (Directo)")
            return

        self._get_or_schedule_eq_file(self.current_track, resume_pos_ms=pos_ms)

    def _get_or_schedule_eq_file(self, track: TrackMetadata, resume_pos_ms: int = 0) -> str:
        if not self.eq_enabled or all(abs(g) < 0.2 for g in self.eq_gains):
            return track.filepath

        sig = f"{track.id}_{'_'.join(f'{g:.1f}' for g in self.eq_gains)}"
        h = hashlib.md5(sig.encode("utf-8")).hexdigest()[:12]
        out_path = str(self.eq_cache_dir / f"eq_{h}.mp3")

        if Path(out_path).exists() and Path(out_path).stat().st_size > 4000:
            self.eq_status_changed.emit("EQ de 5 bandas activo")
            return out_path

        self.eq_status_changed.emit("Aplicando curva EQ...")
        self._eq_worker = EQRenderWorker(track.id, track.filepath, out_path, list(self.eq_gains), resume_pos_ms)
        self._eq_worker.finished_eq.connect(self._on_eq_rendered)
        self._eq_worker.start()
        return track.filepath

    def _on_eq_rendered(self, track_id: str, eq_filepath: str, resume_pos_ms: int) -> None:
        if not self.current_track or self.current_track.id != track_id:
            return
        was_playing = self.is_playing()
        now_pos = self.player.position() or resume_pos_ms
        self._pending_seek_ms = now_pos
        self.player.setSource(QUrl.fromLocalFile(eq_filepath))
        if was_playing:
            self.player.play()
        self.eq_status_changed.emit("EQ de 5 bandas activo")

    # -------------------- Análisis FFT & Visualizador -------------------- #

    def _on_pcm_ready(self, track_id: str, samples: np.ndarray, sr: int, rhythm: dict) -> None:
        if self.current_track and self.current_track.id == track_id:
            self._pcm_samples = samples
            self._pcm_sr = sr
            self._rhythm = rhythm

    def _smooth_position_sec(self) -> float:
        """Posición de reproducción en segundos, interpolada con reloj monotónico (sin saltos)."""
        raw = self.player.position()
        now = time.monotonic()
        if raw != self._last_raw_pos:
            self._last_raw_pos = raw
            predicted = self._pos_ref_ms + (now - self._pos_ref_wall) * 1000.0
            # Si hay un salto grande (seek) o es la primera muestra, reanclar; si no, corregir suavemente
            if abs(predicted - raw) > 160.0 or self._pos_ref_wall == 0.0:
                self._pos_ref_ms = float(raw)
            else:
                self._pos_ref_ms = predicted + (raw - predicted) * 0.25
            self._pos_ref_wall = now
        est = self._pos_ref_ms + (now - self._pos_ref_wall) * 1000.0
        return max(0.0, (est - self.sync_offset_ms) / 1000.0)

    def _update_visualizer_frame(self) -> None:
        num_bars = 32
        if not self.is_playing():
            # Decaimiento suave a cero cuando está en pausa
            self._smooth_bars *= 0.86
            self._onset_smooth *= 0.8
            self._pos_ref_wall = 0.0  # reanclar la posición interpolada al reanudar
            self.spectrum_updated.emit(self._smooth_bars.copy(), np.zeros(128, dtype=np.float32))
            self.beat_info.emit(0.0, 0.0, 0.0, 0.0)
            return

        pos_sec = self._smooth_position_sec()

        if self._pcm_samples is not None and len(self._pcm_samples) > 2048:
            sr = self._pcm_sr
            win = 2048
            center_idx = int(pos_sec * sr)
            start = max(0, min(len(self._pcm_samples) - win, center_idx - win // 2))
            chunk = self._pcm_samples[start:start + win]
            raw_bars, wave_pts = self._analyzer.process(chunk, sr)
        else:
            # Aún decodificando: sin movimiento inventado (las barras bajan suavemente)
            raw_bars = np.zeros(num_bars, dtype=np.float32)
            wave_pts = np.zeros(128, dtype=np.float32)

        # Ritmo: posición en beats según la rejilla de tempo detectada
        r = self._rhythm
        bpm = 0.0
        bpos = 0.0
        conf = 0.0
        beat_kick = 0.0
        if r and r.get("bpm", 0.0) > 0 and len(r.get("beat_times", [])) > 1:
            bpm = float(r["bpm"])
            bpos = beat_position(r["beat_times"], bpm, pos_sec)
            conf = float(r.get("confidence", 0.0))
            env = r["env"]
            fi = int(pos_sec * r["fps"])
            onset_raw = float(env[fi]) / 3.2 if 0 <= fi < len(env) else 0.0
            self._onset_smooth = max(min(1.0, onset_raw), self._onset_smooth * 0.78)
            frac = bpos - int(bpos)
            beat_kick = max(0.0, 1.0 - frac * 2.8) ** 2
        else:
            self._onset_smooth *= 0.80

        # Reflejar ganancias del ecualizador de 5 bandas y acento rítmico del beat en el espectro visual
        if self.eq_enabled:
            band_centers = np.linspace(0, num_bars - 1, 5)
            eq_curve = np.interp(np.arange(num_bars), band_centers, self.eq_gains)
            eq_mult = 10.0 ** (eq_curve / 36.0)
            raw_bars = np.clip(raw_bars * eq_mult, 0.0, 1.0)

        if bpm > 0:
            # Acentuar graves y brillo rítmico justo en el golpe del beat para máxima respuesta musical
            bass_weights = np.exp(-np.arange(num_bars) / 7.5).astype(np.float32)
            raw_bars = np.clip(raw_bars + bass_weights * (beat_kick * 0.14 + self._onset_smooth * 0.12), 0.0, 1.0)

        # Suavizado temporal con respuesta ágil: ataque rápido y caída dinámica entre pulsos
        attack = 0.72
        release = 0.25
        up = raw_bars > self._smooth_bars
        self._smooth_bars = np.where(
            up,
            self._smooth_bars * (1 - attack) + raw_bars * attack,
            self._smooth_bars * (1 - release) + raw_bars * release,
        ).astype(np.float32)

        self.spectrum_updated.emit(self._smooth_bars.copy(), wave_pts)
        self.beat_info.emit(bpm, bpos, conf, self._onset_smooth)

    # -------------------- Callbacks internos de QtMultimedia -------------------- #

    def _on_position_changed(self, pos_ms: int) -> None:
        dur_ms = self.player.duration()
        if dur_ms <= 0 and self.current_track:
            dur_ms = int(self.current_track.duration * 1000)
        self.position_changed.emit(pos_ms, dur_ms)

    def _on_duration_changed(self, dur_ms: int) -> None:
        if self._pending_seek_ms > 0 and dur_ms > 0:
            target = self._pending_seek_ms
            self._pending_seek_ms = -1
            self.player.setPosition(target)
        self.position_changed.emit(self.player.position(), dur_ms)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.state_changed.emit(state == QMediaPlayer.PlaybackState.PlayingState)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.BufferedMedia or status == QMediaPlayer.MediaStatus.LoadedMedia:
            if self._pending_seek_ms > 0:
                target = self._pending_seek_ms
                self._pending_seek_ms = -1
                self.player.setPosition(target)
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next_track(auto_triggered=True)
