# 🌿 SpotiLinux — Desktop (Linux ELF) & Mobile (Android APK) Music Player + Google Drive Cloud + Maomao Dancer

> **Documento de Arquitectura y Continuidad para Desarrolladores e IAs (AI Handoff Guide)**  
> Este `README.md` documenta de forma exhaustiva la arquitectura, módulos, flujos de datos, decisiones de ingeniería, puente nativo Android/Java, integración con Google Drive API v3, cinemática de Maomao y comandos de compilación. **Si eres otra IA o desarrollador retomando este proyecto, lee este documento completo antes de modificar cualquier archivo para continuar exactamente sobre la arquitectura existente sin empezar desde cero ni desviarte.**

---

## 📌 1. Resumen Ejecutivo del Proyecto

**SpotiLinux** es un ecosistema de reproducción musical tipo Spotify multiplataforma compuesto por:
1. **Aplicación de Escritorio para Linux** (`main.py` + binario compilado ELF en `dist/SpotiLinux`).
2. **Aplicación Móvil Nativa/Híbrida para Android** (`android-app/` compilada y firmada en `dist/SpotiLinux-Mobile.apk`).

### Características Principales Incluidas:
- **Reproducción Local con Auto-Rastreo y Carpeta Persistente de la Aplicación**:
  - **En Linux**: Rastrea automáticamente `~/Music`, `~/Música`, `~/Downloads` y `~/Descargas` y copia/guarda las canciones dentro de la carpeta propia de la aplicación (`music_library/`), además de persistir la base de datos en `data/library.json`. Así el usuario no tiene que re-importar archivos en cada sesión.
  - **En Android (`.apk`)**: Rastrea todos los archivos de audio del teléfono mediante `MediaStore.Audio.Media.EXTERNAL_CONTENT_URI` y los copia/guarda automáticamente en el directorio persistente de la aplicación (`/storage/emulated/0/Android/data/com.spotilinux.mobile/files/SpotiLinuxMusic`), evitando tener que re-importar canciones al cerrar o reiniciar la app.
- **Nube Privada y Compartida con Google Drive + Sistema de Cuentas Multi-Dispositivo**:
  - **Sistema de Cuentas de Usuario (`Usuario`, `Correo`, `Contraseña`) y Servidor Central**: Permite registrarse o iniciar sesión tanto en Linux como en Android. Almacena las credenciales con hash SHA-256 (`spotilinux_database.json`) y sincroniza playlists, canciones favoritas y catálogo de canciones de Drive. Trata a Google Drive como servidor central en la nube sincronizado en tiempo real (mediante la API de Google Apps Script Web App desplegada en `https://script.google.com/macros/s/AKfycbzwOO_eEw7ldBwGUPwX_MqYZfgRA6-N9meQ_z8k43V-0KBc0cmj43ap2qV6vT0ynO4dIQ/exec`, Servidor REST Central y API v3 OAuth), permitiendo que una cuenta creada en el Dispositivo A esté disponible de inmediato para iniciar sesión en el Dispositivo B con todas sus playlists y canciones de Drive restauradas.
  - **Carpeta Raíz Oficial de Google Drive y Subcarpeta `Musica`**: Vinculado por defecto a la carpeta compartida `1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT` (y subcarpeta `Música` con ID `1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T`). Dentro de esta carpeta se aloja la base de datos de cuentas (`spotilinux_database.json`) y la subcarpeta dedicada **`Musica`** que contiene las canciones disponibles para **🎧 Escuchar en línea (Streaming)** o **⬇️ Descargar (Offline)**, además de permitir a los usuarios **⬆️ Subir canciones directamente** desde la aplicación.
  - **Verificación Estricta de Duplicados**: Activada por defecto tanto en descargas como en escaneo de Drive, evitando descargar canciones repetidas o duplicar entradas en la biblioteca local mediante identificadores canónicos `gd_<fileId>` y normalización de títulos y tamaños.
  - Solicita conectarse a la cuenta de Google Drive desde el primer arranque (o mediante el botón `👤 Cuenta` / `☁️ Drive`).
  - Soporta modo público sin token (vía scraping de `embeddedfolderview`) y OAuth 2.0 (navegador / loopback, carga de `credentials.json`, Token OAuth2 `ya29...` o API Key).
- **Motor de Audio Nativo en Segundo Plano en Android (Solución al Silencio tras Pausa Prolongada con Audífonos)**:
  - Toda la reproducción de audio en Android ocurre dentro del servicio nativo en primer plano `MediaPlaybackService.java` usando `android.media.MediaPlayer` + `android.media.audiofx.Equalizer` + `PARTIAL_WAKE_LOCK` + `WifiLock` + `AudioFocusRequest` + `MediaSession` (`FLAG_HANDLES_MEDIA_BUTTONS`).
  - Esto garantiza que al pausar con audífonos Bluetooth/cableados o desde los Ajustes Rápidos de Android y esperar varios minutos, al volver a despausar con los audífonos o desde la pantalla de bloqueo **el audio siempre suene inmediatamente** sin necesidad de abrir la aplicación (evitando la suspensión del `AudioContext` de Chromium WebView).
