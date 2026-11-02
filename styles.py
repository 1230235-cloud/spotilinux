#!/usr/bin/env python3
"""
Hoja de estilos QSS para MaoMusic Desktop — Inspirada fielmente en la interfaz limpia de MaoMusic Mobile:
Paleta OLED (#09090b / #121215 / #18181f), bordes sutiles traslúcidos (rgba 255,255,255,0.08),
botones píldora ('btn-chip' border-radius 9999px), dock de reproducción flotante (#121216),
filas de canciones como tarjetas redondeadas modernas (sin aspecto de Excel) y acento verde vibrante (#1ed760).
"""

SPOTIFY_QSS = """
QMainWindow, QWidget#RootContainer {
    background-color: #09090b;
    color: #FFFFFF;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", "Noto Sans", sans-serif;
}

/* Tarjetas principales limpias con elevación moderna */
QFrame#SidebarCard, QFrame#MainCard, QFrame#RightCard {
    background-color: #121215;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 16px;
}

/* Dock flotante de reproducción estilo MaoMusic Mobile */
QFrame#BottomPlayerBar {
    background-color: #121216;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 16px;
}

QFrame#EqualizerCard {
    background-color: #16161c;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
}

QFrame#NowPlayingMiniCard {
    background-color: #16161c;
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 14px;
}

QPushButton {
    outline: none;
    background-color: #18181f;
    color: #e4e4ec;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 9999px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #242430;
    color: #ffffff;
    border-color: rgba(255, 255, 255, 0.18);
}

QPushButton:pressed {
    background-color: #121217;
}

QPushButton:disabled {
    color: #555562;
    background-color: #121216;
    border-color: rgba(255, 255, 255, 0.03);
}

/* Botones de navegación lateral con diseño de píldora */
QPushButton#NavButton {
    background-color: transparent;
    color: #a0a0ab;
    border: 1px solid transparent;
    border-radius: 9999px;
    padding: 8px 14px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
}

QPushButton#NavButton:hover {
    color: #FFFFFF;
    background-color: #18181f;
    border-color: rgba(255, 255, 255, 0.06);
}

QPushButton#NavButton:pressed {
    background-color: #141419;
}

QPushButton#NavButton[active="true"] {
    color: #1ed760;
    background-color: rgba(30, 215, 96, 0.12);
    border: 1px solid rgba(30, 215, 96, 0.35);
    font-weight: 800;
}

/* Botones Chip / Píldora estilo móvil ('btn-chip') */
QPushButton#ActionChipButton {
    background-color: #18181f;
    color: #e5e5ec;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 9999px;
    padding: 6px 14px;
    font-size: 11.5px;
    font-weight: 700;
}

QPushButton#ActionChipButton:hover {
    background-color: #24242d;
    border-color: rgba(30, 215, 96, 0.50);
    color: #FFFFFF;
}

QPushButton#ActionChipButton:pressed {
    background-color: #141419;
    border-color: #1ed760;
}

QPushButton#ActionChipButton[active="true"] {
    background-color: #1ed760;
    color: #000000;
    border: 1px solid #1ed760;
    font-weight: 800;
}

QPushButton#ActionChipButton[active="true"]:hover {
    background-color: #2bf075;
}

QPushButton#ActionChipButton[active="true"]:pressed {
    background-color: #17a84b;
}

/* Botón grande de reproducción */
QPushButton#SpotifyGreenPlayBig {
    background-color: #1ed760;
    color: #000000;
    border: none;
    border-radius: 24px;
    font-size: 18px;
    font-weight: 800;
}

QPushButton#SpotifyGreenPlayBig:hover {
    background-color: #2bf075;
}

QPushButton#SpotifyGreenPlayBig:pressed {
    background-color: #17a84b;
}

/* Botón de reproducción en el dock */
QPushButton#TransportPlayButton {
    background-color: #1ed760;
    color: #000000;
    border: none;
    border-radius: 22px;
    padding: 0px;
}

QPushButton#TransportPlayButton:hover {
    background-color: #2bf075;
}

QPushButton#TransportPlayButton:pressed {
    background-color: #17a84b;
}

/* Iconos de transporte (prev, next, shuffle, repeat, mute) estilo dark elegante */
QPushButton#TransportIconBtn {
    background-color: #18181f;
    color: #a6a6b4;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 17px;
    padding: 0px;
}

QPushButton#TransportIconBtn:hover {
    color: #FFFFFF;
    background-color: #242430;
    border: 1px solid rgba(255, 255, 255, 0.20);
}

QPushButton#TransportIconBtn:pressed {
    background-color: #131317;
}

QPushButton#TransportIconBtn[active="true"] {
    color: #1ed760;
    background-color: rgba(30, 215, 96, 0.18);
    border: 1px solid rgba(30, 215, 96, 0.50);
    font-weight: bold;
}

QPushButton#GhostSmallButton {
    background-color: #18181f;
    color: #e5e5ec;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 9999px;
    padding: 6px 14px;
    font-size: 11.5px;
    font-weight: 700;
}

QPushButton#GhostSmallButton:hover {
    background-color: #24242d;
    border-color: rgba(30, 215, 96, 0.50);
    color: #FFFFFF;
}

QPushButton#GhostSmallButton:pressed {
    background-color: #141418;
}

QPushButton#GhostSmallButton[active="true"] {
    background-color: rgba(30, 215, 96, 0.16);
    color: #1ed760;
    border: 1px solid rgba(30, 215, 96, 0.50);
    font-weight: 800;
}

/* Buscador redondeado estilo píldora */
QLineEdit#SearchInput {
    background-color: #16161c;
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 9999px;
    padding: 7px 16px;
    font-size: 12.5px;
}

QLineEdit#SearchInput:hover {
    border: 1px solid rgba(255, 255, 255, 0.18);
}

QLineEdit#SearchInput:focus {
    background-color: #1f1f26;
    border: 1px solid #1ed760;
}

/* Listas de playlists y cola de reproducción */
QListWidget#PlaylistSidebarList, QListWidget#QueueListWidget {
    background-color: transparent;
    border: none;
    outline: none;
    color: #d0d0d8;
    font-size: 12.5px;
}

QListWidget#PlaylistSidebarList::item {
    padding: 8px 12px;
    border-radius: 8px;
    margin-bottom: 3px;
}

QListWidget#PlaylistSidebarList::item:hover {
    background-color: rgba(255, 255, 255, 0.05);
    color: #FFFFFF;
}

QListWidget#PlaylistSidebarList::item:selected {
    background-color: rgba(30, 215, 96, 0.14);
    color: #1ed760;
    font-weight: bold;
}

/* Lista de canciones moderna estilo tarjetas (cero Excel) */
QTableWidget#TrackTable {
    background-color: transparent;
    border: none;
    gridline-color: transparent;
    color: #e5e5ec;
    selection-background-color: transparent;
    selection-color: #FFFFFF;
    outline: none;
    font-size: 13px;
}

QTableWidget#TrackTable::item {
    padding: 6px 10px;
    border: 1px solid rgba(255, 255, 255, 0.04);
    background-color: rgba(255, 255, 255, 0.02);
    border-radius: 12px;
    margin: 2px 3px;
}

QTableWidget#TrackTable::item:hover {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.09);
}

QTableWidget#TrackTable::item:selected {
    background-color: rgba(30, 215, 96, 0.14);
    border: 1px solid rgba(30, 215, 96, 0.35);
    color: #1ed760;
}

QHeaderView::section {
    background-color: transparent;
    color: transparent;
    border: none;
    height: 0px;
}

/* Deslizadores modernos y sutiles */
QSlider::groove:horizontal {
    border: none;
    height: 4px;
    background: #27272f;
    border-radius: 2px;
}

QSlider::sub-page:horizontal {
    background: #1ed760;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #FFFFFF;
    border: none;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}

QSlider::handle:horizontal:hover {
    background: #1ed760;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::groove:vertical {
    border: none;
    width: 4px;
    background: #27272f;
    border-radius: 2px;
}

QSlider::add-page:vertical {
    background: #1ed760;
    border-radius: 2px;
}

QSlider::handle:vertical {
    background: #FFFFFF;
    border: none;
    width: 12px;
    height: 12px;
    margin: 0 -4px;
    border-radius: 6px;
}

QSlider::handle:vertical:hover {
    background: #1ed760;
}

QComboBox {
    background-color: #18181f;
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 12px;
}

QComboBox QAbstractItemView {
    background-color: #16161c;
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.10);
    selection-background-color: #1ed760;
    selection-color: #000000;
}

QToolTip {
    background-color: #18181f;
    color: #F2F2F2;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 11.5px;
}

QScrollBar:vertical {
    background: transparent;
    width: 7px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #282830;
    min-height: 30px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #3e3e4a;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    height: 0px;
    background: transparent;
}

QMenu {
    background-color: #16161c;
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 12px;
    padding: 6px;
}

QMenu::item {
    padding: 8px 18px;
    border-radius: 8px;
    font-size: 12.5px;
}

QMenu::item:selected {
    background-color: #24242e;
    color: #1ed760;
}

/* Tarjeta de playlist destacada en Inicio */
QFrame#ActivePlaylistHomeCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #15221b, stop:1 #111316);
    border: 1px solid rgba(30, 215, 96, 0.35);
    border-radius: 14px;
}

/* Fila de pista compacta */
QFrame#CompactTrackRow {
    background-color: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 10px;
}

QFrame#CompactTrackRow:hover {
    background-color: rgba(255, 255, 255, 0.06);
    border-color: rgba(255, 255, 255, 0.10);
}

QFrame#CompactTrackRow[playing="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(30, 215, 96, 0.18), stop:1 #151f19);
    border-color: rgba(30, 215, 96, 0.55);
}

/* Modo Reposo / Tocadiscos OLED con Video */
QFrame#VinylStandbyOverlay {
    background-color: #09090c;
}

QLabel#VinylClock {
    color: #FFFFFF;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 1.5px;
}

QLabel#VinylTitle {
    color: #FFFFFF;
    font-size: 22px;
    font-weight: 800;
}

QLabel#VinylArtist {
    color: #1ed760;
    font-size: 14px;
    font-weight: 600;
}

QLabel#VinylBadge {
    color: #8e8e98;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.2px;
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    padding: 3px 10px;
}
"""
