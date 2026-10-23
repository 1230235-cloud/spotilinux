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
- **Nube Privada con Google Drive (Prompt Inicial + Autodetección de Carpeta `Music`/`Música` + Streaming vs. Descarga)**:
  - Solicita conectarse a la cuenta de Google Drive desde el primer arranque (o mediante el botón `☁️ Drive`).
  - Soporta inicio de sesión OAuth 2.0 (flujo automático en navegador / loopback, carga de `credentials.json`, Token OAuth2 `ya29...` o API Key).
  - **Búsqueda automática de la carpeta de música**: Si el usuario no especifica un ID de carpeta, consulta automáticamente Google Drive API v3 buscando la carpeta `Music` / `Música` / `Musica` del usuario e importa todo su catálogo de audio.
  - **Doble modo de reproducción en la nube**: Permite elegir globalmente o por canción entre **🎧 Escuchar en línea (Stream directo)** o **⬇️ Descargar a la carpeta de la aplicación (Offline)**.
- **Motor de Audio Nativo en Segundo Plano en Android (Solución al Silencio tras Pausa Prolongada con Audífonos)**:
  - Toda la reproducción de audio en Android ocurre dentro del servicio nativo en primer plano `MediaPlaybackService.java` usando `android.media.MediaPlayer` + `android.media.audiofx.Equalizer` + `PARTIAL_WAKE_LOCK` + `WifiLock` + `AudioFocusRequest` + `MediaSession` (`FLAG_HANDLES_MEDIA_BUTTONS`).
  - Esto garantiza que al pausar con audífonos Bluetooth/cableados o desde los Ajustes Rápidos de Android y esperar varios minutos, al volver a despausar con los audífonos o desde la pantalla de bloqueo **el audio siempre suene inmediatamente** sin necesidad de abrir la aplicación (evitando la suspensión del `AudioContext` de Chromium WebView).
- **Gestión Completa de Playlists y Favoritos (`♥`) con Selección Múltiple y Pantalla Principal de Playlists**:
  - **Página Principal estilo Spotify**: Tanto en Linux como en Android, la pantalla de Inicio (`🏠 Inicio`) no muestra una lista plana de canciones sueltas; en su lugar, destaca las **Playlists Disponibles** (Favoritos, Google Drive, playlists personalizadas y tarjeta para crear una nueva). Al pulsar cualquier playlist se abre su vista detallada con sus pistas.
  - **Selección Múltiple de Canciones**: Permite seleccionar varias canciones a la vez con checkboxes (`☑️`) o selección extendida (`Ctrl+Click`/`Shift+Click`) y agregarlas todas juntas a cualquier playlist existente o nueva con un solo clic.
  - **Sincronización en la Nube con Cuenta de Google & Google Drive**: Al vincular la cuenta de Google, SpotiLinux sincroniza automáticamente las playlists y favoritos en `spotilinux_cloud_playlists.json` dentro del Google Drive del usuario, permitiendo acceder a las mismas listas desde cualquier teléfono o PC con esa cuenta (roaming multiplataforma estilo Spotify).
- **Figura Animada de Maomao (*The Apothecary Diaries*) Bailando al Compás**:
  - Renderizado vectorial a 60 FPS (`QPainter` en Linux y HTML5 `Canvas` en Android) donde Maomao baila sincronizada en tiempo real con las bandas de frecuencia de la música (sub-bajos `20–160 Hz` marcan el paso y rebote de rodillas; medios `200–2000 Hz` mueven el torso, abanico/hierbas medicinales y mangas del hanfu; agudos `2–12 kHz` animan las orejitas de gato, el parpadeo y las partículas brillantes). Incluye 4 pasos de baile intercambiables.
- **Visualizador FFT en Vivo (60 FPS) y Ecualizador de 5 Bandas**:
  - Bandas en `60 Hz`, `230 Hz`, `910 Hz`, `3.6 kHz` y `14 kHz` con presets (*Plano, Bass Boost, Synthwave, Lo-Fi Chill, Vocal, Rock, Club*) y visualizador en modos *Barras*, *Onda* y *Radial*.

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
├── audio_engine.py               # Motor QMediaPlayer + decodificador PCM/FFT NumPy + EQ FFmpeg
├── maomao_widget.py              # Widget QPainter a 60 FPS de Maomao bailando al ritmo del espectro
├── visualizer_widget.py          # Visualizador FFT (Barras/Onda/Radial) y panel del Ecualizador
├── gdrive_sync.py                # Cliente Google Drive API v3, OAuth2 loopback, auto-carpeta Music y caché
├── storage.py                    # Persistencia JSON, Playlists, Favoritos y carpeta music_library/
├── metadata.py                   # Extractor ID3/FLAC/Vorbis (mutagen), portadas APIC y color dominante
├── styles.py                     # Hoja de estilos QSS oscura estilo Spotify (#121212 / #1DB954)
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
    ├── AndroidManifest.xml       # Permisos, MainActivity y MediaPlaybackService (foreground mediaPlayback)
    ├── src/com/spotilinux/mobile/
    │   ├── MainActivity.java         # Puente @JavascriptInterface (AndroidMedia), escáner MediaStore, descargas Drive y persistencia JSON
    │   └── MediaPlaybackService.java # Servicio nativo en 1er plano con MediaPlayer, Equalizer, AudioFocus, WakeLock, MediaSession y Notificación
    ├── assets/www/
    │   ├── index.html                # UI móvil responsiva (vertical/horizontal), Maomao Canvas, Playlists, Google Drive y controles conectados a Java
    │   └── demo_tracks/              # Pistas demo integradas dentro del APK
    └── res/                          # Iconos mipmap y recursos Android
