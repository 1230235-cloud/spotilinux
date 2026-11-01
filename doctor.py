#!/usr/bin/env python3
"""
Diagnostic tool for MaoMusic desktop runtime environment.
Checks system binaries, python packages, folder permissions and cloud connectivity.
"""

import argparse
import importlib.util
import json
import os
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from binaries import resolve_binary
from gdrive_sync import DEFAULT_DRIVE_SERVER_URL


@dataclass
class CheckResult:
    name: str
    ok: bool
    required: bool
    detail: str
    hint: str


def detect_distro_id() -> str:
    """Detects Linux distribution family from /etc/os-release."""
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return "unknown"
    try:
        content = os_release.read_text(encoding="utf-8")
        fields = {}
        for line in content.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                fields[k.strip()] = v.strip().strip('"').strip("'")
        distro_id = fields.get("ID", "").lower()
        id_like = fields.get("ID_LIKE", "").lower()
        if distro_id in ("arch", "manjaro") or "arch" in id_like:
            return "arch"
        if distro_id in ("debian", "ubuntu", "linuxmint", "pop") or "debian" in id_like:
            return "debian"
        if distro_id in ("fedora", "rhel", "centos") or "fedora" in id_like:
            return "fedora"
        if distro_id in ("opensuse", "suse") or "suse" in id_like:
            return "opensuse"
        return distro_id or "unknown"
    except Exception:
        return "unknown"


def get_install_hint(package_or_bin: str) -> str:
    """Provides distro-specific installation commands."""
    distro = detect_distro_id()
    hints = {
        "ffmpeg": {
            "arch": "sudo pacman -S ffmpeg",
            "debian": "sudo apt install -y ffmpeg",
            "fedora": "sudo dnf install -y ffmpeg-free",
            "opensuse": "sudo zypper install -y ffmpeg",
        },
        "yt-dlp": {
            "arch": "sudo pacman -S yt-dlp  (o: pip install yt-dlp)",
            "debian": "sudo apt install -y yt-dlp  (o: pip install yt-dlp)",
            "fedora": "sudo dnf install -y yt-dlp  (o: pip install yt-dlp)",
            "opensuse": "sudo zypper install -y yt-dlp  (o: pip install yt-dlp)",
        },
        "rclone": {
            "arch": "sudo pacman -S rclone",
            "debian": "sudo apt install -y rclone",
            "fedora": "sudo dnf install -y rclone",
            "opensuse": "sudo zypper install -y rclone",
        },
        "python": {
            "arch": "sudo pacman -S python",
            "debian": "sudo apt install -y python3 python3-venv",
            "fedora": "sudo dnf install -y python3",
            "opensuse": "sudo zypper install -y python3",
        },
        "qt_xcb": {
            "debian": "sudo apt install -y libxcb-cursor0 libgl1",
            "arch": "sudo pacman -S libxcb xcb-util-cursor",
            "fedora": "sudo dnf install -y xcb-util-cursor",
            "opensuse": "sudo zypper install -y libxcb-cursor0",
        },
    }
    fallback_hint = f"Instalar '{package_or_bin}' usando el gestor de paquetes de tu distribución Linux."
    return hints.get(package_or_bin, {}).get(distro, fallback_hint)


