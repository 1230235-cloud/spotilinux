#!/usr/bin/env python3
"""
Generador de pistas de demostración musicales con síntesis polifónica (acordes, bajo, melodía y percusión),
carátulas generadas en alta resolución (Pillow) y metadatos ID3 + portada incrustada mediante FFmpeg.
"""

import math
import os
import subprocess
import tempfile
import wave
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter


DEMO_TRACKS = [
    {
        "filename": "01_midnight_horizon.mp3",
        "title": "Midnight Horizon",
        "artist": "Neon Pulse",
        "album": "Outrun Dreams 1984",
        "genre": "Synthwave",
        "year": "2026",
        "track_num": "1",
        "bpm": 112,
        "duration": 28,
        "root_freq": 110.0,  # A2
        "chords": [
            [0, 3, 7, 10],    # Am7
            [-4, 0, 3, 7],    # Fmaj7
            [-9, -5, -2, 0],  # Cmaj7
            [-5, -2, 2, 5],   # G6
        ],
        "melody": [12, 15, 19, 17, 15, 12, 10, 12, 15, 22, 19, 17, 15, 14, 12, 10],
        "colors": ((18, 10, 48), (236, 56, 188), (0, 242, 254)),
        "style": "synthwave",
    },
    {
        "filename": "02_rainy_tokyo_lofi.mp3",
        "title": "Rainy Tokyo Coffee",
        "artist": "Kuroshio Beats",
        "album": "Late Night Study Sessions",
        "genre": "Lo-Fi Chill",
        "year": "2026",
        "track_num": "2",
        "bpm": 82,
        "duration": 30,
        "root_freq": 130.81,  # C3
        "chords": [
            [2, 5, 9, 12],    # Dm9
            [7, 11, 14, 17],  # G13
            [0, 4, 7, 11],    # Cmaj9
            [9, 12, 16, 19],  # Am7
        ],
        "melody": [16, 14, 12, 9, 12, 14, 16, 19, 17, 16, 14, 12, 9, 7, 9, 12],
        "colors": ((24, 34, 56), (242, 153, 74), (242, 201, 76)),
        "style": "lofi",
    },
    {
        "filename": "03_emerald_deep_house.mp3",
        "title": "Emerald Groove",
        "artist": "Solstice Club",
        "album": "Ibiza Sunset Terrace",
        "genre": "Deep House",
        "year": "2026",
        "track_num": "3",
        "bpm": 124,
        "duration": 26,
        "root_freq": 98.0,  # G2
        "chords": [
            [0, 3, 7, 10],   # Gm7
            [5, 8, 12, 15],  # Cm7
            [3, 7, 10, 14],  # Ebmaj7
            [2, 5, 9, 12],   # Dm7
        ],
        "melody": [12, 15, 19, 22, 19, 15, 17, 14, 10, 15, 14, 12, 10, 7, 10, 12],
        "colors": ((10, 46, 36), (29, 185, 84), (110, 255, 190)),
        "style": "house",
    },
    {
        "filename": "04_cybernetic_odyssey.mp3",
        "title": "Cybernetic Odyssey",
        "artist": "Valkyrie Protocol",
        "album": "Neo-Veracruz 2099",
        "genre": "Midtempo / Electro",
        "year": "2026",
        "track_num": "4",
        "bpm": 100,
        "duration": 27,
        "root_freq": 82.41,  # E2
        "chords": [
            [0, 3, 7],       # Em
            [8, 12, 15],     # C
            [5, 9, 12],      # A
            [7, 11, 14],     # B
        ],
        "melody": [12, 12, 15, 14, 12, 10, 7, 10, 12, 15, 19, 18, 15, 12, 11, 12],
        "colors": ((32, 8, 24), (255, 42, 84), (255, 180, 0)),
        "style": "electro",
    },
    {
        "filename": "05_aurora_borealis.mp3",
        "title": "Aurora Borealis",
        "artist": "Nordic Echoes",
        "album": "Glacier Reflections",
        "genre": "Ambient Electronic",
        "year": "2026",
        "track_num": "5",
        "bpm": 90,
        "duration": 32,
        "root_freq": 146.83,  # D3
        "chords": [
            [0, 4, 7, 11],   # Dmaj7
            [-3, 1, 4, 9],   # Bm7
            [-7, -3, 0, 4],  # Gmaj7
            [-5, -1, 2, 7],  # Asus4
        ],
        "melody": [11, 14, 16, 19, 18, 16, 14, 11, 9, 11, 14, 16, 14, 11, 9, 7],
        "colors": ((8, 28, 52), (58, 123, 213), (0, 210, 255)),
        "style": "ambient",
    },
    {
        "filename": "06_velvet_starlight.mp3",
        "title": "Velvet Starlight",
        "artist": "The Midnight Cruisers",
        "album": "Coastline Drive",
        "genre": "Indie Synthpop",
        "year": "2026",
        "track_num": "6",
        "bpm": 118,
        "duration": 28,
        "root_freq": 123.47,  # B2
        "chords": [
            [0, 4, 7, 11],   # Bmaj7
            [-5, -1, 2, 6],  # F#maj
            [-3, 0, 4, 7],   # G#m7
            [-7, -3, 0, 4],  # Emaj7
        ],
        "melody": [11, 14, 16, 19, 16, 14, 11, 14, 16, 21, 19, 16, 14, 11, 9, 11],
        "colors": ((42, 16, 68), (155, 81, 224), (255, 126, 179)),
        "style": "synthwave",
    },
]


