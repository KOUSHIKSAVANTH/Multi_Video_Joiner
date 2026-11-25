"""The processing pipeline: acquire sources, conform them to one format, export clips and/or join."""

from __future__ import annotations

import csv
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import timeparse as tp
from .downloader import AccessOptions, Downloader
from .ffmpeg_tools import Cancelled, MediaInfo, probe, run_ffmpeg

# --------------------------------------------------------------------------- models

CANVASES = {
    # key: (label, aspect width, aspect height)
    "landscape": ("Landscape 16:9", 16, 9),
    "vertical": ("Vertical 9:16 (Shorts / Reels / TikTok)", 9, 16),
    "square": ("Square 1:1", 1, 1),
    "portrait": ("Portrait 4:5 (Instagram feed)", 4, 5),
    "source": ("Keep source size", 0, 0),
}

TRANSITIONS = {
    # key: (label, xfade transition name)
    "none": ("None (hard cut)", None),
    "fade": ("Crossfade", "fade"),
    "fadeblack": ("Fade through black", "fadeblack"),
    "fadewhite": ("Fade through white", "fadewhite"),
    "dissolve": ("Dissolve", "dissolve"),
    "wipeleft": ("Wipe", "wipeleft"),
    "slideleft": ("Slide", "slideleft"),
    "circleopen": ("Circle open", "circleopen"),
    "pixelize": ("Pixelize", "pixelize"),
    "radial": ("Radial", "radial"),
}

MODES = {
    "join": "Join into one video",
    "clips": "Export separate clips",
    "both": "Export clips and join",
}


