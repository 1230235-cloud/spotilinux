#!/usr/bin/env python3
"""
SpotiLinux — Reproductor de Música de Escritorio estilo Spotify para Linux.
Incluye:
  - Escaneo e importación de archivos locales (MP3, FLAC, WAV, OGG, M4A, OPUS) + pistas demo sintetizadas
  - Conexión a Nube Privada con Google Drive (OAuth2, Carpeta de Drive, enlaces directos y streaming en caché)
  - Figura animada de Maomao (The Apothecary Diaries) que baila en tiempo real al compás de la música
  - Extracción automática de etiquetas ID3 y carátulas incrustadas (APIC)
  - Creación y gestión de Playlists personalizadas y Favoritos ("Canciones que te gustan")
  - Cola de reproducción interactiva, modo Aleatorio (Shuffle) y Repetición (Off / All / One)
  - Visualizador de espectro FFT / ondas en tiempo real a 60 FPS y Ecualizador de 5 bandas
"""

import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional
import urllib.request

# Evitar deadlock nativo de PipeWire en Qt 6.12 usando la capa de compatibilidad PulseAudio de PipeWire
if "QT_AUDIO_BACKEND" not in os.environ:
    os.environ["QT_AUDIO_BACKEND"] = "pulseaudio"

import doctor
from app_version import APP_VERSION_CODE, APP_VERSION_NAME
from download_queue import DownloadQueue, QueueRunner, parse_playlist_txt
from download_queue_ui import DownloadQueuePanel, QueueSignals
from vinyl_view import VinylStandbyOverlay
import icons
from PyQt6.QtCore import Qt, QSize, QPoint, QRectF, QTimer, QThread, QUrl, pyqtSignal, qInstallMessageHandler, QtMsgType, QEvent
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QDesktopServices,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from audio_engine import AudioEngine
from cloud_service import CloudSyncManager
from gdrive_sync import (
    CLOUD_DB_FILENAME,
    DEFAULT_ROOT_DRIVE_FOLDER_ID,
    GDriveCloudPlaylistSyncWorker,
    GDriveOAuthLoopbackWorker,
    GDriveStreamDownloadWorker,
    GDriveSyncWorker,
    GDriveUploadWorker,
    GoogleDriveConfig,
    canonical_drive_track_id,
    download_cloud_database,
    extract_drive_id_from_url,
    fetch_google_user_profile,
    generate_recovery_key,
    hash_password,
    merge_account_records,
    normalize_track_key,
    sync_cloud_playlists_with_drive,
    upload_cloud_database,
    validate_recovery_key,
)
from generate_demos import ensure_demo_tracks
from maomao_widget import MaomaoDancerWidget
from metadata import SUPPORTED_EXTENSIONS, TrackMetadata, format_duration, clean_track_title
from storage import LibraryStore, Playlist
from styles import SPOTIFY_QSS
from visualizer_widget import AudioVisualizerWidget, EqualizerPanel


_ROUNDED_PIXMAP_CACHE: dict[tuple[str, int, int], QPixmap] = {}


def make_rounded_pixmap(path: str, size: int, radius: int = 6) -> QPixmap:
    """Carga una imagen y la recorta con esquinas redondeadas nítidas utilizando caché en memoria."""
    cache_key = (path, size, radius)
    if cache_key in _ROUNDED_PIXMAP_CACHE:
        return _ROUNDED_PIXMAP_CACHE[cache_key]

    target = QPixmap(size, size)
    target.fill(Qt.GlobalColor.transparent)

    src = QPixmap(path) if path and Path(path).exists() else QPixmap()
    if src.isNull():
        src = QPixmap(size, size)
        src.fill(QColor(40, 40, 48))
    else:
        src = src.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if src.width() != size or src.height() != size:
            x = max(0, (src.width() - size) // 2)
            y = max(0, (src.height() - size) // 2)
            src = src.copy(x, y, size, size)

    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), float(radius), float(radius))
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, src)
    painter.end()

    if len(_ROUNDED_PIXMAP_CACHE) > 300:
        _ROUNDED_PIXMAP_CACHE.clear()

    _ROUNDED_PIXMAP_CACHE[cache_key] = target
    return target


def make_liked_cover_pixmap(size: int, radius: int = 8, symbol: str = "♥", colors: Optional[tuple] = None) -> QPixmap:
    """Crea un icono cuadrado con degradado para vistas especiales (Favoritos, Google Drive, etc.)."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), float(radius), float(radius))
    grad = QLinearGradient(0, 0, size, size)
    if colors:
        grad.setColorAt(0.0, QColor(*colors[0]))
        grad.setColorAt(0.6, QColor(*colors[1]))
        grad.setColorAt(1.0, QColor(*colors[2]))
    else:
        grad.setColorAt(0.0, QColor(69, 10, 245))
        grad.setColorAt(0.6, QColor(142, 115, 235))
        grad.setColorAt(1.0, QColor(196, 239, 217))
    painter.fillPath(path, grad)

    painter.setPen(QColor(255, 255, 255))
    font = QFont("Sans Serif", max(12, int(size * 0.40)), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()
    return pix


class CircularDownloadButton(QPushButton):
    """
    Botón circular OLED Dark (#18181f) con animación de progreso circular.
    Muestra un anillo que se cierra en sentido horario conforme avanza la descarga.
    """
    def __init__(self, size: int = 34, parent=None) -> None:
        super().__init__(parent)
        self.btn_size = size
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._state = "idle"  # "idle" | "downloading" | "completed" | "error"
        self._progress = 0.0  # 0.0 a 1.0
        self._display_progress = 0.0
        self._hovered = False

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def set_progress(self, current: int, total: int = 100) -> None:
        if total > 0:
            self._progress = max(0.0, min(1.0, current / float(total)))
        else:
            self._progress = 0.0
        self._display_progress = self._progress
        if self._progress >= 1.0:
            self._state = "completed"
        elif self._progress > 0.0:
            self._state = "downloading"
        self.update()

    def reset(self) -> None:
        self._state = "idle"
        self._progress = 0.0
        self._display_progress = 0.0
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            w = rect.width()
            h = rect.height()

            # 1. Fondo circular
            bg_color = QColor("#22222b") if self._hovered else QColor("#18181f")
            painter.setBrush(bg_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(1, 1, w - 2, h - 2)

            # 2. Borde o Anillo de progreso
            inset = 3
            arc_rect = QRectF(inset, inset, w - inset * 2, h - inset * 2)

            if self._state == "downloading":
                # Guía de fondo gris tenue
                guide_pen = QPen(QColor(255, 255, 255, 30), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(guide_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(arc_rect)

                # Arco activo verde neón cerrándose de 0 a 360 grados
                span_angle = -int(self._display_progress * 360 * 16)
                arc_pen = QPen(QColor("#1ed760"), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(arc_pen)
                painter.drawArc(arc_rect, 90 * 16, span_angle)

                # Icono central o texto de porcentaje
                pct_text = f"{int(self._display_progress * 100)}%"
                painter.setPen(QColor("#1ed760"))
                font = QFont("Sans Serif", max(7, int(w * 0.25)), QFont.Weight.Bold)
                painter.setFont(font)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, pct_text)
            elif self._state == "completed":
                border_pen = QPen(QColor(30, 215, 96, 120), 2.0)
                painter.setPen(border_pen)
                painter.drawEllipse(arc_rect)
                icon_pix = icons.get_pixmap("check", "#1ed760", int(w * 0.48))
                px = int((w - icon_pix.width()) / 2)
                py = int((h - icon_pix.height()) / 2)
                painter.drawPixmap(px, py, icon_pix)
            else:
                border_col = QColor("#1ed760") if self._hovered else QColor(255, 255, 255, 25)
                border_pen = QPen(border_col, 1.2)
                painter.setPen(border_pen)
                painter.drawEllipse(arc_rect)
                icon_color = "#1ed760" if self._hovered else "#e4e4ec"
                icon_pix = icons.get_pixmap("download", icon_color, int(w * 0.46))
                px = int((w - icon_pix.width()) / 2)
                py = int((h - icon_pix.height()) / 2)
                painter.drawPixmap(px, py, icon_pix)
        finally:
            painter.end()


class HeroBannerWidget(QFrame):
    """Encabezado dinámico con degradado adaptativo al color dominante de la carátula actual."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.dominant_rgb = (29, 115, 72)
        self.setMinimumHeight(184)

        main_lay = QHBoxLayout(self)
        main_lay.setContentsMargins(24, 18, 24, 16)
        main_lay.setSpacing(22)

        self.lbl_cover = QLabel()
        self.lbl_cover.setFixedSize(138, 138)
        main_lay.addWidget(self.lbl_cover)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        info_col.addStretch()

        self.lbl_badge = QLabel("PLAYLIST")
        self.lbl_badge.setStyleSheet("color: #E0E0E0; font-size: 11px; font-weight: 800; letter-spacing: 1.2px;")

        self.lbl_title = QLabel("Tu Biblioteca")
        self.lbl_title.setStyleSheet("color: #FFFFFF; font-size: 30px; font-weight: 900;")

        self.lbl_subtitle = QLabel("Tus pistas locales, nube de Google Drive y Maomao bailando al compás")
        self.lbl_subtitle.setStyleSheet("color: #D0D0D8; font-size: 13px;")
        self.lbl_subtitle.setWordWrap(True)

        self.lbl_stats = QLabel("0 canciones")
        self.lbl_stats.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: 600;")

        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.btn_play_banner = QPushButton()
        self.btn_play_banner.setObjectName("SpotifyGreenPlayBig")
        self.btn_play_banner.setFixedSize(46, 46)
        self.btn_play_banner.setIcon(icons.get_icon("play", "#000000", 22))
        self.btn_play_banner.setIconSize(QSize(22, 22))
        self.btn_play_banner.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_banner.setToolTip("Reproducir vista actual")

        self.btn_shuffle_banner = QPushButton(" Aleatorio")
        self.btn_shuffle_banner.setObjectName("ActionChipButton")
        self.btn_shuffle_banner.setIcon(icons.get_icon("shuffle", "#e4e4ec", 16))
        self.btn_shuffle_banner.setIconSize(QSize(16, 16))
        self.btn_shuffle_banner.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_download_playlist = CircularDownloadButton(size=38)
        self.btn_download_playlist.setObjectName("CircularDownloadPlaylist")
        self.btn_download_playlist.setToolTip("Descargar canciones de esta playlist")
        self.btn_download_playlist.setVisible(False)

        self.btn_add_tracks = QPushButton(" Agregar canciones")
        self.btn_add_tracks.setObjectName("ActionChipButton")
        self.btn_add_tracks.setIcon(icons.get_icon("plus", "#e4e4ec", 16))
        self.btn_add_tracks.setIconSize(QSize(16, 16))
        self.btn_add_tracks.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_tracks.setVisible(False)

        self.btn_edit_playlist = QPushButton(" Editar")
        self.btn_edit_playlist.setObjectName("ActionChipButton")
        self.btn_edit_playlist.setIcon(icons.get_icon("edit", "#e4e4ec", 16))
        self.btn_edit_playlist.setIconSize(QSize(16, 16))
        self.btn_edit_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_playlist.setVisible(False)

        self.btn_delete_playlist = QPushButton(" Eliminar")
        self.btn_delete_playlist.setObjectName("ActionChipButton")
        self.btn_delete_playlist.setIcon(icons.get_icon("trash", "#e4e4ec", 16))
        self.btn_delete_playlist.setIconSize(QSize(16, 16))
        self.btn_delete_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete_playlist.setVisible(False)

        # Botones de compatibilidad invisibles
        self.btn_cloud_sync_banner = QPushButton()
        self.btn_cloud_sync_banner.setVisible(False)
        self.btn_download_yt_banner = QPushButton()
        self.btn_download_yt_banner.setVisible(False)

        actions_row.addWidget(self.btn_play_banner)
        actions_row.addWidget(self.btn_shuffle_banner)
        actions_row.addWidget(self.btn_download_playlist)
        actions_row.addWidget(self.btn_add_tracks)
        actions_row.addWidget(self.btn_edit_playlist)
        actions_row.addWidget(self.btn_delete_playlist)
        actions_row.addStretch()

        info_col.addWidget(self.lbl_badge)
        info_col.addWidget(self.lbl_title)
        info_col.addWidget(self.lbl_subtitle)
        info_col.addWidget(self.lbl_stats)
        info_col.addSpacing(4)
        info_col.addLayout(actions_row)

        main_lay.addLayout(info_col, stretch=1)

    def set_banner_data(
        self,
        badge: str,
        title: str,
        subtitle: str,
        stats: str,
        pixmap: QPixmap,
        rgb: tuple[int, int, int],
        is_custom_playlist: bool = False,
        show_download: bool = False,
    ) -> None:
        self.lbl_badge.setText(badge.upper())
        self.lbl_title.setText(title)
        self.lbl_subtitle.setText(subtitle)
        self.lbl_stats.setText(stats)
        self.lbl_cover.setPixmap(pixmap)
        self.dominant_rgb = rgb
        self.btn_add_tracks.setVisible(is_custom_playlist)
        self.btn_edit_playlist.setVisible(is_custom_playlist)
        self.btn_delete_playlist.setVisible(is_custom_playlist)
        self.btn_download_playlist.setVisible(is_custom_playlist or show_download)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r, g, b = self.dominant_rgb
        grad = QLinearGradient(0, 0, 0, float(self.height()))
        grad.setColorAt(0.0, QColor(r, g, b, 235))
        grad.setColorAt(0.75, QColor(int(r * 0.35), int(g * 0.35), int(b * 0.35), 180))
        grad.setColorAt(1.0, QColor(18, 18, 18, 255))

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, float(self.width()), float(self.height())), 10.0, 10.0)
        painter.fillPath(path, grad)


