#!/usr/bin/env python3
"""
Empaquetador del ejecutable nativo ELF de Linux (dist/SpotiLinux) y lanzador .desktop.
"""

import base64
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"

MODULES = [
    "main.py",
    "audio_engine.py",
    "rhythm.py",
    "visualizer_widget.py",
    "maomao_widget.py",
    "gdrive_sync.py",
    "metadata.py",
    "storage.py",
    "styles.py",
    "generate_demos.py",
]


def generate_app_icon(icon_path: Path) -> None:
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Círculo exterior negro/esmeralda
    draw.ellipse((8, 8, size - 8, size - 8), fill=(18, 22, 20, 255), outline=(29, 185, 84, 255), width=8)
    # Círculo interior verde Spotify
    draw.ellipse((28, 28, size - 28, size - 28), fill=(29, 185, 84, 255))

    # Silueta estilizada de moños de Maomao + ondas de música
    draw.ellipse((46, 56, 86, 96), fill=(24, 72, 50, 255), outline=(238, 108, 138, 255), width=4)
    draw.ellipse((size - 86, 56, size - 46, 96), fill=(24, 72, 50, 255), outline=(238, 108, 138, 255), width=4)

    # Ondas negras centrales estilo Spotify
    for i, (y_off, w_arc) in enumerate([(92, 116), (124, 96), (154, 74)]):
        stroke = 14 - i * 2
        draw.arc(
            (size // 2 - w_arc // 2, y_off, size // 2 + w_arc // 2, y_off + 46),
            start=205,
            end=335,
            fill=(12, 14, 12, 255),
            width=stroke,
        )

    img.save(icon_path, format="PNG")


def build_elf_executable() -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    icon_path = BASE_DIR / "icon.png"
    if not icon_path.exists():
        generate_app_icon(icon_path)
    shutil.copy2(icon_path, DIST_DIR / "icon.png")

    # 1. Crear archivo ZIP con todos los módulos Python de la aplicación
    zip_path = DIST_DIR / "spotilinux_payload.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for mod in MODULES:
            zf.write(BASE_DIR / mod, arcname=mod)

    payload_b64 = base64.b64encode(zip_path.read_bytes()).decode("ascii")
    zip_path.unlink(missing_ok=True)

    # Dividir base64 en líneas para literal C
    chunks = [f'"{payload_b64[i:i+120]}"' for i in range(0, len(payload_b64), 120)]
    c_b64_literal = "\n    ".join(chunks)

    venv_py = str(BASE_DIR / "venv" / "bin" / "python")
    base_dir_str = str(BASE_DIR)

    c_source = f"""/*
 * Ejecutable nativo ELF para Linux de SpotiLinux (Maomao & Google Drive Edition)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <limits.h>

static const char *DEFAULT_PROJECT_DIR = "{base_dir_str}";
static const char *VENV_PYTHON = "{venv_py}";
static const char *EMBEDDED_ZIP_B64 =
    {c_b64_literal};

int file_exists(const char *path) {{
    struct stat st;
    return (stat(path, &st) == 0);
}}

int main(int argc, char *argv[]) {{
    char main_script[PATH_MAX];
    snprintf(main_script, sizeof(main_script), "%s/main.py", DEFAULT_PROJECT_DIR);

    /* Si la carpeta del proyecto está disponible, ejecutar directamente desde ella */
    if (file_exists(main_script)) {{
        chdir(DEFAULT_PROJECT_DIR);
        const char *py_bin = file_exists(VENV_PYTHON) ? VENV_PYTHON : "python3";
        char **new_argv = malloc(sizeof(char *) * (argc + 2));
        new_argv[0] = (char *)py_bin;
        new_argv[1] = main_script;
        for (int i = 1; i < argc; i++) {{
            new_argv[i + 1] = argv[i];
        }}
        new_argv[argc + 1] = NULL;
        execvp(py_bin, new_argv);
        perror("Error al iniciar SpotiLinux");
        return 1;
    }}

    /* Si el ejecutable fue movido a otra ruta, extraer el payload embebido en ~/.local/share/SpotiLinux */
    const char *home = getenv("HOME");
    if (!home) home = "/tmp";
    char app_dir[PATH_MAX];
    snprintf(app_dir, sizeof(app_dir), "%s/.local/share/SpotiLinux", home);
    mkdir(app_dir, 0755);

    char b64_file[PATH_MAX];
    char zip_file[PATH_MAX];
    snprintf(b64_file, sizeof(b64_file), "%s/payload.b64", app_dir);
    snprintf(zip_file, sizeof(zip_file), "%s/payload.zip", app_dir);

    FILE *fp = fopen(b64_file, "w");
    if (fp) {{
        fputs(EMBEDDED_ZIP_B64, fp);
        fclose(fp);
        char cmd[PATH_MAX * 3];
        snprintf(cmd, sizeof(cmd), "base64 -d '%s' > '%s' && unzip -o -q '%s' -d '%s'", b64_file, zip_file, zip_file, app_dir);
        system(cmd);
        unlink(b64_file);
        unlink(zip_file);
    }}

    chdir(app_dir);
    snprintf(main_script, sizeof(main_script), "%s/main.py", app_dir);
    const char *py_bin = file_exists(VENV_PYTHON) ? VENV_PYTHON : "python3";
    execlp(py_bin, py_bin, main_script, (char *)NULL);
    perror("Error al ejecutar SpotiLinux");
    return 1;
}}
"""
    c_path = DIST_DIR / "spotilinux_launcher.c"
    c_path.write_text(c_source, encoding="utf-8")

    out_bin = DIST_DIR / "MaoMusic"
    subprocess.run(["gcc", "-O2", str(c_path), "-o", str(out_bin)], check=True)
    c_path.unlink(missing_ok=True)
    out_bin.chmod(0o755)

    # Copiar también a la raíz del proyecto como ./MaoMusic y mantener ./SpotiLinux
    shutil.copy2(out_bin, BASE_DIR / "MaoMusic")
    (BASE_DIR / "MaoMusic").chmod(0o755)
    shutil.copy2(out_bin, BASE_DIR / "SpotiLinux")
    (BASE_DIR / "SpotiLinux").chmod(0o755)
    shutil.copy2(out_bin, DIST_DIR / "SpotiLinux")
    (DIST_DIR / "SpotiLinux").chmod(0o755)

    # Crear archivo MaoMusic.desktop y SpotiLinux.desktop
    desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=MaoMusic (Maomao & Cloud Edition)
Comment=Reproductor de música estilo Spotify con Google Drive y Maomao bailando al compás
Exec={out_bin}
Icon={icon_path}
Terminal=false
Categories=AudioVideo;Audio;Player;Qt;
StartupWMClass=MaoMusic
"""
    desktop_file = BASE_DIR / "MaoMusic.desktop"
    desktop_file.write_text(desktop_content, encoding="utf-8")
    desktop_file.chmod(0o755)
    shutil.copy2(desktop_file, DIST_DIR / "MaoMusic.desktop")

    # Mantener SpotiLinux.desktop para compatibilidad
    desktop_spoti = BASE_DIR / "SpotiLinux.desktop"
    desktop_spoti.write_text(desktop_content.replace("MaoMusic", "SpotiLinux"), encoding="utf-8")
    desktop_spoti.chmod(0o755)
    shutil.copy2(desktop_spoti, DIST_DIR / "SpotiLinux.desktop")

    return out_bin


if __name__ == "__main__":
    binary = build_elf_executable()
    print(f"✔ Ejecutable nativo ELF de Linux compilado en: {binary}")
