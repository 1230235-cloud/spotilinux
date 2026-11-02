#!/usr/bin/env python3
"""
Modo Tocadiscos / Disco de Vinilo OLED con Video de Fondo (Inspirado en MaoMusic Mobile)
- Disco de vinilo giratorio con surcos realistas, reflejos de luz y carátula circular centrada.
- Brazo de tocadiscos (tonearm) estilizado que se apoya sobre el disco al reproducir y descansa al pausar.
- Reproductor de video de fondo sincronizado con la pista o fondo atmosférico de partículas OLED.
- Reloj ambiental, metadatos y controles de reproducción táctiles y elegantes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal, QUrl, QTime
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPixmap,
    QPen,
    QBrush,
    QRadialGradient,
    QConicalGradient,
    QKeyEvent,
)
from PyQt6.QtWidgets import (
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget


def clean_str_key(s: str) -> str:
    """Normaliza texto para comparación tolerante de nombres."""
    if not s:
        return ""
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8")
    return "".join(c.lower() for c in s if c.isalnum())


class VinylDiscWidget(QWidget):
    """Widget que dibuja el disco de vinilo giratorio y el brazo de tocadiscos."""

    play_pause_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._angle: float = 0.0
        self._arm_angle: float = 0.0
        self._target_arm_angle: float = 0.0
        self._is_playing: bool = False
        self._cover_pixmap: Optional[QPixmap] = None

        # Timer de animación a ~35 FPS
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(28)
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

    def set_playing(self, playing: bool) -> None:
        self._is_playing = playing
        self._target_arm_angle = 24.0 if playing else 0.0

    def set_cover(self, cover_path: Optional[str | Path]) -> None:
        if cover_path and Path(cover_path).exists():
            pix = QPixmap(str(cover_path))
            if not pix.isNull():
                self._cover_pixmap = pix
                self.update()
                return
        self._cover_pixmap = None
        self.update()

    def _on_tick(self) -> None:
        need_update = False
        if self._is_playing:
            self._angle = (self._angle + 1.25) % 360.0
            need_update = True

        # Interpolación suave del brazo
        if abs(self._target_arm_angle - self._arm_angle) > 0.1:
            self._arm_angle += (self._target_arm_angle - self._arm_angle) * 0.15
            need_update = True
        else:
            self._arm_angle = self._target_arm_angle

        if need_update:
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.play_pause_clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = self.width()
        h = self.height()
        center = QPointF(w * 0.46, h * 0.50)
        radius = min(w * 0.38, h * 0.42)

        if radius <= 15:
            return

        # 1. Aura reactiva / sombra exterior del vinilo
        shadow_rect = QRectF(center.x() - radius - 10, center.y() - radius - 10, (radius + 10) * 2, (radius + 10) * 2)
        shadow_grad = QRadialGradient(center, radius + 14)
        if self._is_playing:
            shadow_grad.setColorAt(0.85, QColor(30, 215, 96, 70))
            shadow_grad.setColorAt(0.96, QColor(30, 215, 96, 20))
        else:
            shadow_grad.setColorAt(0.85, QColor(0, 0, 0, 180))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(shadow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(shadow_rect)

        # 2. Guardar estado y rotar disco
        painter.save()
        painter.translate(center)
        painter.rotate(self._angle)

        # Cuerpo del disco negro OLED
        disc_rect = QRectF(-radius, -radius, radius * 2, radius * 2)
        painter.setBrush(QColor("#111116"))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1.5))
        painter.drawEllipse(disc_rect)

        # Surcos concéntricos finos (grooves de vinilo)
        groove_steps = 16
        step = (radius * 0.52) / groove_steps
        for i in range(groove_steps):
            r_groove = radius * 0.44 + i * step
            alpha = 26 if (i % 2 == 0) else 12
            painter.setPen(QPen(QColor(255, 255, 255, alpha), 1.0))
            painter.drawEllipse(QRectF(-r_groove, -r_groove, r_groove * 2, r_groove * 2))

        # Reflejo de luz cónico (sheen brillante realista en ángulo)
        sheen_grad = QConicalGradient(0, 0, 50)
        sheen_grad.setColorAt(0.00, QColor(255, 255, 255, 0))
        sheen_grad.setColorAt(0.12, QColor(255, 255, 255, 38))
        sheen_grad.setColorAt(0.25, QColor(255, 255, 255, 0))
        sheen_grad.setColorAt(0.50, QColor(255, 255, 255, 0))
        sheen_grad.setColorAt(0.62, QColor(255, 255, 255, 38))
        sheen_grad.setColorAt(0.75, QColor(255, 255, 255, 0))
        sheen_grad.setColorAt(1.00, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(sheen_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(disc_rect)

        # 3. Etiqueta central circular con la carátula del álbum
        center_r = radius * 0.40
        label_rect = QRectF(-center_r, -center_r, center_r * 2, center_r * 2)

        clip_path = QPainterPath()
        clip_path.addEllipse(label_rect)
        painter.save()
        painter.setClipPath(clip_path)

        if self._cover_pixmap and not self._cover_pixmap.isNull():
            scaled = self._cover_pixmap.scaled(
                int(center_r * 2),
                int(center_r * 2),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(int(-center_r), int(-center_r), scaled)
        else:
            painter.setBrush(QColor("#181822"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(label_rect)
            painter.setPen(QColor("#1ed760"))
            f = painter.font()
            f.setPointSize(int(center_r * 0.42))
            painter.setFont(f)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, "🎵")

        # Borde sutil traslúcido sobre la carátula
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1.5))
        painter.drawEllipse(label_rect)
        painter.restore()

        # Agujero del eje central (spindle hole metálico)
        hole_r = center_r * 0.16
        hole_rect = QRectF(-hole_r, -hole_r, hole_r * 2, hole_r * 2)
        painter.setBrush(QColor("#08080a"))
        painter.setPen(QPen(QColor(210, 210, 220, 220), 1.8))
        painter.drawEllipse(hole_rect)

        painter.restore()

        # 4. Brazo de tocadiscos (Tonearm)
        self._paint_tonearm(painter, w, h, center, radius)

    def _paint_tonearm(self, painter: QPainter, w: int, h: int, center: QPointF, radius: float) -> None:
        pivot_x = center.x() + radius * 0.95
        pivot_y = center.y() - radius * 0.85

        painter.save()
        painter.translate(pivot_x, pivot_y)
        painter.rotate(self._arm_angle)

        # Base circular del brazo
        painter.setBrush(QColor("#242430"))
        painter.setPen(QPen(QColor(255, 255, 255, 80), 1.5))
        painter.drawEllipse(QPointF(0, 0), 15, 15)

        # Pivote metálico cromado
        painter.setBrush(QColor("#a0a0b0"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, 0), 6, 6)

        # Brazo metálico curvo
        arm_path = QPainterPath()
        arm_path.moveTo(0, 0)
        arm_len = radius * 1.05
        arm_path.cubicTo(-arm_len * 0.25, arm_len * 0.35, -arm_len * 0.50, arm_len * 0.65, -arm_len * 0.80, arm_len * 0.90)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(210, 210, 225), 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(arm_path)

        # Cabezal de la aguja (cartridge)
        head_end = QPointF(-arm_len * 0.80, arm_len * 0.90)
        painter.setBrush(QColor("#1ed760" if self._is_playing else "#555562"))
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1.0))
        painter.drawRoundedRect(QRectF(head_end.x() - 11, head_end.y() - 6, 22, 12), 3, 3)

        painter.restore()


class VinylStandbyOverlay(QFrame):
    """Pantalla completa de modo reposo y tocadiscos estilo MaoMusic Mobile."""

    closed = pyqtSignal()
    play_pause_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()
    shuffle_requested = pyqtSignal()
    repeat_requested = pyqtSignal()

    def __init__(self, base_dir: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir).resolve()
        self.setObjectName("VinylStandbyOverlay")
        self.video_enabled: bool = True
        self._current_video_path: Optional[str] = None
        self._hue: float = 145.0

        # Video nativo de fondo
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setMuted(True)
        self.player.setAudioOutput(self.audio_output)

        self.video_widget = QVideoWidget(self)
        self.video_widget.setStyleSheet("background: transparent;")
        self.video_widget.lower()
        self.player.setVideoOutput(self.video_widget)

        # Layout frontal de UI
        ui_lay = QVBoxLayout(self)
        ui_lay.setContentsMargins(32, 24, 32, 28)
        ui_lay.setSpacing(12)

        # Barra superior (Reloj + Botón Video + Salir)
        top_bar = QHBoxLayout()
        self.lbl_clock = QLabel("00:00", self)
        self.lbl_clock.setObjectName("VinylClock")
        self.lbl_clock.setStyleSheet("color: #FFFFFF; font-size: 20px; font-weight: 800; letter-spacing: 1.5px;")

        self.btn_toggle_video = QPushButton("🎬  Video: ON", self)
        self.btn_toggle_video.setObjectName("ActionChipButton")
        self.btn_toggle_video.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_video.clicked.connect(self._on_toggle_video)

        self.btn_exit = QPushButton("✕  Salir", self)
        self.btn_exit.setObjectName("GhostSmallButton")
        self.btn_exit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_exit.clicked.connect(self.close_overlay)

        top_bar.addWidget(self.lbl_clock)
        top_bar.addStretch()
        top_bar.addWidget(self.btn_toggle_video)
        top_bar.addWidget(self.btn_exit)
        ui_lay.addLayout(top_bar)

        # Centro: Disco de vinilo giratorio
        disc_holder = QHBoxLayout()
        disc_holder.addStretch()
        self.vinyl_disc = VinylDiscWidget(self)
        self.vinyl_disc.play_pause_clicked.connect(self.play_pause_requested)
        disc_holder.addWidget(self.vinyl_disc, stretch=1)
        disc_holder.addStretch()
        ui_lay.addLayout(disc_holder, stretch=1)

        # Detalles de la pista activa
        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        info_col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_track_title = QLabel("MaoMusic", self)
        self.lbl_track_title.setObjectName("VinylTitle")
        self.lbl_track_title.setStyleSheet("color: #FFFFFF; font-size: 22px; font-weight: 800;")
        self.lbl_track_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_track_artist = QLabel("Modo Reposo • Tocadiscos", self)
        self.lbl_track_artist.setObjectName("VinylArtist")
        self.lbl_track_artist.setStyleSheet("color: #1ED760; font-size: 14px; font-weight: 600;")
        self.lbl_track_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_badge = QLabel("REPOSO OLED • TOCADISCOS ACTIVO", self)
        self.lbl_badge.setObjectName("VinylBadge")
        self.lbl_badge.setStyleSheet(
            "color: #8E8E98; font-size: 10px; font-weight: 800; letter-spacing: 1.2px; "
            "background: rgba(255,255,255,0.06); padding: 4px 10px; border-radius: 6px;"
        )
        self.lbl_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_col.addWidget(self.lbl_track_title)
        info_col.addWidget(self.lbl_track_artist)
        info_col.addWidget(self.lbl_badge)
        ui_lay.addLayout(info_col)

        # Fila de Controles de reproducción discretos estilo móvil
        ctrls_row = QHBoxLayout()
        ctrls_row.setSpacing(14)
        ctrls_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_shuffle = QPushButton("🔀", self)
        self.btn_shuffle.setObjectName("TransportIconBtn")
        self.btn_shuffle.setFixedSize(36, 36)
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shuffle.clicked.connect(self.shuffle_requested)

        self.btn_prev = QPushButton("⏮", self)
        self.btn_prev.setObjectName("TransportIconBtn")
        self.btn_prev.setFixedSize(36, 36)
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.clicked.connect(self.prev_requested)

        self.btn_play = QPushButton("▶", self)
        self.btn_play.setObjectName("TransportPlayButton")
        self.btn_play.setFixedSize(46, 46)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.clicked.connect(self.play_pause_requested)

        self.btn_next = QPushButton("⏭", self)
        self.btn_next.setObjectName("TransportIconBtn")
        self.btn_next.setFixedSize(36, 36)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.clicked.connect(self.next_requested)

        self.btn_repeat = QPushButton("🔁", self)
        self.btn_repeat.setObjectName("TransportIconBtn")
        self.btn_repeat.setFixedSize(36, 36)
        self.btn_repeat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_repeat.clicked.connect(self.repeat_requested)

        for b in (self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next, self.btn_repeat):
            ctrls_row.addWidget(b)

        ui_lay.addLayout(ctrls_row)

        # Reloj digital actualizado cada segundo
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

        # Animación de fondo atmosférico reactivo
        self._bg_timer = QTimer(self)
        self._bg_timer.setInterval(40)
        self._bg_timer.timeout.connect(self._on_bg_tick)
        self._bg_timer.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.video_widget.setGeometry(self.rect())
        self.video_widget.lower()

    def _on_bg_tick(self) -> None:
        if not self._current_video_path or not self.video_enabled:
            self._hue = (self._hue + 0.35) % 360.0
            self.update()

    def _update_clock(self) -> None:
        self.lbl_clock.setText(QTime.currentTime().toString("HH:mm"))

    def _on_toggle_video(self) -> None:
        self.video_enabled = not self.video_enabled
        if not self.video_enabled:
            self.stop_video()
        self.btn_toggle_video.setText("🎬  Video: ON" if self.video_enabled else "🎬  Video: OFF")

    def find_video_for_track(self, track_filepath: str, title: str, artist: str) -> Optional[Path]:
        """Busca si existe un video local correspondiente al archivo o título de la canción."""
        candidates_dirs = [
            self.base_dir / "data" / "videos",
            self.base_dir / "music_library" / "videos",
            self.base_dir / "music_library",
            Path.home() / "Videos",
            Path.home() / "Vídeos",
        ]

        # 1. Si el filepath mismo tiene un hermano .mp4 / .webm
        if track_filepath and not track_filepath.startswith("gdrive://"):
            p = Path(track_filepath)
            for ext in (".mp4", ".webm", ".mkv"):
                cand = p.with_suffix(ext)
                if cand.exists():
                    return cand

        clean_t = clean_str_key(title)
        clean_full = clean_str_key(f"{artist} {title}")

        # 2. Buscar en carpetas de videos
        for cdir in candidates_dirs:
            if not cdir.exists():
                continue
            for vfile in cdir.glob("*.*"):
                if vfile.suffix.lower() in (".mp4", ".webm", ".mkv"):
                    vk = clean_str_key(vfile.stem)
                    if vk and (vk == clean_t or vk in clean_full or clean_t in vk):
                        return vfile

        return None

    def update_state(
        self,
        title: str,
        artist: str,
        cover_path: Optional[str | Path],
        track_filepath: str,
        is_playing: bool,
    ) -> None:
        self.lbl_track_title.setText(title or "MaoMusic")
        self.lbl_track_artist.setText(artist or "Tocadiscos OLED")
        self.btn_play.setText("⏸" if is_playing else "▶")
        self.vinyl_disc.set_playing(is_playing)
        self.vinyl_disc.set_cover(cover_path)

        if not self.video_enabled:
            self.stop_video()
            return

        vpath = self.find_video_for_track(track_filepath, title, artist)
        if vpath and vpath.exists():
            v_str = str(vpath.resolve())
            if self._current_video_path != v_str:
                self._current_video_path = v_str
                self.player.setSource(QUrl.fromLocalFile(v_str))
                self.player.play()
                self.video_widget.setVisible(True)
            elif is_playing and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.player.play()
            elif not is_playing and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
        else:
            self.stop_video()

    def sync_playback_position(self, pos_secs: float, is_playing: bool) -> None:
        if not self.video_enabled or not self._current_video_path:
            return
        if is_playing and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.player.play()
        elif not is_playing and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()

        vid_pos_sec = self.player.position() / 1000.0
        if abs(vid_pos_sec - pos_secs) > 1.2:
            self.player.setPosition(int(pos_secs * 1000))

    def stop_video(self) -> None:
        self._current_video_path = None
        self.player.stop()
        self.video_widget.setVisible(False)

    def close_overlay(self) -> None:
        self.stop_video()
        self.setVisible(False)
        self.closed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_overlay()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        if not self._current_video_path or not self.video_enabled:
            grad = QRadialGradient(w * 0.46, h * 0.50, max(w, h) * 0.70)
            c1 = QColor.fromHsv(int(self._hue) % 360, 160, 48, 140)
            c2 = QColor("#09090c")
            grad.setColorAt(0.0, c1)
            grad.setColorAt(0.55, QColor("#0e0e14"))
            grad.setColorAt(1.0, c2)
            painter.fillRect(self.rect(), grad)
        else:
            painter.fillRect(self.rect(), QColor(9, 9, 12, 160))