```

---

## 🖥️ 3. Arquitectura Detallada: Versión de Escritorio Linux

### 3.1 Flujo de Audio, Espectro FFT y Ecualizador (`audio_engine.py`)
- **`AudioEngine`**:
  - Utiliza `QMediaPlayer` y `QAudioOutput` de `PyQt6.QtMultimedia` para reproducción de baja latencia.
  - Cuando se carga una pista, lanza **`WaveformDecodeWorker` (`QThread`)**, que invoca `ffmpeg` para decodificar la pista a PCM mono (`22050 Hz`, `int16`) en memoria RAM.
  - Un `QTimer` a **60 FPS (`16 ms`)** calcula la ventana de muestras correspondiente al milisegundo exacto de reproducción (`player.position()`), aplica una ventana de Hanning y ejecuta `np.fft.rfft`, agrupando las magnitudes en **28 bandas logarítmicas** (`bars`) y **96 puntos de forma de onda** (`waveform`).
  - Emite la señal `spectrum_updated(bars, waveform)` en cada fotograma hacia `AudioVisualizerWidget` y `MaomaoDancerWidget`.
- **Ecualizador de 5 Bandas (`EQRenderWorker`)**:
  - Cuando el usuario activa el ecualizador o cambia una banda (`60Hz, 230Hz, 910Hz, 3.6kHz, 14kHz`), `EQRenderWorker` ejecuta `ffmpeg` en segundo plano con una cadena de filtros `equalizer=f=...:width_type=o:w=1.2:g=...` y hace un *hot-swap* transparente manteniendo el milisegundo exacto de reproducción (`seek_ms`).

### 3.2 Animación de Maomao al Compás (`maomao_widget.py`)
- **`MaomaoDancerWidget`**:
  - Recibe las 28 barras del espectro a 60 FPS en `update_from_spectrum(bars, is_playing)`.
  - Divide la energía en tres bandas musicales:
    - **Sub-bajos y graves (`bars[0:6]`)**: Controlan el detector de pulso/beat (`beat_pulse`), el rebote vertical de rodillas (`bounce`) y los anillos de luz bajo sus pies.
    - **Medios y voces (`bars[6:16]`)**: Controlan el balanceo lateral de caderas (`hip_sway`), la inclinación de cabeza (`head_tilt`) y el movimiento de brazos sosteniendo su abanico o ramita de hierbas medicinales.
    - **Agudos (`bars[16:28]`)**: Controlan el movimiento de las orejitas de gato (`ear_twitch`), el vuelo de los listones del cabello y la emisión de partículas/notas musicales.
  - Soporta 4 estilos de baile conmutables (`next_dance_style()`):
    1. `0`: *Danza Imperial con Abanico*
    2. `1`: *Pasito Boticaria Feliz (Hierbas)*
    3. `2`: *Neko Groove Nocturno*
    4. `3`: *Giro Ceremonial del Palacio*

### 3.3 Nube Privada de Google Drive (`gdrive_sync.py`)
- **`GoogleDriveConfig`**: Almacena `access_token`, `refresh_token`, `client_id`, `client_secret`, `api_key` y `folder_id`.
- **`GDriveOAuthLoopbackWorker`**: Levanta un servidor HTTP temporal en `127.0.0.1:8765`, abre el navegador del sistema en `https://accounts.google.com/o/oauth2/v2/auth` con scopes `drive.readonly` + `drive.file` (este último es necesario para escribir el JSON de playlists en la nube), captura el `code` de autorización y lo canjea automáticamente por `access_token` y `refresh_token`.
- **Cuenta de Google y Playlists en la nube**:
  - `fetch_google_user_profile(token)` → lee `drive/v3/about?fields=user` (nombre, correo, foto) y se guarda en `LibraryStore.google_user`.
  - `sync_cloud_playlists_with_drive(token, payload)` + `GDriveCloudPlaylistSyncWorker` (QThread) → busca/crea `spotilinux_cloud_playlists.json` en el Drive, **combina** (unión, nunca borra) playlists y `likedIds` locales con los de la nube y sube el resultado. Formato compartido con Android: `{app, version, updatedAt, user, playlists:[{id,name,description,track_ids|trackIds}], likedIds}` (Linux usa `track_ids`, Android `trackIds`; ambos lados aceptan los dos).
  - `main.py::_sync_playlists_to_cloud()` se llama al iniciar con token, al conectar la cuenta, al crear/editar/borrar playlists, al agregar/quitar canciones y al marcar favoritos.
