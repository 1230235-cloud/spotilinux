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

import json
import os
import random
import sys
import time
import uuid
from pathlib import Path
from typing import Optional
import urllib.request

from PyQt6.QtCore import Qt, QSize, QRectF, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
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
    hash_password,
    merge_account_records,
    sync_cloud_playlists_with_drive,
    upload_cloud_database,
)
from generate_demos import ensure_demo_tracks
from maomao_widget import MaomaoDancerWidget
from metadata import SUPPORTED_EXTENSIONS, TrackMetadata, format_duration
from storage import LibraryStore, Playlist
from styles import SPOTIFY_QSS
from visualizer_widget import AudioVisualizerWidget, EqualizerPanel


def make_rounded_pixmap(path: str, size: int, radius: int = 6) -> QPixmap:
    """Carga una imagen y la recorta con esquinas redondeadas nítidas."""
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

        self.btn_play_banner = QPushButton("▶")
        self.btn_play_banner.setObjectName("SpotifyGreenPlayBig")
        self.btn_play_banner.setFixedSize(50, 50)
        self.btn_play_banner.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_banner.setToolTip("Reproducir vista actual")

        self.btn_shuffle_banner = QPushButton("🔀 Aleatorio")
        self.btn_shuffle_banner.setObjectName("ActionChipButton")
        self.btn_shuffle_banner.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_cloud_sync_banner = QPushButton("☁️ Conectar / Sincronizar Google Drive")
        self.btn_cloud_sync_banner.setObjectName("ActionChipButton")
        self.btn_cloud_sync_banner.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_add_tracks = QPushButton("＋ Agregar canciones")
        self.btn_add_tracks.setObjectName("ActionChipButton")
        self.btn_add_tracks.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_tracks.setVisible(False)

        self.btn_edit_playlist = QPushButton("✎ Editar")
        self.btn_edit_playlist.setObjectName("ActionChipButton")
        self.btn_edit_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_playlist.setVisible(False)

        self.btn_delete_playlist = QPushButton("🗑 Eliminar")
        self.btn_delete_playlist.setObjectName("ActionChipButton")
        self.btn_delete_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete_playlist.setVisible(False)

        self.btn_download_playlist = QPushButton("⬇️ Descargar Playlist")
        self.btn_download_playlist.setObjectName("ActionChipButton")
        self.btn_download_playlist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_download_playlist.setToolTip("Descarga las canciones de Google Drive de esta playlist")
        self.btn_download_playlist.setVisible(False)

        actions_row.addWidget(self.btn_play_banner)
        actions_row.addWidget(self.btn_shuffle_banner)
        actions_row.addWidget(self.btn_cloud_sync_banner)
        actions_row.addWidget(self.btn_add_tracks)
        actions_row.addWidget(self.btn_download_playlist)
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

        self.btn_play_mini = QPushButton("▶")
        self.btn_play_mini.setFixedSize(30, 30)
        self.btn_play_mini.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_mini.setStyleSheet(
            "background-color: #1DB954; color: #000000; border-radius: 15px; font-weight: bold; font-size: 12px; border: none;"
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
            verify_duplicates=self.chk_dedup.isChecked(),
        )
        links = [
            line.strip()
            for line in self.txt_direct_links.toPlainText().splitlines()
            if line.strip()
        ]
        return cfg, links, self.chk_preload.isChecked()


