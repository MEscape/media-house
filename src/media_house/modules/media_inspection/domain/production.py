"""Camera and clip information from metadata tags.

Tag names differ by vendor and container (QuickTime keys, Android keys, plain names). The alias
table maps the common ones to stable fields; unknown tags are never dropped (they stay in
``ProductionMetadata.tags``). Consumer files rarely carry most of these, and absent fields stay
``None``: nothing is guessed.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from media_house.modules.media_inspection.domain.model import ProductionMetadata

#: field -> tag names (lower-case) in order of preference.
_ALIASES: Mapping[str, Sequence[str]] = {
    "camera_make": (
        "com.apple.quicktime.make",
        "com.android.manufacturer",
        "make",
        "manufacturer",
        "camera_manufacturer",
        "camera_make",
    ),
    "camera_model": (
        "com.apple.quicktime.model",
        "com.android.model",
        "model",
        "camera_model",
        "camera_model_name",
    ),
    "lens": ("lens", "lens_model", "lensmodel", "lens_name"),
    "focal_length": ("focal_length", "focallength"),
    "aperture": ("aperture", "f_number", "fnumber"),
    "shutter_speed": ("shutter_speed", "shutterspeed", "exposure_time"),
    "iso": ("iso", "iso_speed", "isospeed"),
    "white_balance": ("white_balance", "whitebalance", "wb"),
    "exposure": ("exposure", "exposure_mode", "exposure_bias"),
    "reel_id": ("reel_id", "reel", "reel_name", "tape_name"),
    "clip_id": ("clip_id", "clip_name", "clip"),
    "scene": ("scene",),
    "take": ("take",),
    "device": ("device", "device_name", "device_model"),
    "software": ("com.apple.quicktime.software", "software", "encoder"),
}
_CREATION_TIME = ("creation_time", "com.apple.quicktime.creationdate", "date", "encoded_date")


def production_from_tags(
    container_tags: Mapping[str, str],
    stream_tags: Sequence[Mapping[str, str]] = (),
) -> ProductionMetadata:
    """Production fields from the container's tags, completed by the streams' tags."""
    sources = [_lower(container_tags), *(_lower(tags) for tags in stream_tags)]

    def first(names: Sequence[str]) -> str | None:
        for source in sources:
            for name in names:
                value = source.get(name, "").strip()
                if value:
                    return value
        return None

    return ProductionMetadata(
        creation_time=_parse_time(first(_CREATION_TIME)),
        camera_make=first(_ALIASES["camera_make"]),
        camera_model=first(_ALIASES["camera_model"]),
        lens=first(_ALIASES["lens"]),
        focal_length=first(_ALIASES["focal_length"]),
        aperture=first(_ALIASES["aperture"]),
        shutter_speed=first(_ALIASES["shutter_speed"]),
        iso=first(_ALIASES["iso"]),
        white_balance=first(_ALIASES["white_balance"]),
        exposure=first(_ALIASES["exposure"]),
        reel_id=first(_ALIASES["reel_id"]),
        clip_id=first(_ALIASES["clip_id"]),
        scene=first(_ALIASES["scene"]),
        take=first(_ALIASES["take"]),
        device=first(_ALIASES["device"]),
        software=first(_ALIASES["software"]),
        tags=dict(container_tags),
    )


def _lower(tags: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in tags.items()}


def _parse_time(text: str | None) -> datetime | None:
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        return None