- **Gestión Completa de Playlists y Favoritos (`♥`) con Selección Múltiple y Pantalla Principal de Playlists**:
  - **Página Principal estilo Spotify**: Tanto en Linux como en Android, la pantalla de Inicio (`🏠 Inicio`) no muestra una lista plana de canciones sueltas; en su lugar, destaca las **Playlists Disponibles** (Favoritos, Google Drive, playlists personalizadas y tarjeta para crear una nueva). Al pulsar cualquier playlist se abre su vista detallada con sus pistas.
  - **Selección Múltiple de Canciones**: Permite seleccionar varias canciones a la vez con checkboxes (`☑️`) o selección extendida (`Ctrl+Click`/`Shift+Click`) y agregarlas todas juntas a cualquier playlist existente o nueva con un solo clic.
  - **Sincronización en la Nube con Cuenta de Google & Google Drive**: Al vincular la cuenta de Google, SpotiLinux sincroniza automáticamente las playlists y favoritos en `spotilinux_cloud_playlists.json` dentro del Google Drive del usuario, permitiendo acceder a las mismas listas desde cualquier teléfono o PC con esa cuenta (roaming multiplataforma estilo Spotify).
- **Figura Animada de Maomao (*The Apothecary Diaries*) Bailando al Compás Real (BPM & Beat Grid)**:
  - Renderizado vectorial a 60 FPS (`QPainter` en Linux y HTML5 `Canvas` en Android) donde Maomao baila sincronizada con el **tempo real (BPM) y la rejilla de pulsos (`beat_pos`)** de la canción, evitando oscilaciones caóticas por picos instantáneos de energía.
  - Incluye normalización automática half/double-time al rango natural de baile (`72–142 BPM`), fraseo coreográfico de 4 y 8 tiempos, indicador en vivo `♩ XXX BPM` con 4 luces de compás en la esquina superior derecha y 3 estilos de danza intercambiables (*Danza Imperial*, *Recolección de Hierbas 🌿*, *Paso Neko :3 🐾*).
- **Analizador de Espectro Musical (dB + Tilt + AGC) y Ecualizador de 5 Bandas**:
  - Espectro logarítmico (`45 Hz – 9 kHz`) con conversión a decibelios (`dB`), compensación de agudos (`+3.5 dB/octava`), control automático de ganancia lento (`AGC`) y ataque/decaimiento musical para que las barras, la onda neón y el modo radial se muevan con suavidad profesional al ritmo del beat.
  - Bandas de ecualización en `60 Hz`, `230 Hz`, `910 Hz`, `3.6 kHz` y `14 kHz` con presets (*Plano, Bass Boost, Synthwave, Lo-Fi Chill, Vocal, Rock, Club*).
- **Diseño Visual y Botonera Profesional**:
  - Conserva la paleta oficial (negro `#000000`, grafito `#121212`, verde esmeralda `#1DB954`/`#1ED760`, carmesí `#A22044` y jade `#2A7666`) con botones rediseñados (`NavButton`, `ActionChipButton`, `SpotifyGreenPlayBig`, `TransportPlayButton`, `TransportIconBtn`, `GhostSmallButton`) con gradientes sutiles, bordes iluminados, estados `:hover`/`:pressed` y jerarquía tipográfica limpia.

---

## 📂 2. Estructura del Proyecto