class AccountDialog(QDialog):
    """
    Modal para Crear Cuenta (Usuario, Correo, Contraseña) o Iniciar Sesión.
    Guarda y sincroniza la base de datos de cuentas, playlists y canciones de Drive
    en https://drive.google.com/drive/folders/1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT (spotilinux_database.json).
    """

    def __init__(self, parent: QWidget, store: LibraryStore) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("👤 Cuenta SpotiLinux — Vincular Datos y Playlists en Google Drive")
        self.setFixedSize(520, 480)
        self.mode: str = "register"  # "register" | "login"
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
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)

        title = QLabel("👤 Cuenta SpotiLinux Multi-Dispositivo")
        title.setStyleSheet("font-size: 17px; font-weight: 900; color: #1ED760;")
        lay.addWidget(title)

        desc = QLabel(
            f"Tus playlists y las canciones del Drive se guardan vinculadas a tu cuenta en:\n"
            f"📁 Drive ID: {DEFAULT_ROOT_DRIVE_FOLDER_ID} ({CLOUD_DB_FILENAME} + carpeta «Musica»)"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #A6A6B4; font-size: 11px; font-weight: normal;")
        lay.addWidget(desc)

        if self.store.user_account.get("username"):
            curr_u = self.store.user_account.get("username")
            curr_e = self.store.user_account.get("email")
            active_box = QFrame()
            active_box.setStyleSheet(
                "background-color: rgba(29, 185, 84, 0.12); border: 1px solid rgba(29, 185, 84, 0.4); border-radius: 8px;"
            )
            ab_lay = QHBoxLayout(active_box)
            ab_lay.setContentsMargins(10, 6, 10, 6)
            lbl_curr = QLabel(f"✔ Sesión activa: <b>{curr_u}</b> ({curr_e})")
            lbl_curr.setStyleSheet("color: #1ED760; font-size: 12px; border: none; background: transparent;")
            btn_logout = QPushButton("Cerrar sesión")
            btn_logout.setObjectName("DangerBtn")
            btn_logout.clicked.connect(self._do_logout)
            ab_lay.addWidget(lbl_curr, stretch=1)
            ab_lay.addWidget(btn_logout)
            lay.addWidget(active_box)

        tab_row = QHBoxLayout()
        self.btn_tab_register = QPushButton("✨ Crear Cuenta Nueva")
        self.btn_tab_register.setObjectName("TabBtn")
        self.btn_tab_register.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_register.clicked.connect(lambda: self._set_mode("register"))

        self.btn_tab_login = QPushButton("🔑 Iniciar Sesión")
        self.btn_tab_login.setObjectName("TabBtn")
        self.btn_tab_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_login.clicked.connect(lambda: self._set_mode("login"))

        tab_row.addWidget(self.btn_tab_register)
        tab_row.addWidget(self.btn_tab_login)
        lay.addLayout(tab_row)

        form = QFormLayout()
        form.setSpacing(10)

        self.lbl_user = QLabel("Usuario:")
        self.inp_username = QLineEdit(self.store.user_account.get("username", ""))
        self.inp_username.setPlaceholderText("Ej. jinsoon005")

        self.lbl_email = QLabel("Correo electrónico:")
        self.inp_email = QLineEdit(self.store.user_account.get("email", ""))
        self.inp_email.setPlaceholderText("ejemplo@correo.com")

        self.lbl_pass = QLabel("Contraseña:")
        self.inp_password = QLineEdit()
        self.inp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.inp_password.setPlaceholderText("Contraseña de tu cuenta SpotiLinux")

        self.inp_token = QLineEdit(self.store.gdrive_config.access_token)
        self.inp_token.setPlaceholderText("Opcional: Token OAuth2 de Google Drive para escritura directa en la nube")

        form.addRow(self.lbl_user, self.inp_username)
        form.addRow(self.lbl_email, self.inp_email)
        form.addRow(self.lbl_pass, self.inp_password)
        form.addRow("Token Drive (Opcional):", self.inp_token)
        lay.addLayout(form)

        self.lbl_feedback = QLabel("Completa usuario, correo y contraseña para vincular tus datos.")
        self.lbl_feedback.setWordWrap(True)
        self.lbl_feedback.setStyleSheet("color: #1ED760; font-size: 11px;")
        lay.addWidget(self.lbl_feedback)

        lay.addStretch()

        bottom_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("TabBtn")
        btn_cancel.clicked.connect(self.reject)

        self.btn_submit = QPushButton("✨ Crear Cuenta y Vincular Datos")
        self.btn_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_submit.clicked.connect(self._on_submit)

        bottom_row.addStretch()
        bottom_row.addWidget(btn_cancel)
        bottom_row.addWidget(self.btn_submit)
        lay.addLayout(bottom_row)

        self._set_mode("register")

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        for btn, active in ((self.btn_tab_register, mode == "register"), (self.btn_tab_login, mode == "login")):
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if mode == "register":
            self.lbl_user.setText("Usuario:")
            self.inp_username.setPlaceholderText("Nombre de usuario (ej. maomao_fan)")
            self.lbl_email.setVisible(True)
            self.inp_email.setVisible(True)
            self.btn_submit.setText("✨ Crear Cuenta y Guardar en Drive")
            self.lbl_feedback.setText("Se creará la cuenta con tus playlists y canciones del Drive.")
        else:
            self.lbl_user.setText("Usuario o Correo:")
            self.inp_username.setPlaceholderText("Escribe tu usuario o tu correo electrónico")
            self.lbl_email.setVisible(False)
            self.inp_email.setVisible(False)
            self.btn_submit.setText("🔑 Iniciar Sesión y Recuperar Playlists")
            self.lbl_feedback.setText("Ingresa tu usuario/correo y contraseña para cargar tus playlists y canciones.")

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

    def _on_submit(self) -> None:
        tok = self.inp_token.text().strip()
        if tok:
            self.store.gdrive_config.access_token = tok
            self.store.save()

        username = self.inp_username.text().strip()
        email = self.inp_email.text().strip().lower()
        password = self.inp_password.text()

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
        else:
            if not username or not password:
                QMessageBox.warning(
                    self,
                    "Campos requeridos",
                    "Por favor ingresa tu Usuario (o Correo) y tu Contraseña para iniciar sesión.",
                )
                return

        self.lbl_feedback.setText("☁️ Consultando base de datos en Google Drive...")
        QApplication.processEvents()

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
                    "Ya existe una cuenta con ese correo y una contraseña distinta. Usa «Iniciar Sesión».",
                )
                return

            new_record = {
                "username": username,
                "email": email,
                "password_hash": pw_hash,
                "updated_at": time.time(),
                "playlists": {pid: pl.to_dict() for pid, pl in self.store.playlists.items()},
                "liked_ids": list(self.store.liked_ids),
                "drive_tracks": self._collect_current_drive_tracks(),
            }
            merged_record = merge_account_records(existing or {}, new_record)
            accounts[acc_key] = merged_record
            upload_cloud_database(self.store.gdrive_config, self.store.accounts_db_path, db, existing_file_id)
            self.authenticated_account = merged_record
            self.accept()
        else:
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
                    f"No se encontró ninguna cuenta para «{username}».\nPuedes crearla en la pestaña «Crear Cuenta Nueva».",
                )
                return

            if found_acc.get("password_hash") != pw_hash:
                QMessageBox.warning(self, "Contraseña incorrecta", "La contraseña ingresada no coincide con la cuenta.")
                return

            # Fusionar datos locales con los de la cuenta recuperada y guardar
            current_record = {
                "username": found_acc.get("username", username),
                "email": found_acc.get("email", found_key),
                "password_hash": pw_hash,
                "updated_at": time.time(),
                "playlists": {pid: pl.to_dict() for pid, pl in self.store.playlists.items()},
                "liked_ids": list(self.store.liked_ids),
                "drive_tracks": self._collect_current_drive_tracks(),
            }
            merged_record = merge_account_records(found_acc, current_record)
            accounts[found_key] = merged_record
            upload_cloud_database(self.store.gdrive_config, self.store.accounts_db_path, db, existing_file_id)
            self.authenticated_account = merged_record
            self.accept()


