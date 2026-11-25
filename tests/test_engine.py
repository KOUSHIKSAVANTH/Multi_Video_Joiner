import subprocess
import threading

import pytest

from mvjoiner.engine import (
    ClipItem,
    JobSettings,
    Pipeline,
    canvas_size,
    chapter_starts,
    ffmetadata,
    safe_overlap,
    xfade_offsets,
)
from mvjoiner.ffmpeg_tools import Cancelled, find_ffmpeg, probe
from mvjoiner.project import load_project, save_project


def test_canvas_sizes():
    assert canvas_size("landscape", 1080) == (1920, 1080)
    assert canvas_size("vertical", 1080) == (1080, 1920)
    assert canvas_size("square", 720) == (720, 720)
    assert canvas_size("portrait", 1080) == (1080, 1350)
    assert canvas_size("landscape", 720) == (1280, 720)
    assert canvas_size("source", 1080) is None


def test_transition_math():
    durations = [5.0, 4.0, 3.0]
    assert xfade_offsets(durations, 1.0) == [4.0, 7.0]
    assert chapter_starts(durations, 0.0) == [0.0, 5.0, 9.0]
    assert safe_overlap([5.0, 1.0], 2.0) == pytest.approx(0.45)
    assert safe_overlap([5.0], 1.0) == 0.0


def test_ffmetadata_escapes_titles():
    text = ffmetadata([(0, 1.5, "a=b;c")])
    assert "START=0" in text and "END=1500" in text
    assert "title=a\\=b\\;c" in text


def test_project_roundtrip(tmp_path):
    items = [ClipItem("https://youtu.be/abc", 1, 5, "wide"), ClipItem("C:/x.mp4")]
    settings = JobSettings(mode="both", canvas="vertical", transition="fade")
    path = save_project(tmp_path / "demo", items, settings)
    assert path.suffix == ".mvj"
    loaded_items, loaded_settings = load_project(path)
    assert loaded_items == items
    assert loaded_settings == settings


needs_ffmpeg = pytest.mark.skipif(find_ffmpeg() is None, reason="FFmpeg not available")


def _make_clip(path, size, seconds, audio=True):
    args = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    if find_ffmpeg() is None:
        pytest.skip("FFmpeg not available")
    return [
        _make_clip(folder / "a.mp4", "640x360", 4),
        _make_clip(folder / "b.mp4", "360x640", 3, audio=False),
    ]


@needs_ffmpeg
def test_join_with_transition_and_chapters(clips, tmp_path):
    items = [ClipItem(str(clips[0]), 0.5, 3.5, "first"), ClipItem(str(clips[1]), label="second")]
    settings = JobSettings(
        mode="both", output_dir=str(tmp_path), canvas="square", resolution=360,
        transition="fade", transition_duration=0.5, fps=25,
    )
    result = Pipeline(items, settings).run()

    info = probe(result.joined)
    assert (info.width, info.height) == (360, 360)
    assert info.has_audio
    assert info.duration == pytest.approx(3 + 3 - 0.5, abs=0.2)
    assert len(result.clips) == 2 and all(c.exists() for c in result.clips)
    assert result.manifest.read_text().count("\n") == 3
    assert "0:00 first" in result.chapters_file.read_text()


@needs_ffmpeg
def test_cancel_stops_pipeline(clips, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        Pipeline([ClipItem(str(clips[0]))], JobSettings(output_dir=str(tmp_path)), cancel=cancel).run()
