#!/usr/bin/env python3
"""
Widgets de visualización de espectro/ondas de audio en tiempo real a 60 FPS
y panel de Ecualizador Paramétrico de 5 bandas con curva de respuesta interactiva.
"""

import math
from typing import Optional
import numpy as np
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from audio_engine import EQ_BANDS, EQ_PRESETS


class AudioVisualizerWidget(QWidget):
    """Lienzo acelerado con QPainter para renderizar espectro FFT, onda oscilográfica o reactor circular."""

    mode_changed = pyqtSignal(str)

    MODES = [
        ("bars", "Barras FFT"),
        ("wave", "Onda Neón"),
        ("radial", "Pulso Circular"),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(165)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Haz clic para cambiar el modo del visualizador (Barras FFT / Onda / Circular)")

        self.mode: str = "bars"
        self.bars = np.zeros(32, dtype=np.float32)
        self.peaks = np.zeros(32, dtype=np.float32)
        self.waveform = np.zeros(128, dtype=np.float32)
        self.accent_color = QColor(29, 185, 84)  # Spotify Green #1DB954
        self.cover_pixmap: Optional[QPixmap] = None
        self._angle: float = 0.0
        self.beat_frac: float = 0.0   # 0 en el beat, →1 antes del siguiente
        self.beat_onset: float = 0.0
        self._playing: bool = False

    def set_beat(self, bpm: float, beat_pos: float, confidence: float, onset: float) -> None:
        """Sincroniza el pulso visual con la rejilla de beats real de la canción."""
        self._playing = bpm > 0
        self.beat_frac = (beat_pos - math.floor(beat_pos)) if bpm > 0 else 0.0
        self.beat_onset = max(onset, self.beat_onset * 0.82)

    def set_mode(self, mode: str) -> None:
        if mode in ("bars", "wave", "radial"):
            self.mode = mode
            self.update()

    def next_mode(self) -> str:
        keys = [m[0] for m in self.MODES]
        idx = (keys.index(self.mode) + 1) % len(keys) if self.mode in keys else 0
        self.mode = keys[idx]
        self.mode_changed.emit(self.mode)
        self.update()
        return self.mode

    def set_accent_color(self, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        # Asegurar brillo suficiente para el visualizador
        self.accent_color = QColor(
            min(255, max(45, int(r * 1.35))),
            min(255, max(185, int(g * 1.35))),
            min(255, max(95, int(b * 1.35))),
        )
        self.update()

    def set_cover(self, cover_path: str) -> None:
        if cover_path:
            pix = QPixmap(cover_path)
            if not pix.isNull():
                self.cover_pixmap = pix.scaled(
                    84, 84, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
                )
        self.update()

    def update_audio_data(self, spectrum: np.ndarray, waveform: np.ndarray) -> None:
        self.bars = spectrum
        self.waveform = waveform
        # Actualizar picos flotantes con caída suave
        for i in range(len(self.bars)):
            if self.bars[i] >= self.peaks[i]:
                self.peaks[i] = self.bars[i]
            else:
                self.peaks[i] = max(0.0, self.peaks[i] - 0.014)

        self._angle = (self._angle + 0.35) % 360.0
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.next_mode()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # Fondo oscuro redondeado
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, w, h), 10.0, 10.0)
        grad_bg = QLinearGradient(0, 0, 0, h)
        grad_bg.setColorAt(0.0, QColor(22, 22, 26))
        grad_bg.setColorAt(1.0, QColor(12, 12, 14))
        painter.fillPath(bg_path, grad_bg)

        # Rejilla horizontal muy tenue (da lectura de nivel, aspecto de analizador profesional)
        painter.setPen(QPen(QColor(255, 255, 255, 12), 1.0))
        for frac in (0.25, 0.5, 0.75):
            gy = 26.0 + (h - 40.0) * frac
            painter.drawLine(QPointF(12.0, gy), QPointF(w - 12.0, gy))
        # Borde fino
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 10.0, 10.0)

        if self.mode == "bars":
            self._draw_bars(painter, w, h)
        elif self.mode == "wave":
            self._draw_wave(painter, w, h)
        else:
            self._draw_radial(painter, w, h)

        # Etiqueta sutil del modo en la esquina superior derecha
        mode_label = dict(self.MODES).get(self.mode, "FFT")
        painter.setPen(QColor(165, 165, 175, 160))
        font = QFont("Sans Serif", 8, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(10, 8, w - 20, 18), Qt.AlignmentFlag.AlignRight, f"● {mode_label.upper()}")

    def _draw_bars(self, painter: QPainter, w: float, h: float) -> None:
        n = len(self.bars)
        if n == 0:
            return
        pad_x = 14.0
        pad_top = 26.0
        pad_bottom = 14.0
        usable_w = max(10.0, w - pad_x * 2)
        usable_h = max(10.0, h - pad_top - pad_bottom)

        gap = 3.5
        bar_w = max(2.5, (usable_w - gap * (n - 1)) / n)

        bar_grad = QLinearGradient(0, pad_top, 0, h - pad_bottom)
        bar_grad.setColorAt(0.0, QColor(0, 242, 254))
        bar_grad.setColorAt(0.45, QColor(29, 185, 84))
        bar_grad.setColorAt(1.0, QColor(17, 105, 48))

        # Línea base con pulso en cada beat
        base_alpha = int(40 + 90 * (1.0 - self.beat_frac) ** 2) if self._playing else 40
        painter.setPen(QPen(QColor(29, 185, 84, base_alpha), 1.4))
        painter.drawLine(QPointF(pad_x, h - pad_bottom + 1.0), QPointF(w - pad_x, h - pad_bottom + 1.0))

        for i in range(n):
            val = float(np.clip(self.bars[i], 0.03, 1.0))
            bh = val * usable_h
            x = pad_x + i * (bar_w + gap)
            y = h - pad_bottom - bh

            rect = QRectF(x, y, bar_w, bh)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bar_grad)
            painter.drawRoundedRect(rect, 2.5, 2.5)

            # Reflejo tenue bajo la línea base
            refl_h = min(bh * 0.28, pad_bottom - 3.0)
            if refl_h > 1.0:
                painter.setBrush(QColor(29, 185, 84, 38))
                painter.drawRoundedRect(QRectF(x, h - pad_bottom + 2.0, bar_w, refl_h), 2.0, 2.0)
                painter.setBrush(bar_grad)

            # Indicador de pico flotante
            pk = float(np.clip(self.peaks[i], 0.03, 1.0))
            py = h - pad_bottom - pk * usable_h - 3.5
            painter.setBrush(QColor(220, 255, 235, 210))
            painter.drawRoundedRect(QRectF(x, max(pad_top - 4, py), bar_w, 2.5), 1.0, 1.0)

    def _draw_wave(self, painter: QPainter, w: float, h: float) -> None:
        pts = self.waveform
        n = len(pts)
        if n < 2:
            return

        mid_y = h * 0.54
        amp = h * 0.38

        path = QPainterPath()
        fill_path = QPainterPath()
        fill_path.moveTo(10.0, mid_y)

        for i, v in enumerate(pts):
            x = 10.0 + (i / (n - 1)) * (w - 20.0)
            y = mid_y - float(v) * amp
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
            fill_path.lineTo(x, y)

        fill_path.lineTo(w - 10.0, mid_y)
        fill_path.closeSubpath()

        grad = QLinearGradient(0, mid_y - amp, 0, mid_y + amp)
        grad.setColorAt(0.0, QColor(29, 185, 84, 115))
        grad.setColorAt(0.5, QColor(0, 242, 254, 35))
        grad.setColorAt(1.0, QColor(29, 185, 84, 115))
        painter.fillPath(fill_path, grad)

        # Línea de brillo exterior
        glow_pen = QPen(QColor(29, 185, 84, 95), 5.0)
        painter.setPen(glow_pen)
        painter.drawPath(path)

        # Línea principal nítida
        core_pen = QPen(QColor(130, 255, 195), 2.2)
        painter.setPen(core_pen)
        painter.drawPath(path)

    def _draw_radial(self, painter: QPainter, w: float, h: float) -> None:
        cx, cy = w * 0.5, h * 0.54
        base_r = min(w, h) * 0.23
        max_len = min(w, h) * 0.22

        energy = float(np.mean(self.bars)) if len(self.bars) else 0.0
        beat_kick = (1.0 - self.beat_frac) ** 3 if self._playing else 0.0
        pulse_r = base_r * (1.0 + energy * 0.10 + beat_kick * 0.09 + self.beat_onset * 0.04)

        # Halo radial reactivo
        halo = QRadialGradient(QPointF(cx, cy), pulse_r + max_len)
        halo.setColorAt(0.0, QColor(29, 185, 84, 95))
        halo.setColorAt(0.65, QColor(0, 210, 255, 40))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(cx, cy), pulse_r + max_len, pulse_r + max_len)

        # Rayos de espectro alrededor del círculo
        n = len(self.bars)
        for i in range(n):
            angle = (i / n) * 2.0 * math.pi + math.radians(self._angle * 0.35)
            val = float(self.bars[i])
            r1 = pulse_r + 3.0
            r2 = r1 + max(3.0, val * max_len)

            x1 = cx + math.cos(angle) * r1
            y1 = cy + math.sin(angle) * r1
            x2 = cx + math.cos(angle) * r2
            y2 = cy + math.sin(angle) * r2

            color = QColor.fromHsvF((0.36 + (i / n) * 0.18) % 1.0, 0.82, 0.96)
            painter.setPen(QPen(color, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Disco central
        painter.setPen(QPen(QColor(29, 185, 84, 220), 2.2))
        painter.setBrush(QColor(18, 18, 22))
        painter.drawEllipse(QPointF(cx, cy), pulse_r, pulse_r)
        painter.setBrush(QColor(29, 185, 84))
        painter.drawEllipse(QPointF(cx, cy), 5.0, 5.0)


class EQCurvePreview(QWidget):
    """Dibuja la curva suave de respuesta en frecuencia del ecualizador de 5 bandas."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(64)
        self.gains = [0.0, 0.0, 0.0, 0.0, 0.0]
        self.enabled = True

    def set_state(self, enabled: bool, gains: list[float]) -> None:
        self.enabled = enabled
        self.gains = list(gains[:5])
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24))
        painter.drawRoundedRect(QRectF(0, 0, w, h), 8.0, 8.0)

        mid_y = h / 2.0
        painter.setPen(QPen(QColor(60, 60, 70), 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(10, mid_y), QPointF(w - 10, mid_y))

        xs = [18.0 + i * ((w - 36.0) / 4.0) for i in range(5)]
        ys = [mid_y - (g / 12.0) * (h * 0.38) if self.enabled else mid_y for g in self.gains]

        # Interpolación suave
        dense_x = np.linspace(xs[0], xs[-1], 80)
        dense_y = np.interp(dense_x, xs, ys)

        path = QPainterPath()
        fill = QPainterPath()
        fill.moveTo(dense_x[0], mid_y)
        for i, (dx, dy) in enumerate(zip(dense_x, dense_y)):
            if i == 0:
                path.moveTo(dx, dy)
            else:
                path.lineTo(dx, dy)
            fill.lineTo(dx, dy)
        fill.lineTo(dense_x[-1], mid_y)
        fill.closeSubpath()

        accent = QColor(29, 185, 84) if self.enabled else QColor(110, 110, 120)
        fill_color = QColor(29, 185, 84, 55) if self.enabled else QColor(110, 110, 120, 30)
        painter.fillPath(fill, fill_color)
        painter.setPen(QPen(accent, 2.2))
        painter.drawPath(path)

        for x, y in zip(xs, ys):
            painter.setBrush( QColor(255, 255, 255) if self.enabled else QColor(150, 150, 150))
            painter.drawEllipse(QPointF(x, y), 3.8, 3.8)


class EqualizerPanel(QFrame):
    """Panel interactivo de Ecualizador de 5 bandas con presets y aplicación en tiempo real."""

    eq_changed = pyqtSignal(bool, str, list)  # enabled, preset_name, gains[5]

    def __init__(self, enabled: bool = True, preset: str = "Plano", gains: Optional[list[float]] = None) -> None:
        super().__init__()
        self.setObjectName("EqualizerCard")
        self._updating_ui = False
        self.gains = list(gains) if gains and len(gains) == 5 else [0.0, 0.0, 0.0, 0.0, 0.0]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Cabecera: Switch Activar + Selector de Preset
        top_row = QHBoxLayout()
        self.chk_enable = QCheckBox("Ecualizador")
        self.chk_enable.setChecked(enabled)
        self.chk_enable.setStyleSheet("font-weight: bold; color: #FFFFFF; font-size: 13px;")
        self.chk_enable.toggled.connect(self._on_control_changed)

        self.cmb_preset = QComboBox()
        for preset_name in EQ_PRESETS:
            self.cmb_preset.addItem(preset_name)
        self.cmb_preset.addItem("Personalizado")
        if preset in EQ_PRESETS:
            self.cmb_preset.setCurrentText(preset)
        else:
            self.cmb_preset.setCurrentText("Personalizado")
        self.cmb_preset.currentTextChanged.connect(self._on_preset_selected)

        top_row.addWidget(self.chk_enable)
        top_row.addStretch()
        top_row.addWidget(self.cmb_preset)
        layout.addLayout(top_row)

        # Curva de respuesta
        self.curve_widget = EQCurvePreview()
        self.curve_widget.set_state(enabled, self.gains)
        layout.addWidget(self.curve_widget)

        # 5 Sliders verticales (-12 dB a +12 dB)
        sliders_row = QHBoxLayout()
        sliders_row.setSpacing(6)
        self.sliders: list[QSlider] = []
        self.val_labels: list[QLabel] = []

        for i, band in enumerate(EQ_BANDS):
            col = QVBoxLayout()
            col.setSpacing(3)

            lbl_val = QLabel(f"{self.gains[i]:+.1f} dB")
            lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_val.setStyleSheet("color: #1DB954; font-size: 10px; font-weight: bold;")

            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(-120, 120)  # décimas de dB
            slider.setValue(int(round(self.gains[i] * 10)))
            slider.setFixedHeight(92)
            slider.valueChanged.connect(self._on_slider_moved)
            slider.sliderReleased.connect(self._emit_eq_change)

            lbl_freq = QLabel(band["label"])
            lbl_freq.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_freq.setStyleSheet("color: #B3B3B3; font-size: 10px;")

            col.addWidget(lbl_val)
            col.addWidget(slider, alignment=Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(lbl_freq)

            self.sliders.append(slider)
            self.val_labels.append(lbl_val)
            sliders_row.addLayout(col)

        layout.addLayout(sliders_row)

        # Pie con estado y botón Reset
        bottom_row = QHBoxLayout()
        self.lbl_status = QLabel("EQ de 5 bandas listo")
        self.lbl_status.setStyleSheet("color: #8E8E93; font-size: 11px;")

        btn_reset = QPushButton("Restablecer")
        btn_reset.setObjectName("GhostSmallButton")
        btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset.clicked.connect(lambda: self.cmb_preset.setCurrentText("Plano"))

        bottom_row.addWidget(self.lbl_status)
        bottom_row.addStretch()
        bottom_row.addWidget(btn_reset)
        layout.addLayout(bottom_row)

    def _on_preset_selected(self, preset_name: str) -> None:
        if self._updating_ui or preset_name not in EQ_PRESETS:
            return
        self._updating_ui = True
        target_gains = EQ_PRESETS[preset_name]
        for i, g in enumerate(target_gains):
            self.sliders[i].setValue(int(round(g * 10)))
            self.val_labels[i].setText(f"{g:+.1f} dB")
        self.gains = list(target_gains)
        self.curve_widget.set_state(self.chk_enable.isChecked(), self.gains)
        self._updating_ui = False
        self._emit_eq_change()

    def _on_slider_moved(self) -> None:
        if self._updating_ui:
            return
        self.gains = [s.value() / 10.0 for s in self.sliders]
        for i, g in enumerate(self.gains):
            self.val_labels[i].setText(f"{g:+.1f} dB")
        self.curve_widget.set_state(self.chk_enable.isChecked(), self.gains)

        self._updating_ui = True
        self.cmb_preset.setCurrentText("Personalizado")
        self._updating_ui = False

    def _on_control_changed(self) -> None:
        self.curve_widget.set_state(self.chk_enable.isChecked(), self.gains)
        self._emit_eq_change()

    def _emit_eq_change(self) -> None:
        self.eq_changed.emit(
            self.chk_enable.isChecked(),
            self.cmb_preset.currentText(),
            list(self.gains),
        )

    def set_status_text(self, text: str) -> None:
        self.lbl_status.setText(text)
