# 🌿 Guía de Arquitectura, Contexto y Reglas de Desarrollo para Agentes IA — MaoMusic

> **Destinatario**: Cualquier Agente de Inteligencia Artificial que continúe, mantenga, extienda o depure el ecosistema **MaoMusic** (anteriormente conocido como *SpotiLinux*).
> **Fecha de última actualización**: Octubre 2026.
> **Versión base**: v2.8.3 (versionCode 20).

---

## 1. Visión General y Propósito del Proyecto

**MaoMusic** es una plataforma musical completa y multiplataforma con estética premium OLED Dark (`#09090b`), inspirada en Spotify y en el personaje de **Maomao** (*Kusuriya no Hitorigoto* / Los diarios de la boticaria). El ecosistema está compuesto por dos núcleos principales sincronizados:

1. **Cliente de Escritorio para Linux**:
   - Desarrollado en Python 3.14 con **PyQt6**, motor de audio desacoplado (FFT / Pygame / QMediaPlayer) y empaquetado como binario nativo ELF autónomo (`dist/MaoMusic`, `./MaoMusic`, `./SpotiLinux`).
2. **Cliente Móvil para Android**:
   - Aplicación híbrida de alto rendimiento (`android-app/`) con Android nativo (Java 11, `android-34`), `WebView` acelerado por hardware, `ForegroundService` para reproducción continua en segundo plano y lienzo Canvas a **60 FPS** para la animación procedimental de Maomao.
3. **Nube Privada Multi-dispositivo (Google Drive Cloud & Web App Proxy)**:
   - Sincronización transparente de playlists, favoritos, historial y base de datos de cuentas sin necesidad de un backend tradicional costoso, apalancando la API v3 de Google Drive y un micro-proxy en Google Apps Script.

---

## 2. Infraestructura en la Nube y Secretos / IDs Canónicos

El sistema utiliza Google Drive como almacenamiento compartido de música, videos y base de datos JSON de usuarios. **NO modifiques ni elimines estos identificadores**, ya que romperías la sincronización existente entre dispositivos:

| Recurso | ID / URL Canónica | Propósito |
|---|---|---|
| **Carpeta Raíz Drive** | `1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT` | Carpeta raíz donde reside la base de datos `spotilinux_database.json` |
| **Subcarpeta `Musica`** | `1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T` | Directorio de canciones MP3, FLAC, WAV, M4A, etc. accesibles por streaming |
| **Subcarpeta `videos`** | `1tKyv8TEFVGP0f-r8ohMeDj5lz3OC-bsa` | Subcarpeta con videos musicales `.mp4` para el modo Vinilo con video de fondo |
| **Google Apps Script Proxy** | `https://script.google.com/macros/s/AKfycbzwOO_eEw7ldBwGUPwX_MqYZfgRA6-N9meQ_z8k43V-0KBc0cmj43ap2qV6vT0ynO4dIQ/exec` | Backend serverless en Apps Script para listar, descargar y sincronizar la base de datos sin requerir OAuth interactivo obligatorio para lectura pública |
| **Servidor Cloud REST de respaldo** | `https://api.restful-api.dev/objects/ff808181a09d98f701a16e7026147e2a` | Endpoint JSON REST complementario para telemetría y sincronización rápida |
| **Repositorio Git Oficial** | `https://github.com/1230235-cloud/spotilinux.git` | Rama principal: `main` |

El código fuente del script de Google Apps Script está respaldado localmente en `google_drive_server_backend.gs`.

---

## 3. Estructura del Código y Componentes

```
.
├── MaoMusic                       # Ejecutable nativo ELF para Linux (raíz)
├── SpotiLinux                     # Ejecutable nativo ELF (alias de retrocompatibilidad)
├── MaoMusic.desktop               # Lanzador XDG de escritorio Linux
├── SpotiLinux.desktop             # Lanzador XDG heredado
├── icon.png                       # Icono oficial (Maomao durmiente 512x512 PNG)
├── MaoMusic.apk                   # APK firmado instalable para Android (raíz)
├── SpotiLinux-Mobile.apk          # APK firmado (alias de retrocompatibilidad)
├── version.json                   # Manifiesto de versiones y auto-actualizaciones
├── agentes.md                     # Este documento de referencia
│
├── main.py                        # GUI Linux Desktop en PyQt6
├── audio_engine.py                # Motor de decodificación de audio y espectro FFT
├── maomao_widget.py               # Widget QPainter de Maomao bailarina para Linux
├── gdrive_sync.py                 # Conector cliente Google Drive / Apps Script
├── storage.py                     # Base de datos local JSON/SQLite y gestor de playlists
├── metadata.py                    # Extractor de etiquetas ID3 y portadas en caché
├── rhythm.py                      # Detección de BPM y compás rítmico
├── visualizer_widget.py           # Barras de ecualizador y espectrograma Qt
│
├── build_linux_binary.py          # Script empaquetador ELF en C + GCC
├── build_android_apk.sh           # Script de compilación sin Gradle del APK Android
├── .android-sdk/                  # SDK local (aapt2, d8, zipalign, apksigner, android-34)
│
└── android-app/                   # Proyecto Android
    ├── AndroidManifest.xml        # Declaración de permisos, servicios y proveedores
    ├── res/                       # Iconos y drawables (`res/drawable/ic_launcher.png`)
    ├── src/com/spotilinux/mobile/
    │   ├── MainActivity.java      # Interfaz WebView, puente JS y selector de archivos
    │   ├── MediaPlaybackService.java # Servicio en segundo plano con notificación interactiva
    │   └── GenericFileProvider.java  # FileProvider para instalación automática del APK
    └── assets/www/
        └── index.html             # SPA completa: HTML5, Web Audio API, Canvas 60 FPS
```

