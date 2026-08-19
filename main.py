import sys
import shutil
import subprocess
import threading
from pathlib import Path

import yt_dlp

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
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
OUTPUT_DIR = BASE_DIR / "output"

DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------
# WORKER SIGNALS
# ---------------------------------------------------------

class WorkerSignals(QObject):
    status = Signal(str)
    progress = Signal(int)
    finished = Signal(str)
    error = Signal(str)


# ---------------------------------------------------------
# DOWNLOAD + JOIN WORKER
# ---------------------------------------------------------

class VideoWorker:
    def __init__(self, urls, output_folder, quality):
        self.urls = urls
        self.output_folder = Path(output_folder)
        self.quality = quality
        self.signals = WorkerSignals()

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()

    def run(self):
        try:
            self.clean_temp()

            total = len(self.urls)
            downloaded_files = []

            for index, url in enumerate(self.urls, start=1):

                self.signals.status.emit(
                    f"Downloading {index}/{total}..."
                )

                file_path = self.download(url, index)

                if not file_path:
                    raise RuntimeError(
                        f"Download failed for URL #{index}"
                    )

                downloaded_files.append(file_path)

                self.signals.progress.emit(
                    int((index / total) * 70)
                )

            self.signals.status.emit(
                "Normalizing videos..."
            )

            normalized_files = []

            for index, file_path in enumerate(downloaded_files, start=1):

                normalized = self.normalize(
                    file_path,
                    index
                )

                normalized_files.append(normalized)

                self.signals.progress.emit(
                    70 + int((index / total) * 20)
                )

            self.signals.status.emit(
                "Joining videos..."
            )

            final_file = self.join(
                normalized_files
            )

            self.signals.progress.emit(100)

            self.signals.status.emit(
                "Completed!"
            )

            self.clean_temp()

            self.signals.finished.emit(
                str(final_file)
            )

        except Exception as error:

            self.signals.error.emit(
                str(error)
            )

    # -----------------------------------------------------
    # DOWNLOAD
    # -----------------------------------------------------

    def download(self, url, index):

        output_template = str(
            DOWNLOAD_DIR /
            f"video_{index:03d}.%(ext)s"
        )

        if self.quality == "1080p":
            height = 1080

        elif self.quality == "720p":
            height = 720

        else:
            height = 480

        options = {
            "format":
                f"bv*[height<={height}]+ba/"
                f"b[height<={height}]/b",

            "merge_output_format": "mp4",

            "outtmpl": output_template,

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "retries": 3,

            "fragment_retries": 3,
        }

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            requested = info.get(
                "requested_downloads"
            )

            if requested:

                filepath = requested[0].get(
                    "filepath"
                )

                if filepath:
                    path = Path(filepath)

                    if path.exists():
                        return path

            # Fallback: locate downloaded file
            candidates = list(
                DOWNLOAD_DIR.glob(
                    f"video_{index:03d}.*"
                )
            )

            candidates = [
                p for p in candidates
                if p.suffix.lower()
                in {
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                    ".avi"
                }
            ]

            if candidates:
                return candidates[0]

        return None

    # -----------------------------------------------------
    # NORMALIZE
    # -----------------------------------------------------

    def normalize(self, input_file, index):

        output_file = (
            TEMP_DIR /
            f"normalized_{index:03d}.mp4"
        )

        command = [
            "ffmpeg",
            "-y",

            "-i",
            str(input_file),

            # Video
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

            # Audio
            "-c:a",
            "aac",

            "-b:a",
            "192k",

            "-ar",
            "48000",

            # Avoid weird timestamps
            "-vsync",
            "cfr",

            str(output_file)
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:

            raise RuntimeError(
                "FFmpeg normalization failed:\n\n"
                + result.stderr[-3000:]
            )

        return output_file

    # -----------------------------------------------------
    # JOIN
    # -----------------------------------------------------

    def join(self, files):

        concat_file = (
            TEMP_DIR /
            "concat.txt"
        )

        with open(
            concat_file,
            "w",
            encoding="utf-8"
        ) as file:

            for video in files:

                safe_path = (
                    str(video)
                    .replace("\\", "/")
                    .replace("'", "'\\''")
                )

                file.write(
                    f"file '{safe_path}'\n"
                )

        final_file = (
            self.output_folder /
            "joined_video.mp4"
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

            str(final_file)
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:

            raise RuntimeError(
                "FFmpeg joining failed:\n\n"
                + result.stderr[-3000:]
            )

        return final_file

    # -----------------------------------------------------
    # CLEAN TEMP
    # -----------------------------------------------------

    def clean_temp(self):

        for folder in [
            DOWNLOAD_DIR,
            TEMP_DIR
        ]:

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

        self.setWindowTitle(
            "Multi Video Downloader & Joiner"
        )

        self.resize(850, 650)

        self.setup_ui()

    # -----------------------------------------------------
    # UI
    # -----------------------------------------------------

    def setup_ui(self):

        central = QWidget()

        self.setCentralWidget(
            central
        )

        layout = QVBoxLayout(
            central
        )

        # Title

        title = QLabel(
            "MULTI VIDEO DOWNLOADER"
        )

        title.setFont(
            QFont(
                "Segoe UI",
                20,
                QFont.Bold
            )
        )

        title.setAlignment(
            Qt.AlignCenter
        )

        layout.addWidget(title)

        subtitle = QLabel(
            "Download multiple supported videos and join them automatically"
        )

        subtitle.setAlignment(
            Qt.AlignCenter
        )

        layout.addWidget(subtitle)

        # URL input

        url_layout = QHBoxLayout()

        self.url_input = QLineEdit()

        self.url_input.setPlaceholderText(
            "Paste a YouTube or Instagram URL..."
        )

        add_button = QPushButton(
            "Add URL"
        )

        add_button.clicked.connect(
            self.add_url
        )

        url_layout.addWidget(
            self.url_input
        )

        url_layout.addWidget(
            add_button
        )

        layout.addLayout(
            url_layout
        )

        # URL list

        self.url_list = QListWidget()

        self.url_list.setDragDropMode(
            QListWidget.InternalMove
        )

        layout.addWidget(
            self.url_list
        )

        # Buttons

        button_layout = QHBoxLayout()

        remove_button = QPushButton(
            "Remove Selected"
        )

        remove_button.clicked.connect(
            self.remove_selected
        )

        clear_button = QPushButton(
            "Clear All"
        )

        clear_button.clicked.connect(
            self.clear_all
        )

        button_layout.addWidget(
            remove_button
        )

        button_layout.addWidget(
            clear_button
        )

        layout.addLayout(
            button_layout
        )

        # Quality

        quality_layout = QHBoxLayout()

        quality_label = QLabel(
            "Maximum Quality:"
        )

        self.quality_box = QSpinBox()

        self.quality_box.setRange(
            480,
            1080
        )

        self.quality_box.setSingleStep(
            240
        )

        self.quality_box.setValue(
            1080
        )

        self.quality_box.setSuffix(
            "p"
        )

        quality_layout.addWidget(
            quality_label
        )

        quality_layout.addWidget(
            self.quality_box
        )

        quality_layout.addStretch()

        layout.addLayout(
            quality_layout
        )

        # Output

        output_layout = QHBoxLayout()

        self.output_label = QLineEdit(
            str(OUTPUT_DIR)
        )

        self.output_label.setReadOnly(
            True
        )

        output_button = QPushButton(
            "Choose Output"
        )

        output_button.clicked.connect(
            self.choose_output
        )

        output_layout.addWidget(
            self.output_label
        )

        output_layout.addWidget(
            output_button
        )

        layout.addLayout(
            output_layout
        )

        # Start button

        self.start_button = QPushButton(
            "DOWNLOAD & JOIN"
        )

        self.start_button.setMinimumHeight(
            50
        )

        self.start_button.setFont(
            QFont(
                "Segoe UI",
                13,
                QFont.Bold
            )
        )

        self.start_button.clicked.connect(
            self.start_download
        )

        layout.addWidget(
            self.start_button
        )

        # Progress

        self.progress = QProgressBar()

        self.progress.setValue(
            0
        )

        layout.addWidget(
            self.progress
        )

        # Status

        self.status_label = QLabel(
            "Ready"
        )

        self.status_label.setAlignment(
            Qt.AlignCenter
        )

        layout.addWidget(
            self.status_label
        )

    # -----------------------------------------------------
    # ADD URL
    # -----------------------------------------------------

    def add_url(self):

        text = (
            self.url_input
            .text()
            .strip()
        )

        if not text:
            return

        # Allow multiple URLs separated
        # by new lines or spaces

        urls = text.splitlines()

        for url in urls:

            url = url.strip()

            if url:

                self.url_list.addItem(
                    QListWidgetItem(url)
                )

        self.url_input.clear()

    # -----------------------------------------------------
    # REMOVE
    # -----------------------------------------------------

    def remove_selected(self):

        rows = sorted(
            [
                index.row()
                for index
                in self.url_list.selectedIndexes()
            ],
            reverse=True
        )

        for row in rows:

            self.url_list.takeItem(
                row
            )

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    def clear_all(self):

        self.url_list.clear()

    # -----------------------------------------------------
    # OUTPUT
    # -----------------------------------------------------

    def choose_output(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Output Folder"
        )

        if folder:

            self.output_label.setText(
                folder
            )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    def start_download(self):

        urls = []

        for index in range(
            self.url_list.count()
        ):

            urls.append(
                self.url_list.item(index).text()
            )

        if not urls:

            QMessageBox.warning(
                self,
                "No URLs",
                "Add at least one URL."
            )

            return

        output_folder = Path(
            self.output_label.text()
        )

        output_folder.mkdir(
            parents=True,
            exist_ok=True
        )

        quality = f"{self.quality_box.value()}p"

        self.start_button.setEnabled(
            False
        )

        self.progress.setValue(
            0
        )

        self.status_label.setText(
            "Starting..."
        )

        self.worker = VideoWorker(
            urls,
            output_folder,
            quality
        )

        self.worker.signals.status.connect(
            self.update_status
        )

        self.worker.signals.progress.connect(
            self.progress.setValue
        )

        self.worker.signals.finished.connect(
            self.download_finished
        )

        self.worker.signals.error.connect(
            self.download_error
        )

        self.worker.start()

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    def update_status(self, message):

        self.status_label.setText(
            message
        )

    # -----------------------------------------------------
    # FINISHED
    # -----------------------------------------------------

    def download_finished(
        self,
        file_path
    ):

        self.start_button.setEnabled(
            True
        )

        QMessageBox.information(
            self,
            "Completed",
            "All videos were downloaded "
            "and joined successfully.\n\n"
            f"Saved to:\n{file_path}"
        )

        self.status_label.setText(
            "Completed!"
        )

    # -----------------------------------------------------
    # ERROR
    # -----------------------------------------------------

    def download_error(
        self,
        message
    ):

        self.start_button.setEnabled(
            True
        )

        self.progress.setValue(
            0
        )

        self.status_label.setText(
            "Failed"
        )

        QMessageBox.critical(
            self,
            "Error",
            message
        )


# ---------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------

def main():

    app = QApplication(
        sys.argv
    )

    window = MainWindow()

    window.show()

    sys.exit(
        app.exec()
    )


if __name__ == "__main__":
    main()