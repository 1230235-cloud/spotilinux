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
        self.bpm: float = 0.0
        self.beat_pos: float = 0.0
        self.beat_frac: float = 0.0   # 0 en el beat, →1 antes del siguiente
        self.beat_onset: float = 0.0
        self._playing: bool = False

    def set_beat(self, bpm: float, beat_pos: float, confidence: float, onset: float) -> None:
        """Sincroniza el pulso visual con la rejilla de beats real de la canción."""
        self._playing = bpm > 0
        self.bpm = bpm
        self.beat_pos = beat_pos
        self.beat_frac = (beat_pos - math.floor(beat_pos)) if bpm > 0 else 0.0
        self.beat_onset = max(onset, self.beat_onset * 0.80)

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
                self.peaks[i] = max(0.0, self.peaks[i] - 0.016)

        energy = float(np.mean(self.bars)) if len(self.bars) else 0.0
        self._angle = (self._angle + 0.30 + energy * 0.45) % 360.0
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

        beat_kick = (1.0 - self.beat_frac) ** 2.6 if self._playing else 0.0
        bass_e = float(np.mean(self.bars[:8])) if len(self.bars) >= 8 else 0.0
        mid_e = float(np.mean(self.bars[8:20])) if len(self.bars) >= 20 else 0.0
        high_e = float(np.mean(self.bars[20:])) if len(self.bars) > 20 else 0.0

        # Fondo oscuro redondeado con resplandor sutil al golpe del beat
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, w, h), 10.0, 10.0)
        grad_bg = QLinearGradient(0, 0, 0, h)
        top_glow = int(min(45, (beat_kick * 18 + bass_e * 22)))
        grad_bg.setColorAt(0.0, QColor(18, 24 + top_glow // 2, 24))
        grad_bg.setColorAt(1.0, QColor(10, 11, 14))
        painter.fillPath(bg_path, grad_bg)

        # Rejilla horizontal muy tenue (da lectura de nivel, aspecto de analizador profesional)
        painter.setPen(QPen(QColor(255, 255, 255, 13), 1.0))
        for frac in (0.25, 0.5, 0.75):
            gy = 26.0 + (h - 40.0) * frac
            painter.drawLine(QPointF(12.0, gy), QPointF(w - 12.0, gy))
        # Borde fino reactivo al beat
        border_alpha = int(22 + 55 * beat_kick) if self._playing else 18
        painter.setPen(QPen(QColor(30, 215, 96, border_alpha), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 10.0, 10.0)

        if self.mode == "bars":
            self._draw_bars(painter, w, h, beat_kick, bass_e, mid_e, high_e)
        elif self.mode == "wave":
            self._draw_wave(painter, w, h, beat_kick, bass_e, mid_e, high_e)
        else:
            self._draw_radial(painter, w, h, beat_kick, bass_e, mid_e, high_e)

        # HUD superior izquierdo: verificación en vivo de BPM, compás 1-2-3-4 y respuesta por banda
        font = QFont("Sans Serif", 8, QFont.Weight.Bold)
        painter.setFont(font)
        if self._playing and self.bpm > 0:
            painter.setPen(QColor(30, 215, 96, 225))
            bpm_txt = f"♩ {int(round(self.bpm))} BPM"
            painter.drawText(QRectF(12, 6, 90, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, bpm_txt)
            beat_in_bar = int(math.floor(self.beat_pos)) % 4
            for b in range(4):
                dx = 78.0 + b * 10.0
                dy = 14.0
                active_b = (b == beat_in_bar)
                painter.setPen(Qt.PenStyle.NoPen)
                if active_b:
                    painter.setBrush(QColor(0, 242, 254) if b == 0 else QColor(30, 215, 96))
                    painter.drawEllipse(QPointF(dx, dy), 3.4, 3.4)
                else:
                    painter.setBrush(QColor(255, 255, 255, 45))
                    painter.drawEllipse(QPointF(dx, dy), 2.1, 2.1)
        else:
            painter.setPen(QColor(145, 145, 155, 150))
            painter.drawText(QRectF(12, 6, 120, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "♩ EN ESPERA")

        # Etiqueta del modo en la esquina superior derecha
        mode_label = dict(self.MODES).get(self.mode, "FFT")
        painter.setPen(QColor(185, 195, 205, 185))
        painter.drawText(QRectF(10, 6, w - 20, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"● {mode_label.upper()}")

    def _draw_bars(
        self,
        painter: QPainter,
        w: float,
        h: float,
        beat_kick: float,
        bass_e: float,
        mid_e: float,
        high_e: float,
    ) -> None:
        n = len(self.bars)
        if n == 0:
            return
        pad_x = 14.0
        pad_top = 24.0
        pad_bottom = 14.0
        usable_w = max(10.0, w - pad_x * 2)
        usable_h = max(10.0, h - pad_top - pad_bottom)

        gap = 3.2
        bar_w = max(2.5, (usable_w - gap * (n - 1)) / n)

        # Línea base con pulso en cada beat
        base_alpha = int(45 + 120 * beat_kick) if self._playing else 40
        painter.setPen(QPen(QColor(30, 215, 96, base_alpha), 1.5))
        painter.drawLine(QPointF(pad_x, h - pad_bottom + 1.0), QPointF(w - pad_x, h - pad_bottom + 1.0))

        for i in range(n):
            val = float(np.clip(self.bars[i], 0.03, 1.0))
            bh = val * usable_h
            x = pad_x + i * (bar_w + gap)
            y = h - pad_bottom - bh

            # El gradiente de cada barra cambia dinámicamente con su altura, zona de frecuencia y el golpe del beat
            bar_grad = QLinearGradient(0, y, 0, h - pad_bottom)
            zone = i / max(1, n - 1)
            if val > 0.78:
                # Picos intensos iluminan en cian/rosa imperial
                bar_grad.setColorAt(0.0, QColor(255, 75, 130) if zone < 0.3 else QColor(90, 250, 255))
                bar_grad.setColorAt(0.45, QColor(30, 215, 96))
                bar_grad.setColorAt(1.0, QColor(18, 105, 52))
            elif zone < 0.28:
                # Graves (Sub-bass / Kick)
                bar_grad.setColorAt(0.0, QColor(38, 238, 118))
                bar_grad.setColorAt(1.0, QColor(16, 98, 48))
            elif zone < 0.68:
                # Medios (Voces / Sintetizadores)
                bar_grad.setColorAt(0.0, QColor(0, 228, 245))
                bar_grad.setColorAt(0.55, QColor(29, 185, 84))
                bar_grad.setColorAt(1.0, QColor(16, 92, 54))
            else:
                # Agudos (Hi-hats / Brillo)
                bar_grad.setColorAt(0.0, QColor(120, 248, 255))
                bar_grad.setColorAt(1.0, QColor(22, 132, 145))

            rect = QRectF(x, y, bar_w, bh)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bar_grad)
            painter.drawRoundedRect(rect, 2.5, 2.5)

            # Reflejo tenue bajo la línea base
            refl_h = min(bh * 0.28, pad_bottom - 3.0)
            if refl_h > 1.0:
                painter.setBrush(QColor(30, 215, 96, 42))
                painter.drawRoundedRect(QRectF(x, h - pad_bottom + 2.0, bar_w, refl_h), 2.0, 2.0)

            # Indicador de pico flotante
            pk = float(np.clip(self.peaks[i], 0.03, 1.0))
            py = h - pad_bottom - pk * usable_h - 3.2
            painter.setBrush(QColor(255, 120, 165, 230) if pk > 0.82 else QColor(210, 255, 235, 215))
            painter.drawRoundedRect(QRectF(x, max(pad_top - 4, py), bar_w, 2.4), 1.0, 1.0)

    def _draw_wave(
        self,
        painter: QPainter,
        w: float,
        h: float,
        beat_kick: float,
        bass_e: float,
        mid_e: float,
        high_e: float,
    ) -> None:
        pts = self.waveform
        n = len(pts)
        if n < 2:
            return

        mid_y = h * 0.56
        amp = h * (0.34 + 0.07 * beat_kick)

        path = QPainterPath()
        mirror_path = QPainterPath()
        fill_path = QPainterPath()
        fill_path.moveTo(10.0, mid_y)

        # Modulamos la onda estabilizada con la envolvente de las 32 barras para que su silueta refleje
        # tanto la forma de onda real como el balance graves/medios/agudos de la canción
        nb = len(self.bars)
        for i, v in enumerate(pts):
            u = i / (n - 1)
            x = 10.0 + u * (w - 20.0)
            b_idx = min(nb - 1, int(u * nb)) if nb > 0 else 0
            band_env = 0.45 + 0.75 * float(self.bars[b_idx]) if nb > 0 else 1.0
            val = float(v) * band_env
            y = mid_y - val * amp
            ym = mid_y + val * amp * 0.48
            if i == 0:
                path.moveTo(x, y)
                mirror_path.moveTo(x, ym)
            else:
                path.lineTo(x, y)
                mirror_path.lineTo(x, ym)
            fill_path.lineTo(x, y)

        fill_path.lineTo(w - 10.0, mid_y)
        fill_path.closeSubpath()

        grad = QLinearGradient(0, mid_y - amp, 0, mid_y + amp)
        grad.setColorAt(0.0, QColor(0, 242, 254, int(105 + 60 * beat_kick)))
        grad.setColorAt(0.5, QColor(29, 185, 84, 40))
        grad.setColorAt(1.0, QColor(162, 32, 68, int(85 + 50 * bass_e)))
        painter.fillPath(fill_path, grad)

        # Onda espejo secundaria
        painter.setPen(QPen(QColor(0, 210, 255, 85), 1.5))
        painter.drawPath(mirror_path)

        # Línea de brillo exterior
        glow_pen = QPen(QColor(29, 185, 84, int(90 + 70 * beat_kick)), 5.0)
        painter.setPen(glow_pen)
        painter.drawPath(path)

        # Línea principal nítida
        core_pen = QPen(QColor(155, 255, 215), 2.2)
        painter.setPen(core_pen)
        painter.drawPath(path)

    def _draw_radial(
        self,
        painter: QPainter,
        w: float,
        h: float,
        beat_kick: float,
        bass_e: float,
        mid_e: float,
        high_e: float,
    ) -> None:
        cx, cy = w * 0.5, h * 0.56
        base_r = min(w, h) * 0.21
        max_len = min(w, h) * 0.24

        energy = float(np.mean(self.bars)) if len(self.bars) else 0.0
        pulse_r = base_r * (1.0 + bass_e * 0.16 + beat_kick * 0.14 + self.beat_onset * 0.06)

        # Halo radial reactivo
        halo = QRadialGradient(QPointF(cx, cy), pulse_r + max_len)
        halo.setColorAt(0.0, QColor(30, 215, 96, int(85 + 75 * beat_kick)))
        halo.setColorAt(0.60, QColor(0, 210, 255, int(35 + 45 * energy)))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(cx, cy), pulse_r + max_len, pulse_r + max_len)

        # Rayos de espectro alrededor del círculo + puntos de pico flotantes
        n = len(self.bars)
        for i in range(n):
            angle = (i / n) * 2.0 * math.pi + math.radians(self._angle * 0.45)
            val = float(self.bars[i])
            pk = float(self.peaks[i])
            r1 = pulse_r + 2.5
            r2 = r1 + max(2.5, val * max_len)
            r_pk = r1 + max(4.0, pk * max_len + 3.0)

            x1 = cx + math.cos(angle) * r1
            y1 = cy + math.sin(angle) * r1
            x2 = cx + math.cos(angle) * r2
            y2 = cy + math.sin(angle) * r2

            hue = (0.36 + (i / n) * 0.22 + beat_kick * 0.04) % 1.0
            color = QColor.fromHsvF(hue, 0.85, 0.98)
            painter.setPen(QPen(color, 3.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            # Punto de pico exterior
            px = cx + math.cos(angle) * r_pk
            py = cy + math.sin(angle) * r_pk
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(220, 255, 245, 200))
            painter.drawEllipse(QPointF(px, py), 1.6, 1.6)

        # Disco central con anillo doble que late al compás
        painter.setPen(QPen(QColor(30, 215, 96, int(180 + 70 * beat_kick)), 2.2))
        painter.setBrush(QColor(16, 18, 22))
        painter.drawEllipse(QPointF(cx, cy), pulse_r, pulse_r)
        inner_r = max(3.5, (4.5 + 4.5 * beat_kick + 3.5 * bass_e))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 242, 254) if beat_kick > 0.55 else QColor(30, 215, 96))
        painter.drawEllipse(QPointF(cx, cy), inner_r, inner_r)


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
