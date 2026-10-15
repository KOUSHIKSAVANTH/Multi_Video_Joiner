import re
import sys
import shutil
import subprocess
import threading
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------
# PATHS
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "dataset_output"

DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------
# TIME RANGE PARSER
# ---------------------------------------------------------

def parse_timestamp(ts_str: str) -> float:
    """
    Convert a timestamp string into seconds.
    Supports formats like:
      - '7' or '7.5' or '7s'
      - '1:25' (MM:SS)
      - '01:02:30' (HH:MM:SS)
    """
    cleaned = ts_str.strip().lower().rstrip("s").strip()
    if not cleaned:
        raise ValueError("Empty timestamp.")

    parts = cleaned.split(":")
    if len(parts) == 1:
        seconds = float(parts[0])
    elif len(parts) == 2:
        minutes = int(parts[0])
        seconds = minutes * 60 + float(parts[1])
    elif len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = hours * 3600 + minutes * 60 + float(parts[2])
    else:
        raise ValueError(f"Invalid timestamp format: '{ts_str}'")

    if seconds < 0:
        raise ValueError("Timestamp cannot be negative.")
    return seconds


def parse_time_range(range_str: str) -> tuple[float, float]:
    """
    Parse a time range string into (start_seconds, end_seconds).
    Supports formats like:
      - '0-7'
      - '0 - 10s'
      - '1:25-1:30'
      - '1:25 - 1:30'
      - '0:05 to 0:12'
    """
    cleaned = range_str.strip()
    match = re.split(r"\s*(?:-|–|—|to)\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)
    if len(match) != 2 or not match[0] or not match[1]:
        raise ValueError(
            f"Invalid time range '{range_str}'. "
            "Use formats like '0-7' or '1:25-1:30'."
        )

    start_sec = parse_timestamp(match[0])
    end_sec = parse_timestamp(match[1])

    if end_sec <= start_sec:
        raise ValueError(
            f"End time ({end_sec:.1f}s) must be greater than start time ({start_sec:.1f}s)."
        )

    return start_sec, end_sec


def format_seconds_label(seconds: float) -> str:
    """Format seconds as a filename-safe label like 01m25s."""
    total_sec = int(round(seconds))
    mins, secs = divmod(total_sec, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs:02d}h{mins:02d}m{secs:02d}s"
    return f"{mins:02d}m{secs:02d}s"


def sanitize_label(label: str) -> str:
    """Sanitize camera angle label for safe folder and file names."""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", label.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "clip"


# ---------------------------------------------------------
# WORKER SIGNALS
# ---------------------------------------------------------

class WorkerSignals(QObject):
    status = Signal(str)
    progress = Signal(int)
    finished = Signal(str)
    error = Signal(str)


# ---------------------------------------------------------
# PARTIAL DOWNLOAD + NORMALIZE WORKER
# ---------------------------------------------------------

class ClipWorker:
    def __init__(
        self,
        clip_items,
        output_folder,
        quality,
        cookie_source="auto",
        cookie_file="",
        normalize_clips=True,
        group_by_angle=True,
        join_all=False,
    ):
        """
        clip_items: list of dicts with keys:
            - url (str)
            - start (float)
            - end (float)
            - angle (str)
        """
        self.clip_items = clip_items
        self.output_folder = Path(output_folder)
        self.quality = quality
        self.cookie_source = cookie_source
        self.cookie_file = cookie_file
        self.normalize_clips = normalize_clips
        self.group_by_angle = group_by_angle
        self.join_all = join_all
        self.signals = WorkerSignals()

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()

    def run(self):
        try:
            self.clean_temp()

            total = len(self.clip_items)
            saved_files = []

            for index, item in enumerate(self.clip_items, start=1):
                url = item["url"]
                start_sec = item["start"]
                end_sec = item["end"]
                angle = sanitize_label(item["angle"])
                duration = end_sec - start_sec

                self.signals.status.emit(
                    f"Downloading clip {index}/{total} "
                    f"[{angle} | {start_sec:.1f}s - {end_sec:.1f}s]..."
                )

                raw_clip = self.download_segment(
                    url=url,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    index=index,
                )

                if not raw_clip or not raw_clip.exists():
                    raise RuntimeError(
                        f"Failed to download segment for clip #{index}: {url}"
                    )

                # Determine target destination for this dataset clip
                if self.group_by_angle:
                    target_dir = self.output_folder / angle
                else:
                    target_dir = self.output_folder
                target_dir.mkdir(parents=True, exist_ok=True)

                time_tag = (
                    f"{format_seconds_label(start_sec)}_"
                    f"{format_seconds_label(end_sec)}"
                )
                final_clip_path = self.get_unique_path(
                    target_dir / f"{angle}_{time_tag}.mp4"
                )

                if self.normalize_clips:
                    self.signals.status.emit(
                        f"Normalizing clip {index}/{total} ({angle})..."
                    )
                    self.normalize(
                        input_file=raw_clip,
                        output_file=final_clip_path,
                        expected_duration=duration,
                    )
                else:
                    self.remux_or_trim_exact(
                        input_file=raw_clip,
                        output_file=final_clip_path,
                        expected_duration=duration,
                    )

                saved_files.append(final_clip_path)

                progress_pct = int((index / total) * (85 if self.join_all else 100))
                self.signals.progress.emit(progress_pct)

            joined_path = None
            if self.join_all and len(saved_files) > 1:
                self.signals.status.emit("Joining clips into compilation...")
                joined_path = self.join(saved_files)

            self.signals.progress.emit(100)
            self.signals.status.emit("Completed!")
            self.clean_temp()

            summary_msg = (
                f"Saved {len(saved_files)} clip(s) to:\n{self.output_folder}"
            )
            if joined_path:
                summary_msg += f"\n\nJoined compilation:\n{joined_path}"

            self.signals.finished.emit(summary_msg)

        except Exception as error:
            self.signals.error.emit(str(error))

    # -----------------------------------------------------
    # RESTRICTION-RESILIENT PARTIAL DOWNLOAD
    # -----------------------------------------------------

    def _get_height_limit(self) -> int:
        if self.quality == "1080p":
            return 1080
        elif self.quality == "720p":
            return 720
        return 480

    def _build_attempt_profiles(self):
        """
        Build a list of yt-dlp configuration profiles to bypass common
        public video restrictions (bot checks, client blocks, age/login walls).
        """
        profiles = []

        # If user explicitly picked a cookies.txt file or specific browser
        if self.cookie_source == "file" and self.cookie_file:
            profiles.append({"cookiefile": self.cookie_file, "client": "default"})
        elif self.cookie_source not in ("auto", "none", "file"):
            profiles.append(
                {
                    "cookiesfrombrowser": (self.cookie_source,),
                    "client": "default",
                }
            )

        # Profile 1: Multi-client mobile/web fallback (bypasses most public YT restrictions without cookies)
        profiles.append(
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["ios", "android", "web_creator", "tv", "web"]
                    }
                },
                "client": "multi_client",
            }
        )

        # Profile 2: TV / Embedded player client (helps with age-gated or embed-allowed public videos)
        profiles.append(
            {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["tv_embedded", "mweb", "ios"]
                    }
                },
                "client": "tv_embedded",
            }
        )

        # Profile 3: Auto-detect browser cookies if enabled ('auto') for Instagram/YouTube rate limits
        if self.cookie_source == "auto":
            for browser in ("chrome", "edge", "firefox", "brave"):
                profiles.append(
                    {
                        "cookiesfrombrowser": (browser,),
                        "client": f"cookies_{browser}",
                    }
                )

        return profiles

    def download_segment(self, url: str, start_sec: float, end_sec: float, index: int):
        """
        Download only the [start_sec, end_sec] portion of a video.
        Tries yt-dlp's native download_ranges first, then falls back to
        direct FFmpeg HTTP stream slicing if a site stream rejects range cuts.
        """
        height = self._get_height_limit()
        output_template = str(DOWNLOAD_DIR / f"clip_{index:03d}.%(ext)s")
        profiles = self._build_attempt_profiles()
        last_error = None

        # Method A: Native yt-dlp partial range download
        for profile in profiles:
            self._clear_index_files(index)
            options = {
                "format": (
                    f"bv*[height<={height}][protocol^=http]+ba[protocol^=http]/"
                    f"bv*[height<={height}]+ba/"
                    f"b[height<={height}]/b"
                ),
                "merge_output_format": "mp4",
                "outtmpl": output_template,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "retries": 5,
                "fragment_retries": 5,
                "extractor_retries": 3,
                "download_ranges": download_range_func(
                    None, [(start_sec, end_sec)]
                ),
                "force_keyframes_at_cuts": True,
                "http_headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            }

            if "extractor_args" in profile:
                options["extractor_args"] = profile["extractor_args"]
            if "cookiesfrombrowser" in profile:
                options["cookiesfrombrowser"] = profile["cookiesfrombrowser"]
            if "cookiefile" in profile:
                options["cookiefile"] = profile["cookiefile"]

            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=True)
                    found = self._locate_downloaded_file(info, index)
                    if found and found.exists() and found.stat().st_size > 0:
                        return found
            except Exception as err:
                last_error = err

            # Method B fallback on the same profile: Extract stream URLs & slice directly via FFmpeg
            try:
                direct_clip = self._ffmpeg_direct_stream_slice(
                    url=url,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    index=index,
                    height=height,
                    profile=profile,
                )
                if direct_clip and direct_clip.exists() and direct_clip.stat().st_size > 0:
                    return direct_clip
            except Exception as fallback_err:
                if last_error is None:
                    last_error = fallback_err

        raise RuntimeError(
            f"Unable to download clip #{index} ({url}).\n"
            f"Last error: {last_error}\n\n"
            "Tip: Select your logged-in browser under 'Browser Cookies' "
            "or click 'Update yt-dlp' if the site recently changed."
        )

    def _ffmpeg_direct_stream_slice(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        index: int,
        height: int,
        profile: dict,
    ):
        """
        Fallback: extract direct media stream URL(s) without downloading the full
        file, then use FFmpeg fast input seeking (-ss before -i) to pull only
        the target seconds over HTTP.
        """
        extract_opts = {
            "format": f"bv*[height<={height}]+ba/b[height<={height}]/b",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
        }
        if "extractor_args" in profile:
            extract_opts["extractor_args"] = profile["extractor_args"]
        if "cookiesfrombrowser" in profile:
            extract_opts["cookiesfrombrowser"] = profile["cookiesfrombrowser"]
        if "cookiefile" in profile:
            extract_opts["cookiefile"] = profile["cookiefile"]

        with yt_dlp.YoutubeDL(extract_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        duration = end_sec - start_sec
        output_file = DOWNLOAD_DIR / f"clip_{index:03d}.mp4"

        requested_formats = info.get("requested_formats")
        command = ["ffmpeg", "-y"]

        if requested_formats and len(requested_formats) >= 2:
            video_url = requested_formats[0]["url"]
            audio_url = requested_formats[1]["url"]
            command.extend(
                [
                    "-ss",
                    f"{start_sec:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    video_url,
                    "-ss",
                    f"{start_sec:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    audio_url,
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0?",
                ]
            )
        else:
            stream_url = info.get("url")
            if not stream_url:
                return None
            command.extend(
                [
                    "-ss",
                    f"{start_sec:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    stream_url,
                ]
            )

        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_file),
            ]
        )

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-1500:])
        return output_file

    def _locate_downloaded_file(self, info, index: int):
        if info:
            requested = info.get("requested_downloads")
            if requested:
                filepath = requested[0].get("filepath")
                if filepath:
                    path = Path(filepath)
                    if path.exists():
                        return path

        candidates = [
            p
            for p in DOWNLOAD_DIR.glob(f"clip_{index:03d}.*")
            if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi"}
        ]
        return candidates[0] if candidates else None

    def _clear_index_files(self, index: int):
        for item in DOWNLOAD_DIR.glob(f"clip_{index:03d}.*"):
            try:
                item.unlink()
            except Exception:
                pass

    # -----------------------------------------------------
    # NORMALIZE / EXACT TRIM
    # -----------------------------------------------------

    def normalize(self, input_file: Path, output_file: Path, expected_duration: float):
        """
        Normalize resolution (1920x1080 padded), frame rate (30fps), pixel format,
        and enforce exact clip duration.
        """
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_file),
            "-t",
            f"{expected_duration:.3f}",
            "-vf",
            (
                "scale=1920:1080:"
                "force_original_aspect_ratio=decrease,"
                "pad=1920:1080:"
                "(ow-iw)/2:"
                "(oh-ih)/2"
            ),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-vsync",
            "cfr",
            "-movflags",
            "+faststart",
            str(output_file),
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg normalization failed:\n\n" + result.stderr[-3000:]
            )

        return output_file

    def remux_or_trim_exact(
        self, input_file: Path, output_file: Path, expected_duration: float
    ):
        """
        Keep original resolution/aspect ratio while re-encoding cleanly to MP4
        with exact duration.
        """
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_file),
            "-t",
            f"{expected_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_file),
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg clip export failed:\n\n" + result.stderr[-3000:]
            )

        return output_file

    # -----------------------------------------------------
    # OPTIONAL JOIN
    # -----------------------------------------------------

    def join(self, files):
        concat_file = TEMP_DIR / "concat.txt"

        with open(concat_file, "w", encoding="utf-8") as file:
            for video in files:
                safe_path = (
                    str(video).replace("\\", "/").replace("'", "'\\''")
                )
                file.write(f"file '{safe_path}'\n")

        final_file = self.get_unique_path(
            self.output_folder / "joined_compilation.mp4"
        )

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(final_file),
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg joining failed:\n\n" + result.stderr[-3000:]
            )

        return final_file

    @staticmethod
    def get_unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 2
        while True:
            candidate = parent / f"{stem}_{counter:03d}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    # -----------------------------------------------------
    # CLEAN TEMP
    # -----------------------------------------------------

    def clean_temp(self):
        for folder in [DOWNLOAD_DIR, TEMP_DIR]:
            for item in folder.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except Exception:
                    pass


