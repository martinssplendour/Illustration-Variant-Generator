"""File cleanup utilities for ephemeral runtime data."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


def cleanup_folder(folder: Path, max_age_minutes: int) -> int:
    """Deletes old files in the folder and returns how many were removed."""
    if not folder.exists():
        return 0

    removed = 0
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        if max_age_minutes > 0:
            mtime = datetime.utcfromtimestamp(entry.stat().st_mtime)
            if mtime > cutoff:
                continue
        try:
            entry.unlink()
            removed += 1
        except OSError:
            continue
    return removed
