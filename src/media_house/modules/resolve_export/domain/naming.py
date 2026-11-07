"""File names of deliveries: consistent, versioned, never reused."""

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe(text: str) -> str:
    return _UNSAFE.sub("-", text).strip("-._") or "video"


def delivery_name(
    project: str, timeline_version: int, profile_id: str, number: int, extension: str
) -> str:
    """``<project>_v<timeline>_<profile>_<number>.<ext>``: the export number only goes up."""
    stem = f"{safe(project)}_v{timeline_version:03d}_{safe(profile_id)}_{number:03d}"
    return f"{stem}.{extension.lstrip('.')}"
