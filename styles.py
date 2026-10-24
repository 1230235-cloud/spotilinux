#!/usr/bin/env python3
"""
Hoja de estilos QSS inspirada en Spotify Desktop para Linux (Dark Theme #000000 / #121212 / #1DB954).
"""

SPOTIFY_QSS = """
QMainWindow, QWidget#RootContainer {
    background-color: #000000;
    color: #FFFFFF;
    font-family: "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
}

QFrame#SidebarCard, QFrame#MainCard, QFrame#RightCard {
    background-color: #121212;
    border: 1px solid #1C1C1C;
    border-radius: 12px;
}

QFrame#BottomPlayerBar {
    background-color: #000000;
    border-top: 1px solid #1A1A1A;
}

QFrame#EqualizerCard {
    background-color: #181818;
    border: 1px solid #262626;
    border-radius: 10px;
}

QFrame#NowPlayingMiniCard {
    background-color: #181818;
    border-radius: 10px;
}

QPushButton {
    outline: none;
}

QPushButton#NavButton {
    background-color: transparent;
    color: #B3B3B3;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 8px;
    padding: 10px 14px;
    text-align: left;
    font-size: 14px;
    font-weight: 600;
}

QPushButton#NavButton:hover {
    color: #FFFFFF;
    background-color: #1C1C1C;
}

QPushButton#NavButton:pressed {
    background-color: #151515;
}

QPushButton#NavButton[active="true"] {
    color: #FFFFFF;
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(29,185,84,0.20), stop:1 #202020);
    border-left: 3px solid #1DB954;
    font-weight: 700;
}

QPushButton#ActionChipButton {
    background-color: #18181f;
    color: #e5e5ec;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 16px;
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 700;
}

QPushButton#ActionChipButton:hover {
    background-color: #24242d;
    border-color: rgba(30, 215, 96, 0.6);
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

QPushButton#SpotifyGreenPlayBig {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2BE06A, stop:1 #17A84B);
    color: #04140A;
    border: none;
    border-radius: 25px;
    font-size: 20px;
    font-weight: bold;
}

QPushButton#SpotifyGreenPlayBig:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #45F07D, stop:1 #1DB954);
}

QPushButton#SpotifyGreenPlayBig:pressed {
    background-color: #148F3E;
}

QPushButton#TransportPlayButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFFFFF, stop:1 #E4E4E4);
    color: #000000;
    border: none;
    border-radius: 18px;
    font-size: 15px;
    font-weight: bold;
}

QPushButton#TransportPlayButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3BE675, stop:1 #1DB954);
}

QPushButton#TransportPlayButton:pressed {
    background-color: #17A04A;
}

QPushButton#TransportIconBtn {
    background-color: transparent;
    color: #B3B3B3;
    border: none;
    border-radius: 15px;
    font-size: 15px;
    font-weight: 600;
    padding: 4px 8px;
}

QPushButton#TransportIconBtn:hover {
    color: #FFFFFF;
    background-color: #1F1F1F;
}

QPushButton#TransportIconBtn:pressed {
    background-color: #151515;
}

QPushButton#TransportIconBtn[active="true"] {
    color: #1ED760;
    background-color: rgba(29, 185, 84, 0.18);
    border: 1px solid rgba(29, 185, 84, 0.65);
    font-weight: bold;
}

QPushButton#GhostSmallButton {
    background-color: #202020;
    color: #DCDCDC;
    border: 1px solid #2E2E2E;
    border-radius: 12px;
    padding: 4px 11px;
    font-size: 11px;
    font-weight: 600;
}

QPushButton#GhostSmallButton:hover {
    background-color: #2C2C2C;
    border-color: #1DB954;
    color: #FFFFFF;
}

QPushButton#GhostSmallButton:pressed {
    background-color: #171717;
}

QLineEdit#SearchInput {
    background-color: #242424;
    color: #FFFFFF;
    border: 1px solid #2A2A2A;
    border-radius: 19px;
    padding: 8px 16px;
    font-size: 13px;
}

QLineEdit#SearchInput:hover {
    border: 1px solid #3A3A3A;
}

QLineEdit#SearchInput:focus {
    background-color: #2A2A2A;
    border: 1px solid #1DB954;
}

QListWidget#PlaylistSidebarList, QListWidget#QueueListWidget {
    background-color: transparent;
    border: none;
    outline: none;
    color: #D0D0D0;
    font-size: 13px;
}

QListWidget#PlaylistSidebarList::item {
    padding: 8px 10px;
    border-radius: 6px;
    margin-bottom: 2px;
}

QListWidget#PlaylistSidebarList::item:hover {
    background-color: #1F1F1F;
    color: #FFFFFF;
}

QListWidget#PlaylistSidebarList::item:selected {
    background-color: #282828;
    color: #1DB954;
    font-weight: bold;
}

QTableWidget#TrackTable {
    background-color: transparent;
    border: none;
    gridline-color: transparent;
    color: #EAEAEA;
    selection-background-color: #282828;
    selection-color: #FFFFFF;
    outline: none;
    font-size: 13px;
}

QTableWidget#TrackTable::item {
    padding: 6px 8px;
    border-bottom: 1px solid #1B1B1B;
}

QTableWidget#TrackTable::item:hover {
    background-color: #202020;
}

QHeaderView::section {
    background-color: transparent;
    color: #A7A7A7;
    border: none;
    border-bottom: 1px solid #282828;
    padding: 8px 8px;
    font-size: 11px;
    font-weight: 700;
}

QSlider::groove:horizontal {
    border: none;
    height: 4px;
    background: #4D4D4D;
    border-radius: 2px;
}

QSlider::sub-page:horizontal {
    background: #1DB954;
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
    background: #1ED760;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::groove:vertical {
    border: none;
    width: 5px;
    background: #3A3A3A;
    border-radius: 2px;
}

QSlider::add-page:vertical {
    background: #1DB954;
    border-radius: 2px;
}

QSlider::handle:vertical {
    background: #FFFFFF;
    border: none;
    width: 13px;
    height: 13px;
    margin: 0 -4px;
    border-radius: 6px;
}

QComboBox {
    background-color: #282828;
    color: #FFFFFF;
    border: 1px solid #3A3A3A;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
}

QComboBox QAbstractItemView {
    background-color: #282828;
    color: #FFFFFF;
    selection-background-color: #1DB954;
    selection-color: #000000;
}

QToolTip {
    background-color: #1E1E1E;
    color: #F2F2F2;
    border: 1px solid #2F2F2F;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 12px;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #3A3A3A;
    min-height: 34px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover {
    background: #5E5E5E;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    height: 0px;
    background: transparent;
}


QMenu {
    background-color: #282828;
    color: #FFFFFF;
    border: 1px solid #383838;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item {
    padding: 7px 20px;
    border-radius: 4px;
    font-size: 13px;
}

QMenu::item:selected {
    background-color: #3E3E3E;
    color: #1DB954;
}

QFrame#ActivePlaylistHomeCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a2720, stop:1 #121417);
    border: 1px solid rgba(29, 185, 84, 0.4);
    border-radius: 12px;
}

QFrame#CompactTrackRow {
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 8px;
}

QFrame#CompactTrackRow:hover {
    background-color: rgba(29, 185, 84, 0.12);
    border-color: rgba(29, 185, 84, 0.35);
}

QFrame#CompactTrackRow[playing="true"] {
    background-color: rgba(29, 185, 84, 0.18);
    border-color: #1DB954;
}
"""
