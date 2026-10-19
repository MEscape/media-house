"""Versioned profiles: every threshold of every stage is a field, none is hidden in an algorithm.

A profile is part of the result's identity, so re-tuning one value recomputes from the stored
upstream analyses without touching any upstream module.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields

from media_house.modules.audio_visual_alignment.domain.errors import InvalidAlignmentConfig

STAGES = ("speech", "text", "sound", "boundaries", "opportunities")


@dataclass(frozen=True, slots=True)
class AlignmentProfile:
    name: str = "standard"
    version: int = 1
    #: Stages beyond the always-on clock model. Order does not matter.
    stages: tuple[str, ...] = STAGES

    # --- clock ----------------------------------------------------------------------------
    #: A metadata-derived video origin correction smaller than this is rounding, not an offset.
    clock_tolerance_seconds: float = 0.002
    #: Audio and video lengths further apart than this are reported (never an error by itself).
    duration_tolerance_seconds: float = 1.0
    #: Coarse speech-versus-mouth lag probe (the mouth signal is a few samples per second).
    probe_window_seconds: float = 1.0
    probe_hop_seconds: float = 0.5
    probe_max_lag_seconds: float = 3.0
    probe_lag_step_seconds: float = 0.5
    #: Seconds of speech-and-mouth evidence the probe needs before it may name a lag.
    probe_min_support_seconds: float = 30.0
    probe_min_correlation: float = 0.3
    #: A runner-up lag within this share of the best peak makes the probe ambiguous.
    probe_ambiguity_ratio: float = 0.9
    #: A peak at least this far from zero lag is reported as an indicated offset.
    probe_offset_seconds: float = 1.0

    # --- speech visibility ------------------------------------------------------------------
    #: Speech shorter than this inside one shot is not judged.
    min_speech_seconds: float = 0.6
    min_mouth_samples: int = 4
    #: Spread (standard deviation) of the jaw opening that reads as a moving mouth / a still one.
    speaking_spread: float = 0.03
    quiet_spread: float = 0.012

    # --- speaker to person ------------------------------------------------------------------
    link_min_support_seconds: float = 8.0
    link_min_shots: int = 2
    #: One shot alone is enough when it supports the link for at least this long.
    link_solo_support_seconds: float = 30.0
    link_min_share: float = 0.7
    #: A second identity with at least this share of the support makes the link a conflict.
    link_conflict_share: float = 0.3

    # --- speech to picture ------------------------------------------------------------------
    #: Shots this close before/after a passage are considered (an illustration may lead or trail).
    text_lead_seconds: float = 2.0
    text_lag_seconds: float = 2.0
    min_term_length: int = 4
    max_candidates: int = 3
    #: Embedding similarity must stand this many standard deviations above the other shots.
    semantic_min_z: float = 1.5
    semantic_min_shots: int = 5

    # --- sound to action --------------------------------------------------------------------
    audio_event_kinds: tuple[str, ...] = ("energy_spike",)
    video_event_kinds: tuple[str, ...] = ("motion_peak", "gesture", "track_entered")
    event_tolerance_seconds: float = 0.35
    min_event_strength: float = 0.5

    # --- opportunities ----------------------------------------------------------------------
    window_min_seconds: float = 1.5
    window_boundary_margin_seconds: float = 0.25

    def __post_init__(self) -> None:
        unknown = set(self.stages) - set(STAGES)
        if unknown:
            raise InvalidAlignmentConfig(f"unknown stages {sorted(unknown)}")
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, float) and (not math.isfinite(value) or value < 0):
                raise InvalidAlignmentConfig(f"{f.name} must be a finite, non-negative number")
        if self.probe_hop_seconds <= 0 or self.probe_window_seconds <= 0:
            raise InvalidAlignmentConfig("probe windows must have a positive length")
        if self.probe_lag_step_seconds <= 0:
            raise InvalidAlignmentConfig("the probe lag step must be positive")
        if self.quiet_spread >= self.speaking_spread:
            raise InvalidAlignmentConfig("quiet_spread must be below speaking_spread")
        if not 0 < self.link_conflict_share < self.link_min_share <= 1:
            raise InvalidAlignmentConfig("need 0 < link_conflict_share < link_min_share <= 1")

    def runs(self, stage: str) -> bool:
        return stage in self.stages

    def fingerprint(self) -> Mapping[str, object]:
        """Every field, in a form the Media Library hashes (the stage list is sorted)."""
        out: dict[str, object] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, tuple):
                value = sorted(value) if f.name == "stages" else list(value)
            out[f.name] = value
        return out


#: ``fast`` answers "where do the clocks stand and who is on screen"; ``standard`` relates the rest.
PROFILES: Mapping[str, AlignmentProfile] = {
    "fast": AlignmentProfile(name="fast", stages=("speech",)),
    "standard": AlignmentProfile(name="standard"),
}
DEFAULT_PROFILE = "standard"


def get_profile(name: str) -> AlignmentProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise InvalidAlignmentConfig(
            f"unknown profile {name!r}; choose one of {sorted(PROFILES)}"
        ) from None
