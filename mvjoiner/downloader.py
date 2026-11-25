"""yt-dlp integration: metadata lookup, full downloads and partial (time-range) downloads."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ffmpeg_tools import Cancelled, find_ffmpeg, run_ffmpeg

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv"}

BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium", "safari")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class _QuietLogger:
    """Swallow yt-dlp console output; errors surface through exceptions instead."""

    def __init__(self, log: Callable[[str], None] | None = None):
        self._log = log

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    _js_hint_shown = False

    def warning(self, msg: str) -> None:
        if not self._log:
            return
        if "JavaScript runtime" in msg:
            # Shown once per session instead of on every request.
            if not _QuietLogger._js_hint_shown:
                _QuietLogger._js_hint_shown = True
                self._log(
                    "Tip: install Deno (https://deno.com) for the most reliable YouTube downloads; "
                    "yt-dlp uses it to unlock all formats."
                )
            return
        self._log(f"yt-dlp warning: {msg}")

    def error(self, msg: str) -> None:
        if self._log:
            self._log(f"yt-dlp: {msg}")


@dataclass
class AccessOptions:
    """How to reach videos that need sign-in or are blocked for anonymous clients."""

    cookies: str = "auto"  # auto | none | file | <browser name>
    cookie_file: str = ""


def _base_options(log: Callable[[str], None] | None = None) -> dict:
    opts = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(log),
        "geo_bypass": True,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "http_headers": dict(_HEADERS),
    }
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    return opts


def _profiles(access: AccessOptions) -> list[tuple[str, dict]]:
    """
    Ordered list of option sets to try. The first one that works wins.
    Covers explicit cookies, yt-dlp defaults, alternative YouTube player clients
    (which avoid many bot checks) and, in auto mode, cookies from installed browsers.
    """
    profiles: list[tuple[str, dict]] = []

    if access.cookies == "file" and access.cookie_file:
        profiles.append(("cookies.txt", {"cookiefile": access.cookie_file}))
    elif access.cookies in BROWSERS:
        profiles.append((f"{access.cookies} cookies", {"cookiesfrombrowser": (access.cookies,)}))

    profiles.append(("default client", {}))
    profiles.append(
        (
            "alternative clients",
            {"extractor_args": {"youtube": {"player_client": ["tv", "web_safari", "mweb", "android"]}}},
        )
    )
    profiles.append(
        (
            "embedded client",
            {"extractor_args": {"youtube": {"player_client": ["tv_embedded", "web_embedded"]}}},
        )
    )

    if access.cookies == "auto":
        for browser in ("chrome", "edge", "firefox", "brave"):
            profiles.append((f"{browser} cookies", {"cookiesfrombrowser": (browser,)}))
    return profiles


def fetch_info(url: str, access: AccessOptions | None = None) -> tuple[str, float | None]:
    """Return (title, duration_seconds) for a URL without downloading it."""
    import yt_dlp

    access = access or AccessOptions(cookies="none")
    last_error: Exception | None = None
    # Metadata lookups should be quick: try only the cheap anonymous profiles.
    for _name, profile in [p for p in _profiles(access) if "cookiesfrombrowser" not in p[1]][:3]:
        opts = _base_options() | {"skip_download": True, "extract_flat": False} | profile
        opts["retries"] = 1
        opts["extractor_retries"] = 1
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            title = info.get("title") or url
            duration = info.get("duration")
            return title, float(duration) if duration else None
        except Exception as err:  # noqa: BLE001 - try the next profile
            last_error = err
    raise RuntimeError(str(last_error) if last_error else "Unknown error")


class Downloader:
    def __init__(
        self,
        work_dir: Path,
        max_height: int,
        access: AccessOptions,
        cancel: threading.Event,
        log: Callable[[str], None],
    ):
        self.work_dir = work_dir
        self.max_height = max_height
        self.access = access
        self.cancel = cancel
        self.log = log

    # ------------------------------------------------------------------ public

    def download(
        self,
        url: str,
        index: int,
        start: float | None,
        end: float | None,
        on_progress: Callable[[float], None],
    ) -> Path:
        """Download a URL (or only [start, end] of it) and return the local file."""
        import yt_dlp
        from yt_dlp.utils import download_range_func

        stem = f"src_{index:03d}"
        last_error: Exception | None = None
        ranged = start is not None and end is not None

        for name, profile in _profiles(self.access):
            self._check_cancel()
            self._clear(stem)
            self.log(f"  trying {name}…")

            opts = _base_options(self.log) | profile
            h = self.max_height
            opts |= {
                "format": (
                    f"bv*[height<={h}][protocol^=http]+ba[protocol^=http]/"
                    f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
                ),
                "merge_output_format": "mp4",
                "outtmpl": str(self.work_dir / f"{stem}.%(ext)s"),
                "progress_hooks": [self._hook(on_progress)],
            }
            if ranged:
                opts["download_ranges"] = download_range_func(None, [(start, end)])
                opts["force_keyframes_at_cuts"] = True

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                found = self._locate(info, stem)
                if found:
                    return found
            except Cancelled:
                raise
            except Exception as err:  # noqa: BLE001
                if self.cancel.is_set():
                    raise Cancelled() from None
                last_error = err
                self.log(f"  {name} failed: {_short(err)}")

            if ranged:
                # Fallback: resolve the direct stream URL and let FFmpeg seek into it over HTTP.
                try:
                    clip = self._slice_stream(url, stem, start, end, profile, on_progress)
                    if clip:
                        return clip
                except Cancelled:
                    raise
                except Exception as err:  # noqa: BLE001
                    last_error = last_error or err
                    self.log(f"  direct stream slice failed: {_short(err)}")

        raise RuntimeError(
            f"Could not download {url}\n\nLast error: {_short(last_error)}\n\n"
            "Tips: pick your signed-in browser under 'Browser cookies', "
            "or use Tools → Update yt-dlp if the site changed recently."
        )

    # ----------------------------------------------------------------- helpers

    def _hook(self, on_progress: Callable[[float], None]):
        def hook(status: dict) -> None:
            if self.cancel.is_set():
                raise Cancelled()
            if status.get("status") == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate")
                done = status.get("downloaded_bytes")
                if total and done:
                    on_progress(min(1.0, done / total))

        return hook

    def _slice_stream(self, url, stem, start, end, profile, on_progress) -> Path | None:
        import yt_dlp

        h = self.max_height
        opts = _base_options() | profile | {"format": f"bv*[height<={h}]+ba/b[height<={h}]/b"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        duration = end - start
        out = self.work_dir / f"{stem}.mp4"
        seek = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]
        formats = info.get("requested_formats")
        if formats and len(formats) >= 2:
            args = [*seek, "-i", formats[0]["url"], *seek, "-i", formats[1]["url"],
                    "-map", "0:v:0", "-map", "1:a:0?"]
        elif info.get("url"):
            args = [*seek, "-i", info["url"]]
        else:
            return None
        args += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", str(out)]
        run_ffmpeg(args, duration=duration, on_progress=on_progress, cancel=self.cancel)
        return out if out.exists() and out.stat().st_size > 0 else None

    def _locate(self, info: dict | None, stem: str) -> Path | None:
        if info:
            for item in info.get("requested_downloads") or []:
                path = Path(item.get("filepath") or "")
                if path.is_file() and path.stat().st_size > 0:
                    return path
        for path in self.work_dir.glob(f"{stem}.*"):
            if path.suffix.lower() in VIDEO_SUFFIXES and path.stat().st_size > 0:
                return path
        return None

    def _clear(self, stem: str) -> None:
        for path in self.work_dir.glob(f"{stem}*"):
            try:
                path.unlink()
            except OSError:
                pass

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()


def _short(err: object) -> str:
    text = str(err or "unknown error").replace("ERROR: ", "").strip()
    return text.splitlines()[0][:300] if text else "unknown error"
