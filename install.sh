#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# MaoMusic (SpotiLinux) — Instalador Portable y Automatizado para Linux Desktop
# ==============================================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

DRY_RUN=0
NO_SYSTEM_DEPS=0
UNINSTALL=0

show_help() {
    cat <<EOF
Uso: ./install.sh [OPCIONES]

Instala MaoMusic de forma portable y prepara el entorno en tu distribución Linux.

Opciones:
  --dry-run           Muestra las acciones que se realizarían sin modificar nada.
  --no-system-deps    Omite la instalación de paquetes con sudo/gestor de paquetes del sistema.
  --uninstall         Elimina el acceso directo de escritorio y el binario en ~/.local/bin.
  -h, --help          Muestra este mensaje de ayuda.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=1
            ;;
        --no-system-deps)
            NO_SYSTEM_DEPS=1
            ;;
        --uninstall)
            UNINSTALL=1
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Opción no reconocida: $arg"
            show_help
            exit 1
            ;;
    esac
done

# Modo Desinstalación
if [ "$UNINSTALL" -eq 1 ]; then
    echo "🗑️ Desinstalando accesos directos de MaoMusic..."
    DESKTOP_TARGET="${HOME}/.local/share/applications/maomusic.desktop"
    BIN_TARGET="${HOME}/.local/bin/maomusic"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[DRY-RUN] Se eliminaría: $DESKTOP_TARGET"
        echo "[DRY-RUN] Se eliminaría: $BIN_TARGET"
        echo "[DRY-RUN] (Los datos de usuario en data/ y music_library/ se conservan intactos)."
        exit 0
    fi

    rm -f "$DESKTOP_TARGET"
    rm -f "$BIN_TARGET"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${HOME}/.local/share/applications" 2>/dev/null || true
    fi
    echo "✔ Desinstalación completada. (Tus playlists y canciones locales en data/ y music_library/ se conservaron)."
    exit 0
fi

echo "═══════════════════════════════════════════════════════════════════"
echo " 🌿 Instalador Oficial de MaoMusic Desktop"
echo "═══════════════════════════════════════════════════════════════════"

# 1. Detección de Distribución Linux
DISTRO="unknown"
if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    ID_LOWER="$(echo "${ID:-}" | tr '[:upper:]' '[:lower:]')"
    LIKE_LOWER="$(echo "${ID_LIKE:-}" | tr '[:upper:]' '[:lower:]')"

    if [[ "$ID_LOWER" == *"arch"* || "$ID_LOWER" == *"manjaro"* || "$LIKE_LOWER" == *"arch"* ]]; then
        DISTRO="arch"
    elif [[ "$ID_LOWER" == *"debian"* || "$ID_LOWER" == *"ubuntu"* || "$ID_LOWER" == *"mint"* || "$LIKE_LOWER" == *"debian"* ]]; then
        DISTRO="debian"
    elif [[ "$ID_LOWER" == *"fedora"* || "$ID_LOWER" == *"rhel"* || "$LIKE_LOWER" == *"fedora"* ]]; then
        DISTRO="fedora"
    elif [[ "$ID_LOWER" == *"suse"* || "$LIKE_LOWER" == *"suse"* ]]; then
        DISTRO="opensuse"
    fi
fi
echo "➜ Distribución detectada: $DISTRO"

# 2. Instalación de dependencias del sistema (opcional)
if [ "$NO_SYSTEM_DEPS" -eq 0 ]; then
    echo "➜ Verificando dependencias del sistema con gestor nativo..."
    CMD_SYS=""
    case "$DISTRO" in
        arch)
            CMD_SYS="sudo pacman -S --needed --noconfirm python ffmpeg"
            ;;
        debian)
            CMD_SYS="sudo apt update && sudo apt install -y python3 python3-venv ffmpeg libxcb-cursor0 libgl1"
            ;;
        fedora)
            CMD_SYS="sudo dnf install -y python3 ffmpeg-free"
            ;;
        opensuse)
            CMD_SYS="sudo zypper install -y python3 ffmpeg"
            ;;
        *)
            echo "⚠ Distribución no identificada automáticamente. Continuando con dependencias de usuario (--no-system-deps)."
            ;;
    esac

    if [ -n "$CMD_SYS" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "[DRY-RUN] Comando del sistema: $CMD_SYS"
        else
            echo "Ejecutando: $CMD_SYS"
            eval "$CMD_SYS" || echo "⚠ Aviso: No se pudieron instalar algunos paquetes del sistema vía sudo. Se intentará continuar con entorno local."
        fi
    fi
else
    echo "➜ Omitiendo dependencias del sistema (--no-system-deps activo)."
fi

# 3. Comprobación de versión de Python (mínimo 3.10)
PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Error crítico: Se requiere Python 3.10 o superior para ejecutar MaoMusic."
    exit 1
fi
echo "✔ Python compatible encontrado: $("$PYTHON_BIN" --version)"

# 4. Creación y actualización de Virtualenv
if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY-RUN] Se crearía virtualenv en: $DIR/venv"
    echo "[DRY-RUN] Se instalarían dependencias desde: $DIR/requirements.txt"
else
    if [ ! -d "$DIR/venv" ]; then
        echo "➜ Creando entorno virtual Python en ./venv..."
        "$PYTHON_BIN" -m venv "$DIR/venv"
    fi

    echo "➜ Instalando y actualizando paquetes en ./venv..."
    "$DIR/venv/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
    "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"
fi

# 5. Generación de lanzador de escritorio y comando global
APP_DIR="$DIR"
DESKTOP_DIR="${HOME}/.local/share/applications"
BIN_DIR="${HOME}/.local/bin"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY-RUN] Se generaría: $DESKTOP_DIR/maomusic.desktop"
    echo "[DRY-RUN] Se crearía enlace simbólico: $BIN_DIR/maomusic -> $APP_DIR/run.sh"
else
    mkdir -p "$DESKTOP_DIR" "$BIN_DIR"

    # Generar .desktop a partir de plantilla .in
    if [ -f "$DIR/MaoMusic.desktop.in" ]; then
        sed "s|@APP_DIR@|$APP_DIR|g" "$DIR/MaoMusic.desktop.in" > "$DESKTOP_DIR/maomusic.desktop"
        chmod 644 "$DESKTOP_DIR/maomusic.desktop"
        echo "✔ Acceso directo creado en: $DESKTOP_DIR/maomusic.desktop"
    fi

    # Generar symlink en ~/.local/bin/maomusic
    ln -sf "$APP_DIR/run.sh" "$BIN_DIR/maomusic"
    chmod +x "$APP_DIR/run.sh"
    echo "✔ Comando rápido creado en: $BIN_DIR/maomusic"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi
fi

# 6. Ejecutar diagnóstico de entorno (doctor.py)
echo ""
echo "➜ Ejecutando diagnóstico de entorno final..."
if [ "$DRY_RUN" -eq 1 ]; then
    echo "[DRY-RUN] Se ejecutaría: $DIR/venv/bin/python doctor.py"
else
    "$DIR/venv/bin/python" "$DIR/doctor.py"
fi

echo ""
echo "🎉 ¡Instalación completada con éxito!"
echo "• Para iniciar la app desde la terminal:  ./run.sh  (o escribe 'maomusic')"
echo "• También la encontrarás en el menú de aplicaciones de tu escritorio como 'MaoMusic'."
