"""Create Start menu / desktop shortcuts for portable and pip installs (the installer does this itself)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import APP_NAME

ASSETS = Path(__file__).resolve().parent / "assets"


def supported() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


def _launch_command() -> tuple[str, str, str]:
    """(target, arguments, working directory) that start this copy of the app."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return str(exe), "", str(exe.parent)
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    if sys.platform == "win32" and pythonw.is_file():
        python = pythonw  # no console window
    package_parent = Path(__file__).resolve().parents[1]
    return str(python), "-m mvjoiner", str(package_parent)


def _windows_shortcut(link: Path) -> None:
    target, args, workdir = _launch_command()
    icon = target if getattr(sys, "frozen", False) else str(ASSETS / "icon.ico")
    link.parent.mkdir(parents=True, exist_ok=True)
    # Values are passed through environment variables to avoid any quoting problems.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MVJ_LINK);"
        "$s.TargetPath = $env:MVJ_TARGET; $s.Arguments = $env:MVJ_ARGS;"
        "$s.WorkingDirectory = $env:MVJ_WORKDIR; $s.IconLocation = $env:MVJ_ICON;"
        "$s.Description = $env:MVJ_DESC; $s.Save()"
    )
    env = os.environ | {
        "MVJ_LINK": str(link), "MVJ_TARGET": target, "MVJ_ARGS": args, "MVJ_WORKDIR": workdir,
        "MVJ_ICON": icon, "MVJ_DESC": "Download, trim and join videos",
    }
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _linux_entry(path: Path) -> None:
    target, args, workdir = _launch_command()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={APP_NAME}\nComment=Download, trim and join videos\n"
        f'Exec="{target}" {args}\nPath={workdir}\nIcon={ASSETS / "logo.png"}\n'
        "Categories=AudioVideo;Video;\nTerminal=false\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def create_shortcuts(desktop: bool = False) -> list[Path]:
    """Add the app to the Start menu (Windows) or app launcher (Linux); optionally to the desktop."""
    created: list[Path] = []
    if sys.platform == "win32":
        programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        targets = [programs / f"{APP_NAME}.lnk"]
        if desktop:
            targets.append(Path.home() / "Desktop" / f"{APP_NAME}.lnk")
        for link in targets:
            _windows_shortcut(link)
            created.append(link)
    elif sys.platform.startswith("linux"):
        entry = Path.home() / ".local" / "share" / "applications" / "multi-video-joiner.desktop"
        _linux_entry(entry)
        created.append(entry)
        if desktop:
            copy = Path.home() / "Desktop" / "multi-video-joiner.desktop"
            _linux_entry(copy)
            created.append(copy)
    else:
        raise RuntimeError("Shortcuts can only be created on Windows and Linux.")
    return created
