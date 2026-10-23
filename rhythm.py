#!/usr/bin/env python3
"""
Análisis musical de SpotiLinux (solo NumPy):

  1. analyze_rhythm(): detecta el tempo (BPM) y una rejilla de beats (con corrección de deriva)
     a partir del audio PCM completo. Maomao y las ondas se sincronizan con esa rejilla usando la
     posición exacta de reproducción, por eso bailan "al compás" y no según la energía del instante.
  2. SpectrumAnalyzer: espectro de 32 barras con escala logarítmica (dB), inclinación espectral y
     control de ganancia automático, para que las barras no se saturen ni se vean "locas".
"""

from __future__ import annotations

import numpy as np


# --------------------------------------------------------------------------- #
#                         DETECCIÓN DE TEMPO Y BEATS                          #
# --------------------------------------------------------------------------- #

def _onset_envelope(samples: np.ndarray, sr: int, hop: int = 256, win: int = 1024) -> tuple[np.ndarray, float]:
    """Envolvente de inicio de notas (spectral flux sobre magnitud comprimida). Devuelve (env, fps)."""
    x = samples.astype(np.float32)
    if len(x) < win * 4:
        return np.zeros(1, dtype=np.float32), sr / hop

    n_frames = (len(x) - win) // hop + 1
    hann = np.hanning(win).astype(np.float32)
    max_bin = int(4500.0 / (sr / win))  # solo hasta ~4.5 kHz: ahí vive el ritmo
    freqs = np.arange(max_bin) * (sr / win)
    weights = np.where(freqs < 220.0, 1.6, 1.0).astype(np.float32)  # el bombo pesa más

    prev = None
    flux = np.zeros(n_frames, dtype=np.float32)
    chunk = 1500
    for s in range(0, n_frames, chunk):
        e = min(n_frames, s + chunk)
        idx = (np.arange(s, e) * hop)[:, None] + np.arange(win)[None, :]
        mag = np.abs(np.fft.rfft(x[idx] * hann, axis=1))[:, :max_bin]
        logmag = np.log1p(mag * 25.0)
        if prev is not None:
            logmag_ext = np.vstack([prev, logmag])
        else:
            logmag_ext = np.vstack([logmag[:1], logmag])
        diff = np.maximum(0.0, np.diff(logmag_ext, axis=0)) * weights
        flux[s:e] = diff.sum(axis=1)
        prev = logmag[-1:]

    fps = sr / hop
    # Quitar la media local (~0.6 s) y normalizar
    k = max(3, int(fps * 0.6))
    kernel = np.ones(k, dtype=np.float32) / k
    local_mean = np.convolve(flux, kernel, mode="same")
    env = np.maximum(0.0, flux - local_mean)
    env_std = float(np.std(env)) + 1e-6
    env = env / env_std
    # Suavizado leve para tolerar pequeñas imprecisiones de tempo
    g = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    env = np.convolve(env, g, mode="same")
    return env.astype(np.float32), fps


def _comb_score(env: np.ndarray, fps: float, bpm: float, n_offsets: int) -> tuple[float, float]:
    period = fps * 60.0 / bpm
    n = int((len(env) - 2) / period)
    if n < 8:
        return 0.0, 0.0
    k = np.arange(n, dtype=np.float64)
    offs = np.linspace(0.0, period, n_offsets, endpoint=False)
    idx = np.clip((offs[:, None] + k[None, :] * period).astype(np.int64), 0, len(env) - 1)
    scores = env[idx].mean(axis=1)
    j = int(np.argmax(scores))
    return float(scores[j]), float(offs[j])


def analyze_rhythm(samples: np.ndarray, sr: int) -> dict:
    """
    Devuelve {bpm, confidence, beat_times (np.ndarray en segundos), env, fps}.
    beat_times es una rejilla de beats que se re-sincroniza localmente con el audio real.
    """
    env, fps = _onset_envelope(samples, sr)
    if len(env) < int(fps * 8):
        return {"bpm": 0.0, "confidence": 0.0, "beat_times": np.zeros(0), "env": env, "fps": fps}

    # --- 1) Búsqueda gruesa de tempo (68-180 BPM) con prior hacia ~110 BPM ---
    coarse = np.arange(68.0, 180.5, 0.5)
    prior_center = np.log2(110.0)
    best = (-1.0, 110.0, 0.0)
    for bpm in coarse:
        sc, off = _comb_score(env, fps, float(bpm), 24)
        prior = np.exp(-0.5 * ((np.log2(bpm) - prior_center) / 0.55) ** 2)
        total = sc * (0.55 + 0.45 * prior)
        if total > best[0]:
            best = (total, float(bpm), off)

    # --- 2) Refinamiento fino (±1 BPM, paso 0.05) ---
    bpm0 = best[1]
    fine_best = (-1.0, bpm0, best[2])
    for bpm in np.arange(bpm0 - 1.0, bpm0 + 1.0001, 0.05):
        sc, off = _comb_score(env, fps, float(bpm), 48)
        if sc > fine_best[0]:
            fine_best = (sc, float(bpm), off)
    _, bpm, off_frames = fine_best

    # --- 3) Rejilla de beats con re-sincronización local (evita deriva en canciones largas) ---
    period = fps * 60.0 / bpm
    total_frames = len(env)
    t = float(off_frames)
    beats = []
    half_win = max(1, int(period * 0.14))
    while t < total_frames - 1:
        beats.append(t)
        pred = t + period
        c = int(round(pred))
        lo, hi = max(0, c - half_win), min(total_frames, c + half_win + 1)
        if hi - lo > 2:
            seg = env[lo:hi]
            prox = np.exp(-0.5 * ((np.arange(lo, hi) - pred) / (half_win * 0.7 + 1e-6)) ** 2)
            j = int(np.argmax(seg * prox))
            shift = (lo + j - pred) * 0.30
        else:
            shift = 0.0
        t = pred + shift
    beat_frames = np.array(beats, dtype=np.float64)
    beat_times = beat_frames / fps

    # Retroceder la rejilla hacia el inicio por si el primer beat no estaba en t≈0
    period_s = 60.0 / bpm
    first = float(beat_times[0])
    pre = []
    while first - period_s > -period_s * 0.5:
        first -= period_s
        pre.append(first)
        if len(pre) > 64:
            break
    if pre:
        beat_times = np.concatenate([np.array(pre[::-1]), beat_times])

    # --- 4) Confianza: cuánto más fuerte es la envolvente justo en los beats que el promedio ---
    bi = np.clip(beat_frames.astype(np.int64), 0, total_frames - 1)
    ratio = float(np.mean(env[bi])) / (float(np.mean(env)) + 1e-6)
    confidence = float(np.clip((ratio - 1.0) / 1.2, 0.0, 1.0))

    return {"bpm": float(bpm), "confidence": confidence, "beat_times": beat_times, "env": env, "fps": float(fps)}