def check_environment(online: bool = False, base_dir: Optional[Path] = None) -> List[CheckResult]:
    """Runs all environment checks and returns structured results."""
    results: List[CheckResult] = []
    root = base_dir or BASE_DIR

    # 1. Python version (>= 3.10)
    py_ver = sys.version_info
    py_ok = (py_ver.major == 3 and py_ver.minor >= 10)
    results.append(
        CheckResult(
            name="Python >= 3.10",
            ok=py_ok,
            required=True,
            detail=f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}",
            hint=get_install_hint("python") if not py_ok else "",
        )
    )

    # 2. Python Packages
    packages = [
        ("PyQt6", True),
        ("numpy", True),
        ("PIL", True),  # Pillow
        ("requests", True),
    ]
    for pkg_name, is_req in packages:
        spec = importlib.util.find_spec(pkg_name)
        ok = spec is not None
        display = "Pillow" if pkg_name == "PIL" else pkg_name
        detail = "Disponible en entorno actual" if ok else "No encontrado"
        hint = f"pip install {display.lower()}" if not ok else ""
        results.append(CheckResult(name=f"Paquete Python: {display}", ok=ok, required=is_req, detail=detail, hint=hint))

    # 3. System Binaries
    binaries = [
        ("yt-dlp", True),
        ("ffmpeg", True),
        ("rclone", False),
    ]
    for b_name, is_req in binaries:
        path = resolve_binary(b_name)
        ok = path is not None
        detail = path if ok else "No encontrado en PATH ni en virtualenv"
        hint = get_install_hint(b_name) if not ok else ""
        results.append(CheckResult(name=f"Binario: {b_name}", ok=ok, required=is_req, detail=detail, hint=hint))

    # 4. Folder permissions
    folders = [
        (root / "data", True),
        (root / "music_library", True),
    ]
    for f_path, is_req in folders:
        try:
            f_path.mkdir(parents=True, exist_ok=True)
            test_file = f_path / ".doctor_write_test"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            results.append(CheckResult(name=f"Escritura: {f_path.name}/", ok=True, required=is_req, detail="Permiso concedido", hint=""))
        except Exception as exc:
            results.append(CheckResult(
                name=f"Escritura: {f_path.name}/",
                ok=False,
                required=is_req,
                detail=f"Fallo de permisos: {exc}",
                hint=f"chmod -R u+rw '{f_path}'",
            ))

    # 5. Online connectivity (optional)
    if online:
        url = f"{DEFAULT_DRIVE_SERVER_URL}?action=list_music"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MaoMusic-Doctor"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                ok = resp.status == 200
                detail = f"Conectado a Google Apps Script (HTTP {resp.status})"
                results.append(CheckResult(name="Conectividad Drive Cloud", ok=ok, required=False, detail=detail, hint=""))
        except Exception as exc:
            results.append(CheckResult(
                name="Conectividad Drive Cloud",
                ok=False,
                required=False,
                detail=f"Fallo al conectar: {exc}",
                hint="Verifica tu conexión a internet o la URL del script",
            ))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="MaoMusic Environment Doctor")
    parser.add_argument("--online", action="store_true", help="Comprueba conectividad con Google Drive")
    parser.add_argument("--json", action="store_true", help="Salida estructurada en JSON")
    args = parser.parse_args()

    results = check_environment(online=args.online)

    if args.json:
        data = [asdict(r) for r in results]
        print(json.dumps(data, indent=2, ensure_ascii=False))
        has_required_failure = any(not r.ok and r.required for r in results)
        return 1 if has_required_failure else 0

    print("═══════════════════════════════════════════════════════════════════")
    print(" 🌿 Diagnóstico de Entorno MaoMusic Desktop (Doctor)")
    print("═══════════════════════════════════════════════════════════════════")
    
    has_required_failure = False
    for r in results:
        if r.ok:
            symbol = "✔"
            status_txt = "OK"
        elif r.required:
            symbol = "✖"
            status_txt = "FALTA (Requerido)"
            has_required_failure = True
        else:
            symbol = "⚠"
            status_txt = "OPCIONAL (Recomendado)"

        print(f" {symbol} {r.name:<28} [{status_txt}]")
        print(f"   Detalle: {r.detail}")
        if not r.ok and r.hint:
            print(f"   Solución sugerida: {r.hint}")
        print("───────────────────────────────────────────────────────────────────")

    if has_required_failure:
        print("❌ Se encontraron componentes requeridos faltantes. Revisa las soluciones sugeridas arriba.")
        return 1
    else:
        print("✔ ¡El entorno cumple con todos los requisitos para ejecutar MaoMusic!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