- **`GDriveSyncWorker`**:
  - Si `config.folder_id` está vacío, realiza primero una consulta automática a Google Drive API v3 buscando carpetas llamadas `Music`, `Música` o `Musica`:
    ```sql
    trashed = false and mimeType = 'application/vnd.google-apps.folder' and (name contains 'Music' or name contains 'Musica' or name contains 'Música')
    ```
  - Lista todas las pistas de audio (`mimeType contains 'audio/'` o extensiones `.mp3, .flac, .wav, .m4a, .ogg, .opus`).
  - Si el usuario eligió **Descargar a la aplicación (`download_immediately=True`)**, descarga los archivos a `music_library/` y extrae sus etiquetas ID3. Si eligió **Escuchar en línea (`download_immediately=False`)**, los registra con URI `gdrive://<file_id>/<filename>` para streaming bajo demanda mediante `GDriveStreamDownloadWorker`.

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
| `downloadDriveTrack(fileId, fileName, token, apiKey, title, artist)` | Descarga una canción desde Google Drive API v3 directamente a la carpeta persistente `SpotiLinuxMusic/` en un hilo nativo de Java y notifica a `window.__onDriveTrackDownloaded(json)` para que quede disponible sin conexión. |
| `saveAppStateJson(json)` / `loadAppStateJson()` | Guarda y carga en disco nativo (`spotilinux_state.json`) las Playlists, Favoritos, credenciales de Google Drive y pistas importadas. |
| `openExternalAuth(url)` | Abre el navegador del sistema para autenticación OAuth de Google si el usuario lo requiere. |

A su vez, `MediaPlaybackService.java` (vía `MainActivity.notifyWebProgress` / `dispatchMediaCommandToWeb`) envía actualizaciones cada `250 ms` hacia el `WebView` llamando a:
- `window.onNativeProgress(positionMs, durationMs, isPlaying)`
- `window.onNativeMediaCommand('next' | 'ended_next' | 'prev' | 'play' | 'toggle')` → `nextTrack()` / `prevTrack()` usan la **cola activa** (ver 4.4), así los botones de audífonos/notificación también respetan la playlist.

### 4.3 Interfaz Móvil Responsiva (`android-app/assets/www/index.html`)
- **Adaptación Vertical y Horizontal (`Portrait` & `Landscape`)**:
  - En modo **Vertical (Portrait)**: El escenario de Maomao + Espectro se ubica en la parte superior y la lista de pistas/playlists debajo, con barra de reproducción flotante y navegación inferior.
  - En modo **Horizontal (Landscape)**: Mediante `@media (orientation: landscape) and (max-height: 600px)`, la pantalla se divide automáticamente en **2 columnas lado a lado** (izquierda: Maomao + Espectro FFT; derecha: buscador, pestañas y lista desplazable de canciones/playlists) y oculta los encabezados redundantes para aprovechar todo el alto disponible.
- **Modal de Bienvenida al Inicio (`#welcomeModal`)**:
  - Al abrir la app por primera vez, solicita directamente conectar la cuenta de Google Drive (buscando automáticamente la carpeta `Music` / `Música` en el Drive del usuario) o rastrear y guardar las canciones del dispositivo.
- **Pestañas de Navegación Móvil (barra inferior de 4 botones)**:
  1. `🏠 Inicio`: Tarjeta de cuenta de Google, cuadrícula de **Playlists Disponibles** (Nueva, Favoritos, Google Drive y playlists del usuario) y tarjeta de la playlist seleccionada (`#activePlaylistBox`) con **▶ Reproducir**, **🔀 Aleatorio**, **➖ Reducir lista / ➕ Mostrar lista**, 🗑 y **＋ Añadir varias canciones**. Por defecto solo muestra 4 canciones (`playlistMaxVisibleCount`) con botón **▼ Ver todas**.
  2. `💿 Canciones`: Biblioteca completa con filtros (Todo / Guardadas / Favoritos / Drive) y modo **☑️ Seleccionar Varias** con barra flotante `＋ A Playlist`.
  3. `☁️ Mi Cuenta`: Cuenta de Google, sincronización de `spotilinux_cloud_playlists.json`, `🔍 Buscar carpeta "Music" e Importar`, selector **🎧 En Línea / ⬇️ Descargar** y descarga individual por pista.
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

Si eres un agente de IA encargado de añadir nuevas funciones o corregir detalles en el futuro, **respeta estrictamente estas 7 reglas arquitectónicas**:

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
