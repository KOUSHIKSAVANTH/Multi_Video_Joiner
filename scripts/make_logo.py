"""
Export the app logo (drawn in mvjoiner/theme.py) to image files:

    mvjoiner/assets/logo.png   512 px, used by the README
    mvjoiner/assets/icon.ico   multi-size, used by the .exe, installer and shortcuts

    python scripts/make_logo.py      (needs: pip install pillow)
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "mvjoiner" / "assets"
sys.path.insert(0, str(ROOT))


def export() -> Path:
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    from mvjoiner.theme import render_icon

    app = QGuiApplication.instance() or QGuiApplication([])
    ASSETS.mkdir(parents=True, exist_ok=True)

    def to_pil(size: int) -> Image.Image:
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        render_icon(size).save(buffer, "PNG")
        return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")

    to_pil(512).save(ASSETS / "logo.png", optimize=True)
    # Render every size separately so small icons stay crisp instead of being downscaled.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [to_pil(s) for s in sizes]
    frames[-1].save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    del app
    print("wrote", ASSETS / "logo.png", "and", ASSETS / "icon.ico")
    return ASSETS / "icon.ico"


if __name__ == "__main__":
    export()
