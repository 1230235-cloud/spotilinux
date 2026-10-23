#!/usr/bin/env python3
"""
Widget animado de Maomao (Kusuriya no Hitorigoto / The Apothecary Diaries)
que baila en tiempo real al compás de la música (bajos, medios y agudos FFT).
"""

import math
import time
from typing import Optional
import numpy as np
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget


class MaomaoDancerWidget(QWidget):
    """
    Renderiza una figura chibi detallada de Maomao que reacciona en tiempo real
    al espectro de frecuencias:
      - Bajos (60-230 Hz): rebote rítmico de rodillas/cuerpo, físicas de moños y paso de baile.
      - Medios (230 Hz-3.6 kHz): balanceo de brazos/mangas de hanfu, giro de cadera e inclinación de cabeza.
      - Agudos (3.6-14 kHz): notas musicales flotantes, destellos, hierbas medicinales y expresiones (:3).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("¡Haz clic en Maomao para cambiar su estilo de baile! 🌿")

        self.is_playing: bool = False
        self.bass_energy: float = 0.0
        self.mid_energy: float = 0.0
        self.high_energy: float = 0.0
        self.overall_energy: float = 0.0

        self.phase: float = 0.0          # reloj de animación en reposo (respiración)
        self.beat_pulse: float = 0.0     # acento de golpe (0..1) a partir de la envolvente real
        self.dance_style: int = 0  # 0: Baile Imperial, 1: Fiesta de Hierbas, 2: Giro Neko :3
        self.STYLE_NAMES = ["Baile Imperial", "Fiesta de Hierbas 🌿", "Modo Neko :3 🐾"]

        # Ritmo detectado (lo entrega AudioEngine.beat_info): baila sobre la rejilla de beats
        self.bpm: float = 0.0
        self.beat_pos: float = 0.0       # posición continua en beats
        self.beat_conf: float = 0.0
        self.amp: float = 0.0            # amplitud de movimiento suavizada (0..1)
        self._fallback_t0: float = time.monotonic()

        self._blink_timer: float = 0.0

    def next_dance_style(self) -> str:
        self.dance_style = (self.dance_style + 1) % len(self.STYLE_NAMES)
        self.update()
        return self.STYLE_NAMES[self.dance_style]

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.next_dance_style()
        super().mousePressEvent(event)

    def update_from_spectrum(self, bars: np.ndarray, is_playing: bool) -> None:
        """Energías por rango de frecuencia (solo para brillo, moños y expresión; el ritmo viene de set_beat)."""
        self.is_playing = is_playing
        if not is_playing or len(bars) == 0:
            self.bass_energy *= 0.85
            self.mid_energy *= 0.85
            self.high_energy *= 0.85
            self.overall_energy *= 0.85
            self.amp *= 0.90
            self.phase = (self.phase + 0.04) % (2 * math.pi * 100)
            self._blink_timer = (self._blink_timer + 0.04) % 12.0
            self.update()
            return

        bass = float(np.mean(bars[:7]))
        mid = float(np.mean(bars[7:20]))
        high = float(np.mean(bars[20:]))
        self.bass_energy = self.bass_energy * 0.6 + bass * 0.4
        self.mid_energy = self.mid_energy * 0.6 + mid * 0.4
        self.high_energy = self.high_energy * 0.6 + high * 0.4
        self.overall_energy = self.bass_energy * 0.45 + self.mid_energy * 0.35 + self.high_energy * 0.20
        # La amplitud sigue la sonoridad general con inercia (no cada golpe individual)
        target = min(1.0, self.overall_energy * 1.5)
        self.amp += (target - self.amp) * 0.08
        self._blink_timer = (self._blink_timer + 0.05) % 10.0

    def set_beat(self, bpm: float, beat_pos: float, confidence: float, onset: float) -> None:
        """Recibe la posición en beats sincronizada con la reproducción y repinta."""
        self.bpm = bpm
        self.beat_pos = beat_pos
        self.beat_conf = confidence
        self.beat_pulse = max(onset, self.beat_pulse * 0.80)
        self.update()

    # ---------------- Coreografía: todo en función de la posición en beats ---------------- #

    def _dance_pose(self) -> dict:
        """Calcula la pose a partir de la posición en beats. Movimiento suave y periódico."""
        if not self.is_playing:
            return {
                "lift": math.sin(self.phase) * 1.6, "sway": math.sin(self.phase * 0.5) * 2.0,
                "head": math.sin(self.phase * 0.5) * 2.5, "arm_l": math.sin(self.phase) * 4.0,
                "arm_r": -math.sin(self.phase) * 4.0, "step_l": 0.0, "step_r": 0.0,
                "skirt": 0.0, "frac": 0.0, "beat_idx": 0, "db": 0.0, "bob": 0.0, "dancing": False,
            }

        bpm = self.bpm
        if bpm > 0:
            b = self.beat_pos
        else:
            # Aún analizando la canción: balanceo suave a 100 BPM para no quedarse quieta
            bpm = 100.0
            b = (time.monotonic() - self._fallback_t0) * bpm / 60.0

        # Normalizar a un rango cómodo de baile (70-140 BPM): canciones rápidas = medio tiempo
        db = b
        if bpm > 142.0:
            db = b / 2.0
        elif bpm < 72.0:
            db = b * 2.0

        frac = db - math.floor(db)
        k = 0.50 + 0.50 * min(1.0, self.amp * 1.2)      # intensidad 0.5..1.0
        if self.bpm > 0:
            k *= 0.80 + 0.20 * self.beat_conf            # menos confianza = movimiento más sobrio
        section = int(math.floor(db / 8.0)) % 2          # cada 8 tiempos cambia el patrón de brazos

        # Rebote: aterriza justo en el beat (lift = 0) y sube entre beats
        lift = (math.sin(math.pi * frac) ** 0.85) * (3.5 + 7.0 * k)
        # Balanceo lateral: izquierda en tiempos pares, derecha en impares
        sway = math.sin(math.pi * db) * (5.0 + 8.0 * k)
        head = math.sin(math.pi * (db - 0.25)) * (3.0 + 5.0 * k)
        skirt = math.sin(math.pi * (db - 0.2)) * (3.0 + 6.0 * k)
        step_l = max(0.0, math.sin(math.pi * db)) * (3.0 + 5.0 * k)
        step_r = max(0.0, -math.sin(math.pi * db)) * (3.0 + 5.0 * k)

        pulse = math.exp(-frac * 3.4)  # golpe corto justo en el beat
        if self.dance_style == 0:      # Imperial: brazos fluidos y elegantes
            a = 16.0 + 18.0 * k
            arm_l = math.sin(math.pi * db + 0.5) * a
            arm_r = math.sin(math.pi * db + math.pi + 0.5) * a if section == 0 else math.sin(math.pi * db + 0.5) * -a
            bob = 0.0
        elif self.dance_style == 1:    # Hierbas: brazos que bombean con el beat
            a = 20.0 + 24.0 * k
            on_even = (int(math.floor(db)) % 2 == 0)
            if section == 0:
                arm_l = a * pulse if on_even else a * 0.25
                arm_r = a * 0.25 if on_even else a * pulse
            else:
                arm_l = a * pulse
                arm_r = -a * pulse
            bob = 0.0
        else:                          # Neko: cabeceo al doble de velocidad y brazos cortos
            a = 12.0 + 14.0 * k
            arm_l = math.sin(2 * math.pi * db) * a
            arm_r = -math.sin(2 * math.pi * db) * a
            bob = math.sin(2 * math.pi * db) * (3.0 + 3.5 * k)
            head += bob

        return {
            "lift": lift, "sway": sway, "head": head, "arm_l": arm_l, "arm_r": arm_r,
            "step_l": step_l, "step_r": step_r, "skirt": skirt, "frac": frac,
            "beat_idx": int(math.floor(b)) % 4, "db": db, "bob": bob, "dancing": True,
        }

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # 1. Fondo de escenario con halo reactivo al bajo
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, w, h), 10.0, 10.0)
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, QColor(20, 32, 28))
        bg_grad.setColorAt(1.0, QColor(12, 14, 16))
        painter.fillPath(bg_path, bg_grad)

        cx = w * 0.5
        floor_y = h - 14.0

        # Halo luminoso detrás de Maomao
        halo_r = min(w, h) * (0.30 + self.bass_energy * 0.20)
        halo = QRadialGradient(QPointF(cx, floor_y - 58.0), max(10.0, halo_r))
        halo.setColorAt(0.0, QColor(29, 185, 84, int(55 + self.bass_energy * 120)))
        halo.setColorAt(0.55, QColor(175, 45, 95, int(30 + self.mid_energy * 85)))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(cx, floor_y - 58.0), halo_r, halo_r)

        # Sombra y ondas en el suelo al compás
        shadow_w = 38.0 + self.bass_energy * 18.0
        painter.setBrush(QColor(0, 0, 0, 130))
        painter.drawEllipse(QPointF(cx, floor_y + 2), shadow_w, 6.5)

        if self.is_playing:
            # Onda en el suelo que nace en cada beat y se expande suavemente
            pose_f = self._dance_pose()["frac"]
            ring_w = shadow_w + 6.0 + pose_f * 40.0
            alpha = int(150 * (1.0 - pose_f) ** 1.6 * (0.4 + 0.6 * self.amp))
            painter.setPen(QPen(QColor(29, 185, 84, alpha), 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, floor_y + 2), ring_w, 3.0 + pose_f * 7.0)

        # 2. Parámetros cinemáticos del baile de Maomao (sincronizados con el compás)
        pose = self._dance_pose()
        bounce = pose["lift"]
        sway_x = pose["sway"]
        head_tilt = pose["head"]
        arm_wave_l = pose["arm_l"]
        arm_wave_r = pose["arm_r"]
        step_l = pose["step_l"]
        step_r = pose["step_r"]
        skirt_sway_v = pose["skirt"]
        beat_frac = pose["frac"]

        body_x = cx + sway_x
        base_y = floor_y - bounce

        # Escala adaptable a la altura del widget dejando margen superior para el título
        scale = min(w / 240.0, (h - 32.0) / 195.0)
        painter.save()
        painter.translate(body_x, base_y)
        painter.scale(scale, scale)

        # ---------------- PIES / ZAPATOS ---------------- #
        painter.setPen(QPen(QColor(25, 20, 28), 1.8))
        painter.setBrush(QColor(55, 38, 48))
        painter.drawRoundedRect(QRectF(-20.0, -10.0 - step_l, 14.0, 9.0), 4.0, 4.0)
        painter.drawRoundedRect(QRectF(6.0, -10.0 - step_r, 14.0, 9.0), 4.0, 4.0)

        # ---------------- FALDA LARGA CARMESÍ (HANFU) ---------------- #
        skirt_sway = skirt_sway_v
        skirt_path = QPainterPath()
        skirt_path.moveTo(-18.0, -56.0)
        skirt_path.lineTo(18.0, -56.0)
        skirt_path.quadTo(30.0 + skirt_sway, -28.0, 26.0 + skirt_sway, -8.0)
        skirt_path.lineTo(-26.0 + skirt_sway, -8.0)
        skirt_path.quadTo(-30.0 + skirt_sway, -28.0, -18.0, -56.0)

        skirt_grad = QLinearGradient(0, -56.0, 0, -8.0)
        skirt_grad.setColorAt(0.0, QColor(162, 32, 68))   # Carmesí Maomao
        skirt_grad.setColorAt(1.0, QColor(112, 18, 46))
        painter.setPen(QPen(QColor(68, 12, 30), 1.8))
        painter.setBrush(skirt_grad)
        painter.drawPath(skirt_path)

        # Pliegues de la falda
        painter.setPen(QPen(QColor(195, 65, 98, 140), 1.3))
        for px in (-10.0, 0.0, 10.0):
            painter.drawLine(QPointF(px * 0.7, -53.0), QPointF(px + skirt_sway * 0.7, -10.0))

        # ---------------- TORSO / CHAQUETA DE BOTICARIA VERDE JADE ---------------- #
        top_path = QPainterPath()
        top_path.addRoundedRect(QRectF(-19.0, -90.0, 38.0, 38.0), 8.0, 8.0)
        top_grad = QLinearGradient(0, -90.0, 0, -52.0)
        top_grad.setColorAt(0.0, QColor(42, 118, 102))  # Verde jade boticaria
        top_grad.setColorAt(1.0, QColor(28, 86, 74))
        painter.setPen(QPen(QColor(18, 55, 48), 1.8))
        painter.setBrush(top_grad)
        painter.drawPath(top_path)

        # Cuello cruzado blanco/crema del Hanfu
        painter.setPen(QPen(QColor(238, 235, 225), 3.0))
        painter.drawLine(QPointF(-10.0, -89.0), QPointF(4.0, -68.0))
        painter.drawLine(QPointF(10.0, -89.0), QPointF(-2.0, -73.0))

        # Cinturón / Faja con bolsita de hierbas medicinales
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(235, 210, 195))
        painter.drawRoundedRect(QRectF(-20.0, -60.0, 40.0, 7.0), 3.0, 3.0)
        painter.setBrush(QColor(218, 92, 122))
        painter.drawEllipse(QPointF(12.0, -54.0), 5.5, 6.5)

        # ---------------- MANGAS ANCHAS ANIMADAS AL COMPÁS ---------------- #
        # Brazo izquierdo
        painter.save()
        painter.translate(-18.0, -83.0)
        base_angle_l = 35.0 if self.dance_style == 0 else (65.0 if self.dance_style == 1 else 50.0)
        painter.rotate(-base_angle_l + arm_wave_l)
        sleeve_l = QPainterPath()
        sleeve_l.moveTo(0, -5)
        sleeve_l.quadTo(-26, -8, -32, 6)
        sleeve_l.quadTo(-22, 18, 0, 8)
        sleeve_l.closeSubpath()
        painter.setPen(QPen(QColor(18, 55, 48), 1.6))
        painter.setBrush(QColor(46, 126, 108))
        painter.drawPath(sleeve_l)
        # Mano izquierda (con vendas sutiles y ramita medicinal en modo fiesta)
        painter.setBrush(QColor(255, 226, 206))
        painter.drawEllipse(QPointF(-32, 3), 5.5, 5.5)
        if self.dance_style == 1:
            painter.setPen(QPen(QColor(46, 213, 115), 2.2))
            painter.drawLine(QPointF(-34, 2), QPointF(-46, -10))
            painter.setBrush(QColor(46, 213, 115))
            painter.drawEllipse(QPointF(-46, -12), 5.0, 3.0)
            painter.drawEllipse(QPointF(-40, -7), 4.0, 2.5)
        painter.restore()

        # Brazo derecho
        painter.save()
        painter.translate(18.0, -83.0)
        base_angle_r = 35.0 if self.dance_style == 0 else (65.0 if self.dance_style == 1 else 50.0)
        painter.rotate(base_angle_r + arm_wave_r)
        sleeve_r = QPainterPath()
        sleeve_r.moveTo(0, -5)
        sleeve_r.quadTo(26, -8, 32, 6)
        sleeve_r.quadTo(22, 18, 0, 8)
        sleeve_r.closeSubpath()
        painter.setPen(QPen(QColor(18, 55, 48), 1.6))
        painter.setBrush(QColor(46, 126, 108))
        painter.drawPath(sleeve_r)
        painter.setBrush(QColor(255, 226, 206))
        painter.drawEllipse(QPointF(32, 3), 5.5, 5.5)
        painter.restore()

        # ---------------- CABEZA, ROSTRO Y CABELLO DE MAOMAO ---------------- #
        painter.save()
        painter.translate(0.0, -94.0)
        painter.rotate(head_tilt)

        # Orejas Neko opcionales en el estilo 3 ("Modo Neko :3")
        if self.dance_style == 2:
            ear_bounce = self.beat_pulse * 5.0
            painter.setPen(QPen(QColor(18, 52, 38), 1.8))
            painter.setBrush(QColor(30, 88, 62))
            left_ear = QPainterPath()
            left_ear.moveTo(-26, -38)
            left_ear.lineTo(-34, -58 - ear_bounce)
            left_ear.lineTo(-12, -45)
            left_ear.closeSubpath()
            right_ear = QPainterPath()
            right_ear.moveTo(26, -38)
            right_ear.lineTo(34, -58 - ear_bounce)
            right_ear.lineTo(12, -45)
            right_ear.closeSubpath()
            painter.drawPath(left_ear)
            painter.drawPath(right_ear)

        # Cabello trasero verde oscuro
        painter.setPen(QPen(QColor(16, 48, 34), 1.8))
        painter.setBrush(QColor(26, 78, 55))
        painter.drawEllipse(QPointF(0, -24), 33.0, 31.0)

        # Dos moños laterales característicos de Maomao con cintas rosas/coral (rebotan con el bajo)
        bun_offset = (self.beat_pulse * 5.0 + self.bass_energy * 2.0) if self.is_playing else 0.0
        for sign in (-1.0, 1.0):
            bx = sign * 33.0
            by = -25.0 - bun_offset
            # Cintas rosas
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(238, 108, 138))
            painter.drawEllipse(QPointF( sign * 26.0, by), 7.5, 7.5)
            # Moño de cabello verde oscuro
            painter.setPen(QPen(QColor(14, 44, 30), 1.8))
            painter.setBrush(QColor(30, 88, 62))
            painter.drawEllipse(QPointF(bx, by), 11.5, 11.5)
            # Mechón suelto colgando
            painter.drawRoundedRect(QRectF(sign * 27.0 - 3.0, -14.0, 6.0, 18.0), 3.0, 3.0)

        # Rostro
        painter.setPen(QPen(QColor(215, 178, 158), 1.5))
        painter.setBrush(QColor(255, 232, 214))
        painter.drawRoundedRect(QRectF(-26.0, -46.0, 52.0, 44.0), 20.0, 20.0)

        # Ojos grandes color índigo/violeta de Maomao (o parpadeo)
        blinking = self._blink_timer > 9.6
        for sign in (-1.0, 1.0):
            ex = sign * 11.5
            ey = -22.0
            if blinking:
                painter.setPen(QPen(QColor(45, 35, 50), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(QPointF(ex - 5.5, ey), QPointF(ex + 5.5, ey))
            else:
                # Esclerótica y pestañas superiores
                painter.setPen(QPen(QColor(32, 24, 38), 2.2))
                painter.setBrush(QColor(255, 255, 255))
                painter.drawEllipse(QPointF(ex, ey), 6.2, 6.8)
                # Iris violeta/azul profundo
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(92, 72, 168))
                painter.drawEllipse(QPointF(ex, ey + 0.5), 4.6, 5.2)
                painter.setBrush(QColor(38, 26, 82))
                painter.drawEllipse(QPointF(ex, ey + 0.8), 2.5, 2.8)
                # Brillo ocular (crece con los agudos)
                spark = 1.8 + self.high_energy * 1.2
                painter.setBrush(QColor(255, 255, 255))
                painter.drawEllipse(QPointF(ex - 1.6, ey - 1.8), spark, spark)

        # Rubor y las pecas icónicas de Maomao sobre la nariz y mejillas
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(245, 145, 155, 110))
        painter.drawEllipse(QPointF(-15.5, -14.5), 5.0, 2.8)
        painter.drawEllipse(QPointF(15.5, -14.5), 5.0, 2.8)

        painter.setBrush(QColor(185, 118, 96, 200))
        for fx, fy in [(-14, -15), (-11, -13), (-16, -13), (14, -15), (11, -13), (16, -13), (-2, -16), (2, -16)]:
            painter.drawEllipse(QPointF(fx, fy), 1.0, 1.0)

        # Boca expresiva (sonrisa :3 o cantando con el ritmo)
        painter.setPen(QPen(QColor(145, 70, 75), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.is_playing and (self.overall_energy > 0.36 or self.dance_style == 2):
            # Expresión felina :3 característica de Maomao cuando descubre venenos/hierbas o buena música
            cat_mouth = QPainterPath()
            cat_mouth.moveTo(-5.0, -9.5)
            cat_mouth.quadTo(-2.5, -6.5, 0.0, -9.5)
            cat_mouth.quadTo(2.5, -6.5, 5.0, -9.5)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(cat_mouth)
        elif self.is_playing and self.bass_energy > 0.25:
            painter.setBrush(QColor(215, 95, 108))
            painter.drawEllipse(QPointF(0.0, -9.0), 3.2, 2.5 + self.bass_energy * 2.5)
        else:
            painter.drawLine(QPointF(-3.5, -9.5), QPointF(3.5, -9.5))

        # Flequillo característico verde esmeralda oscuro partido al centro
        bangs = QPainterPath()
        bangs.moveTo(-27.0, -30.0)
        bangs.quadTo(-22.0, -52.0, 0.0, -50.0)
        bangs.quadTo(22.0, -52.0, 27.0, -30.0)
        bangs.lineTo(20.0, -24.0)
        bangs.lineTo(13.0, -34.0)
        bangs.lineTo(5.0, -24.0)
        bangs.lineTo(0.0, -37.0)
        bangs.lineTo(-5.0, -24.0)
        bangs.lineTo(-13.0, -34.0)
        bangs.lineTo(-20.0, -24.0)
        bangs.closeSubpath()
        painter.setPen(QPen(QColor(14, 44, 30), 1.6))
        painter.setBrush(QColor(34, 96, 68))
        painter.drawPath(bangs)

        painter.restore()  # Fin cabeza
        painter.restore()  # Fin transformación cuerpo

        # 3. Notas musicales flotantes: una nueva sale en cada beat y sube con calma
        if self.is_playing:
            font_notes = QFont("Sans Serif", 11, QFont.Weight.Bold)
            painter.setFont(font_notes)
            symbols = ["♪", "♫", "🌿", "✦"]
            base = pose["db"]
            for i, sym in enumerate(symbols):
                life = ((base * 0.25) + i * 0.25) % 1.0   # cada símbolo vive 4 tiempos
                nx = cx + math.sin((base * 0.25 + i * 0.25) * 2 * math.pi) * (46.0 + i * 8.0)
                ny = (floor_y - 55.0) - life * 100.0
                alpha = int(210 * math.sin(life * math.pi) * (0.35 + 0.65 * self.amp))
                color = QColor(29, 185, 84, alpha) if i % 2 == 0 else QColor(245, 130, 165, alpha)
                painter.setPen(color)
                painter.drawText(QPointF(nx, ny), sym)

        # 4. Etiqueta superior e indicador de estilo
        painter.setPen(QColor(210, 235, 220, 195))
        painter.setFont(QFont("Sans Serif", 8, QFont.Weight.Bold))
        status_txt = f"🌿 {self.STYLE_NAMES[self.dance_style].upper()}"
        painter.drawText(QRectF(10, 6, w - 20, 16), Qt.AlignmentFlag.AlignLeft, status_txt)
        painter.setPen(QColor(29, 185, 84, 215) if self.is_playing else QColor(150, 150, 160, 160))
        if self.is_playing:
            bpm_txt = f"♩ {self.bpm:.0f} BPM" if self.bpm > 0 else "♩ analizando…"
            painter.drawText(QRectF(10, 6, w - 20, 16), Qt.AlignmentFlag.AlignRight, bpm_txt)
            # Cuatro puntos de compás: el activo se ilumina en cada beat
            bi = pose["beat_idx"]
            for i in range(4):
                active = (i == bi)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(29, 185, 84, 235) if active else QColor(90, 100, 96, 150))
                r_dot = 3.2 + (1.4 * (1.0 - beat_frac) if active else 0.0)
                painter.drawEllipse(QPointF(w - 14.0 - (3 - i) * 11.0, 28.0), r_dot, r_dot)
        else:
            painter.drawText(QRectF(10, 6, w - 20, 16), Qt.AlignmentFlag.AlignRight, "○ PAUSA")