```text
spotify-linux/
├── README.md                     # Este documento de arquitectura y continuidad para IAs/devs
├── .gitignore                    # Exclusiones de Git (venv, .android-sdk, cachés temporales)
├── requirements.txt              # Dependencias de Python (PyQt6, mutagen, numpy, pillow, requests)
├── run.sh                        # Script de lanzamiento rápido para escritorio Linux
├── SpotiLinux.desktop            # Lanzador de escritorio XDG para Linux
├── build_linux_binary.py         # Compilador del ejecutable ELF nativo (dist/SpotiLinux)
├── build_android_apk.sh          # Compilador y firmador del APK de Android (dist/SpotiLinux-Mobile.apk)
│
├── main.py                       # Ventana principal PyQt6, vistas, diálogos, menús y señales
├── audio_engine.py               # Motor QMediaPlayer + reloj monótono + análisis rítmico/espectral + EQ FFmpeg
├── rhythm.py                     # Extractor de BPM/rejilla de beats (spectral flux + comb filter) y SpectrumAnalyzer (dB + AGC)
├── maomao_widget.py              # Widget QPainter a 60 FPS de Maomao bailando sincronizada a beat_pos y BPM
├── visualizer_widget.py          # Visualizador FFT (Barras/Onda/Radial) bloqueado al beat y panel del Ecualizador
├── gdrive_sync.py                # Cliente Google Drive API v3, OAuth2 loopback, auto-carpeta Music y caché
├── storage.py                    # Persistencia JSON, Playlists, Favoritos y carpeta music_library/
├── metadata.py                   # Extractor ID3/FLAC/Vorbis (mutagen), portadas APIC y color dominante
├── styles.py                     # Hoja de estilos QSS profesional manteniendo la paleta #121212 / #1DB954
├── generate_demos.py             # Sintetizador de 6 pistas musicales de demostración con tags ID3
│
├── music_library/                # Carpeta persistente de la app en Linux donde se guardan las canciones rastreadas/descargadas
├── demo_tracks/                  # 6 pistas MP3 generadas automáticamente con carátulas embebidas
├── data/
│   ├── library.json              # Base de datos persistente (canciones, playlists, likes, config Drive, EQ)
│   ├── covers_cache/             # Carátulas extraídas o generadas en caché
│   └── gdrive_cache/             # Caché de streaming temporal de Google Drive
│
├── dist/
│   ├── SpotiLinux                # Ejecutable binario ELF nativo para Linux Desktop
│   ├── SpotiLinux.desktop        # Acceso directo de escritorio Linux
│   ├── icon.png                  # Icono oficial de SpotiLinux
│   └── SpotiLinux-Mobile.apk     # Instalador APK firmado para teléfonos Android
│
└── android-app/                  # Código fuente nativo e híbrido de la aplicación Android
    ├── AndroidManifest.xml       # Permisos (incl. RECORD_AUDIO para Visualizer), MainActivity y MediaPlaybackService
    ├── src/com/spotilinux/mobile/
    │   ├── MainActivity.java         # Puente @JavascriptInterface (AndroidMedia), permisos, escáner MediaStore, descargas Drive y persistencia JSON
    │   └── MediaPlaybackService.java # Servicio nativo en 1er plano con MediaPlayer, Equalizer, Visualizer FFT, AudioFocus, WakeLock y MediaSession
    ├── assets/www/
    │   ├── index.html                # UI móvil responsiva, Maomao y ondas sincronizadas al beat/FFT nativo, Playlists y Google Drive
    │   └── tracks/                   # Pistas demo integradas dentro del APK
    └── res/                          # Iconos mipmap y recursos Android
```

---

## 🖥️ 3. Arquitectura Detallada: Versión de Escritorio Linux

### 3.1 Motor Rítmico (`rhythm.py`), Espectro Musical y Ecualizador (`audio_engine.py`)
- **`rhythm.py` (Análisis de Tempo, Rejilla de Beats y Espectro Musical)**:
  - **`analyze_rhythm(samples, sr)`**: Calcula la envolvente de flujo espectral (*spectral flux* con compresión logarítmica enfatizando sub-bajos/bombo y caja), estima el tempo mediante un banco de filtros peine resonantes (*comb filter* entre `68–180 BPM` con refinamiento de `0.05 BPM`) y construye una rejilla de tiempos de pulso (`beat_times`) re-anclada localmente a los onsets reales de la canción.
  - **`beat_position(beat_times, bpm, t)`**: Devuelve la posición continua de beat (`float`, ej. `14.35` = 35 % entre el beat 14 y el 15) para cualquier instante `t` en segundos, además de la intensidad de golpe (`onset`).
  - **`SpectrumAnalyzer`**: Procesa ventanas de `2048` muestras con ventana de Hann, agrupa en **32 bandas logarítmicas** (`45 Hz – 9 kHz`), convierte a escala de decibelios (`dB`) con inclinación espectral de `+3.5 dB/octava` y aplica:
    - **Piso adaptativo por banda (`_band_floor`)**: Rastrea el nivel de ruido residual en cada frecuencia para asegurar que entre golpes de batería las barras desciendan limpiamente y reboten con gran rango dinámico.
    - **Impulso de transitorios por flujo espectral (`flux_db`)**: Detecta onsets instantáneos y resalta los golpes percusivos (`beat_kick`) sin descalibrar el AGC general.
    - **Control automático de ganancia lento (`AGC`)** con ataque ágil (`0.72`) y decaimiento musical (`0.25`) para respuesta inmediata y fluida al compás de la música.
- **`AudioEngine` (`audio_engine.py`)**:
  - Utiliza `QMediaPlayer` y `QAudioOutput` de `PyQt6.QtMultimedia`.
  - Cuando se carga una pista, lanza **`PCMAnalyzerWorker` (`QThread`)**, que decodifica la pista a PCM mono (`22050 Hz`) y ejecuta `analyze_rhythm` en segundo plano (~`0.1 s` por canción).
  - Reinicia los acumuladores del analizador espectral en cada cambio de pista (`reset()`).
  - **Reloj monótono interpolado (`_smooth_position_sec()`)**: `QMediaPlayer.position()` solo se actualiza cada `50–100 ms`. Para evitar saltos en la animación a 60 FPS, `AudioEngine` interpola la posición usando `time.monotonic()` y corrige derivas suavemente.
  - Sincroniza la acentuación de bombo/caja con la posición de beat calculada (`beat_kick`), inyectando golpes vivos en las bandas graves (`0..5`) y medias-agudas en cada pulso real.
  - Emite dos señales a **60 FPS (`16 ms`)**:
    1. `spectrum_updated(bars, bass, mid, high)`
    2. `beat_info(bpm, beat_pos, confidence, onset)`