@dataclass
class ClipItem:
    source: str  # URL or local file path
    start: float | None = None
    end: float | None = None
    label: str = ""
    title: str = ""
    source_duration: float | None = None  # length of the whole source, when known

    @property
    def is_url(self) -> bool:
        return tp.is_url(self.source)

    @property
    def has_range(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def display_name(self) -> str:
        if self.title:
            return self.title
        if self.is_url:
            return self.source
        return Path(self.source).name

    @property
    def clip_duration(self) -> float | None:
        if self.has_range:
            return self.end - self.start
        return self.source_duration

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ClipItem":
        known = {k: data.get(k) for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


@dataclass
class JobSettings:
    mode: str = "join"
    output_dir: str = ""
    output_name: str = "joined_video"
    canvas: str = "landscape"
    resolution: int = 1080
    fill: str = "blur"  # blur | bars
    fps: int = 30
    transition: str = "none"
    transition_duration: float = 0.6
    loudnorm: bool = True
    chapters: bool = True
    group_by_label: bool = True
    cookies: str = "auto"
    cookie_file: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobSettings":
        defaults = cls()
        values = {}
        for name in cls.__dataclass_fields__:
            if name in data:
                try:
                    values[name] = type(getattr(defaults, name))(data[name])
                except (TypeError, ValueError):
                    pass
        return cls(**values)


@dataclass
class JobResult:
    joined: Path | None = None
    clips: list[Path] = field(default_factory=list)
    chapters_file: Path | None = None
    manifest: Path | None = None
    total_duration: float = 0.0


@dataclass
class Callbacks:
    status: Callable[[str], None] = lambda msg: None
    progress: Callable[[float], None] = lambda frac: None
    log: Callable[[str], None] = lambda msg: None
    item_state: Callable[[int, str], None] = lambda index, state: None


# ------------------------------------------------------------------ pure helpers


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def canvas_size(canvas: str, resolution: int, first: MediaInfo | None = None) -> tuple[int, int] | None:
    """Target frame size. `resolution` is the short side. Returns None for per-clip source size."""
    if canvas == "source":
        if first and first.width and first.height:
            return _even(first.width), _even(first.height)
        return None
    _label, aw, ah = CANVASES.get(canvas, CANVASES["landscape"])
    if aw >= ah:
        return _even(resolution * aw / ah), _even(resolution)
    return _even(resolution), _even(resolution * ah / aw)


def video_filter(size: tuple[int, int] | None, fill: str, fps: int) -> str:
    """Filter graph that turns input [0:v] into [v] with the target size, fps and pixel format."""
    tail = f"fps={fps},format=yuv420p,setsar=1"
    if size is None:
        return f"[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,{tail}[v]"
    w, h = size
    if fill == "blur":
        # A blurred, zoomed copy of the clip fills the empty space behind it.
        bw, bh = _even(w / 4), _even(h / 4)
        return (
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=luma_radius=12:luma_power=2:chroma_radius=6:chroma_power=2,"
            f"eq=brightness=-0.06,scale={w}:{h}[bg];"
            f"[fgsrc]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}[v]"
        )
    return (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,{tail}[v]"
    )


def audio_filter(input_label: str, loudnorm: bool) -> str:
    chain = f"[{input_label}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if loudnorm:
        # EBU R128 single-pass normalisation, so quiet and loud sources end up at the same level.
        chain += ",loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"
    return chain + "[a]"


def xfade_offsets(durations: list[float], overlap: float) -> list[float]:
    """Start time of each transition when clips overlap by `overlap` seconds."""
    offsets, elapsed = [], 0.0
    for index, length in enumerate(durations[:-1]):
        elapsed += length
        offsets.append(elapsed - overlap * (index + 1))
    return offsets


def chapter_starts(durations: list[float], overlap: float) -> list[float]:
    """Where each clip begins in the joined video."""
    if not durations:
        return []
    return [0.0, *xfade_offsets(durations, overlap)]


def safe_overlap(durations: list[float], requested: float) -> float:
    """Clamp the transition length so it never exceeds half of the shortest clip."""
    if len(durations) < 2 or requested <= 0:
        return 0.0
    return max(0.0, min(requested, min(durations) / 2 - 0.05))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def ffmetadata(chapters: list[tuple[float, float, str]]) -> str:
    def esc(text: str) -> str:
        for ch in ("\\", "=", ";", "#", "\n"):
            text = text.replace(ch, "\\" + ch)
        return text

    lines = [";FFMETADATA1"]
    for start, end, title in chapters:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(start * 1000)}",
            f"END={int(end * 1000)}",
            f"title={esc(title)}",
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- pipeline


class Pipeline:
    def __init__(
        self,
        items: list[ClipItem],
        settings: JobSettings,
        callbacks: Callbacks | None = None,
        cancel: threading.Event | None = None,
    ):
        if not items:
            raise ValueError("The queue is empty.")
        self.items = items
        self.s = settings
        self.cb = callbacks or Callbacks()
        self.cancel = cancel or threading.Event()
        self.output_dir = Path(settings.output_dir or Path.cwd()).expanduser()
        self.join_enabled = settings.mode in ("join", "both")
        self.clips_enabled = settings.mode in ("clips", "both")
        # Work units: download + conform per item, plus the final join.
        self._units = len(items) * 2 + (1 if self.join_enabled else 0)
        self._done = 0

    # progress -----------------------------------------------------------------

    def _report(self, fraction: float = 0.0) -> None:
        self.cb.progress(min(1.0, (self._done + max(0.0, min(1.0, fraction))) / self._units))

    def _step_done(self) -> None:
        self._done += 1
        self._report()

    # entry point ----------------------------------------------------------------

    def run(self) -> JobResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="mvj_"))
        self.cb.log(f"Working folder: {work}")
        try:
            return self._run(work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, work: Path) -> JobResult:
        result = JobResult()
        access = AccessOptions(self.s.cookies, self.s.cookie_file)
        downloader = Downloader(work, max(self.s.resolution, 480), access, self.cancel, self.cb.log)
        total = len(self.items)

        # 1) Acquire every source (download or use the local file).
        acquired: list[tuple[Path, MediaInfo, bool]] = []  # (file, info, already_trimmed)
        for i, item in enumerate(self.items):
            self._check_cancel()
            n = i + 1
            if item.is_url:
                what = tp.format_range(item.start, item.end) if item.has_range else "full video"
                self.cb.status(f"Downloading {n}/{total} · {what}")
                self.cb.item_state(i, "Downloading")
                self.cb.log(f"[{n}/{total}] Downloading {item.source} ({what})")
                path = downloader.download(item.source, n, item.start, item.end, self._report)
                trimmed = item.has_range
            else:
                path = Path(item.source).expanduser()
                if not path.is_file():
                    raise FileNotFoundError(f"File not found: {path}")
                self.cb.log(f"[{n}/{total}] Using local file {path}")
                trimmed = False
            info = probe(path)
            if not info.has_video:
                raise RuntimeError(f"'{item.display_name}' has no video stream.")
            acquired.append((path, info, trimmed))
            self.cb.item_state(i, "Ready")
            self._step_done()

        # 2) Conform every clip to the same size / fps / codecs.
        first_info = acquired[0][1]
        shared_size = canvas_size(self.s.canvas, self.s.resolution, first_info)
        if self.s.canvas == "source" and not self.join_enabled:
            shared_size = None  # each exported clip keeps its own size

        conformed: list[tuple[Path, float]] = []
        for i, (item, (path, info, trimmed)) in enumerate(zip(self.items, acquired)):
            self._check_cancel()
            n = i + 1
            self.cb.status(f"Processing {n}/{total} · {item.display_name}")
            self.cb.item_state(i, "Processing")
            out = work / f"conformed_{n:03d}.mp4"
            length = self._conform(item, path, info, trimmed, shared_size, out)
            conformed.append((out, length))
            self.cb.log(f"[{n}/{total}] Conformed → {tp.format_duration(length)}")
            self._step_done()

            if self.clips_enabled:
                result.clips.append(self._export_clip(item, n, out))
            self.cb.item_state(i, "Done")

        if self.clips_enabled:
            result.manifest = self._write_manifest(result.clips, conformed)

        # 3) Join.
        if self.join_enabled:
            self._check_cancel()
            self.cb.status("Joining…")
            result.joined, result.chapters_file, result.total_duration = self._join(work, conformed)
            self._step_done()
        else:
            result.total_duration = sum(length for _p, length in conformed)

        self.cb.progress(1.0)
        self.cb.status("Finished")
        return result

    # conform --------------------------------------------------------------------

    def _conform(self, item, path, info, trimmed, size, out) -> float:
        args: list[str] = []
        if item.has_range and not trimmed:
            # Local file: seek into it here.
            if info.duration and item.start >= info.duration:
                raise ValueError(
                    f"'{item.display_name}' is only {tp.format_clock(info.duration)} long, "
                    f"but the range starts at {tp.format_clock(item.start)}."
                )
            length = item.end - item.start
            if info.duration:
                length = min(length, info.duration - item.start)
            args += ["-ss", f"{item.start:.3f}", "-t", f"{length:.3f}"]
        elif item.has_range:
            # Already cut during download; cap to the requested length.
            length = item.end - item.start
            if info.duration:
                length = min(length, info.duration)
        else:
            length = info.duration or 0.0
        args += ["-i", str(path)]

        if info.has_audio:
            audio_in = "0:a:0"
        else:
            # Silent track so every clip has audio and the join stays in sync.
            args += ["-f", "lavfi", "-t", f"{max(length, 0.1):.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            audio_in = "1:a:0"

        graph = video_filter(size, self.s.fill, self.s.fps) + ";" + audio_filter(audio_in, self.s.loudnorm)
        args += [
            "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]",
        ]
        if length:
            args += ["-t", f"{length:.3f}"]
        args += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-video_track_timescale", "90000",
            "-movflags", "+faststart",
            str(out),
        ]
        run_ffmpeg(args, duration=length or None, on_progress=self._report, cancel=self.cancel)
        actual = probe(out).duration
        return actual or length

    # clips export -------------------------------------------------------------------

    def _export_clip(self, item: ClipItem, n: int, conformed: Path) -> Path:
        label = tp.sanitize_label(item.label) if item.label.strip() else ""
        if label:
            base = label
        else:
            base = tp.sanitize_label(Path(item.display_name).stem if not item.is_url else item.display_name,
                                     fallback=f"clip_{n:02d}")
        if item.has_range:
            base += f"_{tp.filename_time_tag(item.start)}_{tp.filename_time_tag(item.end)}"

        folder = self.output_dir
        if self.s.group_by_label and label:
            folder = folder / label
        if self.s.mode == "both":
            folder = self.output_dir / "clips" / (label if (self.s.group_by_label and label) else "")
        folder.mkdir(parents=True, exist_ok=True)
        target = unique_path(folder / f"{base}.mp4")
        shutil.copy2(conformed, target)
        self.cb.log(f"Saved clip → {target}")
        return target

    def _write_manifest(self, clips: list[Path], conformed: list[tuple[Path, float]]) -> Path:
        """Append one row per exported clip to manifest.csv - handy for building datasets."""
        root = self.output_dir
        manifest = root / "manifest.csv"
        new_file = not manifest.exists()
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(manifest, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if new_file:
                writer.writerow(["file", "label", "source", "start", "end", "duration", "created"])
            for item, clip, (_p, length) in zip(self.items, clips, conformed):
                try:
                    rel = clip.relative_to(root).as_posix()
                except ValueError:
                    rel = str(clip)
                writer.writerow([
                    rel,
                    item.label,
                    item.source,
                    f"{item.start:.3f}" if item.has_range else "",
                    f"{item.end:.3f}" if item.has_range else "",
                    f"{length:.3f}",
                    stamp,
                ])
        self.cb.log(f"Manifest updated → {manifest}")
        return manifest

    # join ------------------------------------------------------------------------------

    def _join(self, work: Path, conformed: list[tuple[Path, float]]):
        name = self.s.output_name.strip() or "joined_video"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        target = unique_path(self.output_dir / f"{name}.mp4")

        durations = [length for _p, length in conformed]
        xfade_name = TRANSITIONS.get(self.s.transition, TRANSITIONS["none"])[1]
        overlap = safe_overlap(durations, self.s.transition_duration) if xfade_name else 0.0
        if xfade_name and overlap <= 0 and len(conformed) > 1:
            self.cb.log("Clips are too short for a transition; using hard cuts.")
        total = sum(durations) - overlap * (len(durations) - 1)

        # Chapters: one per clip, titled with its label or title.
        starts = chapter_starts(durations, overlap)
        chapters = []
        for i, (item, start) in enumerate(zip(self.items, starts)):
            end = starts[i + 1] if i + 1 < len(starts) else total
            chapters.append((start, end, item.label.strip() or item.display_name))
        meta_args: list[str] = []
        if self.s.chapters and len(conformed) > 1:
            meta = work / "chapters.ffmeta"
            meta.write_text(ffmetadata(chapters), encoding="utf-8")
            meta_args = ["-i", str(meta)]

        if len(conformed) == 1:
            shutil.copy2(conformed[0][0], target)
        elif overlap > 0:
            self.cb.log(f"Joining {len(conformed)} clips with '{xfade_name}' transitions ({overlap:.2f}s)")
            args: list[str] = []
            for path, _len in conformed:
                args += ["-i", str(path)]
            args += meta_args
            parts = []
            for i in range(len(conformed)):
                parts.append(f"[{i}:v]settb=AVTB,fps={self.s.fps},format=yuv420p[v{i}]")
                parts.append(f"[{i}:a]asettb=AVTB[a{i}]")
            vprev, aprev = "v0", "a0"
            for i, offset in enumerate(xfade_offsets(durations, overlap), start=1):
                vout, aout = f"vx{i}", f"ax{i}"
                parts.append(
                    f"[{vprev}][v{i}]xfade=transition={xfade_name}:duration={overlap:.3f}:offset={offset:.3f}[{vout}]"
                )
                parts.append(f"[{aprev}][a{i}]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[{aout}]")
                vprev, aprev = vout, aout
            args += ["-filter_complex", ";".join(parts), "-map", f"[{vprev}]", "-map", f"[{aprev}]"]
            if meta_args:
                args += ["-map_chapters", str(len(conformed))]
            args += [
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target),
            ]
            run_ffmpeg(args, duration=total, on_progress=self._report, cancel=self.cancel)
        else:
            self.cb.log(f"Joining {len(conformed)} clips (lossless concat)")
            listing = work / "concat.txt"
            with open(listing, "w", encoding="utf-8") as fh:
                for path, _len in conformed:
                    fh.write("file '" + path.as_posix().replace("'", "'\\''") + "'\n")
            args = ["-f", "concat", "-safe", "0", "-i", str(listing), *meta_args, "-map", "0"]
            if meta_args:
                args += ["-map_chapters", "1"]
            args += ["-c", "copy", "-movflags", "+faststart", str(target)]
            run_ffmpeg(args, duration=total, on_progress=self._report, cancel=self.cancel)

        chapters_file = None
        if self.s.chapters and len(conformed) > 1:
            chapters_file = target.with_name(f"{target.stem}_chapters.txt")
            lines = [f"{tp.youtube_timestamp(start)} {title}" for start, _e, title in chapters]
            chapters_file.write_text(
                "# Paste into a YouTube description to get clickable chapters\n" + "\n".join(lines) + "\n",
                encoding="utf-8",
            )
        self.cb.log(f"Saved joined video → {target}")
        return target, chapters_file, total

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()