class QuickAccessCard(QFrame):
    """Tarjeta horizontal compacta estilo pantalla de Inicio de Spotify."""

    def __init__(self, title: str, subtitle: str, pixmap: QPixmap, callback, play_callback=None, is_selected: bool = False) -> None:
        super().__init__()
        self.callback = callback
        self.play_callback = play_callback
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(56)

        bg = "rgba(29, 185, 84, 0.16)" if is_selected else "rgba(255, 255, 255, 0.07)"
        border = "#1DB954" if is_selected else "rgba(255, 255, 255, 0.08)"
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QFrame:hover {{
                background-color: rgba(29, 185, 84, 0.22);
                border-color: #1DB954;
            }}
            """
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(10)

        lbl_img = QLabel()
        lbl_img.setFixedSize(56, 56)
        lbl_img.setPixmap(pixmap)
        lbl_img.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(lbl_img)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.addStretch()
        short_t = title if len(title) <= 22 else title[:20] + "…"
        short_s = subtitle if len(subtitle) <= 26 else subtitle[:24] + "…"
        lbl_t = QLabel(short_t)
        lbl_t.setToolTip(title)
        lbl_t.setStyleSheet(f"color: {'#1ED760' if is_selected else '#FFFFFF'}; font-size: 12px; font-weight: 700; background: transparent; border: none;")
        lbl_s = QLabel(short_s)
        lbl_s.setStyleSheet("color: #B3B3B3; font-size: 11px; background: transparent; border: none;")
        text_col.addWidget(lbl_t)
        text_col.addWidget(lbl_s)
        text_col.addStretch()
        lay.addLayout(text_col, stretch=1)

        self.btn_play_mini = QPushButton()
        self.btn_play_mini.setFixedSize(30, 30)
        self.btn_play_mini.setIcon(icons.get_icon("play", "#000000", 14))
        self.btn_play_mini.setIconSize(QSize(14, 14))
        self.btn_play_mini.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_mini.setStyleSheet(
            "background-color: #1ed760; border-radius: 15px; border: none;"
        )
        if self.play_callback:
            self.btn_play_mini.clicked.connect(self.play_callback)
        lay.addWidget(self.btn_play_mini)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.callback:
            self.callback()
        super().mousePressEvent(event)


class CompactTrackRow(QFrame):
    """Fila compacta para previsualizar canciones de la playlist seleccionada en el Inicio."""

    def __init__(self, index: int, track: TrackMetadata, on_click, is_playing: bool = False) -> None:
        super().__init__()
        self.setObjectName("CompactTrackRow")
        self.setProperty("playing", "true" if is_playing else "false")
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.on_click = on_click

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(10)

        lbl_idx = QLabel("▶" if is_playing else str(index))
        lbl_idx.setFixedWidth(22)
        lbl_idx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_idx.setStyleSheet(f"color: {'#1ED760' if is_playing else '#A7A7A7'}; font-weight: bold; font-size: 11px; background: transparent; border: none;")
        lay.addWidget(lbl_idx)

        lbl_cov = QLabel()
        lbl_cov.setFixedSize(34, 34)
        lbl_cov.setPixmap(make_rounded_pixmap(track.cover_path, 34, 5))
        lbl_cov.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(lbl_cov)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(1)
        lbl_t = QLabel(track.title)
        lbl_t.setStyleSheet(f"color: {'#1ED760' if is_playing else '#FFFFFF'}; font-weight: 700; font-size: 12px; background: transparent; border: none;")
        lbl_a = QLabel(track.artist)
        lbl_a.setStyleSheet("color: #A7A7A7; font-size: 11px; background: transparent; border: none;")
        meta_col.addWidget(lbl_t)
        meta_col.addWidget(lbl_a)
        lay.addLayout(meta_col, stretch=1)

        dur_str = format_duration(track.duration) if track.duration > 0 else "Cloud ☁️"
        lbl_dur = QLabel(dur_str)
        lbl_dur.setStyleSheet("color: #888888; font-size: 11px; background: transparent; border: none;")
        lay.addWidget(lbl_dur)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.on_click:
            self.on_click()
        super().mousePressEvent(ev)


class PlaylistDialog(QDialog):
    """Diálogo modal estilo Spotify para crear o editar una Playlist."""

    def __init__(self, parent: QWidget, title_text: str = "Nueva Playlist", name: str = "", desc: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title_text)
        self.setFixedSize(400, 220)
        self.setStyleSheet(
            """
            QDialog { background-color: #282828; color: #FFFFFF; }
            QLabel { color: #EAEAEA; font-size: 13px; font-weight: 600; }
            QLineEdit {
                background-color: #3E3E3E; color: #FFFFFF;
                border: 1px solid #535353; border-radius: 6px; padding: 8px; font-size: 13px;
            }
            QPushButton {
                background-color: #1DB954; color: #000000; border: none;
                border-radius: 16px; padding: 7px 18px; font-weight: bold;
            }
            QPushButton:hover { background-color: #1ED760; }
            """
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        self.inp_name = QLineEdit(name)
        self.inp_name.setPlaceholderText("Ej. Synthwave Nocturno")
        self.inp_desc = QLineEdit(desc)
        self.inp_desc.setPlaceholderText("Descripción opcional...")

        form.addRow("Nombre:", self.inp_name)
        form.addRow("Descripción:", self.inp_desc)
        lay.addLayout(form)

        lay.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def get_values(self) -> tuple[str, str]:
        return self.inp_name.text().strip(), self.inp_desc.text().strip()


class MultiTrackSelectorDialog(QDialog):
    """Permite seleccionar múltiples canciones a la vez de la biblioteca para agregarlas a una playlist."""

    def __init__(self, parent: QWidget, all_tracks: list[TrackMetadata], current_track_ids: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("＋ Seleccionar varias canciones para la Playlist")
        self.resize(700, 560)
        self.all_tracks = all_tracks
        self.current_ids = set(current_track_ids)
        self.selected_ids: set[str] = set()

        self.setStyleSheet(
            """
            QDialog { background-color: #181818; color: #FFFFFF; }
            QLabel { color: #FFFFFF; font-size: 13px; font-weight: 600; }
            QLineEdit {
                background-color: #282828; color: #FFFFFF;
                border: 1px solid #404040; border-radius: 6px; padding: 7px 10px; font-size: 12px;
            }
            QTableWidget {
                background-color: #121212; color: #FFFFFF;
                border: 1px solid #282828; border-radius: 6px;
            }
            QPushButton {
                background-color: #1DB954; color: #000000; border: none;
                border-radius: 15px; padding: 7px 18px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #1ED760; }
            QPushButton#SecondaryBtn {
                background-color: #2E2E38; color: #FFFFFF; border: 1px solid #444452;
            }
            QPushButton#SecondaryBtn:hover { background-color: #3E3E4C; }
            """
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        header = QLabel("Selecciona las canciones que deseas añadir a esta playlist:")
        header.setStyleSheet("font-size: 14px; font-weight: 800; color: #1DB954;")
        lay.addWidget(header)

        top_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Filtrar por título, artista o álbum...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_table)
        top_row.addWidget(self.search_box, stretch=1)

        btn_select_all = QPushButton("☑ Seleccionar Todas")
        btn_select_all.setObjectName("SecondaryBtn")
        btn_select_all.clicked.connect(self._select_all)
        top_row.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("☐ Desmarcar")
        btn_deselect_all.setObjectName("SecondaryBtn")
        btn_deselect_all.clicked.connect(self._deselect_all)
        top_row.addWidget(btn_deselect_all)

        lay.addLayout(top_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "TÍTULO", "ARTISTA", "ESTADO"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 42)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(2, 190)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 110)
        lay.addWidget(self.table, stretch=1)

        self._checkbox_map: dict[str, QCheckBox] = {}
        self._current_filtered_tracks: list[TrackMetadata] = list(self.all_tracks)
        self._populate_table(self.all_tracks)

        bottom_row = QHBoxLayout()
        self.lbl_count = QLabel("0 canciones seleccionadas")
        self.lbl_count.setStyleSheet("color: #B3B3B3; font-size: 12px; font-weight: normal;")
        bottom_row.addWidget(self.lbl_count)
        bottom_row.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("SecondaryBtn")
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)

        self.btn_confirm = QPushButton("＋ Añadir a Playlist")
        self.btn_confirm.clicked.connect(self.accept)
        bottom_row.addWidget(self.btn_confirm)

        lay.addLayout(bottom_row)

    def _populate_table(self, tracks: list[TrackMetadata]) -> None:
        self._current_filtered_tracks = tracks
        self.table.setRowCount(len(tracks))
        self._checkbox_map.clear()

        for row, t in enumerate(tracks):
            self.table.setRowHeight(row, 38)
            chk = QCheckBox()
            already_in = t.id in self.current_ids
            if already_in:
                chk.setEnabled(False)
                chk.setToolTip("Esta canción ya está en la playlist")
            else:
                chk.setChecked(t.id in self.selected_ids)
                chk.stateChanged.connect(lambda state, tid=t.id: self._on_check_changed(tid, state))
            self._checkbox_map[t.id] = chk

            cell_widget = QWidget()
            ch_lay = QHBoxLayout(cell_widget)
            ch_lay.setContentsMargins(12, 0, 0, 0)
            ch_lay.addWidget(chk)
            self.table.setCellWidget(row, 0, cell_widget)

            item_title = QTableWidgetItem(t.title)
            item_title.setForeground(QColor("#777777") if already_in else QColor("#FFFFFF"))
            self.table.setItem(row, 1, item_title)

            item_artist = QTableWidgetItem(t.artist)
            item_artist.setForeground(QColor("#666666") if already_in else QColor("#B3B3B3"))
            self.table.setItem(row, 2, item_artist)

            status_str = "Ya añadida" if already_in else "Disponible"
            item_status = QTableWidgetItem(status_str)
            item_status.setForeground(QColor("#555555") if already_in else QColor("#1DB954"))
            self.table.setItem(row, 3, item_status)

    def _on_check_changed(self, tid: str, state: int) -> None:
        if state == 2:
            self.selected_ids.add(tid)
        else:
            self.selected_ids.discard(tid)
        self.lbl_count.setText(f"{len(self.selected_ids)} canciones seleccionadas")
        self.btn_confirm.setText(f"＋ Añadir ({len(self.selected_ids)}) a Playlist")

    def _select_all(self) -> None:
        for t in self._current_filtered_tracks:
            if t.id not in self.current_ids:
                self.selected_ids.add(t.id)
                chk = self._checkbox_map.get(t.id)
                if chk and chk.isEnabled():
                    chk.blockSignals(True)
                    chk.setChecked(True)
                    chk.blockSignals(False)
        self.lbl_count.setText(f"{len(self.selected_ids)} canciones seleccionadas")
        self.btn_confirm.setText(f"＋ Añadir ({len(self.selected_ids)}) a Playlist")

    def _deselect_all(self) -> None:
        for t in self._current_filtered_tracks:
            self.selected_ids.discard(t.id)
            chk = self._checkbox_map.get(t.id)
            if chk and chk.isEnabled():
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)
        self.lbl_count.setText(f"{len(self.selected_ids)} canciones seleccionadas")
        self.btn_confirm.setText(f"＋ Añadir ({len(self.selected_ids)}) a Playlist")

    def _filter_table(self, query: str) -> None:
        q = query.strip().lower()
        if not q:
            filtered = self.all_tracks
        else:
            filtered = [
                t for t in self.all_tracks
                if q in t.title.lower() or q in t.artist.lower() or q in t.album.lower()
            ]
        self._populate_table(filtered)

    def get_selected_track_ids(self) -> list[str]:
        return list(self.selected_ids)


class GoogleDriveDialog(QDialog):
    """Modal para conectar Google Drive (preconfigurado con la carpeta raíz y subcarpeta Musica)."""

    def __init__(self, parent: QWidget, config: GoogleDriveConfig) -> None:
        super().__init__(parent)
        self.setWindowTitle("☁️ Conectar Google Drive y Carpeta Musica")
        self.setFixedSize(580, 560)
        self.config = config
        self._oauth_worker: Optional[GDriveOAuthLoopbackWorker] = None

        self.setStyleSheet(
            """
            QDialog { background-color: #1E1E24; color: #FFFFFF; }
            QLabel { color: #E0E0E6; font-size: 12px; font-weight: 600; }
            QLineEdit, QPlainTextEdit {
                background-color: #2C2C34; color: #FFFFFF;
                border: 1px solid #444452; border-radius: 6px; padding: 7px; font-size: 12px;
            }
            QPushButton {
                background-color: #1DB954; color: #000000; border: none;
                border-radius: 15px; padding: 7px 16px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #1ED760; }
            QPushButton#SecondaryBtn {
                background-color: #32323D; color: #FFFFFF; border: 1px solid #4A4A5A;
            }
            QPushButton#SecondaryBtn:hover { background-color: #424250; }
            """
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        header = QLabel("☁️ Google Drive — Base de Datos y Carpeta «Musica»")
        header.setStyleSheet("font-size: 16px; font-weight: 800; color: #1DB954;")
        lay.addWidget(header)

        sub = QLabel(
            f"Carpeta raíz vinculada: {DEFAULT_ROOT_DRIVE_FOLDER_ID}\n"
            f"• Guarda la base de datos de cuentas y playlists ({CLOUD_DB_FILENAME}).\n"
            "• Dentro busca la subcarpeta «Musica» para escuchar en línea, descargar (con filtro de duplicados) o subir más canciones."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #A8A8B3; font-size: 11px; font-weight: normal;")
        lay.addWidget(sub)

        # Botón para cargar credentials.json o iniciar OAuth2 en navegador
        oauth_row = QHBoxLayout()
        btn_load_json = QPushButton("📂 Cargar credentials.json")
        btn_load_json.setObjectName("SecondaryBtn")
        btn_load_json.clicked.connect(self._load_credentials_json)

        self.btn_browser_oauth = QPushButton("🔑 Iniciar sesión con Google (Navegador)")
        self.btn_browser_oauth.clicked.connect(self._start_browser_oauth)

        oauth_row.addWidget(btn_load_json)
        oauth_row.addWidget(self.btn_browser_oauth)
        lay.addLayout(oauth_row)

        form = QFormLayout()
        form.setSpacing(8)

        self.inp_token = QLineEdit(config.access_token)
        self.inp_token.setPlaceholderText("ya29.a0AfH6SM... (Token OAuth2 para subir archivos y guardar BD en Drive)")

        self.inp_folder = QLineEdit(config.folder_id or DEFAULT_ROOT_DRIVE_FOLDER_ID)
        self.inp_folder.setPlaceholderText(f"Por defecto: {DEFAULT_ROOT_DRIVE_FOLDER_ID} (contiene subcarpeta Musica)")

        self.inp_api_key = QLineEdit(config.api_key)
        self.inp_api_key.setPlaceholderText("Opcional: Google Drive API Key (AIzaSy...)")

        self.inp_client_id = QLineEdit(config.client_id)
        self.inp_client_id.setPlaceholderText("Opcional para login OAuth: Client ID")

        self.inp_client_secret = QLineEdit(config.client_secret)
        self.inp_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.inp_client_secret.setPlaceholderText("Opcional para login OAuth: Client Secret")

        form.addRow("Token OAuth 2.0:", self.inp_token)
        form.addRow("Carpeta Raíz Drive:", self.inp_folder)
        form.addRow("API Key (Opcional):", self.inp_api_key)
        form.addRow("Client ID:", self.inp_client_id)
        form.addRow("Client Secret:", self.inp_client_secret)
        lay.addLayout(form)

        lbl_links = QLabel("Enlaces directos adicionales a canciones de Drive (uno por línea, opcional):")
        lay.addWidget(lbl_links)

        self.txt_direct_links = QPlainTextEdit()
        self.txt_direct_links.setPlaceholderText(
            "https://drive.google.com/file/d/1A2B3C4D5E6F.../view?usp=sharing\n"
            "https://drive.google.com/file/d/..."
        )
        self.txt_direct_links.setFixedHeight(68)
        lay.addWidget(self.txt_direct_links)

        self.chk_preload = QCheckBox(
            "⬇️ Descargar la música a la carpeta de la aplicación (Desmarcado = 🎧 Escuchar en línea)"
        )
        self.chk_preload.setStyleSheet("color: #CFCFD8; font-size: 12px;")
        lay.addWidget(self.chk_preload)

        self.chk_dedup = QCheckBox(
            "🛡️ Activar verificación de duplicados al descargar canciones de Google Drive"
        )
        self.chk_dedup.setChecked(bool(getattr(config, "verify_duplicates", True)))
        self.chk_dedup.setStyleSheet("color: #1ED760; font-size: 12px; font-weight: 700;")
        lay.addWidget(self.chk_dedup)

        self.lbl_status = QLabel("Listo para sincronizar la carpeta Musica de Drive.")
        self.lbl_status.setStyleSheet("color: #1DB954; font-size: 11px;")
        lay.addWidget(self.lbl_status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("☁️ Sincronizar Ahora")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _load_credentials_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar credentials.json de Google Cloud", str(Path.home()), "JSON (*.json)"
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            block = raw.get("installed") or raw.get("web") or raw
            cid = block.get("client_id", "")
            csec = block.get("client_secret", "")
            tok = block.get("access_token") or block.get("token", "")
            rtok = block.get("refresh_token", "")
            if cid:
                self.inp_client_id.setText(cid)
            if csec:
                self.inp_client_secret.setText(csec)
            if tok:
                self.inp_token.setText(tok)
            if rtok:
                self.config.refresh_token = rtok
            self.lbl_status.setText("✔ Archivo credentials.json cargado correctamente.")
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"No se pudo leer el archivo JSON:\n{exc}")

    def _start_browser_oauth(self) -> None:
        cid = self.inp_client_id.text().strip()
        csec = self.inp_client_secret.text().strip()
        if not cid or not csec:
            QMessageBox.information(
                self,
                "Falta Client ID / Secret",
                "Primero carga tu archivo credentials.json o escribe tu Client ID y Client Secret de Google Cloud.\n\n"
                "Alternativamente, pega directamente tu Token OAuth 2.0 o sincroniza la carpeta compartida.",
            )
            return

        self.lbl_status.setText("Abriendo navegador para autorizar acceso a Google Drive...")
        if self._oauth_worker is not None and self._oauth_worker.isRunning():
            self.lbl_status.setText("Ya hay una autorización en curso; completa o cierra la pestaña del navegador.")
            return
        self._oauth_worker = GDriveOAuthLoopbackWorker(cid, csec)
        self._oauth_worker.oauth_completed.connect(self._on_oauth_success)
        self._oauth_worker.oauth_error.connect(lambda err: self.lbl_status.setText(f"Error OAuth: {err}"))
        self._oauth_worker.start()

    def _on_oauth_success(self, access_token: str, refresh_token: str) -> None:
        self.inp_token.setText(access_token)
        if refresh_token:
            self.config.refresh_token = refresh_token
        self.lbl_status.setText("✔ ¡Conectado con éxito a Google Drive! Haz clic en Sincronizar Ahora.")

    def get_result(self) -> tuple[GoogleDriveConfig, list[str], bool]:
        cfg = GoogleDriveConfig(
            access_token=self.inp_token.text(),
            refresh_token=self.config.refresh_token,
            client_id=self.inp_client_id.text(),
            client_secret=self.inp_client_secret.text(),
            api_key=self.inp_api_key.text(),
            folder_id=self.inp_folder.text().strip() or DEFAULT_ROOT_DRIVE_FOLDER_ID,
            musica_folder_id=self.config.musica_folder_id,
            check_duplicates=self.chk_dedup.isChecked(),
        )
        links = [
            line.strip()
            for line in self.txt_direct_links.toPlainText().splitlines()
            if line.strip()
        ]
        return cfg, links, self.chk_preload.isChecked()


class AccountDialog(QDialog):
    """
    Modal para Crear Cuenta, Iniciar Sesión, Cambiar Contraseña y Recuperar Cuenta.
    Guarda y sincroniza la base de datos de cuentas, playlists y canciones de Drive
    en https://drive.google.com/drive/folders/1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT (spotilinux_database.json).
    """

    def __init__(self, parent: QWidget, store: LibraryStore) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("👤 Cuenta MaoMusic — Sincronización y Seguridad en Google Drive")
        self.setMinimumSize(540, 520)
        self.mode: str = "register"  # "register" | "login" | "change" | "recover"
        self.authenticated_account: Optional[dict] = None
        self.logged_out: bool = False

        self.setStyleSheet(
            """
            QDialog { background-color: #181820; color: #FFFFFF; }
            QLabel { color: #E4E4EC; font-size: 12px; font-weight: 600; }
            QLineEdit {
                background-color: #262632; color: #FFFFFF;
                border: 1px solid #424254; border-radius: 8px; padding: 8px 10px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #1ED760; }
            QPushButton {
                background-color: #1DB954; color: #050505; border: none;
                border-radius: 16px; padding: 8px 18px; font-weight: 800; font-size: 12px;
            }
            QPushButton:hover { background-color: #1ED760; }
            QPushButton#TabBtn {
                background-color: #262632; color: #CFCFD8; border: 1px solid #3E3E4E; border-radius: 14px;
                padding: 6px 10px; font-size: 11px;
            }
            QPushButton#TabBtn[active="true"] {
                background-color: rgba(29, 185, 84, 0.22); color: #1ED760; border: 1px solid #1ED760;
            }
            QPushButton#DangerBtn {
                background-color: rgba(235, 87, 87, 0.18); color: #FF7B7B; border: 1px solid rgba(235, 87, 87, 0.45);
            }
            """
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(10)

        title = QLabel("👤 Cuenta MaoMusic Multi-Dispositivo")
        title.setStyleSheet("font-size: 17px; font-weight: 900; color: #1ED760;")
        lay.addWidget(title)

        desc = QLabel(
            f"Tus playlists y canciones se sincronizan con tu cuenta en:\n"
            f"☁️ Drive ID: {DEFAULT_ROOT_DRIVE_FOLDER_ID} ({CLOUD_DB_FILENAME} + carpeta «Musica»)"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #A6A6B4; font-size: 11px; font-weight: normal;")
        lay.addWidget(desc)

        if self.store.user_account.get("username"):
            curr_u = self.store.user_account.get("username")
            curr_e = self.store.user_account.get("email")
            curr_k = self.store.user_account.get("recovery_key") or "No asignada (usa Recuperar)"
            active_box = QFrame()
            active_box.setStyleSheet(
                "background-color: rgba(29, 185, 84, 0.12); border: 1px solid rgba(29, 185, 84, 0.4); border-radius: 8px;"
            )
            ab_lay = QVBoxLayout(active_box)
            ab_lay.setContentsMargins(10, 8, 10, 8)
            ab_lay.setSpacing(4)

            top_h = QHBoxLayout()
            lbl_curr = QLabel(f"✔ Sesión activa: <b>{curr_u}</b> ({curr_e})")
            lbl_curr.setStyleSheet("color: #1ED760; font-size: 12px; border: none; background: transparent;")
            btn_logout = QPushButton("Cerrar sesión")
            btn_logout.setObjectName("DangerBtn")
            btn_logout.clicked.connect(self._do_logout)
            top_h.addWidget(lbl_curr, stretch=1)
            top_h.addWidget(btn_logout)
            ab_lay.addLayout(top_h)

            rec_lbl = QLabel(f"🛡️ Clave de recuperación: <b style='color:#FFF;'>{curr_k}</b>")
            rec_lbl.setStyleSheet("color: #A6A6B4; font-size: 11px; border: none; background: transparent;")
            ab_lay.addWidget(rec_lbl)
            lay.addWidget(active_box)

        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self.btn_tab_register = QPushButton("✨ Crear Cuenta")
        self.btn_tab_register.setObjectName("TabBtn")
        self.btn_tab_register.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_register.clicked.connect(lambda: self._set_mode("register"))

        self.btn_tab_login = QPushButton("🔑 Iniciar Sesión")
        self.btn_tab_login.setObjectName("TabBtn")
        self.btn_tab_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_login.clicked.connect(lambda: self._set_mode("login"))

        self.btn_tab_change = QPushButton("🔐 Cambiar Clave")
        self.btn_tab_change.setObjectName("TabBtn")
        self.btn_tab_change.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_change.clicked.connect(lambda: self._set_mode("change"))

        self.btn_tab_recover = QPushButton("🆘 Recuperar")
        self.btn_tab_recover.setObjectName("TabBtn")
        self.btn_tab_recover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_recover.clicked.connect(lambda: self._set_mode("recover"))

        tab_row.addWidget(self.btn_tab_register)
        tab_row.addWidget(self.btn_tab_login)
        tab_row.addWidget(self.btn_tab_change)
        tab_row.addWidget(self.btn_tab_recover)
        lay.addLayout(tab_row)

        self.form = QFormLayout()
        self.form.setSpacing(10)

        self.lbl_user = QLabel("Usuario:")
        self.inp_username = QLineEdit(self.store.user_account.get("username", ""))
        self.inp_username.setPlaceholderText("Ej. jinsoon005")

        self.lbl_email = QLabel("Correo electrónico:")
        self.inp_email = QLineEdit(self.store.user_account.get("email", ""))
        self.inp_email.setPlaceholderText("ejemplo@correo.com")

        self.lbl_pass = QLabel("Contraseña:")
        self.inp_password = QLineEdit()
        self.inp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.inp_password.setPlaceholderText("Contraseña de tu cuenta MaoMusic")
        self._add_password_toggle(self.inp_password)

        self.lbl_new_pass = QLabel("Nueva Contraseña:")
        self.inp_new_password = QLineEdit()
        self.inp_new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.inp_new_password.setPlaceholderText("Nueva contraseña segura")
        self._add_password_toggle(self.inp_new_password)

        self.lbl_recovery_key = QLabel("Clave de Recuperación:")
        self.inp_recovery_key = QLineEdit()
        self.inp_recovery_key.setPlaceholderText("Ej. MAO-ABCD-1234 o tu correo registrado")

        self.lbl_token = QLabel("Token Drive (Opcional):")
        self.inp_token = QLineEdit(self.store.gdrive_config.access_token)
        self.inp_token.setPlaceholderText("Opcional: Token OAuth2 de Google Drive")

        self.form.addRow(self.lbl_user, self.inp_username)
        self.form.addRow(self.lbl_email, self.inp_email)
        self.form.addRow(self.lbl_pass, self.inp_password)
        self.form.addRow(self.lbl_new_pass, self.inp_new_password)
        self.form.addRow(self.lbl_recovery_key, self.inp_recovery_key)
        self.form.addRow(self.lbl_token, self.inp_token)
        lay.addLayout(self.form)

        self.lbl_feedback = QLabel("Completa usuario, correo y contraseña para vincular tus datos.")
        self.lbl_feedback.setWordWrap(True)
        self.lbl_feedback.setStyleSheet("color: #1ED760; font-size: 11px;")
        lay.addWidget(self.lbl_feedback)

        lay.addStretch()

        bottom_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("TabBtn")
        btn_cancel.clicked.connect(self.reject)

        self.btn_submit = QPushButton("✨ Crear Cuenta y Guardar en Drive")
        self.btn_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_submit.clicked.connect(self._on_submit)

        bottom_row.addStretch()
        bottom_row.addWidget(btn_cancel)
        bottom_row.addWidget(self.btn_submit)
        lay.addLayout(bottom_row)

        self._set_mode("register")

    def _add_password_toggle(self, line_edit: QLineEdit) -> QAction:
        act = QAction("👁", line_edit)
        act.setToolTip("Mostrar / Ocultar contraseña")

        def _toggle():
            if line_edit.echoMode() == QLineEdit.EchoMode.Password:
                line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
                act.setText("🔒")
            else:
                line_edit.setEchoMode(QLineEdit.EchoMode.Password)
                act.setText("👁")

        act.triggered.connect(_toggle)
        line_edit.addAction(act, QLineEdit.ActionPosition.TrailingPosition)
        return act

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        tabs = (
            (self.btn_tab_register, mode == "register"),
            (self.btn_tab_login, mode == "login"),
            (self.btn_tab_change, mode == "change"),
            (self.btn_tab_recover, mode == "recover"),
        )
        for btn, active in tabs:
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if mode == "register":
            self.lbl_user.setText("Usuario:")
            self.lbl_user.setVisible(True)
            self.inp_username.setVisible(True)
            self.inp_username.setPlaceholderText("Nombre de usuario (ej. maomao_fan)")
            self.lbl_email.setVisible(True)
            self.inp_email.setVisible(True)
            self.lbl_pass.setText("Contraseña:")
            self.lbl_pass.setVisible(True)
            self.inp_password.setVisible(True)
            self.lbl_new_pass.setVisible(False)
            self.inp_new_password.setVisible(False)
            self.lbl_recovery_key.setVisible(False)
            self.inp_recovery_key.setVisible(False)
            self.btn_submit.setText("✨ Crear Cuenta y Guardar en Drive")
            self.lbl_feedback.setText("Se creará tu cuenta con una clave de recuperación única y sincronización a Drive.")

        elif mode == "login":
            self.lbl_user.setText("Usuario o Correo:")
            self.lbl_user.setVisible(True)
            self.inp_username.setVisible(True)
            self.inp_username.setPlaceholderText("Escribe tu usuario o tu correo electrónico")
            self.lbl_email.setVisible(False)
            self.inp_email.setVisible(False)
            self.lbl_pass.setText("Contraseña:")
            self.lbl_pass.setVisible(True)
            self.inp_password.setVisible(True)
            self.lbl_new_pass.setVisible(False)
            self.inp_new_password.setVisible(False)
            self.lbl_recovery_key.setVisible(False)
            self.inp_recovery_key.setVisible(False)
            self.btn_submit.setText("🔑 Iniciar Sesión y Recuperar Playlists")
            self.lbl_feedback.setText("Ingresa tu usuario/correo y contraseña para cargar tus playlists y canciones.")

        elif mode == "change":
            self.lbl_user.setText("Usuario o Correo:")
            self.lbl_user.setVisible(True)
            self.inp_username.setVisible(True)
            self.inp_username.setPlaceholderText("Tu usuario o correo de la cuenta")
            self.lbl_email.setVisible(False)
            self.inp_email.setVisible(False)
            self.lbl_pass.setText("Contraseña actual:")
            self.lbl_pass.setVisible(True)
            self.inp_password.setVisible(True)
            self.lbl_new_pass.setText("Nueva Contraseña:")
            self.lbl_new_pass.setVisible(True)
            self.inp_new_password.setVisible(True)
            self.lbl_recovery_key.setVisible(False)
            self.inp_recovery_key.setVisible(False)
            self.btn_submit.setText("🔐 Actualizar Contraseña en Drive")
            self.lbl_feedback.setText("Ingresa tu contraseña actual y la nueva contraseña para cambiarla en todos tus dispositivos.")

        elif mode == "recover":
            self.lbl_user.setText("Usuario o Correo:")
            self.lbl_user.setVisible(True)
            self.inp_username.setVisible(True)
            self.inp_username.setPlaceholderText("Tu usuario o correo registrado")
            self.lbl_email.setVisible(False)
            self.inp_email.setVisible(False)
            self.lbl_pass.setVisible(False)
            self.inp_password.setVisible(False)
            self.lbl_recovery_key.setVisible(True)
            self.inp_recovery_key.setVisible(True)
            self.lbl_new_pass.setText("Nueva Contraseña:")
            self.lbl_new_pass.setVisible(True)
            self.inp_new_password.setVisible(True)
            self.btn_submit.setText("🆘 Restablecer Contraseña")
            self.lbl_feedback.setText("Ingresa tu clave de recuperación (ej. MAO-XXXX-YYYY) o tu correo para reestablecer tu clave.")

    def _do_logout(self) -> None:
        self.store.user_account = {}
        self.store.save()
        self.logged_out = True
        self.accept()

    def _collect_current_drive_tracks(self) -> list[dict]:
        items: list[dict] = []
        for t in self.store.tracks.values():
            if t.filepath.startswith("gdrive://") or t.id.startswith("gd_"):
                fid = t.filepath.replace("gdrive://", "") if t.filepath.startswith("gdrive://") else t.id[3:]
                items.append(
                    {
                        "id": t.id,
                        "file_id": fid,
                        "title": t.title,
                        "artist": t.artist,
                        "album": t.album,
                        "genre": t.genre,
                        "duration": t.duration,
                        "filepath": f"gdrive://{fid}" if fid else t.filepath,
                    }
                )
        return items

    def _upload_db(self, db: dict, existing_file_id) -> bool:
        """Sube la BD de cuentas y avisa honestamente si no se pudo sincronizar con la nube."""
        ok, msg = upload_cloud_database(self.store.gdrive_config, self.store.accounts_db_path, db, existing_file_id)
        if not ok:
            QMessageBox.warning(self, "Sincronización pendiente", msg)
        return ok

    def _on_submit(self) -> None:
        tok = self.inp_token.text().strip()
        if tok:
            self.store.gdrive_config.access_token = tok
            self.store.save()

        username = self.inp_username.text().strip()
        email = self.inp_email.text().strip().lower()
        password = self.inp_password.text()
        new_password = self.inp_new_password.text()
        recovery_key_input = self.inp_recovery_key.text().strip()

        if self.mode == "register":
            if not username or not email or not password:
                QMessageBox.warning(
                    self,
                    "Campos requeridos",
                    "Por favor ingresa tu Usuario, Correo electrónico y Contraseña para crear la cuenta.",
                )
                return
            if "@" not in email:
                QMessageBox.warning(self, "Correo inválido", "Por favor ingresa un correo electrónico válido.")
                return
        elif self.mode == "login":
            if not username or not password:
                QMessageBox.warning(
                    self,
                    "Campos requeridos",
                    "Por favor ingresa tu Usuario (o Correo) y tu Contraseña para iniciar sesión.",
                )
                return
        elif self.mode == "change":
            if not username or not password or not new_password:
                QMessageBox.warning(
                    self,
                    "Campos requeridos",
                    "Ingresa tu Usuario/Correo, tu Contraseña actual y la Nueva Contraseña.",
                )
                return
            if len(new_password) < 4:
                QMessageBox.warning(
                    self,
                    "Contraseña muy corta",
                    "La nueva contraseña debe tener al menos 4 caracteres.",
                )
                return
        elif self.mode == "recover":
            if not username or not recovery_key_input or not new_password:
                QMessageBox.warning(
                    self,
                    "Campos requeridos",
                    "Ingresa tu Usuario/Correo, tu Clave de Recuperación y la Nueva Contraseña.",
                )
                return
            if len(new_password) < 4:
                QMessageBox.warning(
                    self,
                    "Contraseña muy corta",
                    "La nueva contraseña debe tener al menos 4 caracteres.",
                )
                return

        self.lbl_feedback.setText("☁️ Conectando con la base de datos en Google Drive...")
        self.btn_submit.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self._process_submit(username, email, password, new_password, recovery_key_input)
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_submit.setEnabled(True)

    def _process_submit(self, username: str, email: str, password: str, new_password: str, recovery_key_input: str) -> None:

        db, existing_file_id = download_cloud_database(self.store.gdrive_config, self.store.accounts_db_path)
        accounts = db.setdefault("accounts", {})
        pw_hash = hash_password(password)

        if self.mode == "register":
            acc_key = email
            existing = accounts.get(acc_key)
            if existing and existing.get("password_hash") and existing.get("password_hash") != pw_hash:
                QMessageBox.warning(
                    self,
                    "Cuenta ya registrada",
                    "Ya existe una cuenta con ese correo y una contraseña distinta. Usa «Iniciar Sesión» o «Recuperar».",
                )
                return

            recovery_key = (existing and existing.get("recovery_key")) or generate_recovery_key(email)
            new_record = {
                "username": username,
                "email": email,
                "password_hash": pw_hash,
                "recovery_key": recovery_key,
                "updated_at": time.time(),
                "playlists": {pid: pl.to_dict() for pid, pl in self.store.playlists.items()},
                "liked_ids": list(self.store.liked_ids),
                "drive_tracks": self._collect_current_drive_tracks(),
            }
            merged_record = merge_account_records(existing or {}, new_record)
            merged_record["recovery_key"] = recovery_key
            accounts[acc_key] = merged_record
            self._upload_db(db, existing_file_id)
            self.authenticated_account = merged_record

            QMessageBox.information(
                self,
                "🎉 Cuenta Creada Exitosamente",
                f"Tu cuenta ha sido creada y sincronizada en Google Drive.\n\n"
                f"🛡️ TU CLAVE DE RECUPERACIÓN ES:\n"
                f"{recovery_key}\n\n"
                f"Guárdala en un lugar seguro. Si alguna vez olvidas tu contraseña, "
                f"esta clave te permitirá restablecerla al instante.",
            )
            self.accept()

        elif self.mode == "login":
            ident_low = username.lower()
            found_key = None
            found_acc = None
            for k, acc in accounts.items():
                if not isinstance(acc, dict):
                    continue
                if k.lower() == ident_low or str(acc.get("email", "")).lower() == ident_low or str(acc.get("username", "")).lower() == ident_low:
                    found_key = k
                    found_acc = acc
                    break

            if not found_acc:
                QMessageBox.warning(
                    self,
                    "Cuenta no encontrada",
                    f"No se encontró ninguna cuenta para «{username}».\nPuedes crearla en la pestaña «Crear Cuenta».",
                )
                return

            if found_acc.get("password_hash") != pw_hash:
                QMessageBox.warning(self, "Contraseña incorrecta", "La contraseña ingresada no coincide con la cuenta. Si la olvidaste, usa la pestaña «Recuperar».")
                return

            # Asignar recovery_key si no tenía
            if not found_acc.get("recovery_key"):
                found_acc["recovery_key"] = generate_recovery_key(found_acc.get("email", found_key))

            current_record = {
                "username": found_acc.get("username", username),
                "email": found_acc.get("email", found_key),
                "password_hash": pw_hash,
                "recovery_key": found_acc.get("recovery_key"),
                "updated_at": time.time(),
                "playlists": {pid: pl.to_dict() for pid, pl in self.store.playlists.items()},
                "liked_ids": list(self.store.liked_ids),
                "drive_tracks": self._collect_current_drive_tracks(),
            }
            merged_record = merge_account_records(found_acc, current_record)
            accounts[found_key] = merged_record
            self._upload_db(db, existing_file_id)
            self.authenticated_account = merged_record
            self.accept()

        elif self.mode == "change":
            ident_low = username.lower()
            found_key = None
            found_acc = None
            for k, acc in accounts.items():
                if not isinstance(acc, dict):
                    continue
                if k.lower() == ident_low or str(acc.get("email", "")).lower() == ident_low or str(acc.get("username", "")).lower() == ident_low:
                    found_key = k
                    found_acc = acc
                    break

            if not found_acc:
                QMessageBox.warning(self, "Cuenta no encontrada", f"No se encontró la cuenta «{username}».")
                return

            if found_acc.get("password_hash") != pw_hash:
                QMessageBox.warning(self, "Contraseña incorrecta", "La contraseña actual no coincide.")
                return

            new_pw_hash = hash_password(new_password)
            found_acc["password_hash"] = new_pw_hash
            found_acc["updated_at"] = time.time()
            if not found_acc.get("recovery_key"):
                found_acc["recovery_key"] = generate_recovery_key(found_acc.get("email", found_key))

            accounts[found_key] = found_acc
            self._upload_db(db, existing_file_id)

            if self.store.user_account.get("email") == found_acc.get("email"):
                self.store.user_account["password_hash"] = new_pw_hash
                self.store.save()

            QMessageBox.information(
                self,
                "🔐 Contraseña Actualizada",
                "Tu contraseña ha sido cambiada y sincronizada correctamente en Google Drive.",
            )
            self._set_mode("login")

        elif self.mode == "recover":
            ident_low = username.lower()
            found_key = None
            found_acc = None
            for k, acc in accounts.items():
                if not isinstance(acc, dict):
                    continue
                if k.lower() == ident_low or str(acc.get("email", "")).lower() == ident_low or str(acc.get("username", "")).lower() == ident_low:
                    found_key = k
                    found_acc = acc
                    break

            if not found_acc:
                QMessageBox.warning(self, "Cuenta no encontrada", f"No se encontró la cuenta «{username}».")
                return

            if not validate_recovery_key(found_acc, recovery_key_input):
                QMessageBox.warning(
                    self,
                    "Clave inválida",
                    "La clave de recuperación o correo ingresado no coincide con la cuenta registrada.",
                )
                return

            new_pw_hash = hash_password(new_password)
            if not found_acc.get("recovery_key") or found_acc.get("recovery_key").startswith("MAO-REC-"):
                found_acc["recovery_key"] = generate_recovery_key(found_acc.get("email", found_key))

            found_acc["password_hash"] = new_pw_hash
            found_acc["updated_at"] = time.time()
            accounts[found_key] = found_acc
            self._upload_db(db, existing_file_id)

            QMessageBox.information(
                self,
                "🎉 Cuenta Recuperada",
                f"¡Se ha restablecido la contraseña con éxito!\n\n"
                f"Tu clave de recuperación es:\n{found_acc.get('recovery_key')}\n\n"
                f"Ya puedes iniciar sesión con tu nueva contraseña.",
            )
            self.inp_password.setText(new_password)
            self._set_mode("login")


GITHUB_VERSION_URL = "https://raw.githubusercontent.com/1230235-cloud/spotilinux/main/version.json"
GITHUB_RELEASES_URL = "https://github.com/1230235-cloud/spotilinux/releases"


def fetch_remote_version(url: str = GITHUB_VERSION_URL, timeout: int = 10) -> dict:
    """Descarga y valida version.json remoto. Lanza excepción con mensaje claro si falla."""
    req = urllib.request.Request(f"{url}?_t={int(time.time())}", headers={"User-Agent": "SpotiLinux-Desktop"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GitHub respondió HTTP {resp.status}")
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict) or "versionCode" not in data:
        raise ValueError("version.json remoto inválido (falta versionCode)")
    data["versionCode"] = int(data["versionCode"])
    return data


class GitHubDesktopUpdateWorker(QThread):
    update_available = pyqtSignal(dict)
    up_to_date = pyqtSignal(dict)
    check_failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            data = fetch_remote_version()
        except Exception as exc:
            logging.getLogger("maomusic").warning("Comprobación de actualización falló: %s", exc)
            self.check_failed.emit(str(exc))
            return
        if data["versionCode"] > APP_VERSION_CODE:
            self.update_available.emit(data)
        else:
            self.up_to_date.emit(data)


class GitUpdateWorker(QThread):
    """Aplica la actualización con git fetch + merge --ff-only (sin tocar cambios locales)."""

    update_done = pyqtSignal(str)
    update_failed = pyqtSignal(str)

    def __init__(self, repo_dir: Path) -> None:
        super().__init__()
        self.repo_dir = repo_dir

    def _git(self, *args: str) -> "subprocess.CompletedProcess":
        import subprocess

        return subprocess.run(
            ["git", *args], cwd=str(self.repo_dir), capture_output=True, text=True, timeout=90
        )

    def run(self) -> None:
        try:
            fetch = self._git("fetch", "origin", "main")
            if fetch.returncode != 0:
                self.update_failed.emit(f"No se pudo contactar GitHub:\n{fetch.stderr or fetch.stdout}")
                return
            merge = self._git("merge", "--ff-only", "origin/main")
            if merge.returncode != 0:
                self.update_failed.emit(
                    "No se pudo aplicar la actualización automáticamente (hay cambios locales en archivos versionados).\n\n"
                    f"{(merge.stderr or merge.stdout).strip()}"
                )
                return
            self.update_done.emit((merge.stdout or "").strip())
        except Exception as exc:
            logging.getLogger("maomusic").exception("Fallo al actualizar")
            self.update_failed.emit(str(exc))


class YouTubeBatchDownloadDialog(QDialog):
    """Diálogo con recuadro multi-línea para descargar listas de canciones o enlaces de YouTube."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📥 Descargar y Sincronizar desde YouTube — MaoMusic")
        self.setFixedSize(540, 440)
        self.setStyleSheet("""
            QDialog {
                background-color: #121214;
                color: #FFFFFF;
                border: 1px solid #27272A;
                border-radius: 12px;
            }
            QLabel {
                color: #E4E4E7;
                font-size: 13px;
            }
            QPlainTextEdit {
                background-color: #18181B;
                color: #FAFAFA;
                border: 1px solid #3F3F46;
                border-radius: 8px;
                padding: 10px;
                font-family: monospace, sans-serif;
                font-size: 13px;
                selection-background-color: #10B981;
            }
            QPlainTextEdit:focus {
                border: 1px solid #10B981;
            }
            QCheckBox {
                color: #A1A1AA;
                font-size: 12px;
            }
            QPushButton#BtnCancel {
                background-color: #27272A;
                color: #A1A1AA;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton#BtnCancel:hover {
                background-color: #3F3F46;
                color: #FFFFFF;
            }
            QPushButton#BtnDownload {
                background-color: #10B981;
                color: #000000;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-weight: 700;
            }
            QPushButton#BtnDownload:hover {
                background-color: #059669;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QLabel("🎵 Lista de Canciones o Videos a Descargar")
        header.setStyleSheet("font-size: 16px; font-weight: 800; color: #FFFFFF;")
        layout.addWidget(header)

        # Destino de Playlist: permite seleccionar una Playlist en la Nube existente o escribir una nueva
        pl_row = QHBoxLayout()
        lbl_pl = QLabel("Playlist en la Nube:")
        lbl_pl.setStyleSheet("color: #E4E4E7; font-size: 12px; font-weight: 700;")

        # Obtener playlists existentes (priorizando las que están en la nube)
        cloud_pls: list[str] = []
        other_pls: list[str] = []
        try:
            target_store = getattr(parent, "store", None)
            if not target_store and hasattr(parent, "window"):
                target_store = getattr(parent.window(), "store", None)
            if target_store and hasattr(target_store, "playlists"):
                for p in target_store.playlists.values():
                    if getattr(p, "is_cloud", False):
                        cloud_pls.append(p.name)
                    else:
                        other_pls.append(p.name)
        except Exception:
            pass

        self.cmb_playlist = QComboBox()
        self.cmb_playlist.setEditable(True)
        self.cmb_playlist.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cmb_playlist.setStyleSheet("""
            QComboBox {
                background-color: #18181B;
                color: #FAFAFA;
                border: 1px solid #3F3F46;
                border-radius: 9999px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QComboBox:focus {
                border: 1px solid #10B981;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QComboBox QAbstractItemView {
                background-color: #18181B;
                color: #FAFAFA;
                selection-background-color: #27272A;
                selection-color: #10B981;
                border: 1px solid #3F3F46;
                padding: 4px;
            }
        """)

        # Mantener retrocompatibilidad con self.txt_playlist_name
        self.txt_playlist_name = self.cmb_playlist.lineEdit()
        self.txt_playlist_name.setPlaceholderText("Elegir Playlist en la Nube o escribir nombre nuevo...")

        self.cmb_playlist.addItem("➕ (Ninguna / Solo Descargas)")
        for cp in sorted(set(cloud_pls), key=str.lower):
            self.cmb_playlist.addItem(f"☁️ {cp}")
        for op in sorted(set(other_pls), key=str.lower):
            if op not in cloud_pls:
                self.cmb_playlist.addItem(f"🎵 {op}")

        self.cmb_playlist.setCurrentIndex(0)
        self.txt_playlist_name.clear()

        self.btn_load_txt = QPushButton("📂 Cargar .txt")
        self.btn_load_txt.setObjectName("BtnCancel")
        self.btn_load_txt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_load_txt.clicked.connect(self._load_txt_file)

        pl_row.addWidget(lbl_pl)
        pl_row.addWidget(self.cmb_playlist, stretch=1)
        pl_row.addWidget(self.btn_load_txt)
        layout.addLayout(pl_row)

        hint = QLabel("Escribe o pega una o varias canciones (una por línea o enlace directo de YouTube):")
        hint.setStyleSheet("color: #A1A1AA; font-size: 12px;")
        layout.addWidget(hint)

        self.txt_list = QPlainTextEdit()
        self.txt_list.setPlaceholderText(
            "Ejemplo:\n"
            "Otaku encubierto\n"
            "Tu compras oro\n"
            "Envenename\n"
            "Que dolor\n"
            "We don't talk soft rock versión\n"
            "https://www.youtube.com/watch?v=..."
        )
        layout.addWidget(self.txt_list)

        self.chk_local = QCheckBox("Guardar también copia local en music_library/")
        self.chk_local.setChecked(True)
        layout.addWidget(self.chk_local)

        btns_row = QHBoxLayout()
        btns_row.addStretch()

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setObjectName("BtnCancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        btns_row.addWidget(self.btn_cancel)

        self.btn_download = QPushButton("🚀 Iniciar Descarga")
        self.btn_download.setObjectName("BtnDownload")
        self.btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_download.clicked.connect(self.accept)
        btns_row.addWidget(self.btn_download)

        layout.addLayout(btns_row)

    def _load_txt_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar lista de canciones (.txt)",
            "",
            "Archivos de texto (*.txt);;Todos los archivos (*)",
        )
        if not file_path:
            return
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            stem = Path(file_path).stem.replace("_", " ").strip()
            if not self.txt_playlist_name.text().strip() and stem:
                self.txt_playlist_name.setText(stem.title())
            self.txt_list.setPlainText(content)
        except Exception as exc:
            QMessageBox.warning(self, "Error al leer archivo", f"No se pudo cargar el archivo:\n{exc}")

    def get_queries(self) -> list[str]:
        raw = self.txt_list.toPlainText()
        return [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]

    def get_target_playlist(self) -> str:
        raw = self.txt_playlist_name.text().strip()
        if not raw or raw.startswith("➕") or raw.startswith("(Ninguna"):
            return ""
        if raw.startswith("☁️"):
            return raw[2:].strip()
        if raw.startswith("🎵"):
            return raw[2:].strip()
        return raw

    def should_keep_local(self) -> bool:
        return self.chk_local.isChecked()


class SpotiLinuxWindow(QMainWindow):
    HOME_PREVIEW_LIMIT = 4  # canciones visibles en la vista previa reducida del Inicio

    remote_session_detected = pyqtSignal(dict)
    remote_session_dismissed = pyqtSignal()

    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.setWindowTitle("MaoMusic — Music Player & Cloud Drive (Maomao Edition 🌿)")
        self.resize(1380, 850)
        self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)

        self._pending_remote_session: Optional[dict] = None
        self._last_broadcast_session_time: float = 0.0

        # 1. Cargar biblioteca instantáneamente desde base de datos persistente (Arranque ultra rápido)
        self.store = LibraryStore(self.base_dir)

        # 2. Inicializar motor de audio y ecualizador
        self.engine = AudioEngine(self.base_dir / "data" / "eq_cache")
        self.engine.shuffle = self.store.shuffle
        self.engine.repeat_mode = self.store.repeat_mode
        self.engine.set_volume(self.store.volume)
        self.engine.eq_enabled = self.store.eq_enabled
        self.engine.eq_gains = list(self.store.eq_gains)

        self._retired_workers: list = []
        self._git_update_worker = None

        # Servicio desacoplado para operaciones en la nube (Propuesta 1)
        self.cloud_service = CloudSyncManager(
            config=self.store.gdrive_config,
            music_library_dir=self.store.music_library_dir,
            gdrive_cache_dir=self.store.gdrive_cache_dir,
            covers_dir=self.store.covers_dir,
            accounts_db_path=self.store.accounts_db_path,
            parent=self,
        )
        self.cloud_service.stream_progress.connect(lambda msg: self.hero_banner.lbl_subtitle.setText(msg))
        self.cloud_service.stream_ready.connect(self._on_cloud_stream_ready)
        self.cloud_service.stream_failed.connect(lambda _tid, err: QMessageBox.warning(self, "Google Drive Stream", err))

        self.cloud_service.sync_progress.connect(lambda msg: self.hero_banner.lbl_subtitle.setText(f"☁️ {msg}"))
        self.cloud_service.sync_finished.connect(self._on_gdrive_sync_finished)

        self.cloud_service.batch_progress.connect(self._on_playlist_download_progress)
        self.cloud_service.batch_track_saved.connect(self._on_playlist_batch_track_saved)
        self.cloud_service.batch_finished.connect(self._on_playlist_batch_finished)

        self.cloud_service.upload_progress.connect(lambda msg: self.hero_banner.lbl_subtitle.setText(msg))
        self.cloud_service.upload_finished.connect(self._on_upload_to_drive_finished)

        self.cloud_service.cloud_db_sync_completed.connect(
            lambda merged: self._on_cloud_sync_success(merged, notify=getattr(self, "_cloud_sync_notify", False))
        )
        self.cloud_service.cloud_db_sync_failed.connect(
            lambda err: self._on_cloud_sync_failed(err, notify=getattr(self, "_cloud_sync_notify", False))
        )

        # Cola de descargas persistente no bloqueante (Fase 3 & 4)
        self.download_queue = DownloadQueue(self.base_dir / "data" / "download_queue.sqlite")
        self.queue_signals = QueueSignals()
        self.queue_runner = QueueRunner(
            queue=self.download_queue,
            on_event=self._on_queue_runner_event,
            max_parallel=int(self.download_queue.get_meta("max_parallel", "1")),
        )
        self.download_queue_panel: Optional[DownloadQueuePanel] = None
        self._library_rescan_timer = QTimer(self)
        self._library_rescan_timer.setSingleShot(True)
        self._library_rescan_timer.setInterval(2000)
        self._library_rescan_timer.timeout.connect(self.rescan_local_library)
        self.queue_signals.summary_changed.connect(self._update_download_badge)
        self.queue_signals.item_completed_for_playlist.connect(self._on_queue_item_completed)
        self.queue_signals.playlist_enqueued.connect(self._on_playlist_enqueued_from_panel)
        self._reconcile_completed_queue_playlists()
        self.queue_runner.start()

        # Estado de navegación actual
        self.current_view: str = "home"  # "home" | "library" | "gdrive" | "liked" | "recent" | "playlist:<id>"
        self.active_genre_filter: str = "Todo"
        self.displayed_tracks: list[TrackMetadata] = []
        self.table_row_tracks: list[Optional[TrackMetadata]] = []
        self._seeking_slider: bool = False
        self._muted: bool = False
        self._pre_mute_volume: int = self.store.volume

        # Vista previa reducible de la playlist seleccionada en Inicio
        first_pid = next(iter(self.store.playlists), None)
        self.home_preview_key: str = f"playlist:{first_pid}" if first_pid else "liked"
        self.home_preview_collapsed: bool = False
        self.home_preview_expanded: bool = False

        self._build_ui()
        self._connect_signals()
        self._setup_shortcuts()

        # Cargar vista inicial y preparar primera canción (cola = playlist seleccionada, no toda la biblioteca)
        self.switch_view("home")
        self._update_shuffle_ui()
        self._update_download_badge()
        _pl_name, initial_ctx = self._get_home_preview_tracks(self.home_preview_key)
        if not initial_ctx:
            initial_ctx = list(self.displayed_tracks)
        if initial_ctx:
            self.engine.context_tracks = list(initial_ctx)
            self._update_now_playing_ui(initial_ctx[0])

        self.remote_session_detected.connect(self._on_remote_session_detected)
        self.remote_session_dismissed.connect(lambda: self.cross_device_banner.hide() if hasattr(self, "cross_device_banner") else None)

        self._cross_device_poll_timer = QTimer(self)
        self._cross_device_poll_timer.setInterval(20000)
        self._cross_device_poll_timer.timeout.connect(self._check_remote_playback_session)
        self._cross_device_poll_timer.start()

        # Solicitar acceso a Google Drive o rastreo automático desde el inicio
        QTimer.singleShot(450, self._check_first_run_welcome)
        QTimer.singleShot(1200, self._deferred_background_scan)
        QTimer.singleShot(2200, self._check_remote_playback_session)
        QTimer.singleShot(2500, self._check_github_updates)

    def _deferred_background_scan(self) -> None:
        """Escanea carpetas locales en segundo plano para no demorar el arranque inicial."""
        def _scan():
            try:
                demo_dir = self.base_dir / "demo_tracks"
                if not demo_dir.exists():
                    ensure_demo_tracks(demo_dir)
                for folder in list(self.store.watched_folders):
                    self.store.scan_folder(Path(folder))
                if not self.store.tracks and demo_dir.exists():
                    self.store.scan_folder(demo_dir)
                self.store.save()
            except Exception as e:
                pass

        threading.Thread(target=_scan, daemon=True, name="DeferredLibraryScan").start()

    def _check_first_run_welcome(self) -> None:
        if self.store.gdrive_config.access_token and not self.store.google_user.get("name"):
            u = fetch_google_user_profile(self.store.gdrive_config.access_token)
            if u:
                self.store.google_user = u
                self.store.save()

        if self.store.welcome_shown:
            return
        self.store.welcome_shown = True
        self.store.save()

        msg = QMessageBox(self)
        msg.setWindowTitle("☁️ Bienvenido a MaoMusic")
        msg.setText(
            "¿Deseas crear/iniciar sesión en tu Cuenta MaoMusic (vincula tus Playlists y las canciones de la "
            f"carpeta «Musica» del Drive {DEFAULT_ROOT_DRIVE_FOLDER_ID}), configurar Google Drive o rastrear la música de tu PC?"
        )
        btn_acc = msg.addButton("👤 Crear / Iniciar Cuenta", QMessageBox.ButtonRole.AcceptRole)
        btn_drive = msg.addButton("☁️ Configurar Google Drive", QMessageBox.ButtonRole.ActionRole)
        btn_scan = msg.addButton("🔍 Auto-Rastrear Música PC", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("Más tarde", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        if msg.clickedButton() == btn_acc:
            self.open_account_dialog()
        elif msg.clickedButton() == btn_drive:
            self.open_google_drive_dialog()
        elif msg.clickedButton() == btn_scan:
            self.auto_scan_pc_music()

    def _check_github_updates(self, manual: bool = False) -> None:
        self._retire_worker("_updater_worker")
        self._updater_worker = GitHubDesktopUpdateWorker(self)
        self._updater_worker.update_available.connect(self._on_update_available)
        if manual:
            self._updater_worker.up_to_date.connect(
                lambda info: QMessageBox.information(
                    self, "Actualizaciones", f"✔ Ya tienes la última versión (v{APP_VERSION_NAME})."
                )
            )
            self._updater_worker.check_failed.connect(
                lambda err: QMessageBox.warning(
                    self, "Actualizaciones", f"No se pudo comprobar si hay actualizaciones:\n{err}"
                )
            )
        self._updater_worker.start()

    def _on_update_available(self, info: dict) -> None:
        v_name = info.get("versionName", "Nueva")
        changelog_items = info.get("changelog", [])
        cl_text = "\n".join(f"• {x}" for x in changelog_items) if changelog_items else "Mejoras de rendimiento y corrección de errores."
        is_git_repo = (Path(self.base_dir) / ".git").exists()

        msg = QMessageBox(self)
        msg.setWindowTitle("🚀 Actualización de MaoMusic Disponible")
        msg.setText(f"<b>Hay una nueva versión de MaoMusic (v{v_name}); tienes la v{APP_VERSION_NAME}.</b>")
        if is_git_repo:
            msg.setInformativeText(f"Novedades:\n{cl_text}\n\n¿Deseas actualizar ahora desde GitHub?")
            btn_update = msg.addButton("Actualizar ahora", QMessageBox.ButtonRole.AcceptRole)
        else:
            msg.setInformativeText(
                f"Novedades:\n{cl_text}\n\nEsta instalación no es un repositorio git; descarga la nueva versión desde GitHub."
            )
            btn_update = msg.addButton("Abrir página de descargas", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Más tarde", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        if msg.clickedButton() != btn_update:
            return
        if not is_git_repo:
            QDesktopServices.openUrl(QUrl(GITHUB_RELEASES_URL))
            return
        self._retire_worker("_git_update_worker")
        self._git_update_worker = GitUpdateWorker(Path(self.base_dir))
        self._git_update_worker.update_done.connect(self._on_git_update_done)
        self._git_update_worker.update_failed.connect(
            lambda err: QMessageBox.warning(self, "Actualización no aplicada", err)
        )
        self._git_update_worker.start()
        self.statusBar().showMessage("Actualizando desde GitHub...", 5000)

    def _on_git_update_done(self, _output: str) -> None:
        reply = QMessageBox.question(
            self,
            "Actualización completada",
            "✔ MaoMusic se actualizó desde GitHub.\n\n¿Reiniciar ahora para aplicar los cambios?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._stop_all_workers()
            os.execv(sys.executable, [sys.executable] + sys.argv)

    # ================================================================== #
    #                       CONSTRUCCIÓN DE LA UI                        #
    # ================================================================== #

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootContainer")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 0)
        root_layout.setSpacing(8)

        self._build_cross_device_banner(root_layout)

        panels_row = QHBoxLayout()
        panels_row.setSpacing(8)

        self._build_left_sidebar(panels_row)
        self._build_center_panel(panels_row)
        self._build_right_panel(panels_row)

        root_layout.addLayout(panels_row, stretch=1)
        self._build_bottom_player_bar(root_layout)

        # Overlay de tocadiscos de vinilo giratorio y video de fondo (Modo Reposo)
        self.vinyl_overlay = VinylStandbyOverlay(self.base_dir, self)
        self.vinyl_overlay.setVisible(False)

    def _build_cross_device_banner(self, parent_layout: QVBoxLayout) -> None:
        self.cross_device_banner = QFrame()
        self.cross_device_banner.setObjectName("CrossDeviceBanner")
        self.cross_device_banner.setStyleSheet("""
            QFrame#CrossDeviceBanner {
                background-color: #161720;
                border: 1px solid #1ed760;
                border-radius: 9px;
            }
        """)
        self.cross_device_banner.setVisible(False)
        lay = QHBoxLayout(self.cross_device_banner)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(12)

        lbl_icon = QLabel("📱")
        lbl_icon.setStyleSheet("font-size: 16px;")
        lay.addWidget(lbl_icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        lbl_title = QLabel("Sesión en otro dispositivo")
        lbl_title.setStyleSheet("color: #1ed760; font-weight: bold; font-size: 11px;")
        self.lbl_cdb_info = QLabel("Canción • Artista (00:00) [Celular]")
        self.lbl_cdb_info.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 500;")
        text_col.addWidget(lbl_title)
        text_col.addWidget(self.lbl_cdb_info)
        lay.addLayout(text_col, stretch=1)

        self.btn_cdb_resume = QPushButton("▶ Continuar aquí")
        self.btn_cdb_resume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cdb_resume.setStyleSheet("""
            QPushButton {
                background-color: #1ed760;
                color: #000000;
                font-weight: bold;
                font-size: 11px;
                border-radius: 13px;
                padding: 5px 14px;
                border: none;
            }
            QPushButton:hover {
                background-color: #1fdf64;
            }
        """)
        self.btn_cdb_resume.clicked.connect(self._resume_remote_session_track)
        lay.addWidget(self.btn_cdb_resume)

        btn_close = QPushButton("✕")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8e8e9f;
                font-size: 12px;
                font-weight: bold;
                border: none;
                padding: 4px 6px;
            }
            QPushButton:hover {
                color: #ffffff;
            }
        """)
        btn_close.clicked.connect(self.cross_device_banner.hide)
        lay.addWidget(btn_close)

        parent_layout.addWidget(self.cross_device_banner)

    def _build_left_sidebar(self, parent_layout: QHBoxLayout) -> None:
        self.sidebar_container = QWidget()
        self.sidebar_container.setObjectName("SidebarContainer")
        self.sidebar_container.setFixedWidth(258)
        left_col = QVBoxLayout(self.sidebar_container)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)

        nav_card = QFrame()
        nav_card.setObjectName("SidebarCard")
        nav_card.setFixedWidth(258)
        nav_lay = QVBoxLayout(nav_card)
        nav_lay.setContentsMargins(12, 14, 12, 14)
        nav_lay.setSpacing(4)

        logo_row = QHBoxLayout()
        lbl_logo_icon = QLabel("●")
        lbl_logo_icon.setStyleSheet("color: #1DB954; font-size: 24px; font-weight: 900;")
        lbl_logo_text = QLabel("MaoMusic")
        lbl_logo_text.setStyleSheet("color: #FFFFFF; font-size: 18px; font-weight: 900; letter-spacing: 0.5px;")
        lbl_maomao_tag = QLabel("🌿 Maomao")
        lbl_maomao_tag.setStyleSheet(
            "background-color: #1E3A2F; color: #1ED760; font-size: 10px; font-weight: bold; "
            "padding: 2px 7px; border-radius: 8px;"
        )
        logo_row.addWidget(lbl_logo_icon)
        logo_row.addWidget(lbl_logo_text)
        logo_row.addStretch()
        logo_row.addWidget(lbl_maomao_tag)
        nav_lay.addLayout(logo_row)
        nav_lay.addSpacing(6)

        self.btn_nav_home = QPushButton("  Inicio")
        self.btn_nav_home.setObjectName("NavButton")
        self.btn_nav_home.setIcon(icons.get_icon("home", "#a0a0ab", 18))
        self.btn_nav_home.setIconSize(QSize(18, 18))
        self.btn_nav_home.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_library = QPushButton("  Tu Biblioteca")
        self.btn_nav_library.setObjectName("NavButton")
        self.btn_nav_library.setIcon(icons.get_icon("library", "#a0a0ab", 18))
        self.btn_nav_library.setIconSize(QSize(18, 18))
        self.btn_nav_library.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_playlists = QPushButton("  Playlists")
        self.btn_nav_playlists.setObjectName("NavButton")
        self.btn_nav_playlists.setIcon(icons.get_icon("playlist", "#a0a0ab", 18))
        self.btn_nav_playlists.setIconSize(QSize(18, 18))
        self.btn_nav_playlists.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_gdrive = QPushButton("  Mi Google Drive")
        self.btn_nav_gdrive.setObjectName("NavButton")
        self.btn_nav_gdrive.setIcon(icons.get_icon("cloud", "#a0a0ab", 18))
        self.btn_nav_gdrive.setIconSize(QSize(18, 18))
        self.btn_nav_gdrive.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_liked = QPushButton("  Canciones que te gustan")
        self.btn_nav_liked.setObjectName("NavButton")
        self.btn_nav_liked.setIcon(icons.get_icon("heart", "#a0a0ab", 18))
        self.btn_nav_liked.setIconSize(QSize(18, 18))
        self.btn_nav_liked.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_recent = QPushButton("  Escuchado recientemente")
        self.btn_nav_recent.setObjectName("NavButton")
        self.btn_nav_recent.setIcon(icons.get_icon("clock", "#a0a0ab", 18))
        self.btn_nav_recent.setIconSize(QSize(18, 18))
        self.btn_nav_recent.setCursor(Qt.CursorShape.PointingHandCursor)

        for b in (self.btn_nav_home, self.btn_nav_library, self.btn_nav_playlists, self.btn_nav_gdrive, self.btn_nav_liked, self.btn_nav_recent):
            nav_lay.addWidget(b)

        left_col.addWidget(nav_card)

        lib_card = QFrame()
        lib_card.setObjectName("SidebarCard")
        lib_card.setFixedWidth(258)
        lib_lay = QVBoxLayout(lib_card)
        lib_lay.setContentsMargins(12, 14, 12, 12)
        lib_lay.setSpacing(8)

        pl_header = QHBoxLayout()
        lbl_pl = QLabel("PLAYLISTS")
        lbl_pl.setStyleSheet("color: #B3B3B3; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        self.btn_new_playlist = QPushButton(" Nueva")
        self.btn_new_playlist.setObjectName("GhostSmallButton")
        self.btn_new_playlist.setIcon(icons.get_icon("plus", "#e4e4ec", 14))
        self.btn_new_playlist.setIconSize(QSize(14, 14))
        self.btn_new_playlist.setCursor(Qt.CursorShape.PointingHandCursor)

        pl_header.addWidget(lbl_pl)
        pl_header.addStretch()
        pl_header.addWidget(self.btn_new_playlist)
        lib_lay.addLayout(pl_header)

        self.playlist_list = QListWidget()
        self.playlist_list.setObjectName("PlaylistSidebarList")
        self.playlist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.playlist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lib_lay.addWidget(self.playlist_list, stretch=1)

        import_box = QVBoxLayout()
        import_box.setSpacing(8)

        self.btn_account_sidebar = QPushButton(" Mi Cuenta")
        self.btn_account_sidebar.setObjectName("ActionChipButton")
        self.btn_account_sidebar.setIcon(icons.get_icon("user", "#e4e4ec", 14))
        self.btn_account_sidebar.setIconSize(QSize(14, 14))
        self.btn_account_sidebar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_account_sidebar.setToolTip("Crea o inicia sesión con tu cuenta MaoMusic")

        self.btn_sidebar_tools = QPushButton(" Herramientas y Nube ▾")
        self.btn_sidebar_tools.setObjectName("ActionChipButton")
        self.btn_sidebar_tools.setIcon(icons.get_icon("tools", "#e4e4ec", 14))
        self.btn_sidebar_tools.setIconSize(QSize(14, 14))
        self.btn_sidebar_tools.setProperty("active", "true")
        self.btn_sidebar_tools.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sidebar_tools.setToolTip("Google Drive, descargas de YouTube, escaneo de música y actualizaciones")

        # Botones de compatibilidad preservados como atributos invisibles para contratos y señales
        self.btn_connect_gdrive = QPushButton()
        self.btn_connect_gdrive.setVisible(False)
        self.btn_upload_gdrive = QPushButton()
        self.btn_upload_gdrive.setVisible(False)
        self.btn_download_yt = QPushButton()
        self.btn_download_yt.setVisible(False)
        self.btn_check_updates = QPushButton()
        self.btn_check_updates.setVisible(False)
        self.btn_auto_scan_pc = QPushButton()
        self.btn_auto_scan_pc.setVisible(False)
        self.btn_import_folder = QPushButton()
        self.btn_import_folder.setVisible(False)
        self.btn_import_files = QPushButton()
        self.btn_import_files.setVisible(False)

        import_box.addWidget(self.btn_account_sidebar)
        import_box.addWidget(self.btn_sidebar_tools)
        lib_lay.addLayout(import_box)

        left_col.addWidget(lib_card, stretch=1)
        parent_layout.addWidget(self.sidebar_container)

    def _build_center_panel(self, parent_layout: QHBoxLayout) -> None:
        center_card = QFrame()
        center_card.setObjectName("MainCard")
        center_lay = QVBoxLayout(center_card)
        center_lay.setContentsMargins(16, 12, 16, 12)
        center_lay.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.btn_toggle_sidebar = QPushButton()
        self.btn_toggle_sidebar.setObjectName("ActionChipButton")
        self.btn_toggle_sidebar.setFixedSize(36, 32)
        self.btn_toggle_sidebar.setIcon(icons.get_icon("menu", "#e4e4ec", 18))
        self.btn_toggle_sidebar.setIconSize(QSize(18, 18))
        self.btn_toggle_sidebar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_sidebar.setToolTip("Mostrar / Ocultar barra lateral (Ctrl+B)")
        top_bar.addWidget(self.btn_toggle_sidebar)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchInput")
        self.search_input.setPlaceholderText("¿Qué quieres reproducir? (título, artista, álbum...)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(200)
        top_bar.addWidget(self.search_input, stretch=1)

        self.btn_view_playlists_tab = QPushButton("🎵 Playlists")
        self.btn_view_playlists_tab.setObjectName("ActionChipButton")
        self.btn_view_playlists_tab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_playlists_tab.clicked.connect(lambda: self.switch_view("playlists_hub"))
        top_bar.addWidget(self.btn_view_playlists_tab)

        self.genre_buttons: dict[str, QPushButton] = {}
        for g_label in ("Todo", "Cloud", "Synthwave", "Lo-Fi", "House", "Electro"):
            btn = QPushButton(g_label)
            btn.setObjectName("ActionChipButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("active", "true" if g_label == "Todo" else "false")
            btn.clicked.connect(lambda _checked, g=g_label: self.set_genre_filter(g))
            self.genre_buttons[g_label] = btn
            top_bar.addWidget(btn)

        # Botón de compatibilidad invisible
        self.btn_top_tools = QPushButton()
        self.btn_top_tools.setVisible(False)

        center_lay.addLayout(top_bar)

        self.hero_banner = HeroBannerWidget()
        center_lay.addWidget(self.hero_banner)

        # Sub-barra interactiva de Playlists (visible en playlists_hub y playlist:*)
        self.playlists_menu_bar = QFrame()
        self.playlists_menu_bar.setObjectName("PlaylistsMenuBar")
        self.playlists_menu_bar.setStyleSheet("""
            QFrame#PlaylistsMenuBar {
                background: #121218;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }
        """)
        pl_menu_lay = QHBoxLayout(self.playlists_menu_bar)
        pl_menu_lay.setContentsMargins(10, 6, 10, 6)
        pl_menu_lay.setSpacing(8)

        lbl_menu_title = QLabel("🎵 MENÚ DE PLAYLISTS:")
        lbl_menu_title.setStyleSheet("color: #1ED760; font-size: 11px; font-weight: 800; letter-spacing: 0.8px;")
        pl_menu_lay.addWidget(lbl_menu_title)

        self.playlists_scroll_area = QScrollArea()
        self.playlists_scroll_area.setFixedHeight(36)
        self.playlists_scroll_area.setWidgetResizable(True)
        self.playlists_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.playlists_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.playlists_scroll_area.setStyleSheet("background: transparent; border: none;")

        self.playlists_pills_container = QWidget()
        self.playlists_pills_container.setStyleSheet("background: transparent;")
        self.playlists_pills_layout = QHBoxLayout(self.playlists_pills_container)
        self.playlists_pills_layout.setContentsMargins(0, 0, 0, 0)
        self.playlists_pills_layout.setSpacing(6)
        self.playlists_scroll_area.setWidget(self.playlists_pills_container)
        pl_menu_lay.addWidget(self.playlists_scroll_area, stretch=1)

        self.btn_import_txt_to_pl = QPushButton("📥 Subir .txt → Crear Playlist")
        self.btn_import_txt_to_pl.setObjectName("ActionChipButton")
        self.btn_import_txt_to_pl.setProperty("active", "true")
        self.btn_import_txt_to_pl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import_txt_to_pl.clicked.connect(self._import_txt_to_playlist_dialog)
        pl_menu_lay.addWidget(self.btn_import_txt_to_pl)

        self.playlists_menu_bar.setVisible(False)
        center_lay.addWidget(self.playlists_menu_bar)

        self.quick_scroll = QScrollArea()
        self.quick_scroll.setObjectName("HomeScroll")
        self.quick_scroll.setWidgetResizable(True)
        self.quick_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.quick_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Estilos acotados por nombre: un estilo sin selector se heredaría a todos los botones hijos
        self.quick_scroll.setStyleSheet("QScrollArea#HomeScroll { background: transparent; border: none; }")

        self.quick_grid_container = QWidget()
        self.quick_grid_container.setObjectName("HomeGridContainer")
        self.quick_grid_container.setStyleSheet("QWidget#HomeGridContainer { background: transparent; }")
        self.quick_grid_main_layout = QVBoxLayout(self.quick_grid_container)
        self.quick_grid_main_layout.setContentsMargins(4, 2, 4, 10)
        self.quick_grid_main_layout.setSpacing(14)
        self.quick_scroll.setWidget(self.quick_grid_container)
        center_lay.addWidget(self.quick_scroll, stretch=1)

        self.track_table = QTableWidget(0, 4)
        self.track_table.setObjectName("TrackTable")
        self.track_table.setHorizontalHeaderLabels(["#", "CANCIÓN", "♥", "⏱"])
        self.track_table.horizontalHeader().setVisible(False)
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.track_table.setShowGrid(False)
        self.track_table.setIconSize(QSize(42, 42))
        self.track_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        header = self.track_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(0, 38)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(2, 44)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(3, 70)

        center_lay.addWidget(self.track_table, stretch=1)
        parent_layout.addWidget(center_card, stretch=1)

    def _build_right_panel(self, parent_layout: QHBoxLayout) -> None:
        self.right_card = QFrame()
        self.right_card.setObjectName("RightCard")
        self.right_card.setFixedWidth(335)

        right_lay = QVBoxLayout(self.right_card)
        right_lay.setContentsMargins(12, 12, 12, 12)
        right_lay.setSpacing(8)

        # 1. Mini tarjeta "Sonando ahora"
        now_card = QFrame()
        now_card.setObjectName("NowPlayingMiniCard")
        now_lay = QHBoxLayout(now_card)
        now_lay.setContentsMargins(10, 8, 10, 8)
        now_lay.setSpacing(10)

        self.lbl_right_cover = QLabel()
        self.lbl_right_cover.setFixedSize(56, 56)
        now_lay.addWidget(self.lbl_right_cover)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(1)
        self.lbl_right_title = QLabel("Selecciona una pista")
        self.lbl_right_title.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: 800;")
        self.lbl_right_title.setWordWrap(True)
        self.lbl_right_artist = QLabel("MaoMusic Audio Engine")
        self.lbl_right_artist.setStyleSheet("color: #1DB954; font-size: 11px; font-weight: 600;")
        self.lbl_right_album = QLabel("Local & Google Drive Cloud")
        self.lbl_right_album.setStyleSheet("color: #9E9EA8; font-size: 10px;")

        meta_col.addWidget(self.lbl_right_title)
        meta_col.addWidget(self.lbl_right_artist)
        meta_col.addWidget(self.lbl_right_album)
        now_lay.addLayout(meta_col, stretch=1)

        right_lay.addWidget(now_card)

        # 2. Escenario de Maomao bailando al compás de la música
        maomao_header = QHBoxLayout()
        lbl_mm = QLabel("🌿 MAOMAO DANCER")
        lbl_mm.setStyleSheet("color: #1ED760; font-size: 11px; font-weight: 800; letter-spacing: 0.8px;")
        self.btn_maomao_style = QPushButton("Paso de Baile")
        self.btn_maomao_style.setObjectName("GhostSmallButton")
        self.btn_maomao_style.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_visual_mode = QPushButton("🌙 Normal")
        self.btn_visual_mode.setObjectName("GhostSmallButton")
        self.btn_visual_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_visual_mode.setToolTip("Alternar modo de escenario: Normal y Libre")
        self.btn_visual_mode.clicked.connect(self.cycle_visual_mode)

        maomao_header.addWidget(lbl_mm)
        maomao_header.addStretch()
        maomao_header.addWidget(self.btn_visual_mode)
        maomao_header.addWidget(self.btn_maomao_style)
        right_lay.addLayout(maomao_header)

        self.maomao_widget = MaomaoDancerWidget()
        self.maomao_widget.setFixedHeight(235)
        right_lay.addWidget(self.maomao_widget)

        # 3. Visualizador de Espectro / Onda a 60 FPS
        vis_header = QHBoxLayout()
        lbl_vis = QLabel("ESPECTRO FFT EN VIVO")
        lbl_vis.setStyleSheet("color: #B3B3B3; font-size: 10px; font-weight: 800; letter-spacing: 0.8px;")
        self.btn_cycle_vis = QPushButton("Modo Visual")
        self.btn_cycle_vis.setObjectName("GhostSmallButton")
        self.btn_cycle_vis.setCursor(Qt.CursorShape.PointingHandCursor)
        vis_header.addWidget(lbl_vis)
        vis_header.addStretch()
        vis_header.addWidget(self.btn_cycle_vis)
        right_lay.addLayout(vis_header)

        self.visualizer = AudioVisualizerWidget()
        self.visualizer.setMinimumHeight(112)
        self.visualizer.setMaximumHeight(125)
        self.visualizer.set_mode(self.store.visualizer_mode)
        right_lay.addWidget(self.visualizer)

        # 4. Selector de pestaña inferior: Cola vs Ecualizador
        tab_switch_row = QHBoxLayout()
        tab_switch_row.setSpacing(6)

        self.btn_tab_queue = QPushButton("☰  Cola")
        self.btn_tab_queue.setObjectName("ActionChipButton")
        self.btn_tab_queue.setProperty("active", "true")
        self.btn_tab_queue.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_tab_eq = QPushButton("🎚  Ecualizador")
        self.btn_tab_eq.setObjectName("ActionChipButton")
        self.btn_tab_eq.setProperty("active", "false")
        self.btn_tab_eq.setCursor(Qt.CursorShape.PointingHandCursor)

        tab_switch_row.addWidget(self.btn_tab_queue)
        tab_switch_row.addWidget(self.btn_tab_eq)
        right_lay.addLayout(tab_switch_row)

        self.right_stack = QStackedWidget()

        queue_page = QWidget()
        q_lay = QVBoxLayout(queue_page)
        q_lay.setContentsMargins(0, 2, 0, 0)
        q_lay.setSpacing(4)

        q_top = QHBoxLayout()
        self.lbl_queue_count = QLabel("Siguientes canciones")
        self.lbl_queue_count.setStyleSheet("color: #B3B3B3; font-size: 11px; font-weight: 700;")
        self.btn_clear_queue = QPushButton("Limpiar cola")
        self.btn_clear_queue.setObjectName("GhostSmallButton")
        self.btn_clear_queue.setCursor(Qt.CursorShape.PointingHandCursor)
        q_top.addWidget(self.lbl_queue_count)
        q_top.addStretch()
        q_top.addWidget(self.btn_clear_queue)
        q_lay.addLayout(q_top)

        self.queue_list = QListWidget()
        self.queue_list.setObjectName("QueueListWidget")
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_list.setIconSize(QSize(32, 32))
        q_lay.addWidget(self.queue_list, stretch=1)

        self.eq_panel = EqualizerPanel(
            enabled=self.store.eq_enabled,
            preset=self.store.eq_preset,
            gains=self.store.eq_gains,
        )

        self.right_stack.addWidget(queue_page)
        self.right_stack.addWidget(self.eq_panel)
        right_lay.addWidget(self.right_stack, stretch=1)

        parent_layout.addWidget(self.right_card)

    def _build_bottom_player_bar(self, parent_layout: QVBoxLayout) -> None:
        bar = QFrame()
        bar.setObjectName("BottomPlayerBar")
        bar.setFixedHeight(86)

        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 8, 16, 8)
        bar_lay.setSpacing(16)

        left_box = QHBoxLayout()
        left_box.setSpacing(12)

        self.lbl_bar_cover = QLabel()
        self.lbl_bar_cover.setFixedSize(56, 56)
        left_box.addWidget(self.lbl_bar_cover)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addStretch()
        self.lbl_bar_title = QLabel("Sin reproducción")
        self.lbl_bar_title.setStyleSheet("color: #FFFFFF; font-size: 14px; font-weight: 700;")
        self.lbl_bar_artist = QLabel("Selecciona una pista")
        self.lbl_bar_artist.setStyleSheet("color: #B3B3B3; font-size: 12px;")
        title_col.addWidget(self.lbl_bar_title)
        title_col.addWidget(self.lbl_bar_artist)
        title_col.addStretch()
        left_box.addLayout(title_col)

        self.btn_bar_like = QPushButton()
        self.btn_bar_like.setObjectName("TransportIconBtn")
        self.btn_bar_like.setFixedSize(34, 34)
        self.btn_bar_like.setIcon(icons.get_icon("heart", "#a6a6b4", 18))
        self.btn_bar_like.setIconSize(QSize(18, 18))
        self.btn_bar_like.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bar_like.setToolTip("Guardar en Canciones que te gustan (Ctrl+L)")
        left_box.addWidget(self.btn_bar_like)
        left_box.addStretch()

        bar_lay.addLayout(left_box, stretch=3)

        center_col = QVBoxLayout()
        center_col.setSpacing(6)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(12)
        btns_row.addStretch()

        self.btn_shuffle = QPushButton()
        self.btn_shuffle.setObjectName("TransportIconBtn")
        self.btn_shuffle.setFixedSize(34, 34)
        self.btn_shuffle.setIcon(icons.get_icon("shuffle", "#1ed760" if self.engine.shuffle else "#a6a6b4", 18))
        self.btn_shuffle.setIconSize(QSize(18, 18))
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shuffle.setToolTip("Modo aleatorio (Shuffle)")
        self._refresh_button_active(self.btn_shuffle, self.engine.shuffle)

        self.btn_prev = QPushButton()
        self.btn_prev.setObjectName("TransportIconBtn")
        self.btn_prev.setFixedSize(34, 34)
        self.btn_prev.setIcon(icons.get_icon("prev", "#e4e4ec", 18))
        self.btn_prev.setIconSize(QSize(18, 18))
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.setToolTip("Anterior (Ctrl+Izquierda)")

        self.btn_play_pause = QPushButton()
        self.btn_play_pause.setObjectName("TransportPlayButton")
        self.btn_play_pause.setFixedSize(44, 44)
        self.btn_play_pause.setIcon(icons.get_icon("play", "#000000", 22))
        self.btn_play_pause.setIconSize(QSize(22, 22))
        self.btn_play_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_pause.setToolTip("Reproducir / Pausar (Espacio)")

        self.btn_next = QPushButton()
        self.btn_next.setObjectName("TransportIconBtn")
        self.btn_next.setFixedSize(34, 34)
        self.btn_next.setIcon(icons.get_icon("next", "#e4e4ec", 18))
        self.btn_next.setIconSize(QSize(18, 18))
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setToolTip("Siguiente (Ctrl+Derecha)")

        self.btn_repeat = QPushButton()
        self.btn_repeat.setObjectName("TransportIconBtn")
        self.btn_repeat.setFixedSize(34, 34)
        rep_active = self.engine.repeat_mode != "off"
        rep_icon = "repeat_one" if self.engine.repeat_mode == "one" else "repeat"
        self.btn_repeat.setIcon(icons.get_icon(rep_icon, "#1ed760" if rep_active else "#a6a6b4", 18))
        self.btn_repeat.setIconSize(QSize(18, 18))
        self.btn_repeat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_repeat.setToolTip("Modo de repetición (Desactivado / Todo / Una canción)")
        self._refresh_button_active(self.btn_repeat, rep_active)

        for b in (self.btn_shuffle, self.btn_prev, self.btn_play_pause, self.btn_next, self.btn_repeat):
            btns_row.addWidget(b)
        btns_row.addStretch()
        center_col.addLayout(btns_row)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)
        self.lbl_pos_time = QLabel("0:00")
        self.lbl_pos_time.setStyleSheet("color: #A7A7A7; font-size: 11px; min-width: 34px;")
        self.lbl_pos_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setCursor(Qt.CursorShape.PointingHandCursor)

        self.lbl_dur_time = QLabel("0:00")
        self.lbl_dur_time.setStyleSheet("color: #A7A7A7; font-size: 11px; min-width: 34px;")

        seek_row.addWidget(self.lbl_pos_time)
        seek_row.addWidget(self.seek_slider, stretch=1)
        seek_row.addWidget(self.lbl_dur_time)
        center_col.addLayout(seek_row)

        bar_lay.addLayout(center_col, stretch=5)

        right_box = QHBoxLayout()
        right_box.setSpacing(8)
        right_box.addStretch()

        self.btn_vinyl_mode = QPushButton(" Vinilo")
        self.btn_vinyl_mode.setObjectName("GhostSmallButton")
        self.btn_vinyl_mode.setIcon(icons.get_icon("vinyl", "#dcdcdc", 14))
        self.btn_vinyl_mode.setIconSize(QSize(14, 14))
        self.btn_vinyl_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_vinyl_mode.setToolTip("Modo Reposo / Tocadiscos con Video de fondo (Ctrl+V)")

        self.btn_quick_eq = QPushButton(" EQ")
        self.btn_quick_eq.setObjectName("GhostSmallButton")
        self.btn_quick_eq.setIcon(icons.get_icon("equalizer", "#dcdcdc", 14))
        self.btn_quick_eq.setIconSize(QSize(14, 14))
        self.btn_quick_eq.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_eq.setToolTip("Abrir Ecualizador de 5 bandas")

        self.btn_toggle_right = QPushButton(" Panel")
        self.btn_toggle_right.setObjectName("GhostSmallButton")
        self.btn_toggle_right.setIcon(icons.get_icon("panel", "#dcdcdc", 14))
        self.btn_toggle_right.setIconSize(QSize(14, 14))
        self.btn_toggle_right.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_right.setToolTip("Mostrar / Ocultar panel derecho (Maomao, Espectro y Cola)")

        self.btn_mute = QPushButton()
        self.btn_mute.setObjectName("TransportIconBtn")
        self.btn_mute.setFixedSize(34, 34)
        self.btn_mute.setIcon(icons.get_icon("volume_high", "#a6a6b4", 18))
        self.btn_mute.setIconSize(QSize(18, 18))
        self.btn_mute.setCursor(Qt.CursorShape.PointingHandCursor)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(self.store.volume)
        self.vol_slider.setFixedWidth(88)
        self.vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)

        # Botón de compatibilidad invisible
        self.btn_quick_cloud = QPushButton()
        self.btn_quick_cloud.setVisible(False)

        right_box.addWidget(self.btn_vinyl_mode)
        right_box.addWidget(self.btn_quick_eq)
        right_box.addWidget(self.btn_toggle_right)
        right_box.addWidget(self.btn_mute)
        right_box.addWidget(self.vol_slider)

        bar_lay.addLayout(right_box, stretch=3)
        parent_layout.addWidget(bar)

    # ================================================================== #
    #                    CONEXIÓN DE SEÑALES Y ATAJOS                    #
    # ================================================================== #

    def _connect_signals(self) -> None:
        self.btn_nav_home.clicked.connect(lambda: self.switch_view("home"))
        self.btn_nav_library.clicked.connect(lambda: self.switch_view("library"))
        self.btn_nav_playlists.clicked.connect(lambda: self.switch_view("playlists_hub"))
        self.btn_nav_gdrive.clicked.connect(lambda: self.switch_view("gdrive"))
        self.btn_nav_liked.clicked.connect(lambda: self.switch_view("liked"))
        self.btn_nav_recent.clicked.connect(lambda: self.switch_view("recent"))
        if hasattr(self, "btn_view_playlists_tab") and self.btn_view_playlists_tab:
            self.btn_view_playlists_tab.clicked.connect(lambda: self.switch_view("playlists_hub"))

        self.btn_new_playlist.clicked.connect(self.create_playlist_dialog)
        self.playlist_list.itemClicked.connect(self._on_playlist_item_clicked)
        self.playlist_list.customContextMenuRequested.connect(self._on_playlist_context_menu)

        self.btn_account_sidebar.clicked.connect(self.open_account_dialog)
        self.btn_sidebar_tools.clicked.connect(lambda: self._open_tools_menu(self.btn_sidebar_tools))
        self.btn_top_tools.clicked.connect(lambda: self._open_tools_menu(self.btn_top_tools))
        self.btn_toggle_sidebar.clicked.connect(self._toggle_sidebar)
        self.btn_vinyl_mode.clicked.connect(self._open_vinyl_mode)
        self.vinyl_overlay.play_pause_requested.connect(self.engine.toggle_play_pause)
        self.vinyl_overlay.prev_requested.connect(self.engine.previous_track)
        self.vinyl_overlay.next_requested.connect(lambda: self.engine.next_track(auto_triggered=False))
        self.vinyl_overlay.shuffle_requested.connect(self.toggle_shuffle)
        self.vinyl_overlay.repeat_requested.connect(self.cycle_repeat_mode)
        self.btn_check_updates.clicked.connect(lambda: self._check_github_updates(manual=True))
        self.btn_connect_gdrive.clicked.connect(self.open_google_drive_dialog)
        self.btn_upload_gdrive.clicked.connect(self.upload_files_to_drive_dialog)
        self.btn_download_yt.clicked.connect(self.open_download_queue_panel)
        self.btn_quick_cloud.clicked.connect(self.open_google_drive_dialog)
        self.hero_banner.btn_cloud_sync_banner.clicked.connect(self.open_google_drive_dialog)
        self.hero_banner.btn_download_yt_banner.clicked.connect(self.open_download_queue_panel)

        self.btn_auto_scan_pc.clicked.connect(self.auto_scan_pc_music)
        self.btn_import_folder.clicked.connect(self.import_folder_dialog)
        self.btn_import_files.clicked.connect(self.import_files_dialog)

        self.search_input.textChanged.connect(self._refresh_track_table)
        self.hero_banner.btn_play_banner.clicked.connect(self.play_current_view)
        self.hero_banner.btn_shuffle_banner.clicked.connect(self.shuffle_play_current_view)
        self.hero_banner.btn_add_tracks.clicked.connect(self._open_multi_track_picker_for_current_playlist)
        self.hero_banner.btn_edit_playlist.clicked.connect(self.edit_current_playlist)
        self.hero_banner.btn_delete_playlist.clicked.connect(self.delete_current_playlist)
        self.hero_banner.btn_download_playlist.clicked.connect(lambda: self._download_entire_playlist())

        self.track_table.cellDoubleClicked.connect(self._on_table_double_clicked)
        self.track_table.cellClicked.connect(self._on_table_cell_clicked)
        self.track_table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._table_press_timer = QTimer(self)
        self._table_press_timer.setSingleShot(True)
        self._table_press_timer.setInterval(450)
        self._table_press_pos = None
        self._table_press_timer.timeout.connect(self._on_table_long_press_timeout)
        self.track_table.viewport().installEventFilter(self)

        self.btn_maomao_style.clicked.connect(lambda: self.maomao_widget.next_dance_style())
        self.btn_cycle_vis.clicked.connect(self.visualizer.next_mode)
        self.visualizer.mode_changed.connect(self._on_visualizer_mode_changed)
        self.btn_tab_queue.clicked.connect(lambda: self._switch_right_tab(0))
        self.btn_tab_eq.clicked.connect(lambda: self._switch_right_tab(1))
        self.btn_clear_queue.clicked.connect(self.engine.clear_queue)
        self.queue_list.itemDoubleClicked.connect(self._on_queue_item_double_clicked)
        self.eq_panel.eq_changed.connect(self._on_eq_changed)

        self.btn_play_pause.clicked.connect(self.engine.toggle_play_pause)
        self.btn_next.clicked.connect(lambda: self.engine.next_track(auto_triggered=False))
        self.btn_prev.clicked.connect(self.engine.previous_track)
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)
        self.btn_repeat.clicked.connect(self.cycle_repeat_mode)
        self.btn_bar_like.clicked.connect(self.toggle_current_track_like)

        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_seeking_slider", True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.seek_slider.valueChanged.connect(self._on_seek_preview)

        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.btn_mute.clicked.connect(self.toggle_mute)
        self.btn_quick_eq.clicked.connect(self._open_eq_drawer)
        self.btn_toggle_right.clicked.connect(lambda: self.right_card.setVisible(not self.right_card.isVisible()))

        # Señales del motor de audio -> UI + Espectro + Maomao Dancer
        self.engine.track_changed.connect(self._on_engine_track_changed)
        self.engine.state_changed.connect(self._on_engine_state_changed)
        self.engine.position_changed.connect(self._on_engine_position_changed)
        self.engine.queue_changed.connect(self._refresh_queue_ui)
        self.engine.spectrum_updated.connect(self._on_spectrum_frame)
        self.engine.beat_info.connect(self._on_beat_info)
        self.engine.eq_status_changed.connect(self.eq_panel.set_status_text)
        self.engine.track_stream_requested.connect(self._play_track_or_stream_from_cloud)

    def _on_beat_info(self, bpm: float, beat_pos: float, conf: float, onset: float) -> None:
        self.maomao_widget.set_beat(bpm, beat_pos, conf, onset)
        self.visualizer.set_beat(bpm, beat_pos, conf, onset)

    def cycle_visual_mode(self) -> None:
        modes = ["dark", "free"]
        cur = getattr(self, "_visual_mode", "dark")
        nxt_idx = (modes.index(cur) + 1) % len(modes) if cur in modes else 0
        self.set_visual_mode(modes[nxt_idx])

    def set_visual_mode(self, mode: str) -> None:
        if mode not in ("dark", "free"):
            mode = "dark"
        self._visual_mode = mode
        from styles import get_stylesheet
        from PyQt6.QtWidgets import QApplication

        labels = {"dark": "🌙 Normal", "free": "📊 Libre"}
        if hasattr(self, "btn_visual_mode"):
            self.btn_visual_mode.setText(labels.get(mode, "🌙 Normal"))

        app = QApplication.instance()
        if app:
            app.setStyleSheet(get_stylesheet("dark"))

        if mode == "free":
            self.maomao_widget.setVisible(False)
            if hasattr(self.visualizer, "set_free_stage_mode"):
                self.visualizer.set_free_stage_mode(True)
            self.visualizer.setMinimumHeight(280)
            self.visualizer.setMaximumHeight(380)
        else: # dark
            self.maomao_widget.setVisible(True)
            if hasattr(self.visualizer, "set_free_stage_mode"):
                self.visualizer.set_free_stage_mode(False)
            self.visualizer.setMinimumHeight(112)
            self.visualizer.setMaximumHeight(125)

    def _on_spectrum_frame(self, bars, waveform) -> None:
        self.visualizer.update_audio_data(bars, waveform)
        self.maomao_widget.update_from_spectrum(bars, self.engine.is_playing())

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.engine.toggle_play_pause)
        QShortcut(
            QKeySequence("Ctrl+Right"),
            self,
            activated=lambda: self.engine.next_track(auto_triggered=False),
        )
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self.engine.previous_track)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search_input.setFocus)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.toggle_current_track_like)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.toggle_mute)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self._toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self._open_vinyl_mode)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "vinyl_overlay") and self.vinyl_overlay.isVisible():
            self.vinyl_overlay.setGeometry(self.rect())

    def _toggle_sidebar(self) -> None:
        """Muestra u oculta la barra lateral izquierda."""
        if hasattr(self, "sidebar_container"):
            self.sidebar_container.setVisible(not self.sidebar_container.isVisible())

    def _open_vinyl_mode(self) -> None:
        """Abre el tocadiscos giratorio con video de fondo o fondo atmosférico."""
        if not hasattr(self, "vinyl_overlay"):
            return
        t = self.engine.current_track
        title = t.title if t else "MaoMusic"
        artist = t.artist if t else "Tocadiscos OLED"
        cov = t.cover_path if t else None
        fpath = t.filepath if t else ""
        self.vinyl_overlay.setGeometry(self.rect())
        self.vinyl_overlay.update_state(title, artist, cov, fpath, self.engine.is_playing())
        self.vinyl_overlay.setVisible(True)
        self.vinyl_overlay.raise_()
        self.vinyl_overlay.setFocus()

    # ================================================================== #
    #                  GOOGLE DRIVE CLOUD STREAMING                      #
    # ================================================================== #

    def _open_tools_menu(self, source_button: Optional[QWidget] = None) -> None:
        """Menú unificado de herramientas, Google Drive, descargas y mantenimiento."""
        menu = QMenu(self)
        menu.setObjectName("ToolsDropdownMenu")

        act_account = menu.addAction("👤  Gestionar Cuenta MaoMusic...")
        act_account.triggered.connect(self.open_account_dialog)

        menu.addSeparator()

        act_gdrive = menu.addAction("☁️  Vincular Carpeta Google Drive...")
        act_gdrive.triggered.connect(self.open_google_drive_dialog)

        act_upload = menu.addAction("⬆️  Subir Canciones a Drive...")
        act_upload.triggered.connect(self.upload_files_to_drive_dialog)

        act_yt = menu.addAction("📥  Descargas de YouTube...")
        act_yt.triggered.connect(self.open_download_queue_panel)

        menu.addSeparator()

        act_scan_pc = menu.addAction("🔍  Auto-Rastrear Música en este PC")
        act_scan_pc.triggered.connect(self.auto_scan_pc_music)

        act_import_folder = menu.addAction("📂  Escanear Carpeta Local...")
        act_import_folder.triggered.connect(self.import_folder_dialog)

        act_import_files = menu.addAction("🎵  Importar Archivos de Audio...")
        act_import_files.triggered.connect(self.import_files_dialog)

        menu.addSeparator()

        act_updates = menu.addAction(f"🔄  Buscar Actualizaciones (v{APP_VERSION_NAME})...")
        act_updates.triggered.connect(lambda: self._check_github_updates(manual=True))

        btn = source_button or self.sender()
        if isinstance(btn, QWidget):
            menu.exec(btn.mapToGlobal(QPoint(0, btn.height() + 4)))
        else:
            menu.exec(QCursor.pos())

    def open_account_dialog(self) -> None:
        dlg = AccountDialog(self, self.store)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.logged_out:
                self.switch_view(self.current_view)
                QMessageBox.information(self, "Sesión cerrada", "Se cerró la sesión de tu cuenta MaoMusic.")
                return
            acc = dlg.authenticated_account
            if acc:
                self.store.user_account = {
                    "username": acc.get("username", ""),
                    "email": acc.get("email", ""),
                    "password_hash": acc.get("password_hash", ""),
                }
                pl_count, drive_count = self.store.apply_account_record(acc)
                self._sync_playlists_to_cloud(notify=False)
                self._refresh_playlist_sidebar()
                self.switch_view(self.current_view)
                QMessageBox.information(
                    self,
                    "👤 Cuenta MaoMusic Vinculada",
                    f"✔ Cuenta activa: {self.store.user_account.get('username')} ({self.store.user_account.get('email')})\n\n"
                    f"• Playlists vinculadas: {len(self.store.playlists)} ({pl_count} sincronizadas)\n"
                    f"• Nuevas canciones de Drive restauradas: {drive_count}\n"
                    f"• Base de datos en Drive: {DEFAULT_ROOT_DRIVE_FOLDER_ID} ({CLOUD_DB_FILENAME})",
                )

    def upload_files_to_drive_dialog(self) -> None:
        """Permite seleccionar archivos de audio del PC y subirlos a la carpeta Musica del Drive."""
        if not self.store.gdrive_config.access_token:
            QMessageBox.information(
                self,
                "Se requiere Token OAuth 2.0",
                "Para subir nuevas canciones a la carpeta «Musica» de Google Drive necesitas conectar tu Token OAuth 2.0.\n\n"
                "Se abrirá la configuración de Google Drive para que lo ingreses.",
            )
            self.open_google_drive_dialog()
            if not self.store.gdrive_config.access_token:
                return

        start_dir = str(Path.home() / "Music") if (Path.home() / "Music").exists() else str(Path.home())
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar canciones para subir a la carpeta «Musica» de Google Drive",
            start_dir,
            f"Archivos de Audio ({exts})",
        )
        if not files:
            return

        file_paths = [Path(f) for f in files]
        self.hero_banner.lbl_subtitle.setText("⬆️ Subiendo canciones a la carpeta «Musica» de Google Drive...")
        self.cloud_service.upload_files(file_paths)

    def _on_queue_runner_event(self, event: dict) -> None:
        ev_type = event.get("type")
        if ev_type == "item_done":
            self.queue_signals.item_completed_for_playlist.emit(
                int(event.get("item_id", 0)),
                str(event.get("query", "")),
                str(event.get("target_playlist", "")),
                dict(event.get("result") or {}),
            )
            self.queue_signals.summary_changed.emit()
            self._library_rescan_timer.start()
        elif ev_type in ("item_started", "item_error", "item_cancelled", "summary_changed", "state_changed"):
            self.queue_signals.summary_changed.emit()

    def _resolve_or_import_queue_track(self, query: str, target_playlist: str, result: dict) -> Optional[TrackMetadata]:
        """Importa o vincula una canción descargada usando su ID canónico de Google Drive (gd_<audio_file_id>) cuando existe."""
        audio_file = str(result.get("audio_file") or "").strip()
        title = str(result.get("title") or "").strip()
        artist = str(result.get("artist") or "").strip()
        sync_info = result.get("sync") if isinstance(result.get("sync"), dict) else {}
        audio_fid = str(sync_info.get("audio_file_id") or "").strip()
        forced_id = f"gd_{audio_fid}" if audio_fid else ""

        track: Optional[TrackMetadata] = None
        if audio_file:
            full_path = self.store.music_library_dir / audio_file
            if full_path.exists():
                track = self.store.import_file(full_path, save_immediately=False, forced_id=forced_id)

        if not track:
            track = self.store.find_track_by_query_or_filename(
                audio_filename=audio_file,
                title=title,
                query=query,
                prefer_real=True,
            )
            if track and forced_id and track.id != forced_id and not track.filepath.startswith("cloud://pending"):
                old_id = track.id
                track.id = forced_id
                if track.genre == "Desconocido":
                    track.genre = "Cloud Music"
                self.store.tracks[forced_id] = track
                if old_id in self.store.tracks:
                    del self.store.tracks[old_id]
                self.store.reconcile_playlist_track_ids(old_id, forced_id)

        if not track and forced_id:
            # Descarga solo en la nube (keep_local=False) pero subida a Google Drive con éxito
            cov_path = self.store.covers_dir / f"{forced_id}.jpg"
            if not cov_path.exists():
                cov_path = self.store.covers_dir / f"cover_{forced_id[:16]}.png"
                if not cov_path.exists():
                    generate_fallback_cover(title or query, artist or "Google Drive Cloud", cov_path, forced_id)
            dom_rgb = compute_dominant_color(cov_path)
            track = TrackMetadata(
                id=forced_id,
                filepath=f"gdrive://{audio_fid}",
                title=title or query,
                artist=artist or "Google Drive Cloud",
                album=target_playlist or "Google Drive (Online)",
                genre="Cloud Music",
                year="Cloud",
                duration=float(result.get("duration") or 180.0),
                cover_path=str(cov_path),
                dominant_color=dom_rgb,
                added_at=time.time(),
            )
            self.store.tracks[forced_id] = track

        if track:
            if target_playlist and track.album in ("", "Desconocido"):
                track.album = target_playlist
            if forced_id and track.genre in ("", "Desconocido"):
                track.genre = "Cloud Music"
        return track

    def _reconcile_completed_queue_playlists(self) -> bool:
        """Asegura que todas las canciones descargadas en la cola con target_playlist estén agrupadas en su Playlist en la Nube con ID canónico gd_..."""
        changed = False
        try:
            done_items = self.download_queue.list(status="done")
        except Exception:
            done_items = []

        for item in done_items:
            tgt_pl = str(item.target_playlist or "").strip()
            if not tgt_pl:
                continue
            try:
                res = json.loads(item.result_json) if getattr(item, "result_json", "") else {}
            except Exception:
                res = {}
            if not isinstance(res, dict):
                res = {}
            track = self._resolve_or_import_queue_track(item.query, tgt_pl, res)
            pl = self.store.get_or_create_playlist_by_name(
                tgt_pl,
                description="Playlist generada desde cola de descargas (en la Nube)",
                is_cloud=True,
            )
            if not pl.is_cloud:
                pl.is_cloud = True
                changed = True
            if track:
                for tid in list(pl.track_ids):
                    old_t = self.store.tracks.get(tid)
                    if old_t and old_t.filepath.startswith("cloud://pending") and (
                        normalize_track_key(old_t.title) == normalize_track_key(track.title)
                        or normalize_track_key(old_t.title) == normalize_track_key(item.query)
                    ):
                        self.store.reconcile_playlist_track_ids(tid, track.id)
                        if tid in self.store.tracks:
                            del self.store.tracks[tid]
                        changed = True
                        break
                if track.id not in pl.track_ids:
                    pl.track_ids.append(track.id)
                    changed = True

        deduped = self.store.deduplicate_library()
        if deduped > 0:
            changed = True
        if changed:
            self.store.save()
        return changed

    def _on_playlist_enqueued_from_panel(self, pl_name: str, queries: list, _keep_local: bool) -> None:
        """Crea o actualiza de inmediato la Playlist en la Nube cuando se encola desde el panel de descargas."""
        if not pl_name:
            return
        self.store.populate_cloud_playlist_from_queries(
            pl_name,
            list(queries),
            description="Playlist generada desde cola de descargas (en la Nube)",
        )
        self._reconcile_completed_queue_playlists()
        self.store.save()
        if hasattr(self, "_sync_playlists_to_cloud"):
            try:
                self._sync_playlists_to_cloud(notify=False)
            except Exception:
                pass
        self._refresh_playlist_sidebar()
        self._rebuild_playlists_menu_bar()
        self._update_download_badge()
        if self.current_view == "home":
            self._rebuild_quick_access_cards()
        else:
            self._refresh_track_table()

    def _on_queue_item_completed(self, item_id: int, query: str, target_playlist: str, result: dict) -> None:
        """Asocia la canción descargada a su playlist destino en el hilo principal de Qt."""
        track = self._resolve_or_import_queue_track(query, target_playlist, result)
        if not target_playlist:
            if track:
                self.store.deduplicate_library()
                self.store.save()
                if hasattr(self, "_sync_playlists_to_cloud"):
                    try:
                        self._sync_playlists_to_cloud(notify=False)
                    except Exception:
                        pass
                self._refresh_track_table()
            return

        if track:
            pl = self.store.get_or_create_playlist_by_name(
                target_playlist,
                description="Playlist generada desde cola de descargas (en la Nube)",
                is_cloud=True,
            )
            pl.is_cloud = True
            # Reconciliar placeholder pendiente si existía
            for tid in list(pl.track_ids):
                old_t = self.store.tracks.get(tid)
                if old_t and old_t.filepath.startswith("cloud://pending") and (
                    normalize_track_key(old_t.title) == normalize_track_key(track.title)
                    or normalize_track_key(old_t.title) == normalize_track_key(query)
                ):
                    self.store.reconcile_playlist_track_ids(tid, track.id)
                    if tid in self.store.tracks:
                        del self.store.tracks[tid]
                    break
            self.store.add_track_to_playlist(pl.id, track.id)
            self.store.deduplicate_library()
            self.store.save()
            if hasattr(self, "_sync_playlists_to_cloud"):
                try:
                    self._sync_playlists_to_cloud(notify=False)
                except Exception:
                    pass
            self._refresh_playlist_sidebar()
            self._rebuild_playlists_menu_bar()
            if self.current_view == "home":
                self._rebuild_quick_access_cards()
            else:
                self._refresh_track_table()

    def _rebuild_playlists_menu_bar(self) -> None:
        """Reconstruye las píldoras interactivas de playlists en la sub-barra superior."""
        if not hasattr(self, "playlists_pills_layout") or not self.playlists_pills_layout:
            return
        while self.playlists_pills_layout.count():
            item = self.playlists_pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        btn_all = QPushButton("🗂 Todas las Playlists")
        btn_all.setObjectName("ActionChipButton")
        btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_all.setProperty("active", "true" if self.current_view == "playlists_hub" else "false")
        btn_all.clicked.connect(lambda: self.switch_view("playlists_hub"))
        self.playlists_pills_layout.addWidget(btn_all)

        btn_cloud = QPushButton("☁️ Playlists en la Nube")
        btn_cloud.setObjectName("ActionChipButton")
        btn_cloud.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cloud.setProperty("active", "true" if self.current_view == "playlists_cloud" else "false")
        btn_cloud.clicked.connect(lambda: self.switch_view("playlists_cloud"))
        self.playlists_pills_layout.addWidget(btn_cloud)

        for pid, pl in self.store.playlists.items():
            count = len(pl.track_ids)
            icon = "☁️ " if pl.is_cloud else "🎵 "
            btn_pl = QPushButton(f"{icon}{pl.name} ({count})")
            btn_pl.setObjectName("ActionChipButton")
            btn_pl.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_pl.setProperty("active", "true" if self.current_view == f"playlist:{pid}" else "false")
            btn_pl.clicked.connect(lambda _checked, p=pid: self.switch_view(f"playlist:{p}"))
            self.playlists_pills_layout.addWidget(btn_pl)

        self.playlists_pills_layout.addStretch()

    def _import_txt_to_playlist_dialog(self) -> None:
        """Permite subir un archivo .txt directamente desde el menú de Playlists para encolar y auto-crearla."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar lista de canciones (.txt) para Playlist",
            "",
            "Archivos de texto (*.txt);;Todos los archivos (*)",
        )
        if not file_path:
            return
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "Error al leer archivo", f"No se pudo leer el archivo:\n{exc}")
            return

        stem = Path(file_path).stem.replace("_", " ").strip().title()
        entries = parse_playlist_txt(content, default_playlist_name=stem)
        if not entries:
            QMessageBox.information(self, "Archivo vacío", "El archivo no contiene canciones válidas.")
            return

        total_added = 0
        total_skipped = 0
        created_pls: set[str] = set()

        grouped: dict[str, list[str]] = {}
        for query, pl_name in entries:
            target_pl = pl_name or stem
            grouped.setdefault(target_pl, []).append(query)

        for pl_name, queries in grouped.items():
            pl, linked, pending = self.store.populate_cloud_playlist_from_queries(
                pl_name,
                queries,
                description=f"Playlist importada desde {stem}.txt (en la Nube)",
            )
            created_pls.add(pl.name)
            added, skipped = self.download_queue.add(queries, keep_local=True, target_playlist=pl_name)
            total_added += added
            total_skipped += skipped

        self.store.save()
        if hasattr(self, "_sync_playlists_to_cloud"):
            try:
                self._sync_playlists_to_cloud(notify=False)
            except Exception:
                pass
        self._refresh_playlist_sidebar()
        self._rebuild_playlists_menu_bar()
        self._update_download_badge()
        self.open_download_queue_panel()
        self.switch_view("playlists_cloud")

        pl_names_str = ", ".join(created_pls)
        QMessageBox.information(
            self,
            "Playlists en la Nube Creadas",
            f"✔ Se importaron {len(grouped)} playlist(s) en la Nube: {pl_names_str}\n"
            f"• Las canciones se agregaron de inmediato a la playlist.\n"
            f"• Descargas encoladas: {total_added} (ya existentes o en cola: {total_skipped})\n"
            f"• Puedes descargarlas a tu biblioteca local desde el botón de la playlist.",
        )

    def _update_download_badge(self) -> None:
        items = self.download_queue.list()
        active = sum(1 for it in items if it.status in ("pending", "running"))
        badge_text = f"📥  Descargas ({active})" if active > 0 else "📥  Descargas..."
        banner_badge_text = f"📥 YouTube ({active})" if active > 0 else "📥 YouTube"
        if hasattr(self, "btn_download_yt"):
            self.btn_download_yt.setText(badge_text)
        if hasattr(self, "hero_banner") and hasattr(self.hero_banner, "btn_download_yt_banner"):
            self.hero_banner.btn_download_yt_banner.setText(banner_badge_text)

    def rescan_local_library(self) -> None:
        """Re-escanea la carpeta de biblioteca local y actualiza la interfaz de forma agrupada."""
        if hasattr(self, "store") and hasattr(self.store, "music_library_dir"):
            self.store.scan_folder(self.store.music_library_dir)
            self.store.save()
            self._refresh_track_table()
            self._refresh_playlist_sidebar()

    def open_download_queue_panel(self) -> None:
        """Abre o enfoca el panel flotante no bloqueante de la cola de descargas."""
        if self.download_queue_panel is None:
            self.download_queue_panel = DownloadQueuePanel(
                queue=self.download_queue,
                runner=self.queue_runner,
                signals=self.queue_signals,
                parent=self,
            )
        self.download_queue_panel.show()
        self.download_queue_panel.raise_()
        self.download_queue_panel.activateWindow()

    def download_from_youtube_dialog(self) -> None:
        """Abre el asistente para agregar canciones a la cola de descargas de forma no bloqueante."""
        results = doctor.check_environment(online=False, base_dir=self.base_dir)
        failed = [r for r in results if r.required and not r.ok]
        if failed:
            hints = "\n".join(f"• {r.name}: {r.hint}" for r in failed)
            QMessageBox.warning(
                self,
                "Dependencias Faltantes",
                f"No se pueden iniciar descargas porque faltan herramientas requeridas en el sistema:\n\n{hints}\n\nEjecuta './install.sh' o 'python doctor.py' en la terminal para resolverlas.",
            )
            return

        dlg = YouTubeBatchDownloadDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        keep_local = dlg.should_keep_local()
        target_playlist = dlg.get_target_playlist()
        raw_text = dlg.txt_list.toPlainText()
        entries = parse_playlist_txt(raw_text, default_playlist_name=target_playlist)
        if not entries:
            queries = dlg.get_queries()
            entries = [(q, target_playlist) for q in queries]

        if not entries:
            QMessageBox.information(self, "Lista vacía", "No ingresaste ninguna canción o enlace para procesar.")
            return

        total_added = 0
        total_skipped = 0
        grouped: dict[str, list[str]] = {}
        for q, pl_name in entries:
            grouped.setdefault(pl_name, []).append(q)

        for pl_name, queries in grouped.items():
            if pl_name:
                self.store.populate_cloud_playlist_from_queries(
                    pl_name,
                    queries,
                    description=f"Playlist importada desde cola de descargas (en la Nube)",
                )
            added, skipped = self.download_queue.add(queries, keep_local=keep_local, target_playlist=pl_name)
            total_added += added
            total_skipped += skipped

        self.store.save()
        if hasattr(self, "_sync_playlists_to_cloud"):
            try:
                self._sync_playlists_to_cloud(notify=False)
            except Exception:
                pass
        self._refresh_playlist_sidebar()
        self._rebuild_playlists_menu_bar()
        self._update_download_badge()
        self.open_download_queue_panel()
        if total_skipped > 0:
            QMessageBox.information(
                self,
                "Canciones encoladas",
                f"✔ Se agregaron {total_added} canciones a la cola de descargas.\n(Se omitieron {total_skipped} por estar ya registradas).",
            )

    def _on_upload_to_drive_finished(self, uploaded_tracks: list[TrackMetadata], status_msg: str) -> None:
        for t in uploaded_tracks:
            self.store.tracks[t.id] = t
        self.store.deduplicate_library()
        self.store.save()
        self._sync_playlists_to_cloud(notify=False)
        self.switch_view("gdrive" if uploaded_tracks else self.current_view)
        self.hero_banner.lbl_subtitle.setText(status_msg)
        QMessageBox.information(self, "⬆️ Subida a Google Drive (Musica)", status_msg)

    def open_google_drive_dialog(self) -> None:
        dlg = GoogleDriveDialog(self, self.store.gdrive_config)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg, direct_links, download_now = dlg.get_result()
            self.store.gdrive_config = cfg
            if cfg.access_token:
                u = fetch_google_user_profile(cfg.access_token)
                if u:
                    self.store.google_user = u
            self.store.save()
            self._sync_playlists_to_cloud(notify=True)
            self.start_gdrive_sync(direct_links=direct_links, download_immediately=download_now)

    def start_gdrive_sync(
        self,
        direct_links: Optional[list[str]] = None,
        download_immediately: bool = False,
        silent_view_keep: bool = False,
    ) -> None:
        self._silent_gdrive_sync = silent_view_keep
        self.hero_banner.lbl_subtitle.setText("☁️ Conectando con la carpeta «Musica» de Google Drive...")
        self.cloud_service.sync_library(
            download_immediately=download_immediately,
            direct_links=direct_links,
            extra_search_dirs=[self.store.music_library_dir, self.store.gdrive_cache_dir],
        )

    def _on_gdrive_sync_finished(self, tracks: list[TrackMetadata], status_msg: str) -> None:
        for t in tracks:
            self.store.tracks[t.id] = t
        self.store.deduplicate_library()
        self.store.save()
        if self.store.user_account.get("email") or self.store.gdrive_config.access_token:
            self._sync_playlists_to_cloud(notify=False)
        target_view = self.current_view if getattr(self, "_silent_gdrive_sync", False) else ("gdrive" if tracks else self.current_view)
        self._silent_gdrive_sync = False
        self.switch_view(target_view)
        self.hero_banner.lbl_subtitle.setText(status_msg)

    def _play_track_or_stream_from_cloud(
        self,
        track: TrackMetadata,
        context_list: Optional[list[TrackMetadata]] = None,
    ) -> None:
        """Si la pista es de Google Drive o pendiente en la nube, gestiona transmisión o encolado."""
        if track.filepath.startswith("cloud://pending"):
            self.download_queue.add([track.title], keep_local=True, target_playlist=track.album)
            self._update_download_badge()
            self.open_download_queue_panel()
            QMessageBox.information(
                self,
                "Descargando Canción de la Nube",
                f"«{track.title}» está en la nube. Se ha añadido a la cola de descargas para guardarse en tu biblioteca local.",
            )
            return

        if track.filepath.startswith("gdrive://"):
            self.lbl_bar_title.setText(f"☁️ Cargando: {track.title}...")
            self.lbl_bar_artist.setText("Transmitiendo desde la carpeta «Musica» de Google Drive...")
            if context_list is not None:
                self.engine.context_tracks = list(context_list)
            self.cloud_service.stream_track(track, self.store.tracks)
        else:
            self.engine.play_track(track, context_list)

    def _on_cloud_stream_ready(self, old_id: str, updated_track: TrackMetadata, _was_duplicate: bool = False) -> None:
        self.store.tracks[old_id] = updated_track
        self.store.deduplicate_library()
        self.store.save()
        # Actualizar referencias en la lista actual
        for i, t in enumerate(self.displayed_tracks):
            if t.id == old_id:
                self.displayed_tracks[i] = updated_track
        for i, t in enumerate(self.engine.context_tracks):
            if t.id == old_id:
                self.engine.context_tracks[i] = updated_track
        self.engine.play_track(updated_track, None)
        self._refresh_track_table()

    # ================================================================== #
    #                  VISTAS, BIBLIOTECA Y PLAYLISTS                    #
    # ================================================================== #

    def switch_view(self, view_key: str) -> None:
        self.current_view = view_key

        is_pl_view = (view_key in ("playlists_hub", "playlists_cloud") or view_key.startswith("playlist:"))

        self._refresh_button_active(self.btn_nav_home, view_key == "home")
        self._refresh_button_active(self.btn_nav_library, view_key == "library")
        self._refresh_button_active(self.btn_nav_playlists, is_pl_view)
        if hasattr(self, "btn_view_playlists_tab") and self.btn_view_playlists_tab:
            self._refresh_button_active(self.btn_view_playlists_tab, is_pl_view)
        self._refresh_button_active(self.btn_nav_gdrive, view_key == "gdrive")
        self._refresh_button_active(self.btn_nav_liked, view_key == "liked")
        self._refresh_button_active(self.btn_nav_recent, view_key == "recent")

        self._refresh_playlist_sidebar()
        if hasattr(self, "playlists_menu_bar") and self.playlists_menu_bar:
            self.playlists_menu_bar.setVisible(is_pl_view)
            if is_pl_view:
                self._rebuild_playlists_menu_bar()

        self.quick_scroll.setVisible(view_key == "home")
        self.track_table.setVisible(view_key != "home")

        if view_key == "home":
            self._rebuild_quick_access_cards()

        self._refresh_track_table()
        self._refresh_queue_ui()

    def _refresh_playlist_sidebar(self) -> None:
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()

        local_pls = [p for p in self.store.playlists.values() if not p.is_cloud]
        cloud_pls = [p for p in self.store.playlists.values() if p.is_cloud]

        if local_pls:
            h_local = QListWidgetItem("── MIS PLAYLISTS ──")
            h_local.setFlags(Qt.ItemFlag.NoItemFlags)
            h_local.setForeground(QColor("#777777"))
            font_local = h_local.font()
            font_local.setBold(True)
            font_local.setPointSize(9)
            h_local.setFont(font_local)
            self.playlist_list.addItem(h_local)
            for pl in local_pls:
                item = QListWidgetItem(f"🎵  {pl.name}  ({len(pl.track_ids)})")
                item.setData(Qt.ItemDataRole.UserRole, pl.id)
                self.playlist_list.addItem(item)
                if self.current_view == f"playlist:{pl.id}":
                    item.setSelected(True)

        if cloud_pls:
            h_cloud = QListWidgetItem("── PLAYLISTS EN LA NUBE ☁️ ──")
            h_cloud.setFlags(Qt.ItemFlag.NoItemFlags)
            h_cloud.setForeground(QColor("#1ED760"))
            font_cloud = h_cloud.font()
            font_cloud.setBold(True)
            font_cloud.setPointSize(9)
            h_cloud.setFont(font_cloud)
            self.playlist_list.addItem(h_cloud)
            for pl in cloud_pls:
                item = QListWidgetItem(f"☁️  {pl.name}  ({len(pl.track_ids)})")
                item.setData(Qt.ItemDataRole.UserRole, pl.id)
                self.playlist_list.addItem(item)
                if self.current_view == f"playlist:{pl.id}":
                    item.setSelected(True)

        self.playlist_list.blockSignals(False)

    def _rebuild_quick_access_cards(self) -> None:
        while self.quick_grid_main_layout.count():
            child = self.quick_grid_main_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                while child.layout().count():
                    sub = child.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        # 1. Tarjeta de Estado de Cuenta SpotiLinux y Base de Datos en Google Drive
        acc_frame = QFrame()
        acc_frame.setObjectName("AccountCard")
        acc_frame.setStyleSheet(
            "QFrame#AccountCard { background-color: rgba(30, 215, 96, 0.08); "
            "border: 1px solid rgba(30, 215, 96, 0.28); border-radius: 10px; }"
        )
        acc_lay = QHBoxLayout(acc_frame)
        acc_lay.setContentsMargins(12, 8, 12, 8)
        acc_lay.setSpacing(10)

        has_spoti_acc = bool(self.store.user_account.get("username") or self.store.user_account.get("email"))
        has_user = bool(self.store.google_user.get("name") or self.store.google_user.get("email"))
        has_token = bool(self.store.gdrive_config.access_token)

        lbl_acc_icon = QLabel("👤" if (has_spoti_acc or has_user) else "☁️")
        lbl_acc_icon.setStyleSheet("font-size: 22px; background: transparent; border: none;")
        acc_lay.addWidget(lbl_acc_icon)

        acc_text_col = QVBoxLayout()
        acc_text_col.setSpacing(2)
        dedup_txt = "🛡️ Duplicados: ON" if self.store.gdrive_config.verify_duplicates else "Duplicados: OFF"
        if has_spoti_acc:
            s_user = self.store.user_account.get("username", "Usuario")
            s_email = self.store.user_account.get("email", "")
            lbl_title = QLabel(f"<b>Cuenta MaoMusic: {s_user}</b> ({s_email})")
            lbl_sub = QLabel(
                f"Base de datos y carpeta «Musica» en Drive ({DEFAULT_ROOT_DRIVE_FOLDER_ID[:10]}…) • {dedup_txt}"
            )
        elif has_user:
            u_name = self.store.google_user.get("name", "Usuario de Google")
            u_email = self.store.google_user.get("email", "")
            lbl_title = QLabel(f"<b>Cuenta Google: {u_name}</b> ({u_email})")
            lbl_sub = QLabel(f"Playlists sincronizadas en Google Drive • Carpeta Musica • {dedup_txt}")
        else:
            lbl_title = QLabel("<b>Crear Cuenta o Iniciar Sesión (Vincular Playlists + Drive «Musica»)</b>")
            lbl_sub = QLabel(
                f"Crea tu cuenta (Usuario, Correo, Contraseña) para guardar tus Playlists y canciones del Drive "
                f"({DEFAULT_ROOT_DRIVE_FOLDER_ID[:10]}…/Musica) y usarlas en varios dispositivos • {dedup_txt}"
            )

        lbl_title.setStyleSheet("color: #FFFFFF; font-size: 13px; background: transparent; border: none;")
        lbl_sub.setStyleSheet("color: #B3B3B3; font-size: 11px; background: transparent; border: none;")
        lbl_sub.setWordWrap(True)
        lbl_title.setWordWrap(True)
        acc_text_col.addWidget(lbl_title)
        acc_text_col.addWidget(lbl_sub)
        acc_lay.addLayout(acc_text_col, stretch=1)

        btn_acc = QPushButton(" Mi Cuenta" if has_spoti_acc else " Crear / Login")
        btn_acc.setObjectName("ActionChipButton")
        btn_acc.setIcon(icons.get_icon("user", "#000000" if not has_spoti_acc else "#e4e4ec", 14))
        btn_acc.setIconSize(QSize(14, 14))
        btn_acc.setProperty("active", "false" if has_spoti_acc else "true")
        btn_acc.setFixedHeight(32)
        btn_acc.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_acc.clicked.connect(self.open_account_dialog)
        acc_lay.addWidget(btn_acc)

        btn_sync = QPushButton(" Sincronizar" if (has_spoti_acc or has_user or has_token) else " Conectar Drive")
        btn_sync.setObjectName("ActionChipButton")
        btn_sync.setIcon(icons.get_icon("refresh" if (has_spoti_acc or has_user or has_token) else "cloud", "#e4e4ec", 14))
        btn_sync.setIconSize(QSize(14, 14))
        btn_sync.setFixedHeight(32)
        btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sync.clicked.connect(self._sync_or_connect_google)
        acc_lay.addWidget(btn_sync)

        self.quick_grid_main_layout.addWidget(acc_frame)

        # 2. Encabezado de Sección: "Tus Playlists"
        sec_header = QLabel("Tus Playlists")
        sec_header.setStyleSheet("color: #FFFFFF; font-size: 16px; font-weight: 800; margin-top: 4px;")
        self.quick_grid_main_layout.addWidget(sec_header)

        # 3. Cuadrícula de Playlists Disponibles
        grid_widget = QWidget()
        grid_widget.setObjectName("HomePlaylistGridHolder")
        grid_widget.setStyleSheet("QWidget#HomePlaylistGridHolder { background: transparent; }")
        grid_lay = QGridLayout(grid_widget)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        grid_lay.setSpacing(12)

        cards_data = []
        cards_data.append(
            (
                "Canciones que te gustan",
                f"{len(self.store.liked_ids)} canciones favoritas",
                make_liked_cover_pixmap(56, radius=6),
                "liked",
            )
        )
        for pid, pl in self.store.playlists.items():
            pl_cov_path = getattr(pl, "cover_path", "")
            if not pl_cov_path or not Path(pl_cov_path).exists():
                from gdrive_sync import get_random_drive_cover_path
                used = {p.cover_path for p in self.store.playlists.values() if getattr(p, "cover_path", "") and p.id != pid}
                rnd = get_random_drive_cover_path(self.store.covers_dir, exclude_paths=used)
                if rnd:
                    pl.cover_path = rnd
                    pl.cover_drive_id = Path(rnd).stem
                    pl.cover_url = f"https://lh3.googleusercontent.com/d/{pl.cover_drive_id}=w600"
                    pl_cov_path = rnd
                    self.store.save()

            if pl_cov_path and Path(pl_cov_path).exists():
                cov = make_rounded_pixmap(pl_cov_path, 56, 6)
            else:
                first_tid = pl.track_ids[0] if pl.track_ids else None
                cov = (
                    make_rounded_pixmap(self.store.tracks[first_tid].cover_path, 56, 6)
                    if first_tid and first_tid in self.store.tracks and Path(self.store.tracks[first_tid].cover_path).exists()
                    else make_liked_cover_pixmap(56, 6, symbol="🎵")
                )
            cards_data.append((pl.name, f"Playlist • {len(pl.track_ids)} pistas", cov, f"playlist:{pid}"))

        idx = 0
        for title, sub, pix, key in cards_data:
            row, col = divmod(idx, 2)
            grid_lay.addWidget(
                QuickAccessCard(
                    title,
                    sub,
                    pix,
                    lambda k=key: self._select_home_preview(k),
                    play_callback=lambda _c=False, k=key: self._play_home_key(k),
                    is_selected=(key == self.home_preview_key),
                ),
                row,
                col,
            )
            idx += 1
        row, col = divmod(idx, 2)
        grid_lay.addWidget(
            QuickAccessCard(
                "➕ Crear Nueva Playlist",
                "Arma tu propia selección",
                make_liked_cover_pixmap(56, radius=6, symbol="＋", colors=((40, 40, 45), (70, 70, 80), (120, 120, 130))),
                self.create_playlist_dialog,
                play_callback=lambda _c=False: self.create_playlist_dialog(),
            ),
            row,
            col,
        )

        self.quick_grid_main_layout.addWidget(grid_widget)

        # 4. Vista previa reducible de la playlist seleccionada
        self.quick_grid_main_layout.addWidget(self._build_home_preview_card())
        self.quick_grid_main_layout.addStretch()

    # ---------------- Vista previa de playlist en Inicio ---------------- #

    def _get_home_preview_tracks(self, key: str) -> tuple[str, list[TrackMetadata]]:
        """Devuelve (nombre, pistas) para una clave de vista: liked | gdrive | playlist:<id>."""
        if key == "liked":
            return "Canciones que te gustan", [self.store.tracks[t] for t in self.store.liked_ids if t in self.store.tracks]
        if key == "gdrive":
            return "Mi Google Drive ☁️", [
                t for t in self.store.tracks.values()
                if t.filepath.startswith("gdrive://") or "Drive" in t.album or Path(t.filepath).name.startswith("gd_")
            ]
        if key.startswith("playlist:"):
            pl = self.store.playlists.get(key.split(":", 1)[1])
            if pl:
                return pl.name, [self.store.tracks[t] for t in pl.track_ids if t in self.store.tracks]
        return "", []

    def _select_home_preview(self, key: str) -> None:
        self.home_preview_key = key
        self.home_preview_expanded = False
        self._rebuild_quick_access_cards()

    def _play_home_key(self, key: str, start_index: int = 0, shuffle: bool = False) -> None:
        """Reproduce SOLO las canciones de la playlist indicada (la cola queda limitada a ella)."""
        _name, tracks = self._get_home_preview_tracks(key)
        if not tracks:
            QMessageBox.information(self, "Playlist vacía", "Esta playlist todavía no tiene canciones.")
            return
        if shuffle:
            self.engine.shuffle = True
            self.store.shuffle = True
            self.store.save()
            self._update_shuffle_ui()
            start_index = random.randrange(len(tracks))
        self.home_preview_key = key
        self._play_track_or_stream_from_cloud(tracks[start_index], tracks)

    def _toggle_home_preview_collapsed(self) -> None:
        self.home_preview_collapsed = not self.home_preview_collapsed
        self._rebuild_quick_access_cards()

    def _toggle_home_preview_expanded(self) -> None:
        self.home_preview_expanded = not self.home_preview_expanded
        self._rebuild_quick_access_cards()

    def _build_home_preview_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("ActivePlaylistHomeCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        # Si la playlist seleccionada ya no existe, usar la primera disponible
        name, tracks = self._get_home_preview_tracks(self.home_preview_key)
        if not name:
            first_pid = next(iter(self.store.playlists), None)
            self.home_preview_key = f"playlist:{first_pid}" if first_pid else "liked"
            name, tracks = self._get_home_preview_tracks(self.home_preview_key)

        info = QVBoxLayout()
        info.setSpacing(2)
        lbl_badge = QLabel(f"PLAYLIST SELECCIONADA  •  {len(tracks)} canciones")
        lbl_badge.setStyleSheet("color: #1ED760; font-size: 10px; font-weight: 800; background: transparent; border: none;")
        lbl_name = QLabel(name)
        lbl_name.setStyleSheet("color: #FFFFFF; font-size: 18px; font-weight: 900; background: transparent; border: none;")
        info.addWidget(lbl_badge)
        info.addWidget(lbl_name)
        lay.addLayout(info)

        key = self.home_preview_key
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        btn_play = QPushButton(" Reproducir")
        btn_play.setObjectName("ActionChipButton")
        btn_play.setIcon(icons.get_icon("play", "#000000", 14))
        btn_play.setIconSize(QSize(14, 14))
        btn_play.setProperty("active", "true")
        btn_play.clicked.connect(lambda _c=False, k=key: self._play_home_key(k))

        btn_shuffle = QPushButton(" Aleatorio")
        btn_shuffle.setObjectName("ActionChipButton")
        btn_shuffle.setIcon(icons.get_icon("shuffle", "#e4e4ec", 14))
        btn_shuffle.setIconSize(QSize(14, 14))
        btn_shuffle.clicked.connect(lambda _c=False, k=key: self._play_home_key(k, shuffle=True))

        btn_open = QPushButton(" Abrir")
        btn_open.setObjectName("ActionChipButton")
        btn_open.setIcon(icons.get_icon("library", "#e4e4ec", 14))
        btn_open.setIconSize(QSize(14, 14))
        btn_open.setToolTip("Abrir la playlist completa (editar, quitar o agregar canciones)")
        btn_open.clicked.connect(lambda _c=False, k=key: self.switch_view(k))

        btn_dl = QPushButton(" Descargar")
        btn_dl.setObjectName("ActionChipButton")
        btn_dl.setIcon(icons.get_icon("download", "#e4e4ec", 14))
        btn_dl.setIconSize(QSize(14, 14))
        btn_dl.setToolTip("Descargar canciones de Google Drive de esta playlist a music_library/")
        btn_dl.clicked.connect(lambda _c=False, k=key: self._download_entire_playlist(k))

        btn_collapse = QPushButton(" Mostrar lista" if self.home_preview_collapsed else " Reducir lista")
        btn_collapse.setObjectName("ActionChipButton")
        btn_collapse.setIcon(icons.get_icon("plus" if self.home_preview_collapsed else "close", "#e4e4ec", 12))
        btn_collapse.setIconSize(QSize(12, 12))
        btn_collapse.clicked.connect(self._toggle_home_preview_collapsed)
        for b in (btn_play, btn_shuffle, btn_open, btn_dl, btn_collapse):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons_row.addWidget(b)
        buttons_row.addStretch()
        lay.addLayout(buttons_row)

        if self.home_preview_collapsed:
            lbl = QLabel("Lista reducida. Pulsa «➕ Mostrar lista» para ver las canciones.")
            lbl.setStyleSheet("color: #8A8A8A; font-size: 11px; background: transparent; border: none;")
            lay.addWidget(lbl)
            return card

        if not tracks:
            lbl = QLabel("Esta playlist aún no tiene canciones. Usa «↗ Abrir» → «＋ Agregar canciones».")
            lbl.setStyleSheet("color: #8A8A8A; font-size: 11px; background: transparent; border: none;")
            lay.addWidget(lbl)
            return card

        active_id = self.engine.current_track.id if self.engine.current_track else None
        visible = tracks if self.home_preview_expanded else tracks[: self.HOME_PREVIEW_LIMIT]
        for i, t in enumerate(visible):
            lay.addWidget(
                CompactTrackRow(
                    i + 1,
                    t,
                    lambda _i=i, k=key: self._play_home_key(k, start_index=_i),
                    is_playing=(t.id == active_id),
                )
            )

        if len(tracks) > self.HOME_PREVIEW_LIMIT:
            btn_more = QPushButton(
                "▲ Mostrar menos"
                if self.home_preview_expanded
                else f"▼ Ver todas las canciones ({len(tracks) - self.HOME_PREVIEW_LIMIT} más)"
            )
            btn_more.setObjectName("GhostSmallButton")
            btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_more.clicked.connect(self._toggle_home_preview_expanded)
            lay.addWidget(btn_more)
        return card

    def set_genre_filter(self, genre: str) -> None:
        self.active_genre_filter = genre
        for g_name, btn in self.genre_buttons.items():
            self._refresh_button_active(btn, g_name == genre)
        self._refresh_track_table()

    def _get_base_tracks_for_view(self) -> tuple[str, str, str, list[TrackMetadata], bool]:
        all_tracks = [t for t in self.store.tracks.values() if not t.filepath.startswith("cloud://pending")]

        if self.current_view == "gdrive":
            tracks = [
                t for t in all_tracks
                if t.filepath.startswith("gdrive://")
                or "Drive" in t.album
                or "Cloud" in t.genre
                or Path(t.filepath).name.startswith("gd_")
            ]
            return (
                "NUBE PRIVADA • GOOGLE DRIVE",
                "Mi Google Drive ☁️",
                "Streaming directo y caché inteligente desde tu unidad privada de Google Drive",
                tracks,
                False,
            )
        if self.current_view == "liked":
            tracks = [self.store.tracks[tid] for tid in self.store.liked_ids if tid in self.store.tracks]
            return (
                "PLAYLIST FAVORITA",
                "Canciones que te gustan",
                "Tus pistas marcadas con corazón ♥ en MaoMusic",
                tracks,
                False,
            )
        if self.current_view == "recent":
            tracks = [self.store.tracks[tid] for tid in self.store.recent_ids if tid in self.store.tracks]
            return (
                "HISTORIAL",
                "Escuchado recientemente",
                "Últimas canciones reproducidas en esta sesión y anteriores",
                tracks,
                False,
            )
        if self.current_view.startswith("playlist:"):
            pid = self.current_view.split(":", 1)[1]
            pl = self.store.playlists.get(pid)
            if pl:
                tracks = [self.store.tracks[tid] for tid in pl.track_ids if tid in self.store.tracks]
                badge_text = "☁️ PLAYLIST EN LA NUBE" if pl.is_cloud else "PLAYLIST PERSONALIZADA"
                desc_text = pl.description or ("Playlist en la Nube (importada desde .txt)" if pl.is_cloud else "Playlist creada en SpotiLinux")
                return (
                    badge_text,
                    pl.name,
                    desc_text,
                    tracks,
                    True,
                )

        if self.current_view == "playlists_cloud":
            cloud_pls = [pl for pl in self.store.playlists.values() if pl.is_cloud]
            pl_track_ids = []
            seen = set()
            for pl in cloud_pls:
                for tid in pl.track_ids:
                    if tid in self.store.tracks and tid not in seen:
                        seen.add(tid)
                        pl_track_ids.append(tid)
            tracks = [self.store.tracks[tid] for tid in pl_track_ids]
            return (
                "PLAYLISTS EN LA NUBE ☁️",
                "Playlists en la Nube / Importadas",
                f"{len(cloud_pls)} playlist(s) importadas listas para reproducir o descargar a tu biblioteca local",
                tracks,
                False,
            )

        if self.current_view == "playlists_hub":
            pls_to_show = list(self.store.playlists.values())
            pl_track_ids = []
            seen = set()
            for pl in pls_to_show:
                for tid in pl.track_ids:
                    if tid in self.store.tracks and tid not in seen:
                        seen.add(tid)
                        pl_track_ids.append(tid)
            tracks = [self.store.tracks[tid] for tid in pl_track_ids]
            cloud_c = sum(1 for p in pls_to_show if p.is_cloud)
            local_c = len(pls_to_show) - cloud_c
            return (
                "TODAS LAS PLAYLISTS • LOCAL & NUBE ☁️",
                "Tus Playlists Agrupadas",
                f"{len(pls_to_show)} playlist(s) disponibles ({cloud_c} en la nube ☁️, {local_c} locales)",
                tracks,
                False,
            )

        if self.current_view == "library":
            return (
                "BIBLIOTECA LOCAL & NUBE",
                "Tu Biblioteca",
                "Todos tus archivos MP3, FLAC, WAV y pistas sincronizadas de Google Drive",
                all_tracks,
                False,
            )

        return (
            "INICIO • SPOTILINUX + MAOMAO",
            "Tus Playlists Disponibles",
            "Selecciona una playlist para escuchar o entra a 'Tu Biblioteca' para ver todas las canciones 🌿",
            all_tracks,
            False,
        )

    def _refresh_track_table(self) -> None:
        badge, title, subtitle, tracks, is_custom_pl = self._get_base_tracks_for_view()

        # Excluir canciones marcadas como ocultas en la cuenta
        if getattr(self.store, "hidden_track_ids", None):
            tracks = [t for t in tracks if t.id not in self.store.hidden_track_ids]

        # Actualizar placeholder según la playlist o vista actual
        if is_custom_pl or self.current_view.startswith("playlist:") or self.current_view in ("liked", "gdrive"):
            self.search_input.setPlaceholderText(f"🔍 Buscar canciones en «{title}»...")
        else:
            self.search_input.setPlaceholderText("🔍 ¿Qué quieres reproducir? (título, artista, álbum...)")

        if self.active_genre_filter == "Cloud ☁️":
            tracks = [
                t for t in tracks
                if t.filepath.startswith("gdrive://") or "Drive" in t.album or "Cloud" in t.genre or Path(t.filepath).name.startswith("gd_")
            ]
        elif self.active_genre_filter != "Todo":
            gf = self.active_genre_filter.lower()
            tracks = [t for t in tracks if gf in t.genre.lower() or gf in t.album.lower()]

        query = self.search_input.text().strip().lower()
        if query:
            if self.current_view == "home":
                self.quick_scroll.setVisible(False)
                self.track_table.setVisible(True)
            tracks = [
                t
                for t in tracks
                if query in t.title.lower()
                or query in t.artist.lower()
                or query in t.album.lower()
                or query in t.genre.lower()
            ]
        elif self.current_view == "home":
            self.quick_scroll.setVisible(True)
            self.track_table.setVisible(False)

        self.displayed_tracks = tracks

        total_secs = sum(t.duration for t in tracks)
        stats_text = f"{len(tracks)} canciones  •  {format_duration(total_secs)}"
        if self.current_view == "home":
            banner_pix = make_liked_cover_pixmap(138, radius=10, symbol="🎵", colors=((29, 185, 84), (16, 95, 170), (140, 50, 210)))
            banner_rgb = (29, 135, 78)
            stats_text = f"{len(self.store.playlists)} playlists disponibles  •  {len(self.store.liked_ids)} favoritas"
        elif self.current_view == "playlists_hub":
            banner_pix = make_liked_cover_pixmap(138, radius=10, symbol="🎵", colors=((30, 215, 96), (18, 140, 70), (24, 24, 32)))
            banner_rgb = (30, 215, 96)
            total_pls = len(self.store.playlists)
            cloud_count = len([p for p in self.store.playlists.values() if p.is_cloud])
            stats_text = f"{total_pls} playlists ({cloud_count} en la nube ☁️)  •  {len(tracks)} canciones"
        elif self.current_view == "playlists_cloud":
            banner_pix = make_liked_cover_pixmap(138, radius=10, symbol="☁️", colors=((20, 160, 120), (14, 90, 80), (18, 24, 30)))
            banner_rgb = (20, 160, 120)
            cloud_count = len([p for p in self.store.playlists.values() if p.is_cloud])
            stats_text = f"{cloud_count} playlists en la nube  •  {len(tracks)} canciones"
        elif self.current_view == "liked":
            banner_pix = make_liked_cover_pixmap(138, radius=10)
            banner_rgb = (82, 42, 188)
        elif self.current_view == "gdrive":
            banner_pix = make_liked_cover_pixmap(138, radius=10, symbol="☁️", colors=((15, 95, 195), (29, 185, 84), (110, 245, 210)))
            banner_rgb = (22, 105, 148)
        elif self.current_view.startswith("playlist:"):
            pid = self.current_view.split(":", 1)[1]
            pl = self.store.playlists.get(pid)
            pl_cov = getattr(pl, "cover_path", "") if pl else ""
            if pl and (not pl_cov or not Path(pl_cov).exists()):
                from gdrive_sync import get_random_drive_cover_path
                used = {p.cover_path for p in self.store.playlists.values() if getattr(p, "cover_path", "") and p.id != pid}
                rnd = get_random_drive_cover_path(self.store.covers_dir, exclude_paths=used)
                if rnd:
                    pl.cover_path = rnd
                    pl.cover_drive_id = Path(rnd).stem
                    pl.cover_url = f"https://lh3.googleusercontent.com/d/{pl.cover_drive_id}=w600"
                    pl_cov = rnd
                    self.store.save()

            if pl_cov and Path(pl_cov).exists():
                banner_pix = make_rounded_pixmap(pl_cov, 138, radius=10)
                banner_rgb = (30, 215, 96)
            elif tracks and Path(tracks[0].cover_path).exists():
                banner_pix = make_rounded_pixmap(tracks[0].cover_path, 138, radius=10)
                banner_rgb = tracks[0].dominant_color
            else:
                banner_pix = make_liked_cover_pixmap(138, radius=10, symbol="🎵")
                banner_rgb = (30, 215, 96)
        elif tracks:
            banner_pix = make_rounded_pixmap(tracks[0].cover_path, 138, radius=10)
            banner_rgb = tracks[0].dominant_color
        else:
            banner_pix = make_liked_cover_pixmap(138, radius=10)
            banner_rgb = (29, 115, 72)

        show_dl = (
            is_custom_pl
            or self.current_view in ("gdrive", "liked", "playlists_cloud")
            or any(t.filepath.startswith("gdrive://") or t.filepath.startswith("cloud://pending") for t in tracks)
        )
        self.hero_banner.set_banner_data(badge, title, subtitle, stats_text, banner_pix, banner_rgb, is_custom_pl, show_download=show_dl)

        active_id = self.engine.current_track.id if self.engine.current_track else None
        self.table_row_tracks = []

        if self.current_view in ("playlists_hub", "playlists_cloud"):
            rows_data: list[tuple[str, Optional[TrackMetadata], Optional[str]]] = []
            if self.current_view == "playlists_cloud":
                source_pls = {pid: pl for pid, pl in self.store.playlists.items() if pl.is_cloud}
            else:
                source_pls = dict(self.store.playlists)

            for pid, pl in source_pls.items():
                p_tracks = [self.store.tracks[tid] for tid in pl.track_ids if tid in self.store.tracks]
                if self.active_genre_filter == "Cloud ☁️":
                    p_tracks = [t for t in p_tracks if t.filepath.startswith("gdrive://") or "Drive" in t.album or "Cloud" in t.genre or Path(t.filepath).name.startswith("gd_")]
                elif self.active_genre_filter != "Todo":
                    gf = self.active_genre_filter.lower()
                    p_tracks = [t for t in p_tracks if gf in t.genre.lower() or gf in t.album.lower()]
                if query:
                    p_tracks = [t for t in p_tracks if query in t.title.lower() or query in t.artist.lower() or query in t.album.lower() or query in t.genre.lower()]

                if p_tracks:
                    pl_label = f"☁️ {pl.name}" if pl.is_cloud else pl.name
                    rows_data.append(("header", None, f"playlist:{pid}:{pl_label}:{len(p_tracks)}"))
                    for t in p_tracks:
                        rows_data.append(("track", t, pl_label))

            self.track_table.setRowCount(len(rows_data))
            for row, (kind, track, extra) in enumerate(rows_data):
                if kind == "header":
                    self.track_table.setRowHeight(row, 38)
                    self.table_row_tracks.append(None)
                    parts = extra.split(":", 3) if extra else []
                    pid = parts[1] if len(parts) > 1 else ""
                    pname = parts[2] if len(parts) > 2 else ""
                    pcount = parts[3] if len(parts) > 3 else ""

                    h_item0 = QTableWidgetItem()
                    h_item0.setIcon(icons.get_icon("music", "#1ED760", 14))
                    h_item0.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    h_item0.setBackground(QColor("#181822"))
                    h_item0.setForeground(QColor("#1ED760"))
                    h_item0.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    h_item0.setData(Qt.ItemDataRole.UserRole, f"header:{pid}")
                    self.track_table.setItem(row, 0, h_item0)

                    h_item1 = QTableWidgetItem(f"{pname.upper()}  •  {pcount} canciones")
                    f_h = h_item1.font()
                    f_h.setBold(True)
                    f_h.setPointSize(11)
                    h_item1.setFont(f_h)
                    h_item1.setBackground(QColor("#181822"))
                    h_item1.setForeground(QColor("#FFFFFF"))
                    h_item1.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.track_table.setItem(row, 1, h_item1)

                    h_item2 = QTableWidgetItem("")
                    h_item2.setBackground(QColor("#181822"))
                    h_item2.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.track_table.setItem(row, 2, h_item2)

                    h_item3 = QTableWidgetItem("▶ Play")
                    h_item3.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    h_item3.setBackground(QColor("#181822"))
                    h_item3.setForeground(QColor("#1ED760"))
                    f_p = h_item3.font()
                    f_p.setBold(True)
                    h_item3.setFont(f_p)
                    h_item3.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    h_item3.setData(Qt.ItemDataRole.UserRole, f"header:{pid}")
                    self.track_table.setItem(row, 3, h_item3)
                else:
                    assert track is not None
                    self.table_row_tracks.append(track)
                    self.track_table.setRowHeight(row, 56)
                    is_active = track.id == active_id
                    is_cloud = track.filepath.startswith("gdrive://") or Path(track.filepath).name.startswith("gd_")

                    num_item = QTableWidgetItem("▶" if is_active else str(len([t for t in self.table_row_tracks if t is not None])))
                    num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    num_item.setForeground(QColor("#1DB954") if is_active else QColor("#A7A7A7"))
                    if is_active:
                        f = num_item.font()
                        f.setBold(True)
                        num_item.setFont(f)
                    num_item.setData(Qt.ItemDataRole.UserRole, track.id)
                    self.track_table.setItem(row, 0, num_item)

                    cloud_tag = "  ☁️" if is_cloud else ""
                    album_str = f"  •  {track.album}" if track.album and track.album != "Desconocido" else ""
                    pl_tag = f"  [{extra}]" if extra else ""
                    title_text = f"{track.title}{cloud_tag}\n{track.artist}{album_str}{pl_tag}"
                    title_item = QTableWidgetItem(title_text)
                    title_item.setIcon(QIcon(make_rounded_pixmap(track.cover_path, 42, radius=8)))
                    title_item.setForeground(QColor("#1DB954") if is_active else QColor("#FFFFFF"))
                    f_title = title_item.font()
                    f_title.setBold(True)
                    title_item.setFont(f_title)
                    self.track_table.setItem(row, 1, title_item)

                    liked = self.store.is_liked(track.id)
                    heart_item = QTableWidgetItem("♥" if liked else "♡")
                    heart_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    heart_item.setForeground(QColor("#1DB954") if liked else QColor("#888888"))
                    f_h = heart_item.font()
                    f_h.setPointSize(14)
                    heart_item.setFont(f_h)
                    self.track_table.setItem(row, 2, heart_item)

                    dur_txt = format_duration(track.duration) if track.duration > 0 else "Cloud ☁️"
                    dur_item = QTableWidgetItem(dur_txt)
                    dur_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    dur_item.setForeground(QColor("#A7A7A7"))
                    self.track_table.setItem(row, 3, dur_item)

            self.displayed_tracks = [t for t in self.table_row_tracks if t is not None]
        else:
            self.track_table.setRowCount(len(tracks))
            self.table_row_tracks = list(tracks)
            self.displayed_tracks = tracks

            for row, track in enumerate(tracks):
                self.track_table.setRowHeight(row, 56)
                is_active = track.id == active_id
                is_cloud = track.filepath.startswith("gdrive://") or Path(track.filepath).name.startswith("gd_")

                # Col 0: # / Play
                num_item = QTableWidgetItem("▶" if is_active else str(row + 1))
                num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                num_item.setForeground(QColor("#1DB954") if is_active else QColor("#A7A7A7"))
                if is_active:
                    f = num_item.font()
                    f.setBold(True)
                    num_item.setFont(f)
                num_item.setData(Qt.ItemDataRole.UserRole, track.id)
                self.track_table.setItem(row, 0, num_item)

                # Col 1: Tarjeta con Portada + Título + Artista / Álbum + Badge de Playlist
                cloud_tag = "  ☁️" if is_cloud else ""
                album_str = f"  •  {track.album}" if track.album and track.album != "Desconocido" else ""
                pl_names = self.store.get_track_playlists(track) if hasattr(self.store, "get_track_playlists") else []
                pl_tag = f"  [{', '.join(pl_names)}]" if pl_names else ""
                title_text = f"{track.title}{cloud_tag}\n{track.artist}{album_str}{pl_tag}"
                title_item = QTableWidgetItem(title_text)
                title_item.setIcon(QIcon(make_rounded_pixmap(track.cover_path, 42, radius=8)))
                title_item.setForeground(QColor("#1DB954") if is_active else QColor("#FFFFFF"))
                f_title = title_item.font()
                f_title.setBold(True)
                title_item.setFont(f_title)
                self.track_table.setItem(row, 1, title_item)

                # Col 2: Me gusta (♥ / ♡)
                liked = self.store.is_liked(track.id)
                heart_item = QTableWidgetItem("♥" if liked else "♡")
                heart_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                heart_item.setForeground(QColor("#1DB954") if liked else QColor("#888888"))
                f_h = heart_item.font()
                f_h.setPointSize(14)
                heart_item.setFont(f_h)
                self.track_table.setItem(row, 2, heart_item)

                # Col 3: Duración
                dur_txt = format_duration(track.duration) if track.duration > 0 else "Cloud ☁️"
                dur_item = QTableWidgetItem(dur_txt)
                dur_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                dur_item.setForeground(QColor("#A7A7A7"))
                self.track_table.setItem(row, 3, dur_item)

    # ================================================================== #
    #                     INTERACCIÓN CON CANCIONES                      #
    # ================================================================== #

    def _on_table_double_clicked(self, row: int, col: int = 0) -> None:
        if 0 <= row < len(self.table_row_tracks):
            track = self.table_row_tracks[row]
            if track is None:
                item_data = self.track_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                if item_data and str(item_data).startswith("header:"):
                    pid = str(item_data).split(":", 1)[1]
                    pl = self.store.playlists.get(pid)
                    if pl and pl.track_ids:
                        pl_tracks = [self.store.tracks[tid] for tid in pl.track_ids if tid in self.store.tracks]
                        if pl_tracks:
                            self._play_track_or_stream_from_cloud(pl_tracks[0], pl_tracks)
                return
            if col == 2:
                return
            self._play_track_or_stream_from_cloud(track, self.displayed_tracks)

    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if not (0 <= row < len(self.table_row_tracks)):
            return
        track = self.table_row_tracks[row]
        if track is None:
            item_data = self.track_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if item_data and str(item_data).startswith("header:") and col in (0, 3):
                pid = str(item_data).split(":", 1)[1]
                pl = self.store.playlists.get(pid)
                if pl and pl.track_ids:
                    pl_tracks = [self.store.tracks[tid] for tid in pl.track_ids if tid in self.store.tracks]
                    if pl_tracks:
                        self._play_track_or_stream_from_cloud(pl_tracks[0], pl_tracks)
            return

        if col == 2:
            self.store.toggle_like(track.id)
            self._sync_playlists_to_cloud()
            self._refresh_track_table()
            if self.engine.current_track and self.engine.current_track.id == track.id:
                self._update_like_button_ui(track.id)
            if self.current_view == "home":
                self._rebuild_quick_access_cards()
        elif col == 0:
            self._play_track_or_stream_from_cloud(track, self.displayed_tracks)

    def _on_table_context_menu(self, pos) -> None:
        selected_rows = sorted(set(idx.row() for idx in self.track_table.selectedIndexes()))
        if not selected_rows:
            row = self.track_table.rowAt(pos.y())
            if 0 <= row < len(self.table_row_tracks):
                selected_rows = [row]

        if not selected_rows:
            return

        selected_tracks = [self.table_row_tracks[r] for r in selected_rows if 0 <= r < len(self.table_row_tracks) and self.table_row_tracks[r] is not None]
        if not selected_tracks:
            return
        selected_track_ids = [t.id for t in selected_tracks]
        n_tracks = len(selected_tracks)
        primary_track = selected_tracks[0]

        menu = QMenu(self)

        if n_tracks == 1:
            act_play = menu.addAction("▶  Reproducir / Escuchar en línea")
            act_download_cloud = None
            if primary_track.filepath.startswith("gdrive://"):
                act_download_cloud = menu.addAction("⬇️  Descargar a la Carpeta de la App (Offline)")
            act_queue = menu.addAction("☰  Añadir a la cola de reproducción")
            liked = self.store.is_liked(primary_track.id)
            act_like = menu.addAction("💔  Quitar de Canciones que te gustan" if liked else "♥  Guardar en Canciones que te gustan")

            menu.addSeparator()
            pl_menu = menu.addMenu("＋  Añadir a Playlist")
            pl_actions: dict[QAction, str] = {}
            for pid, pl in self.store.playlists.items():
                a = pl_menu.addAction(f"🎵 {pl.name}")
                pl_actions[a] = pid
            act_new_pl_with_tracks = pl_menu.addAction("✨ Crear nueva Playlist con esta canción...")

            menu.addSeparator()
            act_move = menu.addAction("⇄  Mover a otra Playlist...")
            act_hide = menu.addAction("👁️‍🗨️  Ocultar de la cuenta (no mostrar más)")

            act_remove_from_pl = None
            act_reorder_up = None
            act_reorder_down = None
            if self.current_view.startswith("playlist:"):
                menu.addSeparator()
                act_reorder_up = menu.addAction("⬆️  Subir una posición en esta Playlist")
                act_reorder_down = menu.addAction("⬇️  Bajar una posición en esta Playlist")
                act_remove_from_pl = menu.addAction(icons.get_icon("playlist_remove", "#e4e4ec", 16), "✕  Quitar de esta Playlist")

            menu.addSeparator()
            act_delete_app = menu.addAction(icons.get_icon("trash", "#ef4444", 16), "🗑  Eliminar de la aplicación")
        else:
            act_play = menu.addAction(f"▶  Reproducir selección ({n_tracks} canciones)")
            act_download_cloud = None
            act_queue = menu.addAction(f"☰  Añadir {n_tracks} canciones a la cola")
            act_like = menu.addAction(f"♥  Guardar las {n_tracks} canciones en Favoritos")

            menu.addSeparator()
            act_move = menu.addAction(f"⇄  Mover {n_tracks} canciones a otra Playlist...")
            act_hide = menu.addAction(f"👁️‍🗨️  Ocultar las {n_tracks} canciones de la cuenta")

            menu.addSeparator()
            pl_menu = menu.addMenu(f"＋  Añadir {n_tracks} canciones seleccionadas a Playlist")
            pl_actions = {}
            for pid, pl in self.store.playlists.items():
                a = pl_menu.addAction(f"🎵 {pl.name}")
                pl_actions[a] = pid
            act_new_pl_with_tracks = pl_menu.addAction(f"✨ Crear nueva Playlist con las {n_tracks} canciones...")

            act_remove_from_pl = None
            if self.current_view.startswith("playlist:"):
                menu.addSeparator()
                act_remove_from_pl = menu.addAction(icons.get_icon("playlist_remove", "#e4e4ec", 16), f"✕  Quitar las {n_tracks} canciones seleccionadas de esta Playlist")

            menu.addSeparator()
            act_delete_app = menu.addAction(icons.get_icon("trash", "#ef4444", 16), f"🗑  Eliminar las {n_tracks} canciones de la aplicación")

        chosen = menu.exec(self.track_table.viewport().mapToGlobal(pos))
        if not chosen:
            return

        if chosen == act_play:
            self._play_track_or_stream_from_cloud(primary_track, selected_tracks if n_tracks > 1 else self.displayed_tracks)
        elif act_download_cloud and chosen == act_download_cloud:
            self._download_cloud_track_to_app_folder(primary_track)
        elif chosen == act_queue:
            for t in selected_tracks:
                self.engine.add_to_queue(t)
        elif chosen == act_like:
            if n_tracks == 1:
                self.store.toggle_like(primary_track.id)
            else:
                for t in selected_tracks:
                    if t.id not in self.store.liked_ids:
                        self.store.liked_ids.append(t.id)
                self.store.save()
            self._sync_playlists_to_cloud()
            self._refresh_track_table()
            if self.engine.current_track and self.engine.current_track.id == primary_track.id:
                self._update_like_button_ui(primary_track.id)
        elif chosen in pl_actions:
            pid = pl_actions[chosen]
            added = self.store.add_multiple_tracks_to_playlist(pid, selected_track_ids)
            self._sync_playlists_to_cloud()
            self._refresh_playlist_sidebar()
            if self.current_view == f"playlist:{pid}":
                self.switch_view(self.current_view)
            QMessageBox.information(
                self,
                "Canciones Agregadas",
                f"Se agregaron {added} canciones a la playlist seleccionada.",
            )
        elif chosen == act_new_pl_with_tracks:
            default_name = f"Mix {primary_track.title}" if n_tracks == 1 else f"Mix ({n_tracks} pistas)"
            dlg = PlaylistDialog(self, "Nueva Playlist", name=default_name)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                name, desc = dlg.get_values()
                pl = self.store.create_playlist(name or default_name, desc)
                self.store.add_multiple_tracks_to_playlist(pl.id, selected_track_ids)
                self._sync_playlists_to_cloud()
                self._refresh_playlist_sidebar()
                self.switch_view(f"playlist:{pl.id}")
        elif act_remove_from_pl and chosen == act_remove_from_pl:
            pid = self.current_view.split(":", 1)[1]
            for tid in selected_track_ids:
                self.store.remove_track_from_playlist(pid, tid)
            self._sync_playlists_to_cloud()
            self.switch_view(self.current_view)
        elif act_reorder_up and chosen == act_reorder_up:
            spid = self.current_view.split(":", 1)[1]
            pl = self.store.playlists.get(spid)
            if pl and primary_track.id in pl.track_ids:
                idx = pl.track_ids.index(primary_track.id)
                if idx > 0:
                    pl.track_ids[idx], pl.track_ids[idx - 1] = pl.track_ids[idx - 1], pl.track_ids[idx]
                    self.store.save()
                    self._sync_playlists_to_cloud()
                    self._refresh_track_table()
        elif act_reorder_down and chosen == act_reorder_down:
            spid = self.current_view.split(":", 1)[1]
            pl = self.store.playlists.get(spid)
            if pl and primary_track.id in pl.track_ids:
                idx = pl.track_ids.index(primary_track.id)
                if idx < len(pl.track_ids) - 1:
                    pl.track_ids[idx], pl.track_ids[idx + 1] = pl.track_ids[idx + 1], pl.track_ids[idx]
                    self.store.save()
                    self._sync_playlists_to_cloud()
                    self._refresh_track_table()
        elif chosen == act_delete_app:
            msg = f"¿Seguro que deseas eliminar «{primary_track.title}» de la aplicación?" if n_tracks == 1 else f"¿Seguro que deseas eliminar las {n_tracks} canciones seleccionadas de la aplicación?"
            confirm = QMessageBox.question(
                self,
                "Eliminar de la aplicación",
                msg + "\n\nSe eliminarán de la biblioteca, de todas las playlists y el archivo local si existe.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm == QMessageBox.StandardButton.Yes:
                for t in selected_tracks:
                    if self.engine.current_track and self.engine.current_track.id == t.id:
                        self.engine.stop()
                    self.store.delete_track_from_app(t.id, delete_local_file=True)
                self._sync_playlists_to_cloud()
                self._refresh_track_table()
                self._refresh_playlist_sidebar()
        elif chosen == act_hide:
            for t in selected_tracks:
                self.store.hide_track(t.id)
                if self.engine.current_track and self.engine.current_track.id == t.id:
                    self.engine.next_track()
            self._sync_playlists_to_cloud()
            self._refresh_track_table()
            self.show_toast(f"🚫 {n_tracks} canción(es) ocultada(s) de tu cuenta.")
        elif chosen == act_move:
            self._prompt_move_tracks_to_playlist(selected_tracks)

    def _prompt_move_tracks_to_playlist(self, tracks_to_move: list[TrackMetadata]) -> None:
        """Mueve canciones a una playlist de destino, removiéndolas de la playlist de origen si aplica."""
        if not self.store.playlists:
            self.show_toast("No hay playlists creadas todavía.")
            return
        pl_names = [pl.name for pl in self.store.playlists.values()]
        chosen_name, ok = QInputDialog.getItem(
            self,
            "Mover a Playlist",
            f"Selecciona la playlist de destino para {len(tracks_to_move)} canción(es):",
            pl_names,
            0,
            False,
        )
        if ok and chosen_name:
            target_pl = next((p for p in self.store.playlists.values() if p.name == chosen_name), None)
            if not target_pl:
                return
            source_pl = None
            if self.current_view.startswith("playlist:"):
                spid = self.current_view.split(":", 1)[1]
                source_pl = self.store.playlists.get(spid)

            for t in tracks_to_move:
                if source_pl and t.id in source_pl.track_ids and source_pl.id != target_pl.id:
                    source_pl.track_ids.remove(t.id)
                if t.id not in target_pl.track_ids:
                    target_pl.track_ids.append(t.id)

            self.store.save()
            self._sync_playlists_to_cloud()
            self._refresh_track_table()
            self.show_toast(f"✔ {len(tracks_to_move)} canción(es) movida(s) a «{target_pl.name}».")

    def eventFilter(self, obj, event):
        if hasattr(self, "track_table") and obj == self.track_table.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._table_press_pos = event.pos()
                self._table_press_timer.start(450)
            elif event.type() == QEvent.Type.MouseMove:
                if self._table_press_pos and (event.pos() - self._table_press_pos).manhattanLength() > 10:
                    self._table_press_timer.stop()
            elif event.type() in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
                self._table_press_timer.stop()
        return super().eventFilter(obj, event)

    def _on_table_long_press_timeout(self) -> None:
        if self._table_press_pos:
            self._on_table_context_menu(self._table_press_pos)

    def _open_multi_track_picker_for_current_playlist(self) -> None:
        """Abre un diálogo interactivo para seleccionar varias canciones con checkboxes y agregarlas juntas."""
        if not self.current_view.startswith("playlist:"):
            return
        pid = self.current_view.split(":", 1)[1]
        pl = self.store.playlists.get(pid)
        if not pl:
            return
        all_tracks = list(self.store.tracks.values())
        dlg = MultiTrackSelectorDialog(self, all_tracks, pl.track_ids)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dlg.get_selected_track_ids()
            if selected_ids:
                added = self.store.add_multiple_tracks_to_playlist(pid, selected_ids)
                self._sync_playlists_to_cloud()
                self._refresh_playlist_sidebar()
                self.switch_view(self.current_view)
                QMessageBox.information(
                    self,
                    "Canciones Agregadas",
                    f"✔ Se agregaron {added} canciones a la playlist «{pl.name}» con éxito.\n"
                    "Sincronizadas con tu cuenta de Google Drive para todos tus dispositivos.",
                )

    def _sync_or_connect_google(self) -> None:
        self._sync_playlists_to_cloud(notify=True)
        self.start_gdrive_sync(silent_view_keep=False)

    def _collect_drive_tracks_for_account(self) -> list[dict]:
        items: list[dict] = []
        for t in self.store.tracks.values():
            if t.filepath.startswith("gdrive://") or t.id.startswith("gd_"):
                fid = t.filepath.replace("gdrive://", "") if t.filepath.startswith("gdrive://") else t.id[3:]
                items.append(
                    {
                        "id": t.id,
                        "file_id": fid,
                        "title": t.title,
                        "artist": t.artist,
                        "album": t.album,
                        "genre": t.genre,
                        "duration": t.duration,
                        "filepath": f"gdrive://{fid}" if fid else t.filepath,
                    }
                )
        return items

    def _serialize_playlist_for_cloud(self, pl: Playlist) -> dict:
        d = pl.to_dict()
        d["trackIds"] = list(pl.track_ids)
        d["isCloud"] = bool(pl.is_cloud)
        snapshots: list[dict] = []
        for tid in pl.track_ids:
            t = self.store.tracks.get(tid)
            if t and not t.filepath.startswith("cloud://pending"):
                fid = t.filepath.replace("gdrive://", "") if t.filepath.startswith("gdrive://") else (t.id[3:] if t.id.startswith("gd_") else "")
                snapshots.append(
                    {
                        "id": t.id,
                        "driveId": fid,
                        "file_id": fid,
                        "title": t.title,
                        "artist": t.artist,
                        "album": t.album,
                        "duration": t.duration,
                    }
                )
        d["tracks_snapshot"] = snapshots
        return d

    def _sync_playlists_to_cloud(self, notify: bool = False) -> None:
        token = self.store.gdrive_config.access_token
        payload = {
            "app": "SpotiLinux",
            "version": "1.6.0",
            "updatedAt": time.time(),
            "user": self.store.google_user,
            "account": self.store.user_account,
            "playlists": [self._serialize_playlist_for_cloud(pl) for pl in self.store.playlists.values()],
            "likedIds": list(self.store.liked_ids),
            "drive_tracks": self._collect_drive_tracks_for_account(),
        }
        self._cloud_sync_notify = notify
        self.cloud_service.sync_cloud_db(payload)

    def _on_cloud_sync_success(self, merged: dict, notify: bool = False) -> None:
        changed = False
        # Restaurar canciones de Drive que vengan en la cuenta o en el archivo sincronizado
        acc_block = merged.get("account") if isinstance(merged.get("account"), dict) else {}
        if acc_block or merged.get("drive_tracks") or merged.get("playlists"):
            fake_acc = {
                "drive_tracks": merged.get("drive_tracks") or acc_block.get("drive_tracks", []),
                "playlists": merged.get("playlists", []),
                "liked_ids": merged.get("likedIds", []),
            }
            pl_r, drv_r = self.store.apply_account_record(fake_acc)
            if pl_r or drv_r:
                changed = True

        cloud_playlists = merged.get("playlists", [])
        if isinstance(cloud_playlists, dict):
            cloud_playlists = list(cloud_playlists.values())
        for cp in cloud_playlists:
            if not isinstance(cp, dict):
                continue
            pid = cp.get("id")
            pname = cp.get("name", "")
            desc_str = str(cp.get("description", cp.get("desc", "")) or "")
            cp_is_cloud = bool(
                cp.get("is_cloud")
                or cp.get("isCloud")
                or "cola de descargas" in desc_str.lower()
                or "en la nube" in desc_str.lower()
            )
            local_pl = self.store.playlists.get(pid) or next(
                (p for p in self.store.playlists.values() if p.name.lower() == pname.lower()), None
            )
            if local_pl:
                if cp_is_cloud and not local_pl.is_cloud:
                    local_pl.is_cloud = True
                    changed = True
                for tid in cp.get("track_ids", cp.get("trackIds", [])):
                    if tid in self.store.tracks and tid not in local_pl.track_ids:
                        local_pl.track_ids.append(tid)
                        changed = True
            else:
                new_pl = Playlist(
                    id=pid or str(uuid.uuid4())[:8],
                    name=pname or "Playlist Cloud",
                    description=desc_str,
                    track_ids=[t for t in cp.get("track_ids", cp.get("trackIds", [])) if t in self.store.tracks],
                    created_at=float(cp.get("created_at", time.time())),
                    cover_url=str(cp.get("cover_url") or cp.get("coverUrl") or ""),
                    cover_drive_id=str(cp.get("cover_drive_id") or cp.get("coverDriveId") or ""),
                    cover_path=str(cp.get("cover_path") or cp.get("coverPath") or ""),
                    is_cloud=cp_is_cloud,
                )
                self.store.playlists[new_pl.id] = new_pl
                changed = True

        for lid in merged.get("likedIds", []):
            if lid in self.store.tracks and lid not in self.store.liked_ids:
                self.store.liked_ids.append(lid)
                changed = True

        u = merged.get("user")
        if u and isinstance(u, dict) and u.get("name"):
            self.store.google_user = u
            changed = True

        if changed:
            self.store.save()
            self._refresh_playlist_sidebar()
            if self.current_view == "home":
                self._rebuild_quick_access_cards()
            else:
                self._refresh_track_table()

        if notify:
            QMessageBox.information(
                self,
                "☁️ Sincronización Exitosa",
                f"Tus playlists y las canciones del Drive ({DEFAULT_ROOT_DRIVE_FOLDER_ID} / Musica) están sincronizadas.\n"
                "Puedes usarlas en varios dispositivos iniciando sesión con tu misma cuenta MaoMusic.",
            )

    def _on_cloud_sync_failed(self, err: str, notify: bool = False) -> None:
        if notify:
            QMessageBox.warning(
                self,
                "Aviso de Sincronización Cloud",
                f"No se pudo sincronizar con Google Drive:\n{err}",
            )

    def _download_entire_playlist(self, key: Optional[str] = None) -> None:
        """Descarga todas las pistas de la playlist a music_library/ verificando duplicados."""
        target_key = key or self.current_view
        if target_key == "home":
            target_key = self.home_preview_key

        name, pl_tracks = self._get_home_preview_tracks(target_key)
        if not pl_tracks and target_key.startswith("playlist:"):
            pid = target_key.split(":", 1)[1]
            pl = self.store.playlists.get(pid)
            if pl:
                name = pl.name
                pl_tracks = [self.store.tracks[t] for t in pl.track_ids if t in self.store.tracks]
        elif not pl_tracks and target_key in ("gdrive", "library"):
            name = "Google Drive (Musica)"
            pl_tracks = [t for t in self.store.tracks.values() if t.filepath.startswith("gdrive://") or "Drive" in t.album or Path(t.filepath).name.startswith("gd_")]

        if not pl_tracks:
            QMessageBox.information(self, "Descargar Playlist", "No hay canciones disponibles en esta lista para descargar.")
            return

        cloud_gdrive = [t for t in pl_tracks if t.filepath.startswith("gdrive://")]
        cloud_pending_txt = [t for t in pl_tracks if t.filepath.startswith("cloud://pending")]
        total_pending = len(cloud_gdrive) + len(cloud_pending_txt)

        if total_pending == 0:
            QMessageBox.information(
                self,
                "Descargar Playlist",
                f"Todas las canciones de «{name}» ya están disponibles localmente en tu biblioteca.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Descargar Playlist a la Biblioteca",
            f"¿Deseas descargar las {total_pending} canciones pendientes de «{name}» a tu biblioteca local?\n\n"
            "Se añadirán a tu biblioteca y estarán disponibles offline.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if cloud_pending_txt:
            queries = [t.title for t in cloud_pending_txt]
            self.download_queue.add(queries, keep_local=True, target_playlist=name)
            self._update_download_badge()
            self.open_download_queue_panel()

        if cloud_gdrive:
            self.hero_banner.lbl_subtitle.setText(
                f"⬇️ Iniciando descarga en segundo plano de {len(cloud_gdrive)} canciones de «{name}»..."
            )
            self.cloud_service.download_playlist_batch(cloud_gdrive, self.store.tracks)

    def _on_playlist_download_progress(self, current: int, total: int, title: str) -> None:
        self.hero_banner.lbl_subtitle.setText(f"⬇️ Descargando ({current}/{total}): «{title}»...")
        if hasattr(self.hero_banner, "btn_download_playlist") and hasattr(self.hero_banner.btn_download_playlist, "set_progress"):
            self.hero_banner.btn_download_playlist.set_progress(current, total)

    def _on_playlist_batch_track_saved(self, old_id: str, updated_track: TrackMetadata, was_duplicate: bool) -> None:
        self.store.tracks[old_id] = updated_track
        self.store.deduplicate_library()
        self.store.save()
        self._refresh_track_table()

    def _on_playlist_batch_finished(self, downloaded: int, duplicates: int, errors: int) -> None:
        if hasattr(self.hero_banner, "btn_download_playlist") and hasattr(self.hero_banner.btn_download_playlist, "set_state"):
            self.hero_banner.btn_download_playlist.set_state("completed")
        parts = []
        if downloaded > 0:
            parts.append(f"{downloaded} canciones descargadas")
        if duplicates > 0:
            parts.append(f"{duplicates} ya existían localmente (vinculadas)")
        if errors > 0:
            parts.append(f"{errors} errores")
        summary = ", ".join(parts) if parts else "completado"
        self.hero_banner.lbl_subtitle.setText(f"✔ Descarga de playlist finalizada: {summary}.")
        QMessageBox.information(
            self,
            "Descarga de Playlist Finalizada",
            f"Se completó la sincronización de la playlist:\n\n• {summary}.\n\nTodos los archivos están listos para reproducir sin conexión.",
        )

    def _download_cloud_track_to_app_folder(self, track: TrackMetadata) -> None:
        """Descarga explícitamente una pista de Google Drive a music_library/ verificando duplicados primero."""
        self.cloud_service.download_track_to_library(track, self.store.tracks)

    def _on_cloud_download_saved(self, old_id: str, updated_track: TrackMetadata, was_duplicate: bool = False) -> None:
        self.store.tracks[old_id] = updated_track
        if hasattr(self.store, "reconcile_playlist_track_ids"):
            self.store.reconcile_playlist_track_ids(old_id, updated_track.id)
        self.store.deduplicate_library()
        self.store.save()
        self._refresh_track_table()
        if was_duplicate:
            msg = f"🛡️ Duplicado evitado: «{updated_track.title}» ya estaba descargada en music_library/"
            self.hero_banner.lbl_subtitle.setText(msg)
        else:
            self.hero_banner.lbl_subtitle.setText(
                f"✔ Guardada en carpeta de la app (music_library/): {updated_track.title}"
            )

    def play_current_view(self) -> None:
        if self.current_view == "home":
            self._play_home_key(self.home_preview_key)
            return
        if not self.displayed_tracks:
            if self.current_view == "gdrive":
                self.open_google_drive_dialog()
            return
        same_context = [t.id for t in self.engine.context_tracks] == [t.id for t in self.displayed_tracks]
        if same_context and self.engine.current_track and any(t.id == self.engine.current_track.id for t in self.displayed_tracks):
            self.engine.toggle_play_pause()
        else:
            # Reiniciar la cola con SOLO las canciones de esta vista/playlist
            self._play_track_or_stream_from_cloud(self.displayed_tracks[0], self.displayed_tracks)

    def shuffle_play_current_view(self) -> None:
        if self.current_view == "home":
            self._play_home_key(self.home_preview_key, shuffle=True)
            return
        if not self.displayed_tracks:
            return
        self.engine.shuffle = True
        self.store.shuffle = True
        self.store.save()
        self._update_shuffle_ui()
        chosen = random.choice(self.displayed_tracks)
        self._play_track_or_stream_from_cloud(chosen, self.displayed_tracks)

    # ================================================================== #
    #                  GESTIÓN DE PLAYLISTS E IMPORTACIÓN                #
    # ================================================================== #

    def _on_playlist_item_clicked(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid:
            self.switch_view(f"playlist:{pid}")

    def _on_playlist_context_menu(self, pos) -> None:
        item = self.playlist_list.itemAt(pos)
        if not item:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        pl = self.store.playlists.get(pid)
        if not pl:
            return

        menu = QMenu(self)
        act_open = menu.addAction("▶  Abrir Playlist")
        act_rename = menu.addAction("✎  Editar nombre y descripción")
        act_delete = menu.addAction("🗑  Eliminar Playlist")

        chosen = menu.exec(self.playlist_list.viewport().mapToGlobal(pos))
        if chosen == act_open:
            self.switch_view(f"playlist:{pid}")
        elif chosen == act_rename:
            self._edit_playlist_by_id(pid)
        elif chosen == act_delete:
            self._delete_playlist_by_id(pid)

    def create_playlist_dialog(self) -> None:
        dlg = PlaylistDialog(self, "Crear Nueva Playlist")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, desc = dlg.get_values()
            pl = self.store.create_playlist(name or "Mi Playlist", desc)
            self._sync_playlists_to_cloud()
            self.switch_view(f"playlist:{pl.id}")

    def edit_current_playlist(self) -> None:
        if self.current_view.startswith("playlist:"):
            pid = self.current_view.split(":", 1)[1]
            self._edit_playlist_by_id(pid)

    def _edit_playlist_by_id(self, pid: str) -> None:
        pl = self.store.playlists.get(pid)
        if not pl:
            return
        dlg = PlaylistDialog(self, "Editar Playlist", name=pl.name, desc=pl.description)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, desc = dlg.get_values()
            self.store.rename_playlist(pid, name, desc)
            self._sync_playlists_to_cloud()
            self.switch_view(f"playlist:{pid}")

    def delete_current_playlist(self) -> None:
        if self.current_view.startswith("playlist:"):
            pid = self.current_view.split(":", 1)[1]
            self._delete_playlist_by_id(pid)

    def _delete_playlist_by_id(self, pid: str) -> None:
        pl = self.store.playlists.get(pid)
        if not pl:
            return
        reply = QMessageBox.question(
            self,
            "Eliminar Playlist",
            f"¿Seguro que deseas eliminar la playlist «{pl.name}»?\n(Los archivos de audio no se borrarán).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.store.delete_playlist(pid)
            self._sync_playlists_to_cloud()
            self.switch_view("home")

    def auto_scan_pc_music(self) -> None:
        """Rastrea automáticamente las carpetas de música y descargas del dispositivo y guarda las canciones en music_library/."""
        discovered = self.store.auto_scan_device_to_app_folder()
        self.switch_view("library")
        QMessageBox.information(
            self,
            "Auto-Rastreo Completado",
            f"✔ Se rastrearon y guardaron {len(discovered)} canciones en la carpeta de la aplicación:\n"
            f"{self.store.music_library_dir}\n\n"
            "Ya no tendrás que volver a importarlas cada vez que abras MaoMusic.",
        )

    def import_folder_dialog(self) -> None:
        start_dir = str(Path.home() / "Music") if (Path.home() / "Music").exists() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de música", start_dir)
        if folder:
            added = self.store.scan_folder(Path(folder), copy_to_app_folder=True)
            self.switch_view("library")
            QMessageBox.information(
                self,
                "Carpeta escaneada y guardada",
                f"Se importaron y guardaron {len(added)} pistas de audio en la carpeta de la app:\n"
                f"{self.store.music_library_dir}",
            )

    def import_files_dialog(self) -> None:
        start_dir = str(Path.home() / "Music") if (Path.home() / "Music").exists() else str(Path.home())
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Importar archivos de audio",
            start_dir,
            f"Archivos de Audio ({exts})",
        )
        if files:
            for f in files:
                self.store.import_file(Path(f), save_immediately=False, copy_to_app_folder=True)
            self.store.save()
            self.switch_view("library")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        imported = 0
        for url in event.mimeData().urls():
            local_path = Path(url.toLocalFile())
            if local_path.is_dir():
                imported += len(self.store.scan_folder(local_path, copy_to_app_folder=True))
            elif local_path.is_file() and local_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                if self.store.import_file(local_path, copy_to_app_folder=True):
                    imported += 1
        if imported > 0:
            self.switch_view(self.current_view)

    # ================================================================== #
    #                 CALLBACKS DEL MOTOR Y CONTROLES                    #
    # ================================================================== #

    def _on_engine_track_changed(self, track: Optional[TrackMetadata]) -> None:
        if not track:
            return
        self.store.record_play(track.id)
        self._update_now_playing_ui(track)
        self._refresh_track_table()
        if self.current_view == "home":
            self._rebuild_quick_access_cards()
        self._broadcast_playback_session(force=True)

    def _update_now_playing_ui(self, track: TrackMetadata) -> None:
        self.lbl_bar_cover.setPixmap(make_rounded_pixmap(track.cover_path, 56, radius=6))
        self.lbl_bar_title.setText(track.title)
        self.lbl_bar_artist.setText(track.artist)
        self.lbl_dur_time.setText(format_duration(track.duration))
        self._update_like_button_ui(track.id)

        self.lbl_right_cover.setPixmap(make_rounded_pixmap(track.cover_path, 56, radius=8))
        self.lbl_right_title.setText(track.title)
        self.lbl_right_artist.setText(track.artist)
        year_suffix = f" ({track.year})" if track.year else ""
        self.lbl_right_album.setText(f"{track.album}{year_suffix}")

        self.visualizer.set_accent_color(track.dominant_color)
        self.visualizer.set_cover(track.cover_path)

        if hasattr(self, "vinyl_overlay") and self.vinyl_overlay.isVisible():
            self.vinyl_overlay.update_state(
                title=track.title,
                artist=track.artist,
                cover_path=track.cover_path,
                track_filepath=track.filepath,
                is_playing=self.engine.is_playing(),
            )

    def _update_like_button_ui(self, track_id: str) -> None:
        liked = self.store.is_liked(track_id)
        self.btn_bar_like.setText("")
        self.btn_bar_like.setIcon(icons.get_icon("heart_filled" if liked else "heart", "#1ed760" if liked else "#a6a6b4", 18))
        self._refresh_button_active(self.btn_bar_like, liked)

    def _on_engine_state_changed(self, is_playing: bool) -> None:
        icon_name = "pause" if is_playing else "play"
        self.btn_play_pause.setText("")
        self.btn_play_pause.setIcon(icons.get_icon(icon_name, "#000000", 22))
        self.hero_banner.btn_play_banner.setText("")
        self.hero_banner.btn_play_banner.setIcon(icons.get_icon(icon_name, "#000000", 22))

        if hasattr(self, "vinyl_overlay") and self.vinyl_overlay.isVisible():
            t = self.engine.current_track
            title = t.title if t else "MaoMusic"
            artist = t.artist if t else "Tocadiscos OLED"
            cov = t.cover_path if t else None
            fpath = t.filepath if t else ""
            self.vinyl_overlay.update_state(title, artist, cov, fpath, is_playing)
        self._broadcast_playback_session(force=True)

    def _on_engine_position_changed(self, pos_ms: int, dur_ms: int) -> None:
        if not self._seeking_slider:
            self.lbl_pos_time.setText(format_duration(pos_ms / 1000.0))
            if dur_ms > 0:
                val = int((pos_ms / dur_ms) * 1000)
                self.seek_slider.blockSignals(True)
                self.seek_slider.setValue(max(0, min(1000, val)))
                self.seek_slider.blockSignals(False)
        if dur_ms > 0:
            self.lbl_dur_time.setText(format_duration(dur_ms / 1000.0))

        if hasattr(self, "vinyl_overlay") and self.vinyl_overlay.isVisible():
            self.vinyl_overlay.sync_playback_position(pos_ms / 1000.0, self.engine.is_playing())
        self._broadcast_playback_session(force=False)

    def _on_seek_preview(self, value: int) -> None:
        if self._seeking_slider:
            dur_ms = self.engine.player.duration()
            if dur_ms <= 0 and self.engine.current_track:
                dur_ms = int(self.engine.current_track.duration * 1000)
            preview_sec = (value / 1000.0) * (dur_ms / 1000.0)
            self.lbl_pos_time.setText(format_duration(preview_sec))

    def _on_seek_released(self) -> None:
        self._seeking_slider = False
        dur_ms = self.engine.player.duration()
        if dur_ms <= 0 and self.engine.current_track:
            dur_ms = int(self.engine.current_track.duration * 1000)
        target_ms = int((self.seek_slider.value() / 1000.0) * dur_ms)
        self.engine.seek(target_ms)
        self._broadcast_playback_session(force=True)

    # ================================================================== #
    #         VINCULACIÓN MULTIDISPOSITIVO (CONTINUAR EN OTRO DISPOSITIVO)#
    # ================================================================== #

    def _on_remote_session_detected(self, session: dict) -> None:
        if self.engine.is_playing():
            if hasattr(self, "cross_device_banner"):
                self.cross_device_banner.hide()
            return
        self._pending_remote_session = session
        title = session.get("title", "Canción")
        artist = session.get("artist", "Artista")
        pos_sec = max(0.0, float(session.get("posSec", 0.0)))
        mins = int(pos_sec // 60)
        secs = int(pos_sec % 60)
        time_str = f"{mins}:{secs:02d}"
        dev_label = "Celular" if session.get("deviceType") == "android" else "Computadora"
        if hasattr(self, "lbl_cdb_info"):
            self.lbl_cdb_info.setText(f"{title} • {artist} ({time_str}) [{dev_label}]")
        if hasattr(self, "cross_device_banner"):
            self.cross_device_banner.show()

    def _resume_remote_session_track(self) -> None:
        if hasattr(self, "cross_device_banner"):
            self.cross_device_banner.hide()
        remote = getattr(self, "_pending_remote_session", None)
        if not remote:
            return

        title_target = clean_track_title(remote.get("title", "")).strip().lower()
        drive_id = str(remote.get("driveId") or "").strip()
        track_id = str(remote.get("trackId") or "").strip()

        # Buscar en tracks locales
        target_track = None
        for t in self.store.tracks.values():
            if drive_id and (getattr(t, "drive_id", "") == drive_id or drive_id in t.filepath):
                target_track = t
                break
            if track_id and t.id == track_id:
                target_track = t
                break
            if title_target and clean_track_title(t.title).strip().lower() == title_target:
                target_track = t
                break

        pos_sec = max(0.0, float(remote.get("posSec", 0.0)))
        if target_track:
            self.play_track(target_track, None)
            QTimer.singleShot(450, lambda: self.engine.seek(int(pos_sec * 1000)))
            self.statusBar().showMessage(f"✔ Continuando «{target_track.title}» desde {format_duration(pos_sec)}", 4000)
        elif drive_id:
            # Crear track virtual de streaming desde Google Drive
            v_track = TrackMetadata(
                id=f"gd_{drive_id}",
                filepath=f"gdrive://{drive_id}/{remote.get('title', 'Cancion')}.mp3",
                title=remote.get("title", "Canción en Nube"),
                artist=remote.get("artist", "Google Drive ☁️"),
                album=remote.get("album", "MaoMusic Stream"),
                genre="Cloud",
                year="",
                duration=float(remote.get("duration", 0.0)),
                cover_path="",
                dominant_color=(29, 185, 84),
            )
            self.store.tracks[v_track.id] = v_track
            self.play_track(v_track, None)
            QTimer.singleShot(600, lambda: self.engine.seek(int(pos_sec * 1000)))
            self.statusBar().showMessage(f"✔ Transmitiendo «{v_track.title}» desde {format_duration(pos_sec)}", 4000)

    def _broadcast_playback_session(self, force: bool = False) -> None:
        track = self.engine.current_track
        if not track:
            return
        now = time.time()
        if not force and (now - getattr(self, "_last_broadcast_session_time", 0.0) < 12.0):
            return
        self._last_broadcast_session_time = now

        pos_sec = max(0.0, self.engine.player.position() / 1000.0)
        dur_sec = max(0.0, self.engine.player.duration() / 1000.0)
        if dur_sec <= 0.0:
            dur_sec = float(track.duration)

        drive_id = ""
        if track.filepath.startswith("gdrive://"):
            drive_id = track.filepath[len("gdrive://"):].split("/")[0]
        elif track.id.startswith("gd_"):
            drive_id = track.id[3:]

        session_obj = {
            "deviceId": self.store.device_id,
            "deviceType": "desktop",
            "trackId": track.id,
            "driveId": drive_id,
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "cover": "",
            "src": track.filepath if track.filepath.startswith("http") else "",
            "posSec": pos_sec,
            "duration": dur_sec,
            "isPlaying": self.engine.is_playing(),
            "updatedAt": int(now * 1000),
            "updatedAtMs": int(now * 1000),
        }

        email = (self.store.user_account.get("email") or "global_playback_session").strip().lower()
        acc = dict(self.store.user_account or {})
        acc["email"] = email
        acc["username"] = acc.get("username") or "Usuario"
        acc["playback_session"] = session_obj
        acc["updatedAt"] = int(now * 1000)
        acc["updated_at"] = int(now * 1000)

        def _do_post():
            try:
                import requests
                from gdrive_sync import DEFAULT_DRIVE_SERVER_URL
                requests.post(
                    DEFAULT_DRIVE_SERVER_URL,
                    json={"action": "save_account", "email": email, "account": acc},
                    headers={"Content-Type": "text/plain;charset=utf-8"},
                    timeout=7,
                )
            except Exception:
                pass

        threading.Thread(target=_do_post, daemon=True, name="BroadcastPlaybackSession").start()

    def _check_remote_playback_session(self) -> None:
        def _do_check():
            try:
                import requests
                from gdrive_sync import DEFAULT_DRIVE_SERVER_URL
                r = requests.get(DEFAULT_DRIVE_SERVER_URL, timeout=8)
                if r.status_code not in (200, 302):
                    self.remote_session_dismissed.emit()
                    return
                data = r.json()
                accounts = data.get("accounts", {})
                email = (self.store.user_account.get("email") or "").strip().lower()

                session = None
                if email and accounts.get(email, {}).get("playback_session"):
                    session = accounts[email]["playback_session"]
                elif accounts.get("global_playback_session", {}).get("playback_session"):
                    session = accounts["global_playback_session"]["playback_session"]
                else:
                    for acc in accounts.values():
                        if isinstance(acc, dict) and acc.get("playback_session", {}).get("title"):
                            session = acc["playback_session"]
                            break

                if not session or not session.get("title"):
                    self.remote_session_dismissed.emit()
                    return

                now_ms = time.time() * 1000
                session_time = session.get("updatedAt") or session.get("updatedAtMs") or 0
                if not session_time or (now_ms - session_time > 12 * 3600 * 1000):
                    self.remote_session_dismissed.emit()
                    return

                if session.get("deviceId") == self.store.device_id or self.engine.is_playing():
                    self.remote_session_dismissed.emit()
                    return

                self.remote_session_detected.emit(session)
            except Exception:
                self.remote_session_dismissed.emit()

        threading.Thread(target=_do_check, daemon=True, name="CheckRemotePlaybackSession").start()

    def _on_volume_changed(self, value: int) -> None:
        self.store.volume = value
        self.engine.set_volume(value)
        self._muted = (value == 0)
        if not hasattr(self, "_volume_save_timer") or self._volume_save_timer is None:
            self._volume_save_timer = QTimer(self)
            self._volume_save_timer.setSingleShot(True)
            self._volume_save_timer.timeout.connect(self.store.save)
        self._volume_save_timer.start(400)
        self.btn_mute.setText("")
        icon_name = "volume_mute" if value == 0 else "volume_high"
        self.btn_mute.setIcon(icons.get_icon(icon_name, "#1ed760" if value > 0 else "#a6a6b4", 18))

    def toggle_mute(self) -> None:
        if not self._muted and self.vol_slider.value() > 0:
            self._pre_mute_volume = self.vol_slider.value()
            self._muted = True
            self.vol_slider.setValue(0)
        else:
            self._muted = False
            self.vol_slider.setValue(self._pre_mute_volume or 75)

    def toggle_shuffle(self) -> None:
        self.engine.shuffle = not self.engine.shuffle
        self.store.shuffle = self.engine.shuffle
        self.store.save()
        self._update_shuffle_ui()
        self._refresh_queue_ui()

    def _update_shuffle_ui(self) -> None:
        is_shuf = bool(self.engine.shuffle)
        self._refresh_button_active(self.btn_shuffle, is_shuf)
        self.btn_shuffle.setText("")
        self.btn_shuffle.setIcon(icons.get_icon("shuffle", "#1ed760" if is_shuf else "#a6a6b4", 18))
        if is_shuf:
            self.btn_shuffle.setToolTip("Modo aleatorio: ACTIVADO (clic para desactivar)")
            self.hero_banner.btn_shuffle_banner.setText(" Aleatorio: ACTIVADO")
            self.hero_banner.btn_shuffle_banner.setIcon(icons.get_icon("shuffle", "#000000", 16))
            self._refresh_button_active(self.hero_banner.btn_shuffle_banner, True)
        else:
            self.btn_shuffle.setToolTip("Modo aleatorio: DESACTIVADO (clic para activar)")
            self.hero_banner.btn_shuffle_banner.setText(" Aleatorio")
            self.hero_banner.btn_shuffle_banner.setIcon(icons.get_icon("shuffle", "#e4e4ec", 16))
            self._refresh_button_active(self.hero_banner.btn_shuffle_banner, False)

    def cycle_repeat_mode(self) -> None:
        modes = ["off", "all", "one"]
        idx = (modes.index(self.engine.repeat_mode) + 1) % len(modes) if self.engine.repeat_mode in modes else 0
        self.engine.repeat_mode = modes[idx]
        self.store.repeat_mode = self.engine.repeat_mode
        self.store.save()
        is_rep = self.engine.repeat_mode != "off"
        rep_icon = "repeat_one" if self.engine.repeat_mode == "one" else "repeat"
        self.btn_repeat.setText("")
        self.btn_repeat.setIcon(icons.get_icon(rep_icon, "#1ed760" if is_rep else "#a6a6b4", 18))
        self._refresh_button_active(self.btn_repeat, is_rep)

    def _repeat_icon_text(self) -> str:
        if self.engine.repeat_mode == "one":
            return "1"
        return ""

    def toggle_current_track_like(self) -> None:
        if not self.engine.current_track:
            return
        tid = self.engine.current_track.id
        self.store.toggle_like(tid)
        self._sync_playlists_to_cloud()
        self._update_like_button_ui(tid)
        self._refresh_track_table()

    def _refresh_queue_ui(self) -> None:
        self.queue_list.clear()
        upcoming = self.engine.get_upcoming_tracks(limit=18)
        user_count = len(self.engine.user_queue)
        self.lbl_queue_count.setText(
            f"En tu cola ({user_count})" if user_count > 0 else "Siguientes en reproducción"
        )
        self.btn_clear_queue.setVisible(user_count > 0)

        for idx, (source_type, track) in enumerate(upcoming):
            badge = "COLA • " if source_type == "user" else ""
            item = QListWidgetItem(f"{badge}{track.title}\n{track.artist}")
            item.setIcon(QIcon(make_rounded_pixmap(track.cover_path, 32, radius=4)))
            item.setData(Qt.ItemDataRole.UserRole, (source_type, idx, track.id))
            if source_type == "user":
                item.setForeground(QColor("#1DB954"))
            self.queue_list.addItem(item)

    def _on_queue_item_double_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        source_type, idx, tid = data
        if source_type == "user" and idx < len(self.engine.user_queue):
            chosen = self.engine.user_queue.pop(idx)
            self._play_track_or_stream_from_cloud(chosen, None)
        elif tid in self.store.tracks:
            self._play_track_or_stream_from_cloud(self.store.tracks[tid], None)

    def _switch_right_tab(self, index: int) -> None:
        self.right_stack.setCurrentIndex(index)
        self._refresh_button_active(self.btn_tab_queue, index == 0)
        self._refresh_button_active(self.btn_tab_eq, index == 1)

    def _open_eq_drawer(self) -> None:
        self.right_card.setVisible(True)
        self._switch_right_tab(1)

    def _retire_worker(self, attr: str) -> None:
        """Detiene con seguridad un QThread previo antes de reemplazarlo (evita 'Destroyed while thread is still running')."""
        old = getattr(self, attr, None)
        if old is None:
            return
        try:
            if old.isRunning():
                old.requestInterruption()
                old.quit()
                if not old.wait(1500):
                    self._retired_workers.append(old)
                    old.finished.connect(lambda o=old: self._retired_workers.remove(o) if o in self._retired_workers else None)
        except RuntimeError:
            pass

    def _stop_all_workers(self) -> None:
        if hasattr(self, "cloud_service"):
            self.cloud_service.shutdown()
        names = (
            "_stream_worker",
            "_gdrive_worker",
            "_upload_worker",
            "_cloud_sync_worker",
            "_playlist_batch_worker",
            "_updater_worker",
            "_git_update_worker",
        )
        threads = [getattr(self, n, None) for n in names] + list(getattr(self, "_retired_workers", []))
        for th in threads:
            try:
                if th is not None and th.isRunning():
                    th.requestInterruption()
                    th.quit()
                    th.wait(2000)
            except RuntimeError:
                pass

    def closeEvent(self, event) -> None:
        if hasattr(self, "download_queue") and hasattr(self, "queue_runner"):
            items = self.download_queue.list()
            running = [it for it in items if it.status == "running"]
            if running:
                reply = QMessageBox.question(
                    self,
                    "Descargas en curso",
                    f"Hay {len(running)} descarga(s) en progreso actualmente.\n\n¿Deseas salir de la aplicación? "
                    "Las descargas en curso se pausarán de forma segura y volverán a estar pendientes la próxima vez.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return

            self.queue_runner.stop()
            if self.download_queue_panel:
                self.download_queue_panel.close()
        self._stop_all_workers()
        event.accept()

    def _on_eq_changed(self, enabled: bool, preset_name: str, gains: list[float]) -> None:
        self.store.eq_enabled = enabled
        self.store.eq_preset = preset_name
        self.store.eq_gains = list(gains)
        self.store.save()
        self.engine.set_equalizer(enabled, gains)

    def _on_visualizer_mode_changed(self, mode: str) -> None:
        self.store.visualizer_mode = mode
        self.store.save()

    @staticmethod
    def _refresh_button_active(btn: QPushButton, active: bool) -> None:
        btn.setProperty("active", "true" if active else "false")
        btn.style().unpolish(btn)
        btn.style().polish(btn)


_FAULTHANDLER_FILE = None


def setup_crash_logging(base_dir: Path | str) -> Path:
    """Configura la red de seguridad anti-cierre: log rotativo, hooks de excepción y faulthandler."""
    global _FAULTHANDLER_FILE
    base_path = Path(base_dir).resolve()
    data_dir = base_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    log_path = data_dir / "maomusic.log"
    root_logger = logging.getLogger()
    if root_logger.level == logging.NOTSET or root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)

    for h in list(root_logger.handlers):
        if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path.resolve()):
            root_logger.removeHandler(h)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=512 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    root_logger.addHandler(handler)

    def _handle_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("MaoMusic").critical(
            "Excepción no capturada en hilo principal:",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        app = QApplication.instance()
        if app:
            try:
                QMessageBox.critical(
                    None,
                    "Error inesperado en MaoMusic",
                    f"Ocurrió un error inesperado:\n\n{exc_value}\n\nLos detalles se guardaron en:\n{log_path}",
                )
            except Exception:
                pass

    def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, KeyboardInterrupt):
            sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)
            return
        thread_name = getattr(args.thread, "name", "SegundoPlano")
        logging.getLogger("MaoMusic").critical(
            f"Excepción no capturada en hilo '{thread_name}':",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        app = QApplication.instance()
        if app:
            msg_text = (
                f"Ocurrió un error en segundo plano ({thread_name}):\n\n"
                f"{args.exc_value}\n\nLos detalles se guardaron en:\n{log_path}"
            )
            try:
                QTimer.singleShot(
                    0,
                    lambda: QMessageBox.critical(
                        None,
                        "Error en hilo de fondo — MaoMusic",
                        msg_text,
                    ),
                )
            except Exception:
                pass

    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception

    fault_log_path = data_dir / "maomusic_fault.log"
    try:
        if _FAULTHANDLER_FILE and not _FAULTHANDLER_FILE.closed:
            _FAULTHANDLER_FILE.close()
        _FAULTHANDLER_FILE = open(fault_log_path, "a", encoding="utf-8")
        faulthandler.enable(file=_FAULTHANDLER_FILE)
    except Exception as e:
        logging.getLogger("MaoMusic").warning(f"No se pudo habilitar faulthandler: {e}")

    def _qt_message_handler(mode: QtMsgType, context, message: str) -> None:
        qt_logger = logging.getLogger("Qt")
        if mode == QtMsgType.QtInfoMsg:
            qt_logger.info(message)
        elif mode == QtMsgType.QtWarningMsg:
            qt_logger.warning(message)
        elif mode == QtMsgType.QtCriticalMsg:
            qt_logger.error(message)
        elif mode == QtMsgType.QtFatalMsg:
            qt_logger.critical(message)
        else:
            qt_logger.debug(message)

    try:
        qInstallMessageHandler(_qt_message_handler)
    except Exception as e:
        logging.getLogger("MaoMusic").warning(f"No se pudo instalar qInstallMessageHandler: {e}")

    return log_path


def resolve_app_data_dir() -> Path:
    """Devuelve la carpeta de datos escribible tanto en modo script como en ejecutable compilado."""
    if getattr(sys, "frozen", False):
        exe_parent = Path(sys.executable).resolve().parent
        # Si existe demo_tracks junto al ejecutable o en el padre, usarlo si es escribible
        if os.access(exe_parent, os.W_OK):
            return exe_parent
        user_dir = Path.home() / ".local" / "share" / "SpotiLinux"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir
    return Path(__file__).resolve().parent


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("MaoMusic")
    app.setStyleSheet(SPOTIFY_QSS)

    base_dir = resolve_app_data_dir()
    setup_crash_logging(base_dir)
    window = SpotiLinuxWindow(base_dir)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
