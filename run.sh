#!/usr/bin/env bash
set -e

# Resolver la ruta real del script incluso cuando se invoca mediante symlink (ej. ~/.local/bin/maomusic)
TARGET="${BASH_SOURCE[0]}"
while [ -L "$TARGET" ]; do
    DIR="$(cd -P "$(dirname "$TARGET")" >/dev/null 2>&1 && pwd)"
    TARGET="$(readlink "$TARGET")"
    [[ $TARGET != /* ]] && TARGET="$DIR/$TARGET"
done
DIR="$(cd -P "$(dirname "$TARGET")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

# Forzar el backend de pulseaudio para Qt Multimedia en PipeWire/Wayland (evita deadlock en pw_thread_loop_lock)
export QT_AUDIO_BACKEND="${QT_AUDIO_BACKEND:-pulseaudio}"

echo "➜ Iniciando MaoMusic Desktop..."

if [ -x "$DIR/venv/bin/python" ]; then
    exec "$DIR/venv/bin/python" "$DIR/main.py" "$@"
else
    exec python3 "$DIR/main.py" "$@"
fi
