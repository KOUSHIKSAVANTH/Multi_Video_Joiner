"""
Regenerate the screenshots in docs/screenshots/ from the real application.

    python scripts/make_screenshots.py

It builds a demo queue (a few generated sample videos plus one public YouTube link),
runs a real job and captures the window at different moments. Your saved queue and
settings are not touched.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mvjoiner.engine import ClipItem, JobSettings  # noqa: E402
from mvjoiner.ffmpeg_tools import require_ffmpeg  # noqa: E402
from mvjoiner.theme import app_icon, apply_theme  # noqa: E402
from mvjoiner.ui import BulkAddDialog, MainWindow  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
DEMO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", the first YouTube video


def make_samples(folder: Path) -> dict[str, Path]:
    ffmpeg = require_ffmpeg()
    specs = {
        "intro_titles.mp4": ("testsrc2=size=1920x1080:rate=30", 12, 330),
        "street_wide_shot.mp4": ("smptehdbars=size=1280x720:rate=25", 40, 440),
        "phone_clip_vertical.mp4": ("testsrc=size=720x1280:rate=30", 18, 550),
        "outro_logo.mp4": ("rgbtestsrc=size=1920x1080:rate=30", 8, 660),
    }
    paths = {}
    for name, (source, seconds, freq) in specs.items():
        path = folder / name
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"{source}:duration={seconds}",
             "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
            check=True,
        )
        paths[name] = path
    return paths


def pump(seconds: float, until=None) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if until and until():
            return
        time.sleep(0.03)


def save(widget, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pump(0.4)
    widget.grab().save(str(OUT / name))
    print("saved", OUT / name)


def main() -> None:
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    apply_theme(app)

    # A neutral location so no personal paths show up in the screenshots.
    public = Path("C:/Users/Public/Videos")
    if sys.platform == "win32" and public.is_dir():
        work = public / "Multi Video Joiner Demo"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir()
    else:
        work = Path(tempfile.mkdtemp(prefix="mvj_shots_"))
    samples_dir = work / "samples"
    samples_dir.mkdir()
    samples = make_samples(samples_dir)

    window = MainWindow(restore_session=False)
    window.resize(1320, 820)
    window.apply_settings(
        JobSettings(
            mode="join",
            output_dir=str(work / "output"),
            output_name="weekend_highlights",
            canvas="vertical",
            resolution=720,
            fill="blur",
            fps=30,
            transition="fade",
            transition_duration=0.5,
            loudnorm=True,
            chapters=True,
        )
    )
    window.show()

    window.add_item(ClipItem(str(samples["intro_titles.mp4"]), 0, 4, "Intro"))
    window.add_item(ClipItem(DEMO_URL, 3, 9, "Zoo"))
    window.add_item(ClipItem(str(samples["street_wide_shot.mp4"]), 12, 18.5, "Wide shot"))
    window.add_item(ClipItem(str(samples["phone_clip_vertical.mp4"]), label="Phone clip"))
    window.add_item(ClipItem(str(samples["outro_logo.mp4"]), label="Outro"))

    def infos_done() -> bool:
        return all(s != "Fetching info…" for s in window._row_states.values())

    pump(30, infos_done)
    window.tree.setCurrentItem(window.tree.topLevelItem(2))

    # 1) Main window with a ready queue.
    save(window, "main-window.png")

    # 2) Bulk add dialog.
    dialog = BulkAddDialog(window, BulkAddDialog.EXAMPLE.split("\n", 1)[1])
    dialog.show()
    save(dialog, "bulk-add.png")
    dialog.close()

    # 3) A real job in progress, with the log open.
    window.tree.clearSelection()
    window.toggle_log()
    window.start_job()
    pump(120, lambda: window.progress.value() > 550 and "Processing" in window.status_label.text())
    save(window, "processing.png")

    # Wait for the job to finish; the "done" dialog is captured, then closed.
    def grab_done_dialog():
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible() and widget.windowTitle() == "All done":
                save(widget, "finished.png")
                widget.close()
                return
        QTimer.singleShot(200, grab_done_dialog)

    QTimer.singleShot(200, grab_done_dialog)
    pump(180, lambda: not window._busy() and (OUT / "finished.png").exists())

    window._pool.shutdown(wait=True)
    app.quit()
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