class GitHubDesktopUpdateWorker(QThread):
    update_available = pyqtSignal(dict)

    def run(self) -> None:
        try:
            url = f"https://raw.githubusercontent.com/1230235-cloud/spotilinux/main/version.json?_t={int(time.time())}"
            req = urllib.request.Request(url, headers={"User-Agent": "SpotiLinux-Desktop"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    remote_code = data.get("versionCode", 0)
                    local_code = 9  # v2.6.1
                    if remote_code > local_code:
                        self.update_available.emit(data)
        except Exception:
            pass


class SpotiLinuxWindow(QMainWindow):
    HOME_PREVIEW_LIMIT = 4  # canciones visibles en la vista previa reducida del Inicio

    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir = base_dir
        self.setWindowTitle("SpotiLinux — Music Player & Cloud Drive (Maomao Edition 🌿)")
        self.resize(1380, 850)
        self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)

        # 1. Asegurar pistas de demostración y cargar biblioteca
        demo_dir = self.base_dir / "demo_tracks"
        ensure_demo_tracks(demo_dir)

        self.store = LibraryStore(self.base_dir)
        for folder in list(self.store.watched_folders):
            self.store.scan_folder(Path(folder))
        if not self.store.tracks:
            self.store.scan_folder(demo_dir)

        # 2. Inicializar motor de audio y ecualizador
        self.engine = AudioEngine(self.base_dir / "data" / "eq_cache")
        self.engine.shuffle = self.store.shuffle
        self.engine.repeat_mode = self.store.repeat_mode
        self.engine.set_volume(self.store.volume)
        self.engine.eq_enabled = self.store.eq_enabled
        self.engine.eq_gains = list(self.store.eq_gains)

        self._gdrive_worker: Optional[GDriveSyncWorker] = None
        self._stream_worker: Optional[GDriveStreamDownloadWorker] = None
        self._cloud_sync_worker: Optional[GDriveCloudPlaylistSyncWorker] = None
        self._upload_worker: Optional[GDriveUploadWorker] = None

        # Estado de navegación actual
        self.current_view: str = "home"  # "home" | "library" | "gdrive" | "liked" | "recent" | "playlist:<id>"
        self.active_genre_filter: str = "Todo"
        self.displayed_tracks: list[TrackMetadata] = []
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
        _pl_name, initial_ctx = self._get_home_preview_tracks(self.home_preview_key)
        if not initial_ctx:
            initial_ctx = list(self.displayed_tracks)
        if initial_ctx:
            self.engine.context_tracks = list(initial_ctx)
            self._update_now_playing_ui(initial_ctx[0])

        # Solicitar acceso a Google Drive o rastreo automático desde el inicio
        QTimer.singleShot(450, self._check_first_run_welcome)
        QTimer.singleShot(2500, self._check_github_updates)

    def _check_first_run_welcome(self) -> None:
        # Sincronizar automáticamente la carpeta compartida 1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT / Musica al iniciar
        if self.store.gdrive_config.access_token and not self.store.google_user.get("name"):
            u = fetch_google_user_profile(self.store.gdrive_config.access_token)
            if u:
                self.store.google_user = u
                self.store.save()
        if self.store.gdrive_config.access_token or self.store.user_account.get("email"):
            self._sync_playlists_to_cloud()
        self.start_gdrive_sync(silent_view_keep=True)

        if self.store.welcome_shown:
            return
        self.store.welcome_shown = True
        self.store.save()

        msg = QMessageBox(self)
        msg.setWindowTitle("☁️ Bienvenido a SpotiLinux")
        msg.setText(
            "¿Deseas crear/iniciar sesión en tu Cuenta SpotiLinux (vincula tus Playlists y las canciones de la "
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
        self._updater_worker = GitHubDesktopUpdateWorker(self)
        self._updater_worker.update_available.connect(self._on_update_available)
        self._updater_worker.start()

    def _on_update_available(self, info: dict) -> None:
        v_name = info.get("versionName", "Nueva")
        changelog_items = info.get("changelog", [])
        cl_text = "\n".join(f"• {x}" for x in changelog_items) if changelog_items else "Mejoras de rendimiento y corrección de errores."

        msg = QMessageBox(self)
        msg.setWindowTitle("🚀 Actualización de SpotiLinux Disponible")
        msg.setText(f"<b>Se ha publicado una nueva versión oficial de SpotiLinux (v{v_name}) en GitHub.</b>")
        msg.setInformativeText(f"Novedades:\n{cl_text}\n\n¿Deseas actualizar el código automáticamente con git pull?")
        btn_update = msg.addButton("Actualizar ahora (git pull)", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Más tarde", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        if msg.clickedButton() == btn_update:
            try:
                import subprocess
                res = subprocess.run(["git", "pull", "origin", "main"], cwd=str(self.base_dir), capture_output=True, text=True, timeout=30)
                if res.returncode == 0:
                    QMessageBox.information(self, "Actualización Completada", "✔ SpotiLinux se actualizó exitosamente desde GitHub.\nPor favor reinicia la aplicación para disfrutar de las novedades.")
                else:
                    QMessageBox.warning(self, "Aviso de Git", f"No se pudo completar el pull automáticamente:\n{res.stderr or res.stdout}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Fallo al ejecutar actualización: {e}")

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

        panels_row = QHBoxLayout()
        panels_row.setSpacing(8)

        self._build_left_sidebar(panels_row)
        self._build_center_panel(panels_row)
        self._build_right_panel(panels_row)

        root_layout.addLayout(panels_row, stretch=1)
        self._build_bottom_player_bar(root_layout)

    def _build_left_sidebar(self, parent_layout: QHBoxLayout) -> None:
        left_col = QVBoxLayout()
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
        lbl_logo_text = QLabel("SpotiLinux")
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

        self.btn_nav_home = QPushButton("🏠   Inicio")
        self.btn_nav_home.setObjectName("NavButton")
        self.btn_nav_home.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_library = QPushButton("💿   Tu Biblioteca")
        self.btn_nav_library.setObjectName("NavButton")
        self.btn_nav_library.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_gdrive = QPushButton("☁️   Mi Google Drive")
        self.btn_nav_gdrive.setObjectName("NavButton")
        self.btn_nav_gdrive.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_liked = QPushButton("💜   Canciones que te gustan")
        self.btn_nav_liked.setObjectName("NavButton")
        self.btn_nav_liked.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_recent = QPushButton("🕘   Escuchado recientemente")
        self.btn_nav_recent.setObjectName("NavButton")
        self.btn_nav_recent.setCursor(Qt.CursorShape.PointingHandCursor)

        for b in (self.btn_nav_home, self.btn_nav_library, self.btn_nav_gdrive, self.btn_nav_liked, self.btn_nav_recent):
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
        self.btn_new_playlist = QPushButton("＋ Nueva")
        self.btn_new_playlist.setObjectName("GhostSmallButton")
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
        import_box.setSpacing(6)

        self.btn_account_sidebar = QPushButton("👤  Crear / Iniciar Cuenta...")
        self.btn_account_sidebar.setObjectName("ActionChipButton")
        self.btn_account_sidebar.setProperty("active", "true")
        self.btn_account_sidebar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_account_sidebar.setToolTip("Crea tu cuenta con Usuario, Correo y Contraseña o inicia sesión para vincular datos en Drive")

        self.btn_connect_gdrive = QPushButton("☁️  Carpeta Drive (Musica)...")
        self.btn_connect_gdrive.setObjectName("ActionChipButton")
        self.btn_connect_gdrive.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_upload_gdrive = QPushButton("⬆️  Subir a Drive (Musica)...")
        self.btn_upload_gdrive.setObjectName("ActionChipButton")
        self.btn_upload_gdrive.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_upload_gdrive.setToolTip("Sube canciones MP3/FLAC/WAV de tu PC a la carpeta Musica del Drive compartido")

        self.btn_auto_scan_pc = QPushButton("🔍  Auto-Rastrear Música PC")
        self.btn_auto_scan_pc.setObjectName("ActionChipButton")
        self.btn_auto_scan_pc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto_scan_pc.setToolTip("Rastrea ~/Music, ~/Música y ~/Downloads y guarda las canciones en music_library/")

        self.btn_import_folder = QPushButton("📂  Escanear Carpeta...")
        self.btn_import_folder.setObjectName("ActionChipButton")
        self.btn_import_folder.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_import_files = QPushButton("🎵  Importar Archivos...")
        self.btn_import_files.setObjectName("ActionChipButton")
        self.btn_import_files.setCursor(Qt.CursorShape.PointingHandCursor)

        import_box.addWidget(self.btn_account_sidebar)
        import_box.addWidget(self.btn_connect_gdrive)
        import_box.addWidget(self.btn_upload_gdrive)
        import_box.addWidget(self.btn_auto_scan_pc)
        import_box.addWidget(self.btn_import_folder)
        import_box.addWidget(self.btn_import_files)
        lib_lay.addLayout(import_box)

        left_col.addWidget(lib_card, stretch=1)
        parent_layout.addLayout(left_col)

    def _build_center_panel(self, parent_layout: QHBoxLayout) -> None:
        center_card = QFrame()
        center_card.setObjectName("MainCard")
        center_lay = QVBoxLayout(center_card)
        center_lay.setContentsMargins(16, 12, 16, 12)
        center_lay.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchInput")
        self.search_input.setPlaceholderText("🔍 ¿Qué quieres reproducir? (título, artista, álbum...)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(210)
        top_bar.addWidget(self.search_input, stretch=1)

        self.genre_buttons: dict[str, QPushButton] = {}
        for g_label in ("Todo", "Cloud ☁️", "Synthwave", "Lo-Fi", "House", "Electro"):
            btn = QPushButton(g_label)
            btn.setObjectName("ActionChipButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("active", "true" if g_label == "Todo" else "false")
            btn.clicked.connect(lambda _checked, g=g_label: self.set_genre_filter(g))
            self.genre_buttons[g_label] = btn
            top_bar.addWidget(btn)

        center_lay.addLayout(top_bar)

        self.hero_banner = HeroBannerWidget()
        center_lay.addWidget(self.hero_banner)

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

        self.track_table = QTableWidget(0, 6)
        self.track_table.setObjectName("TrackTable")
        self.track_table.setHorizontalHeaderLabels(["#", "TÍTULO", "ÁLBUM / ORIGEN", "GÉNERO", "♥", "⏱"])
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.track_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.track_table.setShowGrid(False)
        self.track_table.setIconSize(QSize(40, 40))
        self.track_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        header = self.track_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(0, 42)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.track_table.setColumnWidth(2, 185)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.track_table.setColumnWidth(3, 120)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(4, 46)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.track_table.setColumnWidth(5, 64)

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
        self.lbl_right_artist = QLabel("SpotiLinux Audio Engine")
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
        maomao_header.addWidget(lbl_mm)
        maomao_header.addStretch()
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

        self.btn_bar_like = QPushButton("♡")
        self.btn_bar_like.setObjectName("TransportIconBtn")
        self.btn_bar_like.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bar_like.setToolTip("Guardar en Canciones que te gustan (Ctrl+L)")
        left_box.addWidget(self.btn_bar_like)
        left_box.addStretch()

        bar_lay.addLayout(left_box, stretch=3)

        center_col = QVBoxLayout()
        center_col.setSpacing(6)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(14)
        btns_row.addStretch()

        self.btn_shuffle = QPushButton("🔀")
        self.btn_shuffle.setObjectName("TransportIconBtn")
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shuffle.setToolTip("Modo aleatorio (Shuffle)")
        self._refresh_button_active(self.btn_shuffle, self.engine.shuffle)

        self.btn_prev = QPushButton("⏮")
        self.btn_prev.setObjectName("TransportIconBtn")
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.setToolTip("Anterior (Ctrl+Izquierda)")

        self.btn_play_pause = QPushButton("▶")
        self.btn_play_pause.setObjectName("TransportPlayButton")
        self.btn_play_pause.setFixedSize(36, 36)
        self.btn_play_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play_pause.setToolTip("Reproducir / Pausar (Espacio)")

        self.btn_next = QPushButton("⏭")
        self.btn_next.setObjectName("TransportIconBtn")
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setToolTip("Siguiente (Ctrl+Derecha)")

        self.btn_repeat = QPushButton(self._repeat_icon_text())
        self.btn_repeat.setObjectName("TransportIconBtn")
        self.btn_repeat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_repeat.setToolTip("Modo de repetición (Desactivado / Todo / Una canción)")
        self._refresh_button_active(self.btn_repeat, self.engine.repeat_mode != "off")

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

        self.btn_quick_cloud = QPushButton("☁️ Drive")
        self.btn_quick_cloud.setObjectName("GhostSmallButton")
        self.btn_quick_cloud.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_cloud.setToolTip("Configurar o sincronizar Google Drive")

        self.btn_quick_eq = QPushButton("🎚 EQ")
        self.btn_quick_eq.setObjectName("GhostSmallButton")
        self.btn_quick_eq.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_eq.setToolTip("Abrir Ecualizador de 5 bandas")

        self.btn_toggle_right = QPushButton("◫ Panel")
        self.btn_toggle_right.setObjectName("GhostSmallButton")
        self.btn_toggle_right.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_right.setToolTip("Mostrar / Ocultar panel derecho (Maomao, Espectro y Cola)")

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setObjectName("TransportIconBtn")
        self.btn_mute.setCursor(Qt.CursorShape.PointingHandCursor)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(self.store.volume)
        self.vol_slider.setFixedWidth(92)
        self.vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)

        right_box.addWidget(self.btn_quick_cloud)
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
        self.btn_nav_gdrive.clicked.connect(lambda: self.switch_view("gdrive"))
        self.btn_nav_liked.clicked.connect(lambda: self.switch_view("liked"))
        self.btn_nav_recent.clicked.connect(lambda: self.switch_view("recent"))

        self.btn_new_playlist.clicked.connect(self.create_playlist_dialog)
        self.playlist_list.itemClicked.connect(self._on_playlist_item_clicked)
        self.playlist_list.customContextMenuRequested.connect(self._on_playlist_context_menu)

        self.btn_account_sidebar.clicked.connect(self.open_account_dialog)
        self.btn_connect_gdrive.clicked.connect(self.open_google_drive_dialog)
        self.btn_upload_gdrive.clicked.connect(self.upload_files_to_drive_dialog)
        self.btn_quick_cloud.clicked.connect(self.open_google_drive_dialog)
        self.hero_banner.btn_cloud_sync_banner.clicked.connect(self.open_google_drive_dialog)

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

    def _on_beat_info(self, bpm: float, beat_pos: float, conf: float, onset: float) -> None:
        self.maomao_widget.set_beat(bpm, beat_pos, conf, onset)
        self.visualizer.set_beat(bpm, beat_pos, conf, onset)

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

    # ================================================================== #
    #                  GOOGLE DRIVE CLOUD STREAMING                      #
    # ================================================================== #

    def open_account_dialog(self) -> None:
        dlg = AccountDialog(self, self.store)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.logged_out:
                self.switch_view(self.current_view)
                QMessageBox.information(self, "Sesión cerrada", "Se cerró la sesión de tu cuenta SpotiLinux.")
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
                    "👤 Cuenta SpotiLinux Vinculada",
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
        self._upload_worker = GDriveUploadWorker(
            config=self.store.gdrive_config,
            file_paths=file_paths,
            covers_dir=self.store.covers_dir,
            verify_duplicates=self.store.gdrive_config.verify_duplicates,
        )
        self._upload_worker.upload_progress.connect(lambda msg: self.hero_banner.lbl_subtitle.setText(msg))
        self._upload_worker.upload_finished.connect(self._on_upload_to_drive_finished)
        self._upload_worker.start()

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
        target_cache = self.store.music_library_dir if download_immediately else self.store.gdrive_cache_dir
        self._gdrive_worker = GDriveSyncWorker(
            config=self.store.gdrive_config,
            cache_dir=target_cache,
            covers_dir=self.store.covers_dir,
            direct_links=direct_links,
            download_immediately=download_immediately,
            existing_local_dirs=[self.store.music_library_dir, self.store.gdrive_cache_dir],
        )
        self._gdrive_worker.sync_progress.connect(lambda msg: self.hero_banner.lbl_subtitle.setText(f"☁️ {msg}"))
        self._gdrive_worker.sync_finished.connect(self._on_gdrive_sync_finished)
        self._gdrive_worker.start()

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
        """Si la pista es de Google Drive (gdrive://) y aún no está en caché local, la transmite al vuelo."""
        if track.filepath.startswith("gdrive://"):
            self.lbl_bar_title.setText(f"☁️ Cargando: {track.title}...")
            self.lbl_bar_artist.setText("Transmitiendo desde la carpeta «Musica» de Google Drive...")
            if context_list is not None:
                self.engine.context_tracks = list(context_list)
            self._stream_worker = GDriveStreamDownloadWorker(
                track=track,
                config=self.store.gdrive_config,
                cache_dir=self.store.gdrive_cache_dir,
                covers_dir=self.store.covers_dir,
                existing_local_dirs=[self.store.music_library_dir, self.store.gdrive_cache_dir],
                verify_duplicates=self.store.gdrive_config.verify_duplicates,
            )
            self._stream_worker.stream_ready.connect(self._on_cloud_stream_ready)
            self._stream_worker.stream_failed.connect(
                lambda _tid, err: QMessageBox.warning(self, "Google Drive Stream", err)
            )
            self._stream_worker.start()
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

        self._refresh_button_active(self.btn_nav_home, view_key == "home")
        self._refresh_button_active(self.btn_nav_library, view_key == "library")
        self._refresh_button_active(self.btn_nav_gdrive, view_key == "gdrive")
        self._refresh_button_active(self.btn_nav_liked, view_key == "liked")
        self._refresh_button_active(self.btn_nav_recent, view_key == "recent")

        self._refresh_playlist_sidebar()
        self.quick_scroll.setVisible(view_key == "home")
        self.track_table.setVisible(view_key != "home")

        if view_key == "home":
            self._rebuild_quick_access_cards()

        self._refresh_track_table()
        self._refresh_queue_ui()

    def _refresh_playlist_sidebar(self) -> None:
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        for pid, pl in self.store.playlists.items():
            item = QListWidgetItem(f"🎵  {pl.name}  ({len(pl.track_ids)})")
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self.playlist_list.addItem(item)
            if self.current_view == f"playlist:{pid}":
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
            lbl_title = QLabel(f"<b>Cuenta SpotiLinux: {s_user}</b> ({s_email})")
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

        btn_acc = QPushButton("👤 Mi Cuenta" if has_spoti_acc else "👤 Crear / Login")
        btn_acc.setObjectName("ActionChipButton")
        btn_acc.setProperty("active", "false" if has_spoti_acc else "true")
        btn_acc.setFixedHeight(32)
        btn_acc.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_acc.clicked.connect(self.open_account_dialog)
        acc_lay.addWidget(btn_acc)

        btn_up = QPushButton("⬆️ Subir")
        btn_up.setObjectName("ActionChipButton")
        btn_up.setFixedHeight(32)
        btn_up.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_up.setToolTip("Subir nuevas canciones a la carpeta Musica del Drive")
        btn_up.clicked.connect(self.upload_files_to_drive_dialog)
        acc_lay.addWidget(btn_up)

        btn_sync = QPushButton("🔄 Drive" if (has_spoti_acc or has_user or has_token) else "☁️ Drive")
        btn_sync.setObjectName("ActionChipButton")
        btn_sync.setFixedHeight(32)
        btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sync.clicked.connect(self._sync_or_connect_google)
        acc_lay.addWidget(btn_sync)

        self.quick_grid_main_layout.addWidget(acc_frame)

        # 2. Encabezado de Sección: "🎵 Tus Playlists Disponibles"
        sec_header = QLabel("🎵 Tus Playlists Disponibles")
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
        cloud_tracks = self._get_home_preview_tracks("gdrive")[1]
        cards_data.append(
            (
                "Mi Google Drive ☁️",
                f"{len(cloud_tracks)} pistas en la nube",
                make_liked_cover_pixmap(56, radius=6, symbol="☁️", colors=((15, 95, 195), (29, 185, 84), (110, 245, 210))),
                "gdrive",
            )
        )
        for pid, pl in self.store.playlists.items():
            first_tid = pl.track_ids[0] if pl.track_ids else None
            cov = (
                make_rounded_pixmap(self.store.tracks[first_tid].cover_path, 56, 6)
                if first_tid and first_tid in self.store.tracks
                else make_liked_cover_pixmap(56, 6)
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
        btn_play = QPushButton("▶  Reproducir")
        btn_play.setObjectName("ActionChipButton")
        btn_play.setProperty("active", "true")
        btn_play.clicked.connect(lambda _c=False, k=key: self._play_home_key(k))
        btn_shuffle = QPushButton("🔀 Aleatorio")
        btn_shuffle.setObjectName("ActionChipButton")
        btn_shuffle.clicked.connect(lambda _c=False, k=key: self._play_home_key(k, shuffle=True))
        btn_open = QPushButton("↗ Abrir")
        btn_open.setObjectName("ActionChipButton")
        btn_open.setToolTip("Abrir la playlist completa (editar, quitar o agregar canciones)")
        btn_open.clicked.connect(lambda _c=False, k=key: self.switch_view(k))
        btn_dl = QPushButton("⬇️ Descargar")
        btn_dl.setObjectName("ActionChipButton")
        btn_dl.setToolTip("Descargar canciones de Google Drive de esta playlist a music_library/")
        btn_dl.clicked.connect(lambda _c=False, k=key: self._download_entire_playlist(k))
        btn_collapse = QPushButton("➕ Mostrar lista" if self.home_preview_collapsed else "➖ Reducir lista")
        btn_collapse.setObjectName("ActionChipButton")
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
        all_tracks = list(self.store.tracks.values())

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
                "Tus pistas marcadas con corazón ♥ en SpotiLinux",
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
                return (
                    "PLAYLIST PERSONALIZADA",
                    pl.name,
                    pl.description or "Playlist creada en SpotiLinux",
                    tracks,
                    True,
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
            tracks = [
                t
                for t in tracks
                if query in t.title.lower()
                or query in t.artist.lower()
                or query in t.album.lower()
                or query in t.genre.lower()
            ]

        self.displayed_tracks = tracks

        total_secs = sum(t.duration for t in tracks)
        stats_text = f"{len(tracks)} canciones  •  {format_duration(total_secs)}"
        if self.current_view == "home":
            banner_pix = make_liked_cover_pixmap(138, radius=10, symbol="🎵", colors=((29, 185, 84), (16, 95, 170), (140, 50, 210)))
            banner_rgb = (29, 135, 78)
            stats_text = f"{len(self.store.playlists)} playlists disponibles  •  {len(self.store.liked_ids)} favoritas"
        elif self.current_view == "liked":
            banner_pix = make_liked_cover_pixmap(138, radius=10)
            banner_rgb = (82, 42, 188)
        elif self.current_view == "gdrive":
            banner_pix = make_liked_cover_pixmap(138, radius=10, symbol="☁️", colors=((15, 95, 195), (29, 185, 84), (110, 245, 210)))
            banner_rgb = (22, 105, 148)
        elif tracks:
            banner_pix = make_rounded_pixmap(tracks[0].cover_path, 138, radius=10)
            banner_rgb = tracks[0].dominant_color
        else:
            banner_pix = make_liked_cover_pixmap(138, radius=10)
            banner_rgb = (29, 115, 72)

        show_dl = is_custom_pl or self.current_view in ("gdrive", "liked") or any(t.filepath.startswith("gdrive://") for t in tracks)
        self.hero_banner.set_banner_data(badge, title, subtitle, stats_text, banner_pix, banner_rgb, is_custom_pl, show_download=show_dl)

        self.track_table.setRowCount(len(tracks))
        active_id = self.engine.current_track.id if self.engine.current_track else None

        for row, track in enumerate(tracks):
            self.track_table.setRowHeight(row, 54)
            is_active = track.id == active_id
            is_cloud = track.filepath.startswith("gdrive://") or Path(track.filepath).name.startswith("gd_")

            num_item = QTableWidgetItem("▶" if is_active else str(row + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            num_item.setForeground(QColor("#1DB954") if is_active else QColor("#A7A7A7"))
            if is_active:
                f = num_item.font()
                f.setBold(True)
                num_item.setFont(f)
            num_item.setData(Qt.ItemDataRole.UserRole, track.id)
            self.track_table.setItem(row, 0, num_item)

            cloud_tag = " ☁️" if is_cloud else ""
            title_item = QTableWidgetItem(f"{track.title}{cloud_tag}\n{track.artist}")
            title_item.setIcon(QIcon(make_rounded_pixmap(track.cover_path, 40, radius=5)))
            title_item.setForeground(QColor("#1DB954") if is_active else QColor("#FFFFFF"))
            f_title = title_item.font()
            f_title.setBold(True)
            title_item.setFont(f_title)
            self.track_table.setItem(row, 1, title_item)

            album_item = QTableWidgetItem(track.album)
            album_item.setForeground(QColor("#B3B3B3"))
            self.track_table.setItem(row, 2, album_item)

            genre_item = QTableWidgetItem(track.genre or ("Google Drive" if is_cloud else "Local"))
            genre_item.setForeground(QColor("#9E9EA8"))
            self.track_table.setItem(row, 3, genre_item)

            liked = self.store.is_liked(track.id)
            heart_item = QTableWidgetItem("♥" if liked else "♡")
            heart_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            heart_item.setForeground(QColor("#1DB954") if liked else QColor("#888888"))
            f_h = heart_item.font()
            f_h.setPointSize(13)
            heart_item.setFont(f_h)
            self.track_table.setItem(row, 4, heart_item)

            dur_txt = format_duration(track.duration) if track.duration > 0 else "Cloud ☁️"
            dur_item = QTableWidgetItem(dur_txt)
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            dur_item.setForeground(QColor("#A7A7A7"))
            self.track_table.setItem(row, 5, dur_item)

    # ================================================================== #
    #                     INTERACCIÓN CON CANCIONES                      #
    # ================================================================== #

    def _on_table_double_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(self.displayed_tracks):
            track = self.displayed_tracks[row]
            self._play_track_or_stream_from_cloud(track, self.displayed_tracks)

    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if not (0 <= row < len(self.displayed_tracks)):
            return
        track = self.displayed_tracks[row]
        if col == 4:
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
            if 0 <= row < len(self.displayed_tracks):
                selected_rows = [row]

        if not selected_rows:
            return

        selected_tracks = [self.displayed_tracks[r] for r in selected_rows if 0 <= r < len(self.displayed_tracks)]
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

            act_remove_from_pl = None
            if self.current_view.startswith("playlist:"):
                menu.addSeparator()
                act_remove_from_pl = menu.addAction("✕  Quitar de esta Playlist")
        else:
            act_play = menu.addAction(f"▶  Reproducir selección ({n_tracks} canciones)")
            act_download_cloud = None
            act_queue = menu.addAction(f"☰  Añadir {n_tracks} canciones a la cola")
            act_like = menu.addAction(f"♥  Guardar las {n_tracks} canciones en Favoritos")

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
                act_remove_from_pl = menu.addAction(f"✕  Quitar las {n_tracks} canciones seleccionadas de esta Playlist")

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

    def _sync_playlists_to_cloud(self, notify: bool = False) -> None:
        token = self.store.gdrive_config.access_token
        payload = {
            "app": "SpotiLinux",
            "version": "1.6.0",
            "updatedAt": time.time(),
            "user": self.store.google_user,
            "account": self.store.user_account,
            "playlists": [pl.to_dict() for pl in self.store.playlists.values()],
            "likedIds": list(self.store.liked_ids),
            "drive_tracks": self._collect_drive_tracks_for_account(),
        }
        self._cloud_sync_worker = GDriveCloudPlaylistSyncWorker(
            access_token=token,
            local_payload=payload,
            config=self.store.gdrive_config,
            local_db_path=self.store.accounts_db_path,
        )
        self._cloud_sync_worker.sync_completed.connect(lambda merged: self._on_cloud_sync_success(merged, notify=notify))
        self._cloud_sync_worker.sync_failed.connect(lambda err: self._on_cloud_sync_failed(err, notify=notify))
        self._cloud_sync_worker.start()

    def _on_cloud_sync_success(self, merged: dict, notify: bool = False) -> None:
        changed = False
        # Restaurar canciones de Drive que vengan en la cuenta o en el archivo sincronizado
        acc_block = merged.get("account") if isinstance(merged.get("account"), dict) else {}
        if acc_block or merged.get("drive_tracks"):
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
            local_pl = self.store.playlists.get(pid) or next(
                (p for p in self.store.playlists.values() if p.name.lower() == pname.lower()), None
            )
            if local_pl:
                for tid in cp.get("track_ids", cp.get("trackIds", [])):
                    if tid in self.store.tracks and tid not in local_pl.track_ids:
                        local_pl.track_ids.append(tid)
                        changed = True
            else:
                new_pl = Playlist(
                    id=pid or str(uuid.uuid4())[:8],
                    name=pname or "Playlist Cloud",
                    description=cp.get("description", ""),
                    track_ids=[t for t in cp.get("track_ids", cp.get("trackIds", [])) if t in self.store.tracks],
                    created_at=float(cp.get("created_at", time.time())),
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
                "Puedes usarlas en varios dispositivos iniciando sesión con tu misma cuenta SpotiLinux.",
            )

    def _on_cloud_sync_failed(self, err: str, notify: bool = False) -> None:
        if notify:
            QMessageBox.warning(
                self,
                "Aviso de Sincronización Cloud",
                f"No se pudo sincronizar con Google Drive:\n{err}",
            )

    def _download_entire_playlist(self, key: Optional[str] = None) -> None:
        """Descarga todas las pistas de Google Drive de una playlist a music_library/ verificando duplicados."""
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

        cloud_pending = [t for t in pl_tracks if t.filepath.startswith("gdrive://")]
        if not cloud_pending:
            QMessageBox.information(
                self,
                "Descargar Playlist",
                f"Todas las canciones de «{name}» ya están disponibles localmente sin duplicados.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Descargar Playlist Completa",
            f"¿Deseas descargar las {len(cloud_pending)} canciones de Google Drive de «{name}» a tu carpeta local?\n\n"
            "Se verificarán duplicados automáticamente para evitar archivos repetidos.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.hero_banner.lbl_subtitle.setText(f"⬇️ Descargando {len(cloud_pending)} canciones de «{name}»...")
        for t in cloud_pending:
            self._download_cloud_track_to_app_folder(t)

    def _download_cloud_track_to_app_folder(self, track: TrackMetadata) -> None:
        """Descarga explícitamente una pista de Google Drive a music_library/ verificando duplicados primero."""
        self.hero_banner.lbl_subtitle.setText(
            f"⬇️ Verificando duplicados y descargando «{track.title}» a music_library/..."
        )
        self._stream_worker = GDriveStreamDownloadWorker(
            track=track,
            config=self.store.gdrive_config,
            cache_dir=self.store.music_library_dir,
            covers_dir=self.store.covers_dir,
            existing_local_dirs=[self.store.music_library_dir, self.store.gdrive_cache_dir],
            verify_duplicates=self.store.gdrive_config.verify_duplicates,
        )
        self._stream_worker.stream_ready.connect(self._on_cloud_download_saved)
        self._stream_worker.stream_failed.connect(
            lambda _tid, err: QMessageBox.warning(self, "Google Drive Descarga", err)
        )
        self._stream_worker.start()

    def _on_cloud_download_saved(self, old_id: str, updated_track: TrackMetadata, was_duplicate: bool = False) -> None:
        self.store.tracks[old_id] = updated_track
        self.store.deduplicate_library()
        self.store.save()
        self._refresh_track_table()
        if was_duplicate:
            msg = f"🛡️ Duplicado evitado: «{updated_track.title}» ya estaba descargada en music_library/"
            self.hero_banner.lbl_subtitle.setText(msg)
            QMessageBox.information(
                self,
                "🛡️ Verificación de Duplicados",
                f"La canción «{updated_track.title}» ya existía localmente en:\n{updated_track.filepath}\n\n"
                "Se vinculó el archivo existente sin volver a descargarlo ni duplicarlo.",
            )
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
            "Ya no tendrás que volver a importarlas cada vez que abras SpotiLinux.",
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

    def _update_like_button_ui(self, track_id: str) -> None:
        liked = self.store.is_liked(track_id)
        self.btn_bar_like.setText("♥" if liked else "♡")
        self._refresh_button_active(self.btn_bar_like, liked)

    def _on_engine_state_changed(self, is_playing: bool) -> None:
        icon = "⏸" if is_playing else "▶"
        self.btn_play_pause.setText(icon)
        self.hero_banner.btn_play_banner.setText(icon)

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

    def _on_volume_changed(self, value: int) -> None:
        self.store.volume = value
        self.store.save()
        self.engine.set_volume(value)
        if value == 0:
            self.btn_mute.setText("🔇")
        elif value < 50:
            self.btn_mute.setText("🔉")
        else:
            self.btn_mute.setText("🔊")

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
        if is_shuf:
            self.btn_shuffle.setText("🔀 •")
            self.btn_shuffle.setToolTip("Modo aleatorio: ACTIVADO (clic para desactivar)")
            self.hero_banner.btn_shuffle_banner.setText("🔀 Aleatorio: ACTIVADO")
            self._refresh_button_active(self.hero_banner.btn_shuffle_banner, True)
        else:
            self.btn_shuffle.setText("🔀")
            self.btn_shuffle.setToolTip("Modo aleatorio: DESACTIVADO (clic para activar)")
            self.hero_banner.btn_shuffle_banner.setText("🔀 Aleatorio")
            self._refresh_button_active(self.hero_banner.btn_shuffle_banner, False)

    def cycle_repeat_mode(self) -> None:
        modes = ["off", "all", "one"]
        idx = (modes.index(self.engine.repeat_mode) + 1) % len(modes) if self.engine.repeat_mode in modes else 0
        self.engine.repeat_mode = modes[idx]
        self.store.repeat_mode = self.engine.repeat_mode
        self.store.save()
        self.btn_repeat.setText(self._repeat_icon_text())
        self._refresh_button_active(self.btn_repeat, self.engine.repeat_mode != "off")

    def _repeat_icon_text(self) -> str:
        if self.engine.repeat_mode == "one":
            return "🔂"
        return "🔁"

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
    app.setApplicationName("SpotiLinux")
    app.setStyleSheet(SPOTIFY_QSS)

    base_dir = resolve_app_data_dir()
    window = SpotiLinuxWindow(base_dir)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