def beat_position(beat_times: np.ndarray, bpm: float, t: float) -> float:
    """Posición continua en beats (0.0 = primer beat; 1.0 = segundo...) para el instante t (s)."""
    n = len(beat_times)
    if n < 2 or bpm <= 0:
        return t * max(bpm, 90.0) / 60.0
    i = int(np.searchsorted(beat_times, t, side="right")) - 1
    if i < 0:
        return (t - beat_times[0]) * bpm / 60.0
    if i >= n - 1:
        return float(n - 1) + (t - beat_times[-1]) * bpm / 60.0
    span = beat_times[i + 1] - beat_times[i]
    return float(i) + (t - beat_times[i]) / max(span, 1e-3)


# --------------------------------------------------------------------------- #
#                              ESPECTRO DE BARRAS                             #
# --------------------------------------------------------------------------- #

class SpectrumAnalyzer:
    """FFT de 2048 puntos -> N barras logarítmicas (45 Hz - 9 kHz) en escala dB con AGC."""

    def __init__(self, n_bars: int = 32, win: int = 2048, fmin: float = 45.0, fmax: float = 9000.0) -> None:
        self.n_bars = n_bars
        self.win = win
        self.fmin = fmin
        self.fmax = fmax
        self.hann = np.hanning(win).astype(np.float32)
        self._ref = 0.6          # nivel de referencia (AGC lento)
        self._wave_ref = 0.2
        self._cache_sr = 0
        self._lo = self._hi = None
        self._tilt = None

    def _prepare(self, sr: int) -> None:
        if self._cache_sr == sr:
            return
        self._cache_sr = sr
        bin_hz = sr / self.win
        fmax = min(self.fmax, sr * 0.48)
        edges = np.geomspace(self.fmin, fmax, self.n_bars + 1) / bin_hz
        self._lo = edges[:-1]
        self._hi = edges[1:]
        centers = np.sqrt(edges[:-1] * edges[1:]) * bin_hz
        # Compensa la caída natural de ~3.5 dB/octava de la música (relativo a 250 Hz)
        self._tilt = (3.5 * np.log2(np.maximum(centers, 1.0) / 250.0)).astype(np.float32)

    def process(self, chunk: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
        self._prepare(sr)
        n = self.win
        if len(chunk) < n:
            chunk = np.pad(chunk, (0, n - len(chunk)))
        seg = chunk[:n].astype(np.float32)
        mag = np.abs(np.fft.rfft(seg * self.hann)) / (n / 4.0)  # ≈ amplitud pico

        cum = np.concatenate([[0.0], np.cumsum(mag)])
        lo, hi = self._lo, self._hi
        idx = np.arange(len(mag), dtype=np.float64)
        vals = np.empty(self.n_bars, dtype=np.float32)
        for i in range(self.n_bars):
            a, b = lo[i], max(hi[i], lo[i] + 1.0)
            ia, ib = int(a), int(b)
            if ib - ia >= 1:
                vals[i] = float(np.max(mag[ia:ib + 1]) * 0.55 + np.mean(mag[ia:ib + 1]) * 0.45)
            else:
                vals[i] = float(np.interp((a + b) * 0.5, idx, mag))

        db = 20.0 * np.log10(vals + 1e-6) + self._tilt
        level = np.clip((db + 74.0) / 62.0, 0.0, 1.2)  # -74 dB..-12 dB -> 0..1

        # Control de ganancia automático lento: llena la altura sin saturar
        peak = float(np.percentile(level, 92))
        self._ref = max(peak, self._ref * 0.9988)
        scale = 0.90 / max(self._ref, 0.50)
        bars = np.clip(level * scale, 0.0, 1.0)
        # Curva suave: separa mejor picos de fondo (evita que "todo suba junto")
        bars = bars ** 1.35
        return bars.astype(np.float32), self._wave(seg)

    def _wave(self, seg: np.ndarray) -> np.ndarray:
        pts = np.interp(np.linspace(0, len(seg) - 1, 128), np.arange(len(seg)), seg).astype(np.float32)
        p = float(np.max(np.abs(pts)))
        self._wave_ref = max(p, self._wave_ref * 0.997)
        return np.clip(pts / max(self._wave_ref, 0.12) * 0.85, -1.0, 1.0)
