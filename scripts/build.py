"""
Build the Windows app with PyInstaller (and optionally the installer with Inno Setup).

    pip install -r requirements.txt pyinstaller pillow
    python scripts/build.py              # portable:  dist/MultiVideoJoiner.exe
    python scripts/build.py --installer  # also:      dist/MultiVideoJoiner-Setup.exe

The installer needs Inno Setup 6 (https://jrsoftware.org/isinfo.php); point the
ISCC environment variable at ISCC.exe if it isn't in the default location.
FFmpeg from imageio-ffmpeg is bundled, so users don't need to install anything.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
ICON = ROOT / "mvjoiner" / "assets" / "icon.ico"
sys.path.insert(0, str(ROOT))

# Qt modules the app never uses - keeps the download smaller.
EXCLUDES = (
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest", "tkinter",
)


def pyinstaller(onefile: bool) -> None:
    name = "MultiVideoJoiner"
    # Only remove this build's own output, so the portable and installer builds can coexist.
    if onefile:
        (DIST / f"{name}.exe").unlink(missing_ok=True)
    else:
        shutil.rmtree(DIST / name, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--onefile" if onefile else "--onedir",
        "--name", name,
        "--icon", str(ICON),
        "--add-data", f"{ICON.parent}{os.pathsep}mvjoiner/assets",
        "--collect-data", "imageio_ffmpeg",
        "--collect-submodules", "yt_dlp",
        *[arg for mod in EXCLUDES for arg in ("--exclude-module", mod)],
        "--distpath", str(DIST),
        "--workpath", str(BUILD / ("onefile" if onefile else "onedir")),
        "--specpath", str(BUILD),
        str(ROOT / "main.py"),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)


def find_iscc() -> str:
    candidates = [
        os.environ.get("ISCC", ""),
        shutil.which("iscc") or "",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    sys.exit("Inno Setup (ISCC.exe) not found. Install Inno Setup 6 or set the ISCC environment variable.")


def installer() -> None:
    from mvjoiner import __version__

    pyinstaller(onefile=False)
    subprocess.run(
        [find_iscc(), f"/DAppVersion={__version__}", str(ROOT / "installer" / "MultiVideoJoiner.iss")],
        check=True,
        cwd=ROOT,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--installer", action="store_true", help="also build the Windows installer")
    parser.add_argument("--no-portable", action="store_true", help="skip the single-file portable exe")
    args = parser.parse_args()

    if not ICON.is_file():
        from make_logo import export

        export()
    if not args.no_portable:
        pyinstaller(onefile=True)
    if args.installer:
        installer()
    for item in sorted(DIST.glob("*.exe")):
        print(f"Built {item} ({item.stat().st_size / 1_048_576:.0f} MB)")


if __name__ == "__main__":
    main()