def semitone_to_freq(root: float, semitones: float) -> float:
    return root * (2.0 ** (semitones / 12.0))


def generate_cover_image(track_info: dict, output_path: Path, size: int = 600) -> None:
    """Genera una carátula artística con degradados, formas geométricas y tipografía limpia."""
    c1, c2, c3 = track_info["colors"]
    img = Image.new("RGB", (size, size), c1)
    draw = ImageDraw.Draw(img)

    # Degradado diagonal suave con numpy
    y_idx, x_idx = np.mgrid[0:size, 0:size]
    t = (x_idx * 0.6 + y_idx * 0.4) / size
    t = np.clip(t, 0.0, 1.0)

    arr = np.zeros((size, size, 3), dtype=np.uint8)
    for ch in range(3):
        mid = c1[ch] * (1 - t) + c2[ch] * t
        glow = np.exp(-(((x_idx - size * 0.72) ** 2 + (y_idx - size * 0.28) ** 2) / (2 * (size * 0.34) ** 2)))
        val = mid * (1 - glow * 0.75) + c3[ch] * (glow * 0.75)
        arr[:, :, ch] = np.clip(val, 0, 255).astype(np.uint8)

    img = Image.fromarray(arr, mode="RGB")
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    # Elementos geométricos decorativos estilo vinilo / ondas / sol synthwave
    cx, cy = size // 2, int(size * 0.44)
    radius = int(size * 0.25)
    odraw.ellipse(
        (cx - radius - 25, cy - radius - 25, cx + radius + 25, cy + radius + 25),
        outline=(*c3, 95),
        width=3,
    )
    odraw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=(*c1, 150),
        outline=(*c3, 210),
        width=4,
    )
    # Líneas de espectro decorativas en el centro
    num_bars = 21
    bar_w = 6
    gap = 6
    total_w = num_bars * (bar_w + gap)
    start_x = cx - total_w // 2
    for i in range(num_bars):
        phase = abs(i - num_bars // 2) / (num_bars // 2)
        bh = int((1.0 - phase * 0.75) * radius * 0.9 * (0.65 + 0.35 * math.sin(i * 1.7)))
        bx = start_x + i * (bar_w + gap)
        odraw.rounded_rectangle(
            (bx, cy - bh, bx + bar_w, cy + bh),
            radius=3,
            fill=(*c3, 230),
        )

    # Franja inferior oscura para legibilidad del texto
    odraw.rectangle((0, int(size * 0.72), size, size), fill=(10, 10, 14, 205))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 34)
        font_artist = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans.ttf", 22)
        font_genre = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 15)
    except Exception:
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
            font_artist = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
            font_genre = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
        except Exception:
            font_title = ImageFont.load_default()
            font_artist = ImageFont.load_default()
            font_genre = ImageFont.load_default()

    draw.text((36, 32), track_info["genre"].upper(), fill=c3, font=font_genre)
    draw.text((36, int(size * 0.76)), track_info["title"], fill=(255, 255, 255), font=font_title)
    draw.text((36, int(size * 0.85)), f"{track_info['artist']}  •  {track_info['album']}", fill=(195, 200, 215), font=font_artist)

    img.save(output_path, format="JPEG", quality=92)


