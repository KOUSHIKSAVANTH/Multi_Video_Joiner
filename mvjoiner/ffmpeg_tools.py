"""Locating, probing and running FFmpeg with live progress and cancellation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

# Hide the console window that would otherwise flash up for every FFmpeg call
# when the app runs as a windowed executable on Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class Cancelled(Exception):
    """Raised when the user cancels a running job."""


class FFmpegError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    """
    Find an FFmpeg executable. Order:
    1. MVJ_FFMPEG environment variable
    2. ffmpeg on the system PATH
    3. the copy bundled with the imageio-ffmpeg package
    """
    override = os.environ.get("MVJ_FFMPEG")
    if override and Path(override).is_file():
        return override

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).is_file():
            return bundled
    except Exception:
        pass
    return None


def require_ffmpeg() -> str:
    exe = find_ffmpeg()
    if not exe:
        raise FFmpegError(
            "FFmpeg was not found. Install it and add it to PATH, "
            "or run: pip install imageio-ffmpeg"
        )
    return exe


def ffmpeg_version() -> str:
    exe = find_ffmpeg()
    if not exe:
        return "not found"
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, creationflags=NO_WINDOW
        ).stdout
        first = out.splitlines()[0] if out else ""
        match = re.search(r"ffmpeg version (\S+)", first)
        return match.group(1) if match else first or "unknown"
    except Exception:
        return "unknown"


@dataclass
class MediaInfo:
    duration: float | None
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SIZE_RE = re.compile(r"Stream #.*?Video:.*?,\s*(\d{2,5})x(\d{2,5})[\s,\[]")
_ROTATE_RE = re.compile(r"rotate\s*:\s*(-?\d+)|rotation of (-?\d+(?:\.\d+)?) degrees")


def probe(path: str | Path) -> MediaInfo:
    """Read duration, frame size and stream types by parsing `ffmpeg -i` output (no ffprobe needed)."""
    exe = require_ffmpeg()
    result = subprocess.run(
        [exe, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    text = result.stderr

    duration = None
    match = _DURATION_RE.search(text)
    if match:
        h, m, s = match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    width = height = None
    size = _SIZE_RE.search(text)
    if size:
        width, height = int(size.group(1)), int(size.group(2))
        rot = _ROTATE_RE.search(text)
        if rot:
            angle = abs(int(float(rot.group(1) or rot.group(2)))) % 180
            if angle == 90:
                width, height = height, width

    has_video = bool(re.search(r"Stream #.*?Video:", text))
    has_audio = bool(re.search(r"Stream #.*?Audio:", text))
    if not has_video and not has_audio:
        raise FFmpegError(f"Not a readable media file: {path}")
    return MediaInfo(duration, width, height, has_video, has_audio)


def run_ffmpeg(
    args: list[str],
    *,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """
    Run FFmpeg with the given arguments (without the executable).
    Reports progress as a 0..1 fraction of `duration` and kills the process on cancel.
    """
    cmd = [require_ffmpeg(), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *args]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )

    tail: deque[str] = deque(maxlen=40)

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line.rstrip())

    reader = threading.Thread(target=drain_stderr, daemon=True)
    reader.start()

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            if on_progress and duration and line.startswith("out_time_us="):
                value = line.split("=", 1)[1].strip()
                if value.isdigit():
                    on_progress(min(1.0, int(value) / 1_000_000 / duration))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(timeout=2)

    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        details = "\n".join(line for line in tail if line)
        raise FFmpegError(f"FFmpeg failed (exit code {proc.returncode}):\n{details[-2500:]}")