- **Ecualizador de 5 Bandas (`EQFilterWorker`)**:
  - Cuando el usuario activa el ecualizador o cambia una banda (`60Hz, 230Hz, 910Hz, 3.6kHz, 14kHz`), `EQFilterWorker` ejecuta `ffmpeg` en segundo plano con una cadena de filtros `equalizer=f=...:width_type=o:w=1.2:g=...` y hace un *hot-swap* transparente manteniendo el milisegundo exacto de reproducción (`seek_ms`).

### 3.2 Coreografía de Maomao (`maomao_widget.py`) y Visualizador (`visualizer_widget.py`)
- **`MaomaoDancerWidget` (`maomao_widget.py`)**:
  - **Por qué antes se veía acelerada/"medio loca"**: Anteriormente incrementaba una fase libre en cada frame multiplicada por la energía de graves, provocando saltos bruscos de postura en cada pico de volumen.
  - **Solución implementada (`set_beat` + `_dance_pose`)**: La postura completa de Maomao (rebote de rodillas `bounce`, balanceo de cadera/torso `sway_x`/`hip_x`, pasitos alternados `step_l`/`step_r`, vuelo de la falda `skirt_swing`, brazos `arm_l`/`arm_r` y orejitas `ear_twitch`) es una **función pura y continua de `beat_pos`**, normalizada automáticamente al rango de baile cómodo (`72–142 BPM`) y modulada por una amplitud suavizada (`self.amp`).
  - Incluye variaciones de frase cada 8 tiempos, indicador en vivo `♩ XXX BPM` y 4 círculos luminosos del compás (`1-2-3-4`) en la esquina superior derecha.
- **`AudioVisualizerWidget` (`visualizer_widget.py`)**:
  - Recibe `set_beat(bpm, beat_pos, confidence, onset)` además de las barras suavizadas.
  - **HUD de Verificación Rítmica en Vivo**: En la esquina superior izquierda muestra un display translúcido con el tempo detectado (`♩ XXX BPM`) y **4 luces circulares de compás (`1-2-3-4`)** que se iluminan progresiva y enérgicamente en cada tiempo del compás para verificar de forma visual e inequívoca que la animación sigue la música real.
  - **Modo Barras**: Cabezales flotantes (*peak caps*), reflejo inferior en el piso, líneas guía en dB, gradientes reactivos (el color cambia sutilmente con la intensidad de la música) y línea base que pulsa con el beat.
  - **Modo Onda Neón**: Onda bicapa suave con resplandor cyan/verde cuya envolvente sigue la distribución espectral real y cuya fase avanza bloqueada al compás exacto (sin temblores erráticos).
  - **Modo Radial**: Anillo central que late con el pulso del beat y barras radiales con velocidad angular constante, radio modulado por el espectro y partículas circundantes.

### 3.3 Google Drive como Servidor Central, Sistema de Cuentas y Sincronización Multi-Dispositivo (`gdrive_sync.py` + `google_drive_server_backend.gs`)
- **Arquitectura de Google Drive como Servidor de Base de Datos**:
  - Carpeta raíz central: `1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT` (compartida con permisos de editor).
  - La aplicación trata la carpeta de Google Drive como un backend central para la base de datos `spotilinux_database.json` y el catálogo de canciones en la subcarpeta `Musica`.
  - **Sincronización multi-canal**:
    1. **Servidor Cloud REST (`spoti_1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT`)**: Tanto en Linux como en Android (`MainActivity.java`), la base de datos se envía y consulta automáticamente mediante el endpoint en la nube indexado con el ID de la carpeta de Drive, permitiendo que cualquier dispositivo (sin importar si ya inició sesión en Google o no) guarde y recupere las cuentas al instante.
    2. **Google Apps Script Serverless Backend (`google_drive_server_backend.gs`)**: Script oficial incluido en la raíz del repositorio que permite desplegar en 1 minuto un endpoint REST Web App en Google Apps Script para gestionar lectura y escritura directa en la carpeta de Drive.
    3. **Google Drive API v3 (OAuth 2.0 / Token)**: Cuando el usuario cuenta con token OAuth, la base de datos se sincroniza adicionalmente mediante peticiones PATCH / POST multipart en la API oficial de Drive.
- **Sistema de Cuentas de Usuario (`AccountDialog` en `main.py` + `spotilinux_database.json`)**:
  - Permite crear cuentas (`Usuario`, `Correo electrónico`, `Contraseña`) o iniciar sesión en múltiples dispositivos.
  - Las contraseñas se almacenan protegidas mediante hash criptográfico SHA-256 idéntico y determinístico entre Linux y Android (`hash_password` en Python y `hashPassword` en JavaScript con salt `spotilinux_v2:`).
  - Al iniciar sesión con una cuenta en cualquier dispositivo (Linux o Android), se sincronizan automáticamente:
    1. Las playlists del usuario (`playlists`).
    2. Las canciones marcadas como favoritas (`liked_ids`).
    3. El catálogo de canciones de Google Drive (`drive_tracks`) asociadas a su cuenta.
