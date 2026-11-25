import pytest

from mvjoiner import timeparse as tp


@pytest.mark.parametrize(
    "text, expected",
    [("7", 7), ("7.5", 7.5), ("7s", 7), ("1:25", 85), ("01:02:30", 3750), (" 0:05 ", 5)],
)
def test_parse_timestamp(text, expected):
    assert tp.parse_timestamp(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "-5"])
def test_parse_timestamp_invalid(text):
    with pytest.raises(ValueError):
        tp.parse_timestamp(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0-7", (0, 7)),
        ("1:25 - 1:30", (85, 90)),
        ("0:05 to 0:12", (5, 12)),
        ("1:00–1:10", (60, 70)),
    ],
)
def test_parse_time_range(text, expected):
    assert tp.parse_time_range(text) == expected


@pytest.mark.parametrize("text", ["7-3", "5-5", "1:25", "a-b"])
def test_parse_time_range_invalid(text):
    with pytest.raises(ValueError):
        tp.parse_time_range(text)


def test_optional_range_empty_means_full_video():
    assert tp.parse_optional_range("") == (None, None)
    assert tp.parse_optional_range("   ") == (None, None)


def test_formatting():
    assert tp.format_clock(75.5) == "1:15.5"
    assert tp.format_clock(3725) == "1:02:05"
    assert tp.format_range(None, None) == "Full video"
    assert tp.format_duration(75) == "1m 15s"
    assert tp.format_duration(3725) == "1h 2m"
    assert tp.youtube_timestamp(0) == "0:00"
    assert tp.youtube_timestamp(3723) == "1:02:03"
    assert tp.filename_time_tag(85) == "01m25s"


def test_sanitize_label():
    assert tp.sanitize_label("Low Angle / Close-up!") == "low_angle_close-up"
    assert tp.sanitize_label("   ") == "clip"


@pytest.mark.parametrize(
    "line, expected",
    [
        ("https://youtu.be/abc", ("https://youtu.be/abc", "", "")),
        ("https://youtu.be/abc 1:25-1:30 wide shot", ("https://youtu.be/abc", "1:25-1:30", "wide shot")),
        ("https://youtu.be/abc 1:25 - 1:30 top", ("https://youtu.be/abc", "1:25 - 1:30", "top")),
        ("https://youtu.be/abc | 0-7 | close up", ("https://youtu.be/abc", "0-7", "close up")),
        ("C:/v/intro.mp4 | | intro", ("C:/v/intro.mp4", "", "intro")),
        ("https://youtu.be/abc close-up", ("https://youtu.be/abc", "", "close-up")),
    ],
)
def test_parse_bulk_line(line, expected):
    assert tp.parse_bulk_line(line) == expected


def test_parse_bulk_line_skips_comments_and_blanks():
    assert tp.parse_bulk_line("") is None
    assert tp.parse_bulk_line("# note") is None


def test_parse_bulk_line_bad_range():
    with pytest.raises(ValueError):
        tp.parse_bulk_line("https://youtu.be/abc | 9-3 | x")