---

## 4. Reglas Críticas e Invariantes para Agentes

Al realizar modificaciones en el proyecto, debes cumplir estrictamente las siguientes reglas:

### Regla 1: Compilación Dual Obligatoria
Cualquier cambio realizado en la aplicación web (`android-app/assets/www/index.html`), en Java (`MainActivity.java`) o en Python (`main.py`) **exige recompilar ambos artefactos**:
1. **APK Android**: Ejecutar `./build_android_apk.sh`. Esto generará y firmará el APK con zipalign y apksigner en `dist/MaoMusic.apk`, copiándolo a la raíz como `MaoMusic.apk` y `SpotiLinux-Mobile.apk`.
2. **Binario Linux**: Ejecutar `./venv/bin/python build_linux_binary.py`. Esto compilará el wrapper ELF mediante `gcc -O2`, empaquetará los módulos Python en ZIP base64 embebido y generará `dist/MaoMusic`, `./MaoMusic`, `dist/SpotiLinux` y `./SpotiLinux`.

### Regla 2: Incremento de Versión para Auto-actualizaciones
El sistema móvil incluye un comprobador automático de versiones (`checkGitHubUpdate` en JS y `_check_github_updates` en Python). Para que los dispositivos reconozcan una nueva actualización:
- Incrementa `versionCode` (entero secuencial) y `versionName` (semver) en `version.json`.
- Incrementa en idéntico valor `android:versionCode` y `android:versionName` en `android-app/AndroidManifest.xml`.
- Añade el resumen de novedades en el arreglo `"changelog"` de `version.json`.
- Si `versionCode` no se incrementa, los teléfonos instalados informarán *"MaoMusic ya está al día"* y descartarán la descarga.

### Regla 3: Selector de Archivos en Android (`onShowFileChooser`)
- Al permitir selección de fotos de perfil (`#avatarPicker`), el intent debe configurarse estrictamente con `intent.setType("image/*")`.
- Al permitir importación de canciones locales (`#filePicker`), el intent se configura con `intent.setType("audio/*")` y `EXTRA_ALLOW_MULTIPLE`.
- En `MainActivity.java`, `filePathCallback.onReceiveValue(results)` debe despacharse para que el `input[type=file]` de HTML reciba los datos. Nunca debe enviarse `null` cuando se seleccionan imágenes de perfil.

### Regla 4: Modo Vinilo y Videos de Fondo (`subcarpeta videos/`)
- El reproductor de vinilo reproduce de fondo el video musical sincronizado cuando existe en la subcarpeta `videos/` de Drive (`1tKyv8TEFVGP0f-r8ohMeDj5lz3OC-bsa`) o en almacenamiento local (`.mp4`).
- El emparejamiento de nombres utiliza la función `matchVideoForTrack(track, videosList)`, la cual:
  - Limpia caracteres acentuados y normaliza unicode (`normalize("NFD")`).
  - Decodifica entidades HTML (`&amp;` -> `&`, `&#39;` -> `'`).
  - Descarta sufijos comunes como `(official video)`, `[lyrics]`, `mv`, `hd`, etc.
- Cuando el audio se pausa, el video debe pausarse; cuando el audio avanza, el video se sincroniza (`syncVinylVideoPlayback`).

### Regla 5: Algoritmo de Modo Aleatorio (Shuffle Sin Repetición)
- En modo aleatorio (`shuffleMode = true`), nunca se deben repetir pistas hasta agotar toda la lista disponible.
- Se gestiona mediante una "pila/bolsa" de índices barajados (`shufflePool`).
- Al llegar al final de la cola, la bolsa se vuelve a barajar excluyendo la última pista reproducida para evitar repeticiones consecutivas inmediatas.

### Regla 6: Rendimiento a 60 FPS en WebView
- **Lienzo de Maomao**: Maomao se dibuja proceduralmente usando `<canvas id="maomaoCanvas">` mediante un bucle `requestAnimationFrame`.
- No toques el DOM ni re-renderices listas de canciones completas dentro del bucle de animación o dentro de `ontimeupdate`. Modifica únicamente nodos atómicos (`textContent` o `style.transform`) para evitar caídas de fotogramas o *jank*.

---

## 5. Comandos de Compilación y Verificación

```bash
# Activar entorno virtual
source venv/bin/activate

# Ejecutar aplicación de escritorio en modo desarrollo
python main.py

# Recompilar ejecutable binario ELF de Linux Desktop
./venv/bin/python build_linux_binary.py

# Compilar y firmar el APK de Android
./build_android_apk.sh

# Inspeccionar el estado de Git
git status
```

---

## 6. Publicación a GitHub
Para publicar una nueva versión:
1. Asegurar que `git status` muestre los binarios actualizados (`MaoMusic`, `SpotiLinux`, `MaoMusic.apk`, `SpotiLinux-Mobile.apk`, `dist/*`).
2. Realizar commit semántico: `git commit -m "feat: [descripción] vX.Y.Z"`.
3. Subir a GitHub: `git push origin main`.