- **Prevención Estricta de Duplicados (`verify_duplicates`)**:
  - Activada mediante el interruptor *"🛡️ Activar verificación de duplicados al descargar canciones de Google Drive"* en el diálogo de configuración.
  - `canonical_drive_track_id(file_id)` genera identificadores deterministas `gd_<fileId>`.
  - Al descargar archivos mediante `GDriveSyncWorker` o `GDriveStreamDownloadWorker`, `find_existing_local_duplicate()` compara el ID canónico, el hash de título/artista normalizado y el tamaño en bytes. Si la canción ya existe en `music_library/`, reutiliza el archivo local y omite la re-descarga.
  - `storage.py::deduplicate_library()` purga automáticamente entradas repetidas en la biblioteca local.
- **Subida de Canciones a Google Drive (`GDriveUploadWorker`)**:
  - Botón **`⬆️ Subir a Drive (Musica)...`** disponible en la barra lateral y en la tarjeta de cuenta.
  - Permite al usuario seleccionar uno o varios archivos de audio locales (`.mp3, .flac, .wav, .m4a, .ogg`) y subirlos directamente mediante HTTP POST multipart/resumable a la subcarpeta `Musica` dentro de `1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT`, verificando previamente que no existan duplicados en la nube.
- **Sincronización Pública y OAuth 2.0**:
  - `list_public_drive_folder_items()` permite explorar y reproducir canciones de la carpeta compartida sin necesidad de ingresar credenciales complejas mediante web scraping seguro de `embeddedfolderview`.
  - Para usuarios con credenciales completas, `GDriveOAuthLoopbackWorker` realiza el handshake OAuth 2.0 estándar.

### 3.4 Persistencia y Carpeta Propia de Música (`storage.py`)
- **`LibraryStore`**:
  - Gestiona `self.music_library_dir = base_dir / "music_library"`.
  - El método `auto_scan_device_to_app_folder()` rastrea automáticamente `~/Music`, `~/Música`, `~/Downloads` y `~/Descargas`, copiando cualquier archivo de audio encontrado a `music_library/` e indexándolo en `data/library.json`.
  - Cuando el usuario importa carpetas o archivos manualmente (o por Drag & Drop), también se copian a `music_library/` (`copy_to_app_folder=True`) para garantizar persistencia total.
  - `add_multiple_tracks_to_playlist(pid, track_ids)` agrega canciones en lote (selección múltiple).

### 3.5 Pantalla de Inicio, Cola de Reproducción por Playlist y Selección Múltiple (`main.py`)
- **Inicio (`current_view == "home"`)**: se oculta `track_table` y se muestra `quick_scroll` con: tarjeta de cuenta Google → cuadrícula de playlists (`QuickAccessCard`: clic = seleccionar para vista previa, botón ▶ = reproducir esa playlist) → **vista previa reducible** de la playlist seleccionada (`_build_home_preview_card`).
  - Estado: `home_preview_key` (`"liked" | "gdrive" | "playlist:<id>"`), `home_preview_collapsed` (botón **➖ Reducir lista / ➕ Mostrar lista**), `home_preview_expanded` (por defecto solo se ven `HOME_PREVIEW_LIMIT = 4` canciones con **▼ Ver todas / ▲ Mostrar menos**).
  - Filas compactas `CompactTrackRow`; botón **↗ Abrir** cambia a la vista completa de la playlist.
- **Regla de la cola (corrección importante)**: al reproducir desde una playlist, `AudioEngine.context_tracks` debe contener **solo** las canciones de esa playlist. Se hace siempre con `_play_track_or_stream_from_cloud(track, lista_de_la_playlist)` o `_play_home_key(key, start_index, shuffle)`. `play_current_view()` reinicia la cola si el contexto actual no coincide con la vista (antes solo pausaba y seguía reproduciendo toda la biblioteca). `AudioEngine.previous_track()` descarta del historial las pistas que no pertenecen a la cola activa. Al arrancar, la cola inicial es la playlist seleccionada en Inicio.
- **Selección múltiple**: `track_table` usa `ExtendedSelection` (Ctrl/Shift + clic) y el menú contextual permite agregar/quitar/crear playlist con todas las seleccionadas. Dentro de una playlist, el botón **＋ Agregar canciones** abre `MultiTrackSelectorDialog` (checkboxes, buscador, seleccionar todas).

---

## 📱 4. Arquitectura Detallada: Versión Móvil Android (`android-app/`)

