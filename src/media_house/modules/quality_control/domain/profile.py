"""Thresholds of the checks: versioned data, chosen from what kind of video it is.

A threshold is never buried in a check. Re-tuning one is a new ``PROFILE_VERSION`` so an old report
says which thresholds it used. Chosen explicitly, else from the creator identity's format profile,
else ``generic``.
"""

from dataclasses import dataclass, replace

PROFILE_VERSION = 1


@dataclass(frozen=True, slots=True)
class QualityProfile:
    name: str
    # editing
    min_shot_seconds: float = 0.5
    max_shot_seconds: float = 25.0
    flash_frames: int = 3
    opening_max_seconds: float = 20.0
    #: Longest stretch of one shot with nothing else on top of it before it is called static.
    variation_window_seconds: float = 45.0
    #: Cuts inside one recording are the style of this kind of video (not a finding).
    jump_cuts_expected: bool = False
    jump_cut_gap_seconds: float = 1.0
    repeat_overlap_fraction: float = 0.5
    # captions
    max_chars_per_second: float = 21.0
    min_cue_seconds: float = 0.6
    max_cue_characters: int = 84
    # render
    black_min_seconds: float = 0.12
    black_picture_threshold: float = 0.98
    black_pixel_threshold: float = 0.10
    freeze_min_seconds: float = 0.8
    freeze_noise_db: float = -60.0
    silence_min_seconds: float = 0.75
    silence_noise_db: float = -50.0
    duration_tolerance_seconds: float = 0.15
    sync_tolerance_seconds: float = 0.1
    # pictures
    crushed_fraction_limit: float = 0.40
    clipped_fraction_limit: float = 0.10
    dark_luma_mean: float = 28.0
    bright_luma_mean: float = 225.0
    soft_focus_ratio: float = 0.2
    color_shift_limit: float = 0.22
    border_limit: float = 0.04
    # audio
    target_lufs: float = -14.0
    tolerance_lu: float = 2.0
    true_peak_ceiling_dbtp: float = -1.0
    loudness_range_limit_lu: float = 14.0
    clipping_dbfs: float = -0.1
    dialogue_spread_lu: float = 6.0
    abrupt_end_dbfs: float = -30.0
    # sampling
    frame_spacing_seconds: float = 6.0
    max_frames: int = 60
    max_dialogue_measurements: int = 12


PROFILES: dict[str, QualityProfile] = {
    "generic": QualityProfile("generic"),
    "talking_head": QualityProfile(
        "talking_head",
        jump_cuts_expected=True,
        max_shot_seconds=60.0,
        variation_window_seconds=60.0,
    ),
    "tutorial": QualityProfile("tutorial", max_shot_seconds=45.0, variation_window_seconds=60.0),
    "product_review": QualityProfile(
        "product_review", max_shot_seconds=18.0, jump_cuts_expected=True
    ),
    "short": QualityProfile(
        "short",
        min_shot_seconds=0.3,
        max_shot_seconds=10.0,
        opening_max_seconds=4.0,
        variation_window_seconds=12.0,
        jump_cuts_expected=True,
        max_chars_per_second=25.0,
        target_lufs=-14.0,
    ),
    "cinematic": QualityProfile(
        "cinematic", max_shot_seconds=40.0, variation_window_seconds=90.0, target_lufs=-16.0
    ),
}

_ALIASES = {
    "shorts": "short",
    "shorts_meme": "short",
    "shorts_reels": "short",
    "reels": "short",
    "talking-head": "talking_head",
    "product_demo": "product_review",
    "review": "product_review",
}


def profile_for(requested: str | None, format_profile: str) -> QualityProfile:
    """Explicit name, else the creator identity's format profile, else ``generic``."""
    for name in (requested, format_profile):
        if not name:
            continue
        key = _ALIASES.get(name, name)
        if key in PROFILES:
            return PROFILES[key]
        for known in PROFILES:
            if known in name:
                return PROFILES[known]
    return PROFILES["generic"]


def with_delivery(
    profile: QualityProfile,
    *,
    target_lufs: float | None,
    tolerance_lu: float | None,
    true_peak: float | None,
    range_limit: float | None,
) -> QualityProfile:
    """The delivery the mix was made for replaces the profile's loudness expectations."""
    return replace(
        profile,
        target_lufs=target_lufs if target_lufs is not None else profile.target_lufs,
        tolerance_lu=tolerance_lu if tolerance_lu is not None else profile.tolerance_lu,
        true_peak_ceiling_dbtp=true_peak
        if true_peak is not None
        else profile.true_peak_ceiling_dbtp,
        loudness_range_limit_lu=range_limit
        if range_limit is not None
        else profile.loudness_range_limit_lu,
    )
