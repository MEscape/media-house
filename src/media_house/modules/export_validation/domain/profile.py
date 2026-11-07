"""Delivery profiles: versioned data that says what a delivered file must satisfy.

A delivery profile does not repeat what the output IS (codec, container, frame size, audio format):
that is the render profile it names (``render_profile_id``), so the two cannot disagree. It adds
what only delivery cares about: loudness and peak limits, length and size limits, required
metadata, the naming rule, and the thresholds the measurements are judged by. Nothing about a
platform is assumed anywhere else; a platform that changes its limits is a new profile version.
"""

import re
from dataclasses import dataclass

from media_house.shared.errors import DomainError


class InvalidDeliveryProfile(DomainError):
    code = "export_validation.invalid_profile"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid delivery profile: {detail}",
            user_message=f"The delivery profile is not valid: {detail}",
        )


class UnknownDeliveryProfile(DomainError):
    code = "export_validation.unknown_profile"

    def __init__(self, delivery_id: str, known: list[str]) -> None:
        super().__init__(
            f"Unknown delivery profile {delivery_id!r}",
            user_message=(
                f"There is no delivery profile '{delivery_id}' (known: {', '.join(known)})."
            ),
        )


@dataclass(frozen=True, slots=True)
class DeliveryProfile:
    delivery_id: str
    version: int
    name: str
    #: The render manager profile (and the version) this delivery is made with.
    render_profile_id: str
    render_profile_version: int = 1
    #: Integrated loudness the file should have, and how far it may be off (``None``: not judged).
    loudness_target_lufs: float | None = None
    loudness_tolerance_lu: float = 1.0
    loudness_range_max_lu: float | None = None
    true_peak_max_dbtp: float | None = -1.0
    max_duration_seconds: float | None = None
    max_size_bytes: int | None = None
    #: Container tags that must be present (``title``, ``creation_time``...).
    required_metadata: tuple[str, ...] = ()
    #: A regular expression the delivered file name must match (``None``: any).
    name_pattern: str | None = None
    # --- how the measurements are judged ---------------------------------------------------------
    black_min_seconds: float = 0.25
    black_picture_threshold: float = 0.98
    black_pixel_threshold: float = 0.10
    freeze_min_seconds: float = 2.0
    freeze_noise_db: float = -60.0
    silence_min_seconds: float = 1.0
    silence_noise_db: float = -50.0
    #: A sampled picture this similar (0-1) to the same moment of the review render is the same.
    similarity_min: float = 0.80
    #: A sample point at the start and the end, and at most this many inside.
    max_section_samples: int = 10

    def __post_init__(self) -> None:
        if not re.match(r"^[a-z][a-z0-9_]{1,40}$", self.delivery_id):
            raise InvalidDeliveryProfile(f"'{self.delivery_id}' is not an id (a-z, 0-9, _)")
        if self.version < 1:
            raise InvalidDeliveryProfile("the version starts at 1")
        if self.loudness_tolerance_lu <= 0:
            raise InvalidDeliveryProfile("the loudness tolerance must be positive")
        if not 0.0 < self.similarity_min <= 1.0:
            raise InvalidDeliveryProfile("similarity is between 0 and 1")
        if self.max_section_samples < 1:
            raise InvalidDeliveryProfile("at least one section sample")
        if self.name_pattern is not None:
            try:
                re.compile(self.name_pattern)
            except re.error as exc:
                raise InvalidDeliveryProfile(
                    f"the name pattern is not a regular expression: {exc}"
                ) from exc


DELIVERY_PROFILES: tuple[DeliveryProfile, ...] = (
    DeliveryProfile(
        "youtube_16_9", 1, "YouTube 16:9", "youtube_16_9",
        loudness_target_lufs=-14.0, true_peak_max_dbtp=-1.0,
    ),
    DeliveryProfile(
        "youtube_shorts", 1, "YouTube Shorts", "shorts_9_16",
        loudness_target_lufs=-14.0, true_peak_max_dbtp=-1.0, max_duration_seconds=180.0,
    ),
    DeliveryProfile(
        "instagram_reels", 1, "Instagram Reels", "reels_9_16",
        loudness_target_lufs=-14.0, true_peak_max_dbtp=-1.0, max_duration_seconds=180.0,
    ),
    DeliveryProfile(
        "square", 1, "Square", "square_1_1",
        loudness_target_lufs=-14.0, true_peak_max_dbtp=-1.0,
    ),
    DeliveryProfile("master", 1, "Master", "master_hq", true_peak_max_dbtp=-1.0),
)  # fmt: skip


def get_delivery_profile(
    delivery_id: str, extra: tuple[DeliveryProfile, ...] = ()
) -> DeliveryProfile:
    found = next((p for p in (*DELIVERY_PROFILES, *extra) if p.delivery_id == delivery_id), None)
    if found is None:
        raise UnknownDeliveryProfile(
            delivery_id, [p.delivery_id for p in (*DELIVERY_PROFILES, *extra)]
        )
    return found