### 4.1 Por qué el Audio Corre en Java Nativo (`MediaPlaybackService.java`)
> **⚠️ NOTA CRÍTICA PARA FUTURAS IAs**:  
> En versiones iniciales, el audio se reproducía mediante una etiqueta `<audio>` / `WebAudio` dentro del `WebView`. Eso causaba un fallo grave de Android: cuando el usuario pausaba la música con audífonos Bluetooth y dejaba pasar unos minutos con la pantalla apagada, Chromium suspendía el `AudioContext`. Al volver a presionar Play desde los audífonos o desde los Ajustes Rápidos, el contador de tiempo avanzaba pero **no se escuchaba nada** hasta desbloquear y abrir la aplicación.
>
> **Solución implementada (NO REVERTIR NUNCA A WEBVIEW AUDIO)**:
> - Todo el audio real se reproduce en **`MediaPlaybackService.java`** usando **`android.media.MediaPlayer` nativo de Android** + **`android.media.audiofx.Equalizer`** conectado al `audioSessionId` del `MediaPlayer`.
> - El servicio mantiene un **`PowerManager.PARTIAL_WAKE_LOCK`**, un **`WifiManager.WifiLock`** (para streaming de Google Drive con pantalla apagada) y solicita foco de audio oficial de Android (**`AudioFocusRequest`** con `USAGE_MEDIA` y `CONTENT_TYPE_MUSIC`).
> - La **`MediaSession`** nativa tiene activo `FLAG_HANDLES_MEDIA_BUTTONS | FLAG_HANDLES_TRANSPORT_CONTROLS` y el `AndroidManifest.xml` declara el `intent-filter` `android.intent.action.MEDIA_BUTTON` en `MediaPlaybackService`.
> - Cuando el usuario presiona Play/Pausa en sus audífonos Bluetooth/cableados o en la barra de notificaciones/Ajustes Rápidos después de 1 minuto o de 2 horas, `MediaPlaybackService.resumePlayback()` solicita `requestAudioFocus()`, despierta el `WakeLock` y llama a `mediaPlayer.start()` directamente en el proceso nativo de Android, garantizando sonido instantáneo sin abrir la actividad.

### 4.2 Puente Javascript ↔ Java (`MainActivity.java` -> `window.AndroidMedia`)
`MainActivity.java` inyecta el objeto `@JavascriptInterface` llamado **`window.AndroidMedia`** en el `WebView` con los siguientes métodos:

| Método en `window.AndroidMedia` | Descripción técnica |
| :--- | :--- |
| `playSource(uri, headersJson, title, artist, album, durationMs)` | Ordena a `MediaPlaybackService` reproducir un archivo de `assets/www/` (`demo_tracks/...`), un archivo local (`file:///...`), o un stream HTTP directo de Google Drive API v3 (`https://www.googleapis.com/drive/v3/files/<id>?alt=media`) pasando el header `Authorization: Bearer <token>`. |
| `togglePlayPause()` / `pause()` / `resume()` | Controla el `MediaPlayer` nativo y actualiza la notificación `MediaStyle` y la `MediaSession`. |
| `seekTo(posMs)` | Hace `mediaPlayer.seekTo(posMs)` en el motor nativo y actualiza la barra de progreso del sistema Android. |
| `setRepeat(repeatOne)` | Configura `mediaPlayer.setLooping(repeatOne)` en el servicio nativo. |
| `setEqBand(bandIndex, gainDb)` | Ajusta en tiempo real las 5 bandas del `android.media.audiofx.Equalizer` de hardware. |
| `scanDeviceMusic()` | Escanea `MediaStore.Audio.Media.EXTERNAL_CONTENT_URI` en un hilo de fondo, **copia todas las canciones del teléfono a la carpeta persistente de la app** (`/storage/emulated/0/Android/data/com.spotilinux.mobile/files/SpotiLinuxMusic`) y devuelve el arreglo JSON a `window.__onDeviceMusicScanned(json)`. |
| `importAudioFiles()` | Abre el selector de archivos de Android (`ACTION_OPEN_DOCUMENT` con selección múltiple), copia los audios seleccionados a `SpotiLinuxMusic/` y los añade a la biblioteca. |
| `downloadDriveTrack(fileId, fileName, token, apiKey, title, artist)` | Descarga una canción desde Google Drive API v3 directamente a la carpeta persistente `SpotiLinuxMusic/` usando nombres canónicos `gd_<fileId>__<safeTitle>.mp3`, **verifica duplicados** contra la biblioteca local y notifica a `window.__onDriveTrackDownloaded(json)` sin re-descargar archivos redundantes. |
| `fetchPublicDriveFolder(folderId)` | Consulta la carpeta pública de Google Drive (`1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT` y su subcarpeta `Musica`) mediante el motor nativo de Java para evitar problemas de CORS en WebView y entrega los archivos a `window.__onPublicDriveFolderFetched(json)`. |
| `saveAppStateJson(json)` / `loadAppStateJson()` | Guarda y carga en disco nativo (`spotilinux_state.json`) las Playlists, Favoritos, cuentas registradas, credenciales de Google Drive y pistas importadas. |
| `openExternalAuth(url)` | Abre el navegador del sistema para autenticación OAuth de Google si el usuario lo requiere. |

