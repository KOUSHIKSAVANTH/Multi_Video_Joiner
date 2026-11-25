"""Save and load the queue + settings as a small JSON project file (*.mvj)."""

from __future__ import annotations

import json
from pathlib import Path

from . import __version__
from .engine import ClipItem, JobSettings

PROJECT_SUFFIX = ".mvj"


def save_project(path: str | Path, items: list[ClipItem], settings: JobSettings) -> Path:
    path = Path(path)
    if path.suffix.lower() != PROJECT_SUFFIX:
        path = path.with_suffix(PROJECT_SUFFIX)
    data = {
        "app": "multi-video-joiner",
        "version": __version__,
        "items": [item.to_dict() for item in items],
        "settings": settings.to_dict(),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_project(path: str | Path) -> tuple[list[ClipItem], JobSettings]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError("This is not a Multi Video Joiner project file.")
    items = [ClipItem.from_dict(entry) for entry in data.get("items", []) if entry.get("source")]
    settings = JobSettings.from_dict(data.get("settings", {}))
    return items, settings
