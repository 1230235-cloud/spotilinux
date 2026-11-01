# AGENTS.md — Guía Operativa para Agentes IA (MaoMusic / SpotiLinux)

## 1. Stack Tecnológico y Comandos de Build / Test

- **Escritorio (Linux)**: Python 3 (`PyQt6>=6.6.0`, `numpy>=1.24.0`, `Pillow>=10.0.0`), empaquetado como binario ELF nativo.
- **Móvil (Android)**: Java 11 (`android-34` SDK) + SPA híbrida HTML5/CSS3/JS Canvas 60 FPS (`android-app/assets/www/index.html`).
- **Nube / Sincronización**: Google Drive API v3 + Google Apps Script (`google_drive_server_backend.gs`).

### Comandos Principales
```bash
# Diagnóstico de entorno e instalación portable en Linux
./install.sh   # o diagnóstico: ./venv/bin/python doctor.py

# Ejecutar en desarrollo y suite de pruebas
./run.sh       # o: ./venv/bin/python main.py
./venv/bin/python -m unittest discover tests -v

# Compilar binario nativo Linux (genera dist/MaoMusic y ./MaoMusic)
./venv/bin/python build_linux_binary.py
```

## 2. Regla Estricta: PROHIBIDO Leer o Editar Carpetas Compiladas y Cachés

- **NUNCA** leas, analices ni edites directorios o artefactos compilados/generados:
  - `dist/`, `build/`, `android-app/build/`, `__pycache__/`, `.android-sdk/`, `venv/`
  - Binarios y paquetes raíz: `MaoMusic`, `SpotiLinux`, `MaoMusic.apk`, `SpotiLinux-Mobile.apk`
  - Cachés de tiempo de ejecución: `data/covers_cache/`, `data/eq_cache/`, `data/gdrive_cache/`
- Toda modificación debe realizarse **únicamente en el código fuente** y luego regenerarse mediante los scripts de build.

## 3. Mapa Modular del Proyecto

- `/` (Raíz Python Desktop):
  - `main.py`: Ventana principal PyQt6, reproductor, coordinación y badges de descargas.
  - `download_queue.py` y `download_queue_ui.py`: Cola persistente SQLite y panel no bloqueante.
  - `doctor.py`, `install.sh`, `binaries.py`: Diagnóstico de entorno, instalador y resolución de binarios.
  - `audio_engine.py`: Motor de reproducción de audio, ecualización y cálculo FFT.
  - `maomao_widget.py` y `visualizer_widget.py`: Renderizado procedimental de Maomao y barras de espectro.
  - `rhythm.py`: Detección de BPM, pulsos y fase rítmica.
  - `media_sync_worker.py` y `gdrive_sync.py`: Descarga YouTube, extracción MP3 y sincronización Drive.
  - `storage.py` y `metadata.py`: Persistencia local (`library.json`, `library.sqlite`) y extracción ID3.
  - `tests/`: Suite de pruebas unitarias (`test_core.py`, `test_doctor.py`, `test_download_queue.py`, etc.).
- `android-app/`:
  - `AndroidManifest.xml`: Permisos, versión (`versionCode`/`versionName`) y declaración de servicios.
  - `src/com/spotilinux/mobile/`: Puente nativo (`MainActivity.java`), servicio en segundo plano (`MediaPlaybackService.java`) y proveedor de actualizaciones (`GenericFileProvider.java`).
  - `assets/www/`: Interfaz web móvil (`index.html`), reproductor, modo vinilo y visualizador Canvas.
  - `res/drawable/`: Iconografía y recursos gráficos de la app Android.
- `data/`: Estado local persistente (`library.json`) y subcarpetas de caché local.
- `demo_tracks/` y `music_library/`: Pistas de demostración con portadas y biblioteca local del usuario.

## 4. Reglas de Edición Quirúrgica (Diffs)

1. **Ediciones Mínimas y Localizadas**: Usa siempre reemplazos quirúrgicos (diffs exactos de líneas afectadas) sin reescribir archivos completos, especialmente en `index.html`, `MainActivity.java` y `main.py`.
2. **Lectura Acotada**: Lee únicamente los rangos de líneas necesarios (`StartLine`/`EndLine`) antes de editar.
3. **Preservación de Contratos**: No elimines comentarios de contexto, identificadores canónicos de Google Drive ni firmas del puente `@JavascriptInterface`.
4. **Sincronía de Versiones**: Al publicar cambios, actualiza `version.json` y `android-app/AndroidManifest.xml` de forma atómica antes de compilar.