A su vez, `MediaPlaybackService.java` (vía `MainActivity.notifyWebProgress` / `dispatchMediaCommandToWeb` / `dispatchJs`) envía actualizaciones hacia el `WebView` llamando a:
- `window.onNativeProgress(positionMs, durationMs, isPlaying)` → sincroniza el reloj monótono interpolado (`getSmoothPlaybackSec()`) para que la fase del beat a 60 FPS no tenga saltos.
- `window.onNativeSpectrum([b0, b1, ..., b27])` → alimentado por `android.media.audiofx.Visualizer` conectado al `audioSessionId` del `MediaPlayer` nativo (hasta 24 Hz cuando `MainActivity.uiVisible == true`), entregando 28 bandas logarítmicas (`45 Hz – 9 kHz`) que `index.html` procesa con escala dB, piso adaptativo (`specFloor`), amplificación de transitorios (`fluxBoost`) y detector de onsets/BPM en vivo con HUD visible `♩ BPM • 1-2-3-4`. Si el usuario no concede `RECORD_AUDIO`, usa un generador espectral armónico bloqueado al beat.
- `window.onNativeMediaCommand('next' | 'ended_next' | 'prev' | 'play' | 'toggle')` → `nextTrack()` / `prevTrack()` usan la **cola activa** (ver 4.4), así los botones de audífonos/notificación también respetan la playlist.

### 4.3 Interfaz Móvil Responsiva (`android-app/assets/www/index.html`)
- **Adaptación Vertical y Horizontal (`Portrait` & `Landscape`)**:
  - En modo **Vertical (Portrait)**: El escenario de Maomao + Espectro se ubica en la parte superior y la lista de pistas/playlists debajo, con barra de reproducción flotante y navegación inferior.
  - En modo **Horizontal (Landscape)**: Mediante `@media (orientation: landscape) and (max-height: 600px)`, la pantalla se divide automáticamente en **2 columnas lado a lado** (izquierda: Maomao + Espectro FFT; derecha: buscador, pestañas y lista desplazable de canciones/playlists) y oculta los encabezados redundantes para aprovechar todo el alto disponible.
- **Modal de Bienvenida al Inicio (`#welcomeModal`)**:
  - Al abrir la app por primera vez, solicita directamente conectar la cuenta de Google Drive (apuntando por defecto a la carpeta compartida `1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT`) o rastrear y guardar las canciones del dispositivo.
- **Pestañas de Navegación Móvil (barra inferior de 4 botones)**:
  1. `🏠 Inicio`: Tarjeta de cuenta SpotiLinux / Google Drive, cuadrícula de **Playlists Disponibles** (Nueva, Favoritos, Google Drive y playlists del usuario) y tarjeta de la playlist seleccionada (`#activePlaylistBox`) con **▶ Reproducir**, **🔀 Aleatorio**, **➖ Reducir lista / ➕ Mostrar lista**, 🗑 y **＋ Añadir varias canciones**. Por defecto solo muestra 4 canciones (`playlistMaxVisibleCount`) con botón **▼ Ver todas**.
  2. `💿 Canciones`: Biblioteca completa con filtros (Todo / Guardadas / Favoritos / Drive) y modo **☑️ Seleccionar Varias** con barra flotante `＋ A Playlist`.
  3. `☁️ Mi Cuenta`:
     - **Sistema de Cuentas**: Formulario para **Crear Cuenta** (`Usuario`, `Correo`, `Contraseña`) e **Iniciar Sesión**, sincronizado con `spotilinux_database.json` para restaurar playlists y pistas en cualquier dispositivo.
     - **Carpeta de Google Drive**: Pre-configurada con `1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT` (y subcarpeta `Musica`).
     - **Control de Duplicados**: Casilla para activar/desactivar verificación estricta de duplicados al descargar canciones.
     - **Subir Canciones**: Botón `⬆️ Subir a «Musica»` para subir archivos locales de audio a la carpeta compartida.
     - **Doble modo**: Selector global **🎧 En Línea (Streaming directo)** vs **⬇️ Descargar (Offline)** y descarga individual por pista.
  4. `🎚 Ecualizador`: 5 bandas conectadas al DSP nativo + selector de baile de Maomao.

### 4.4 Cola de Reproducción por Playlist (Android)
- Variables globales: `activePlaybackQueue` (lista de pistas), `activeQueueIndex`, `activeQueueName`.
- **`playTrackFromQueue(lista, índice, nombre)`** es la función a usar al reproducir desde una playlist/favoritos/Drive/biblioteca filtrada: fija la cola a esa lista. `nextTrack()`, `prevTrack()` y el aleatorio solo recorren `activePlaybackQueue` (antes recorrían todo `tracks`, por eso sonaban canciones fuera de la playlist).
- `playTrackIndex(idx)` se mantiene por compatibilidad: si la pista ya está en la cola activa solo cambia el índice; si no, la cola pasa a ser toda la biblioteca.
- La barra inferior muestra `Artista • 🎵 <nombre de la cola>` para que el usuario vea qué playlist está sonando.

---

## 🛠️ 5. Comandos de Compilación y Ejecución

### 5.1 Ejecutar o Compilar la Versión de Escritorio Linux
```bash
# 1. Ejecutar directamente con el entorno virtual:
./run.sh

# 2. Recompilar el ejecutable binario ELF nativo (genera dist/SpotiLinux):
./venv/bin/python build_linux_binary.py
```

