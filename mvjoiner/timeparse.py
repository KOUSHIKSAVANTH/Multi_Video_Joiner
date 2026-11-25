"""Parsing and formatting helpers for timestamps, ranges, labels and bulk input."""

from __future__ import annotations

import re

_RANGE_SPLIT = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def parse_timestamp(text: str) -> float:
    """Convert '7', '7.5s', '1:25' or '01:02:30' into seconds."""
    cleaned = text.strip().lower().rstrip("s").strip()
    if not cleaned:
        raise ValueError("Empty timestamp.")

    parts = cleaned.split(":")
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            raise ValueError
    except ValueError:
        raise ValueError(f"Invalid timestamp '{text.strip()}'.") from None

    if seconds < 0:
        raise ValueError("Timestamp cannot be negative.")
    return seconds


def parse_time_range(text: str) -> tuple[float, float]:
    """Parse '0-7', '1:25 - 1:30' or '0:05 to 0:12' into (start, end) seconds."""
    parts = _RANGE_SPLIT.split(text.strip(), maxsplit=1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Invalid time range '{text.strip()}'. Use a format like '0-7' or '1:25-1:30'."
        )
    start, end = parse_timestamp(parts[0]), parse_timestamp(parts[1])
    if end <= start:
        raise ValueError("The end time must be after the start time.")
    return start, end


def parse_optional_range(text: str) -> tuple[float | None, float | None]:
    """Like parse_time_range, but an empty string means 'the whole video'."""
    if not text or not text.strip():
        return None, None
    return parse_time_range(text)


def looks_like_range(text: str) -> bool:
    try:
        parse_time_range(text)
        return True
    except ValueError:
        return False


def is_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def format_clock(seconds: float | None) -> str:
    """Human clock: 75.5 -> '1:15.5', 3725 -> '1:02:05'."""
    if seconds is None:
        return "?"
    whole = int(seconds)
    frac = seconds - whole
    hrs, rem = divmod(whole, 3600)
    mins, secs = divmod(rem, 60)
    sec_text = f"{secs:02d}" if frac < 0.05 else f"{secs + frac:04.1f}"
    if hrs:
        return f"{hrs}:{mins:02d}:{sec_text}"
    return f"{mins}:{sec_text}"


def format_range(start: float | None, end: float | None) -> str:
    if start is None or end is None:
        return "Full video"
    return f"{format_clock(start)} – {format_clock(end)}"


def format_duration(seconds: float | None) -> str:
    """Compact duration: 0 -> '0s', 75 -> '1m 15s', 3725 -> '1h 2m'."""
    if seconds is None:
        return "–"
    total = int(round(seconds))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs:
        return f"{hrs}h {mins}m"
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def youtube_timestamp(seconds: float) -> str:
    """Timestamp style used in YouTube chapter lists: '0:00', '12:34', '1:02:03'."""
    total = int(seconds)
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def filename_time_tag(seconds: float) -> str:
    """Filename-safe time label: 85 -> '01m25s'."""
    total = int(round(seconds))
    mins, secs = divmod(total, 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs:02d}h{mins:02d}m{secs:02d}s"
    return f"{mins:02d}m{secs:02d}s"


def sanitize_label(label: str, fallback: str = "clip") -> str:
    """Make a label safe for folder and file names."""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", label.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    return cleaned[:60] or fallback


def parse_bulk_line(line: str) -> tuple[str, str, str] | None:
    """
    Parse one line of bulk input into (source, range_text, label).

    Accepted forms:
        https://youtu.be/abc
        https://youtu.be/abc 1:25-1:30 wide_shot
        https://youtu.be/abc | 0-7 | close up
        C:/videos/intro.mp4 | | intro
    Blank lines and lines starting with '#' are ignored.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
        source = parts[0]
        range_text = parts[1] if len(parts) > 1 else ""
        label = " | ".join(p for p in parts[2:] if p) if len(parts) > 2 else ""
        if range_text and not looks_like_range(range_text):
            raise ValueError(f"Invalid time range '{range_text}'.")
        return source, range_text, label

    tokens = line.split()
    source = tokens[0]
    rest = tokens[1:]
    range_text = ""
    # A range may be written with spaces ("1:25 - 1:30"), so try the longest prefix first.
    for take in (3, 2, 1):
        if len(rest) >= take and looks_like_range(" ".join(rest[:take])):
            range_text = " ".join(rest[:take])
            rest = rest[take:]
            break
    return source, range_text, " ".join(rest)