# ---------------------------------------------------------
# MAIN WINDOW
# ---------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.worker = None
        self.setWindowTitle("Camera Angle Dataset Clipper (Partial Video Downloader)")
        self.resize(920, 720)
        self.setup_ui()

    # -----------------------------------------------------
    # UI
    # -----------------------------------------------------

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Title
        title = QLabel("CAMERA ANGLE CLIP DOWNLOADER")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "Download exact timestamp ranges (e.g. 0-7 or 1:25-1:30) without downloading the full video"
        )
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        # Clip Entry Row (URL + Time Range + Manual Camera Angle Input)
        entry_layout = QHBoxLayout()

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube / Instagram / video URL...")

        self.range_input = QLineEdit()
        self.range_input.setPlaceholderText("Time range (e.g. 0-7 or 1:25-1:30)")
        self.range_input.setFixedWidth(210)

        self.angle_input = QLineEdit()
        self.angle_input.setPlaceholderText("Enter camera angle name...")
        self.angle_input.setFixedWidth(200)

        add_button = QPushButton("Add Clip")
        add_button.clicked.connect(self.add_clip)

        entry_layout.addWidget(self.url_input, stretch=1)
        entry_layout.addWidget(self.range_input)
        entry_layout.addWidget(self.angle_input)
        entry_layout.addWidget(add_button)

        layout.addLayout(entry_layout)

        # Queue List
        self.clip_list = QListWidget()
        self.clip_list.setDragDropMode(QListWidget.InternalMove)
        layout.addWidget(self.clip_list)

        # Queue Management Buttons
        button_layout = QHBoxLayout()

        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self.remove_selected)

        clear_button = QPushButton("Clear All")
        clear_button.clicked.connect(self.clear_all)

        update_ytdlp_button = QPushButton("Update yt-dlp")
        update_ytdlp_button.setToolTip(
            "Upgrades yt-dlp to fix sudden YouTube/Instagram download blocks"
        )
        update_ytdlp_button.clicked.connect(self.update_ytdlp)

        button_layout.addWidget(remove_button)
        button_layout.addWidget(clear_button)
        button_layout.addWidget(update_ytdlp_button)
        layout.addLayout(button_layout)

        # Options Row 1: Quality + Restriction Bypass (Cookies)
        options_row1 = QHBoxLayout()

        quality_label = QLabel("Max Quality:")
        self.quality_box = QSpinBox()
        self.quality_box.setRange(480, 1080)
        self.quality_box.setSingleStep(240)
        self.quality_box.setValue(1080)
        self.quality_box.setSuffix("p")

        cookie_label = QLabel("Restriction Bypass (Cookies):")
        self.cookie_combo = QComboBox()
        self.cookie_combo.addItems(
            [
                "Auto-Fallback (Recommended)",
                "None (Client Bypass Only)",
                "Chrome",
                "Edge",
                "Firefox",
                "Brave",
                "Custom cookies.txt...",
            ]
        )
        self.cookie_combo.currentTextChanged.connect(self.on_cookie_mode_changed)

        self.cookie_file_input = QLineEdit()
        self.cookie_file_input.setPlaceholderText("Path to Netscape cookies.txt...")
        self.cookie_file_input.setVisible(False)

        options_row1.addWidget(quality_label)
        options_row1.addWidget(self.quality_box)
        options_row1.addSpacing(20)
        options_row1.addWidget(cookie_label)
        options_row1.addWidget(self.cookie_combo)
        options_row1.addWidget(self.cookie_file_input, stretch=1)
        options_row1.addStretch()

        layout.addLayout(options_row1)

        # Options Row 2: Dataset Organization & Normalization
        options_row2 = QHBoxLayout()

        self.group_by_angle_cb = QCheckBox("Group into subfolders by camera angle")
        self.group_by_angle_cb.setChecked(True)

        self.normalize_cb = QCheckBox("Normalize to 1080p 30fps MP4")
        self.normalize_cb.setChecked(True)

        self.join_all_cb = QCheckBox("Also join all clips into one video")
        self.join_all_cb.setChecked(False)

        options_row2.addWidget(self.group_by_angle_cb)
        options_row2.addSpacing(15)
        options_row2.addWidget(self.normalize_cb)
        options_row2.addSpacing(15)
        options_row2.addWidget(self.join_all_cb)
        options_row2.addStretch()

        layout.addLayout(options_row2)

        # Output Folder Row
        output_layout = QHBoxLayout()

        self.output_label = QLineEdit(str(OUTPUT_DIR))
        self.output_label.setReadOnly(True)

        output_button = QPushButton("Choose Output Folder")
        output_button.clicked.connect(self.choose_output)

        output_layout.addWidget(self.output_label)
        output_layout.addWidget(output_button)
        layout.addLayout(output_layout)

        # Start Button
        self.start_button = QPushButton("DOWNLOAD TRIMMED CLIPS")
        self.start_button.setMinimumHeight(50)
        self.start_button.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.start_button.clicked.connect(self.start_download)
        layout.addWidget(self.start_button)

        # Progress Bar
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        # Status Label
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

    # -----------------------------------------------------
    # COOKIE MODE HANDLER
    # -----------------------------------------------------

    def on_cookie_mode_changed(self, text: str):
        if text == "Custom cookies.txt...":
            self.cookie_file_input.setVisible(True)
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select cookies.txt File",
                "",
                "Text Files (*.txt);;All Files (*)",
            )
            if file_path:
                self.cookie_file_input.setText(file_path)
        else:
            self.cookie_file_input.setVisible(False)

    # -----------------------------------------------------
    # ADD CLIP
    # -----------------------------------------------------

    def add_clip(self):
        url = self.url_input.text().strip()
        range_text = self.range_input.text().strip()
        angle = self.angle_input.text().strip()

        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a video URL.")
            return

        if not range_text:
            QMessageBox.warning(
                self,
                "Missing Time Range",
                "Please enter a time range like '0-7' or '1:25-1:30'.",
            )
            return

        if not angle:
            QMessageBox.warning(
                self,
                "Missing Camera Angle",
                "Please enter the camera angle name for this clip.",
            )
            return

        try:
            start_sec, end_sec = parse_time_range(range_text)
        except ValueError as err:
            QMessageBox.warning(self, "Invalid Time Range", str(err))
            return

        duration = end_sec - start_sec
        display_text = (
            f"[{sanitize_label(angle)}]  "
            f"{start_sec:.1f}s - {end_sec:.1f}s ({duration:.1f}s)  |  {url}"
        )

        item = QListWidgetItem(display_text)
        item.setData(
            Qt.UserRole,
            {
                "url": url,
                "start": start_sec,
                "end": end_sec,
                "angle": angle,
            },
        )
        self.clip_list.addItem(item)

        self.url_input.clear()
        self.range_input.clear()
        self.angle_input.clear()

    # -----------------------------------------------------
    # REMOVE / CLEAR
    # -----------------------------------------------------

    def remove_selected(self):
        rows = sorted(
            [index.row() for index in self.clip_list.selectedIndexes()],
            reverse=True,
        )
        for row in rows:
            self.clip_list.takeItem(row)

    def clear_all(self):
        self.clip_list.clear()

    # -----------------------------------------------------
    # UPDATE YT-DLP
    # -----------------------------------------------------

    def update_ytdlp(self):
        self.status_label.setText("Updating yt-dlp...")
        QApplication.processEvents()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode == 0:
                QMessageBox.information(
                    self,
                    "Updated",
                    "yt-dlp was updated successfully! Restart the app if needed.",
                )
                self.status_label.setText("yt-dlp updated!")
            else:
                QMessageBox.warning(
                    self,
                    "Update Failed",
                    result.stderr[-1500:] or "Could not update yt-dlp.",
                )
                self.status_label.setText("Update failed")
        except Exception as err:
            QMessageBox.warning(self, "Update Error", str(err))
            self.status_label.setText("Ready")

    # -----------------------------------------------------
    # OUTPUT FOLDER
    # -----------------------------------------------------

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if folder:
            self.output_label.setText(folder)

    # -----------------------------------------------------
    # START DOWNLOAD
    # -----------------------------------------------------

    def start_download(self):
        clip_items = []
        for index in range(self.clip_list.count()):
            data = self.clip_list.item(index).data(Qt.UserRole)
            if data:
                clip_items.append(data)

        if not clip_items:
            QMessageBox.warning(
                self, "No Clips", "Add at least one URL + time range to the queue."
            )
            return

        output_folder = Path(self.output_label.text())
        output_folder.mkdir(parents=True, exist_ok=True)

        quality = f"{self.quality_box.value()}p"

        cookie_choice = self.cookie_combo.currentText()
        if cookie_choice.startswith("Auto"):
            cookie_source = "auto"
        elif cookie_choice.startswith("None"):
            cookie_source = "none"
        elif cookie_choice.startswith("Custom"):
            cookie_source = "file"
        else:
            cookie_source = cookie_choice.lower()

        cookie_file = self.cookie_file_input.text().strip()

        self.start_button.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("Starting partial downloads...")

        self.worker = ClipWorker(
            clip_items=clip_items,
            output_folder=output_folder,
            quality=quality,
            cookie_source=cookie_source,
            cookie_file=cookie_file,
            normalize_clips=self.normalize_cb.isChecked(),
            group_by_angle=self.group_by_angle_cb.isChecked(),
            join_all=self.join_all_cb.isChecked(),
        )

        self.worker.signals.status.connect(self.update_status)
        self.worker.signals.progress.connect(self.progress.setValue)
        self.worker.signals.finished.connect(self.download_finished)
        self.worker.signals.error.connect(self.download_error)

        self.worker.start()

    # -----------------------------------------------------
    # SIGNALS
    # -----------------------------------------------------

    def update_status(self, message):
        self.status_label.setText(message)

    def download_finished(self, summary_message):
        self.start_button.setEnabled(True)
        QMessageBox.information(self, "Completed", summary_message)
        self.status_label.setText("Completed!")

    def download_error(self, message):
        self.start_button.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Failed")
        QMessageBox.critical(self, "Error", message)


# ---------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()