### 5.2 Compilar y Firmar el APK de Android (`dist/SpotiLinux-Mobile.apk`)
El proyecto incluye un script autónomo (`build_android_apk.sh`) que utiliza OpenJDK 17 (`/usr/lib/jvm/java-17-openjdk`) y las herramientas oficiales del Android SDK (`aapt2`, `d8`, `zipalign`, `apksigner` de Build-Tools `34.0.0`):
```bash
./build_android_apk.sh
```
> **Nota sobre el Android SDK**: El script busca el SDK en `/home/jinsoon005/.gemini/antigravity/scratch/spotify-linux/.android-sdk` (o en la variable de entorno `ANDROID_SDK_ROOT`). Si en otra máquina no existe `.android-sdk`, basta con instalar `platforms;android-34` y `build-tools;34.0.0` con `sdkmanager`.

---

## 🤖 6. Reglas de Oro para Otra IA que Continúe este Proyecto

Si eres un agente de IA encargado de añadir nuevas funciones o corregir detalles en el futuro, **respeta estrictamente estas 9 reglas arquitectónicas**:

1. **Nunca muevas la reproducción de audio de Android de vuelta al `WebView`**:
   - Toda reproducción en Android debe seguir pasando por `window.AndroidMedia.playSource(...)` hacia `MediaPlaybackService.java` (`android.media.MediaPlayer`). El generador de espectro en `index.html` se sincroniza con `window.onNativeProgress(posMs, durMs, isPlaying)` para animar a Maomao sin bloquear el audio nativo ni depender de `AudioContext` en segundo plano.
2. **Mantén la persistencia de archivos en las carpetas propias de la app**:
   - En Linux, las pistas rastreadas/importadas/descargadas se guardan en `music_library/`.
   - En Android, las pistas rastreadas (`scanDeviceMusic`), importadas (`importAudioFiles`) o descargadas de Drive (`downloadDriveTrack`) se copian a `getExternalFilesDir(null)/SpotiLinuxMusic`.
3. **Sincroniza el estado de Android con `saveAppStateJson`**:
   - Cada vez que se modifiquen `playlists`, favoritos (`liked`), credenciales de Drive o `tracks` en `android-app/assets/www/index.html`, debe invocarse `savePersistentState()` (escribe en `localStorage` y en el archivo nativo `spotilinux_state.json` mediante `window.AndroidMedia.saveAppStateJson`) y, si cambian playlists/favoritos, `uploadPlaylistsToDrive()`.
4. **Paridad de Funciones entre Linux (`main.py`) y Android (`android-app/`)**:
   - Cuando el usuario pida un cambio funcional (nuevas vistas, ajustes de Google Drive, pasos de baile de Maomao, playlists), verifica si aplica a ambas plataformas y actualiza tanto los módulos Python como `android-app/`.
5. **Recompila siempre los artefactos de salida en `dist/`**:
   - Después de editar el código fuente Python o Java/HTML, ejecuta siempre `./venv/bin/python build_linux_binary.py` y `./build_android_apk.sh` para que `dist/SpotiLinux` y `dist/SpotiLinux-Mobile.apk` contengan los cambios más recientes, y copia ambos a la raíz del repo (`SpotiLinux`, `SpotiLinux-Mobile.apk`).
6. **La cola de reproducción siempre es la playlist que el usuario eligió**:
   - Linux: pasa la lista de la playlist como `context_list` (`_play_track_or_stream_from_cloud` / `_play_home_key`). Android: usa `playTrackFromQueue(...)`. Nunca vuelvas a recorrer toda la biblioteca en `next/prev/shuffle` cuando se está reproduciendo una playlist.
7. **El Inicio muestra playlists, no la biblioteca**:
   - La lista de canciones de la playlist seleccionada en Inicio debe seguir siendo reducible (➖/➕) y limitada por defecto (4 canciones, “Ver todas”). La biblioteca completa vive en `💿 Tu Biblioteca` (Linux) / `💿 Canciones` (Android).
8. **En PyQt6, nunca uses `setStyleSheet("background: transparent;")` sin selector de `#objectName` en contenedores padres**:
   - Una regla sin selector en un `QScrollArea` o `QWidget` contenedor se hereda en cascada a todos los `QPushButton` hijos y borra los estilos globales de `styles.py`. Usa siempre `#NombreDelContenedor { background: transparent; border: none; }`.
9. **Carpeta Raíz Oficial de Google Drive, Subcarpeta `Musica` y Verificación de Duplicados**:
   - La carpeta raíz compartida oficial es `1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT`. La base de datos de cuentas (`spotilinux_database.json`) se guarda en la raíz, mientras que todas las canciones residen en la subcarpeta `Musica`.
   - Utiliza siempre identificadores canónicos `gd_<fileId>` y verifica duplicados locales (`find_existing_local_duplicate` / `deduplicate_library`) antes de descargar o agregar canciones para evitar redundancias de archivos y registros duplicados.