def synthesize_track(track_info: dict, sample_rate: int = 44100) -> np.ndarray:
    """Sintetiza una pista musical estéreo completa con acordes, bajo, arpegio/melodía y batería."""
    duration = track_info["duration"]
    bpm = track_info["bpm"]
    root_freq = track_info["root_freq"]
    chords = track_info["chords"]
    melody = track_info["melody"]
    style = track_info["style"]

    num_samples = int(duration * sample_rate)
    t = np.linspace(0, duration, num_samples, endpoint=False, dtype=np.float32)

    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    step_dur = beat_dur / 2.0  # corcheas

    left = np.zeros(num_samples, dtype=np.float32)
    right = np.zeros(num_samples, dtype=np.float32)

    # 1. Pad de acordes estéreo con detune suave
    num_bars = int(math.ceil(duration / bar_dur))
    for b in range(num_bars):
        start_idx = int(b * bar_dur * sample_rate)
        end_idx = min(num_samples, int((b + 1) * bar_dur * sample_rate))
        if start_idx >= end_idx:
            break
        seg_t = t[start_idx:end_idx] - b * bar_dur
        seg_len = end_idx - start_idx
        env = np.ones(seg_len, dtype=np.float32)
        attack = min(seg_len, int(0.08 * sample_rate))
        release = min(seg_len, int(0.12 * sample_rate))
        if attack > 0:
            env[:attack] = np.linspace(0.0, 1.0, attack)
        if release > 0:
            env[-release:] *= np.linspace(1.0, 0.15, release)

        chord = chords[b % len(chords)]
        pad_l = np.zeros(seg_len, dtype=np.float32)
        pad_r = np.zeros(seg_len, dtype=np.float32)
        for note in chord:
            f = semitone_to_freq(root_freq * 2.0, note)
            pad_l += 0.12 * np.sin(2 * np.pi * (f * 0.998) * seg_t) + 0.05 * np.sin(2 * np.pi * (f * 2.0) * seg_t)
            pad_r += 0.12 * np.sin(2 * np.pi * (f * 1.002) * seg_t) + 0.05 * np.cos(2 * np.pi * (f * 2.0) * seg_t)

        # Efecto sidechain suave en cada pulso
        beat_phase = (seg_t % beat_dur) / beat_dur
        sidechain = 0.35 + 0.65 * np.clip(beat_phase * 2.2, 0.0, 1.0)
        left[start_idx:end_idx] += pad_l * env * sidechain
        right[start_idx:end_idx] += pad_r * env * sidechain

    # 2. Línea de bajo (sub + armónicos)
    total_steps = int(math.ceil(duration / step_dur))
    for s in range(total_steps):
        start_idx = int(s * step_dur * sample_rate)
        end_idx = min(num_samples, int((s + 1) * step_dur * sample_rate))
        if start_idx >= end_idx:
            break
        seg_t = t[start_idx:end_idx] - s * step_dur
        seg_len = end_idx - start_idx

        bar_idx = int((s * step_dur) // bar_dur)
        chord = chords[bar_idx % len(chords)]
        bass_note = chord[0] + (12 if (s % 4 == 2 and style != "ambient") else 0)
        bf = semitone_to_freq(root_freq, bass_note)

        b_env = np.exp(-seg_t * (6.5 if style != "ambient" else 2.5)).astype(np.float32)
        bass_wave = (
            0.30 * np.sin(2 * np.pi * bf * seg_t)
            + 0.14 * np.tanh(2.5 * np.sin(2 * np.pi * bf * 2.0 * seg_t))
        ) * b_env
        left[start_idx:end_idx] += bass_wave
        right[start_idx:end_idx] += bass_wave

        # 3. Melodía / Arpegio principal con paneo estéreo
        mel_note = melody[s % len(melody)]
        mf = semitone_to_freq(root_freq * 2.0, mel_note)
        m_env = np.exp(-seg_t * 5.0).astype(np.float32)
        attack_m = min(seg_len, int(0.01 * sample_rate))
        if attack_m > 0:
            m_env[:attack_m] *= np.linspace(0.0, 1.0, attack_m)

        lead = (
            0.16 * np.sin(2 * np.pi * mf * seg_t)
            + 0.07 * np.sin(2 * np.pi * mf * 3.0 * seg_t) * np.exp(-seg_t * 10.0)
        ) * m_env
        pan = 0.5 + 0.35 * math.sin(s * 0.6)
        left[start_idx:end_idx] += lead * (1.0 - pan * 0.5)
        right[start_idx:end_idx] += lead * (0.5 + pan * 0.5)

    # 4. Batería electrónica (Kick, Snare/Clap, Hi-Hat)
    if style != "ambient":
        total_beats = int(math.ceil(duration / beat_dur))
        rng = np.random.default_rng(42)
        for b in range(total_beats):
            b_start = int(b * beat_dur * sample_rate)
            # Kick en cada pulso (house/synthwave) o en 1 y 3 (lofi)
            if style in ("house", "synthwave", "electro") or (b % 2 == 0):
                k_len = min(num_samples - b_start, int(0.22 * sample_rate))
                if k_len > 0:
                    kt = np.linspace(0, k_len / sample_rate, k_len, endpoint=False, dtype=np.float32)
                    freq_sweep = 135.0 * np.exp(-kt * 28.0) + 46.0
                    phase = 2 * np.pi * np.cumsum(freq_sweep) / sample_rate
                    kick = 0.45 * np.sin(phase) * np.exp(-kt * 11.0)
                    left[b_start : b_start + k_len] += kick
                    right[b_start : b_start + k_len] += kick

            # Snare / Clap en pulsos 2 y 4
            if b % 2 == 1:
                s_len = min(num_samples - b_start, int(0.18 * sample_rate))
                if s_len > 0:
                    st = np.linspace(0, s_len / sample_rate, s_len, endpoint=False, dtype=np.float32)
                    noise = rng.standard_normal(s_len).astype(np.float32)
                    body = np.sin(2 * np.pi * 185.0 * st) * np.exp(-st * 22.0)
                    snare = (0.20 * noise * np.exp(-st * 18.0) + 0.15 * body)
                    left[b_start : b_start + s_len] += snare * 0.95
                    right[b_start : b_start + s_len] += snare * 1.05

            # Hi-hat en contratiempo
            hh_start = b_start + int(0.5 * beat_dur * sample_rate)
            if hh_start < num_samples:
                h_len = min(num_samples - hh_start, int(0.06 * sample_rate))
                if h_len > 0:
                    ht = np.linspace(0, h_len / sample_rate, h_len, endpoint=False, dtype=np.float32)
                    h_noise = np.diff(rng.standard_normal(h_len + 1).astype(np.float32))
                    hat = 0.08 * h_noise * np.exp(-ht * 45.0)
                    left[hh_start : hh_start + h_len] += hat * 1.1
                    right[hh_start : hh_start + h_len] += hat * 0.9

    # Fade in y fade out suaves
    fade_samples = int(1.2 * sample_rate)
    if num_samples > fade_samples * 2:
        fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
        fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        left[:fade_samples] *= fade_in
        right[:fade_samples] *= fade_in
        left[-fade_samples:] *= fade_out
        right[-fade_samples:] *= fade_out

    # Normalización con limitador suave
    stereo = np.stack([left, right], axis=1)
    peak = np.max(np.abs(stereo))
    if peak > 1e-5:
        stereo = np.tanh(stereo / peak * 1.35) * 0.88

    return (stereo * 32767.0).astype(np.int16)


def ensure_demo_tracks(demo_dir: Path) -> list[Path]:
    """Crea las pistas de demostración con metadatos ID3 y carátulas incrustadas si no existen."""
    demo_dir.mkdir(parents=True, exist_ok=True)
    covers_dir = demo_dir / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    created_files: list[Path] = []
    for info in DEMO_TRACKS:
        mp3_path = demo_dir / info["filename"]
        cover_path = covers_dir / (Path(info["filename"]).stem + ".jpg")

        if not cover_path.exists():
            generate_cover_image(info, cover_path)

        if mp3_path.exists() and mp3_path.stat().st_size > 10000:
            created_files.append(mp3_path)
            continue

        pcm_stereo = synthesize_track(info)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            wav_path = Path(tmp_wav.name)

        try:
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(pcm_stereo.tobytes())

            # Codificar a MP3 con carátula ID3v2.3 incrustada usando FFmpeg
            cmd = [
                "ffmpeg",
                "-y",
                "-i", str(wav_path),
                "-i", str(cover_path),
                "-map", "0:a:0",
                "-map", "1:v:0",
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                "-c:v", "mjpeg",
                "-id3v2_version", "3",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)",
                "-metadata", f"title={info['title']}",
                "-metadata", f"artist={info['artist']}",
                "-metadata", f"album={info['album']}",
                "-metadata", f"genre={info['genre']}",
                "-metadata", f"date={info['year']}",
                "-metadata", f"track={info['track_num']}",
                str(mp3_path),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            created_files.append(mp3_path)
        finally:
            if wav_path.exists():
                wav_path.unlink()

    return created_files


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "demo_tracks"
    print("Generando pistas de demostración con metadatos ID3 y carátulas...")
    tracks = ensure_demo_tracks(target)
    print(f"¡Listo! {len(tracks)} pistas creadas en {target}")
