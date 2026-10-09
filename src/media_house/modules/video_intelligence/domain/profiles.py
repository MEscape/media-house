"""Processing profiles: every threshold and sampling choice lives here, none in the algorithms.

Settings split by what a change costs:

* ``MeasurementSettings`` decide what is MEASURED from pixels. Changing one re-measures (the
  affected analyzers only; each analyzer's cache key contains just the settings it uses).
* ``ShotSettings``, ``MotionSettings``, ``QualitySettings``, ``TrackingSettings``,
  ``FaceSettings``, ``TextSettings``, ``MeaningSettings``, ``CinemaSettings`` and the content-type
  profiles decide how stored measurements are INTERPRETED. Changing one re-derives the result
  from the cached signals; nothing is decoded.

A profile is versioned: any change of a profile's content must come with a new ``version``.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields

from media_house.modules.video_intelligence.domain.errors import InvalidProfile
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    DeviceKind,
    JsonValue,
)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InvalidProfile(reason)


def _finite_positive(settings: object, *names: str) -> None:
    for name in names:
        value = getattr(settings, name)
        _require(math.isfinite(value) and value > 0, f"{name} must be positive")


def _config(settings: object) -> dict[str, JsonValue]:
    """Every field, so a new setting can never be missing from the processing identity."""
    return {f.name: getattr(settings, f.name) for f in fields(settings)}  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class MeasurementSettings:
    """What is decoded and measured. Widths are proxy resolutions (display orientation)."""

    #: Every frame is reduced to this width for the per-frame signals (cuts, flicker, handles).
    dense_width: int = 128
    #: Width of the sampled frames used for motion and picture quality.
    sample_width: int = 480
    #: Frames compared at a distance (dissolve detection).
    long_gap_frames: int = 12
    histogram_bins: int = 32
    #: Densest sampling rate; static stretches are sampled more sparsely (see below).
    candidate_fps: float = 6.0
    #: Longest stretch without a sampled frame.
    static_gap_seconds: float = 0.25
    #: Change (summed mean frame difference) since the last sampled frame that forces a new one.
    change_threshold: float = 0.05
    #: The camera-motion estimator compares ``grid`` x ``grid`` blocks of the frame.
    motion_grid: int = 3
    #: Luma level (0-1, full range) from which a pixel counts as clipped / crushed.
    clip_level: float = 0.98
    crush_level: float = 0.02
    #: Colour frames for the model-based analyzers: width, densest sampling rate, longest stretch
    #: without one and the change that forces a new one (like the grey samples above).
    rgb_width: int = 640
    rgb_fps: float = 3.0
    rgb_gap_seconds: float = 1.0
    rgb_change_threshold: float = 0.12
    #: Every n-th planned colour frame goes to the detector / face / body / OCR / embedding model.
    detect_every: int = 1
    face_every: int = 1
    body_every: int = 1
    text_every: int = 3
    embed_every: int = 1
    #: Every n-th planned colour frame is described by the vision-language model (keyframes only).
    description_every: int = 10
    #: Detector confidence from which a detection is kept, and the detector model (see adapters).
    detect_min_score: float = 0.4
    detector: str = "ssdlite320_mobilenet_v3_large"
    face_min_score: float = 0.5
    text_min_score: float = 0.5
    #: Persons whose box is at least this tall (fraction of the picture) get an appearance
    #: embedding.
    appearance_min_height: float = 0.15
    description_max_tokens: int = 60

    def __post_init__(self) -> None:
        _require(16 <= self.dense_width <= 512, "dense_width must be 16-512")
        _require(64 <= self.sample_width <= 1920, "sample_width must be 64-1920")
        _require(self.long_gap_frames >= 3, "long_gap_frames must be at least 3")
        _require(8 <= self.histogram_bins <= 256, "histogram_bins must be 8-256")
        _finite_positive(self, "candidate_fps", "static_gap_seconds", "change_threshold")
        _require(1 <= self.motion_grid <= 6, "motion_grid must be 1-6")
        _require(
            0.0 < self.crush_level < self.clip_level < 1.0,
            "crush_level must be below clip_level, both within (0, 1)",
        )
        _require(64 <= self.rgb_width <= 1920, "rgb_width must be 64-1920")
        _finite_positive(self, "rgb_fps", "rgb_gap_seconds", "rgb_change_threshold")
        for name in (
            "detect_every",
            "face_every",
            "body_every",
            "text_every",
            "embed_every",
            "description_every",
            "description_max_tokens",
        ):
            _require(getattr(self, name) >= 1, f"{name} must be at least 1")
        for name in (
            "detect_min_score",
            "face_min_score",
            "text_min_score",
            "appearance_min_height",
        ):
            _require(0.0 < getattr(self, name) < 1.0, f"{name} must be within (0, 1)")

    def config_for(self, analyzer: AnalyzerId) -> dict[str, JsonValue]:
        """The settings that decide ``analyzer``'s stored signals (and nothing else).

        What an analyzer takes from OTHER analyzers (the shot signals every plan is built from, the
        detections the face model is limited to) is part of its cache key as the fingerprint of that
        analyzer's document, not as settings here.
        """
        everything = _config(self)
        return {name: everything[name] for name in _MEASUREMENT_KEYS[analyzer]}


_DENSE_KEYS = ("dense_width", "long_gap_frames", "histogram_bins")
_GRAY_PLAN_KEYS = ("sample_width", "candidate_fps", "static_gap_seconds", "change_threshold")
_RGB_PLAN_KEYS = ("rgb_width", "rgb_fps", "rgb_gap_seconds", "rgb_change_threshold")
_MEASUREMENT_KEYS: dict[AnalyzerId, tuple[str, ...]] = {
    AnalyzerId.SHOTS: _DENSE_KEYS,
    AnalyzerId.MOTION: (*_GRAY_PLAN_KEYS, "motion_grid"),
    AnalyzerId.QUALITY: (*_GRAY_PLAN_KEYS, "clip_level", "crush_level"),
    AnalyzerId.SALIENCY: _GRAY_PLAN_KEYS,
    AnalyzerId.GEOMETRY: _RGB_PLAN_KEYS,
    AnalyzerId.ENTITIES: (*_RGB_PLAN_KEYS, "detect_every", "detect_min_score", "detector"),
    AnalyzerId.FACES: (*_RGB_PLAN_KEYS, "face_every", "face_min_score"),
    AnalyzerId.BODY: (*_RGB_PLAN_KEYS, "body_every"),
    AnalyzerId.TEXT: (*_RGB_PLAN_KEYS, "text_every", "text_min_score"),
    AnalyzerId.EMBEDDINGS: (*_RGB_PLAN_KEYS, "embed_every"),
    AnalyzerId.APPEARANCE: (*_RGB_PLAN_KEYS, "appearance_min_height"),
    AnalyzerId.DESCRIPTIONS: (*_RGB_PLAN_KEYS, "description_every", "description_max_tokens"),
}


@dataclass(frozen=True, slots=True)
class ShotSettings:
    """How per-frame differences become shot boundaries. Differences are fractions (0-1)."""

    #: A hard cut: the mean frame difference is at least this large ...
    cut_diff: float = 0.12
    #: ... and the luma-histogram distance at which a cut's confidence reaches its maximum (two
    #: different scenes can share a histogram, so it adds confidence but is not required).
    cut_hist: float = 0.2
    #: The difference must also stand out from its surroundings by this factor.
    context_radius: int = 8
    context_ratio: float = 2.0
    #: A one-frame outlier whose next frame resembles the previous one is a flash, not a cut.
    flash_return_ratio: float = 0.4
    #: Two candidates closer than this many frames are one event.
    suppress_frames: int = 2
    #: Black frame: mean luma and contrast below these.
    black_luma: float = 0.06
    black_std: float = 0.04
    #: A fade to or from black ramps over at least this many frames.
    fade_min_frames: int = 4
    #: Dissolve: long-gap difference at least ``dissolve_diff`` while no single step exceeds
    #: ``dissolve_step_max``, lasting no longer than ``dissolve_max_frames``.
    dissolve_diff: float = 0.12
    dissolve_step_max: float = 0.05
    dissolve_min_frames: int = 4
    dissolve_max_frames: int = 90
    #: The frames of a dissolve change this many times more per frame than the quiet frames next
    #: to it. Steady camera motion changes the picture just as much before and after, so it is
    #: not mistaken for a dissolve (a dissolve between two moving shots is therefore not found).
    dissolve_ratio: float = 2.5
    #: Frame difference below this counts as visually stable (handles).
    stable_diff: float = 0.02

    def __post_init__(self) -> None:
        _finite_positive(
            self,
            "cut_diff",
            "cut_hist",
            "context_ratio",
            "flash_return_ratio",
            "black_luma",
            "black_std",
            "dissolve_diff",
            "dissolve_step_max",
            "dissolve_ratio",
            "stable_diff",
        )
        _require(self.context_radius >= 1, "context_radius must be at least 1")
        _require(self.suppress_frames >= 1, "suppress_frames must be at least 1")
        _require(self.fade_min_frames >= 2, "fade_min_frames must be at least 2")
        _require(self.dissolve_min_frames >= 2, "dissolve_min_frames must be at least 2")
        _require(
            self.dissolve_max_frames >= self.dissolve_min_frames,
            "dissolve_max_frames must not be below dissolve_min_frames",
        )

    def to_config(self) -> dict[str, JsonValue]:
        return _config(self)


@dataclass(frozen=True, slots=True)
class MotionSettings:
    """How camera-motion estimates become a movement description.

    Speeds are fractions of the frame width (or height) per second; ``zoom_rate`` is the change
    of the natural logarithm of the picture scale per second.
    """

    #: Fewer valid pairs than this leave the movement unknown.
    min_pairs: int = 2
    #: Estimates below this confidence (0-1) are ignored.
    min_confidence: float = 0.15
    static_speed: float = 0.01
    pan_speed: float = 0.04
    zoom_rate: float = 0.04
    #: Rms deviation of the camera path from its smoothed course (fraction of frame width).
    shake_residual: float = 0.004
    smooth_samples: int = 5
    #: Share of pairs that must agree on a direction for a pan or tilt.
    consistency: float = 0.7
    #: Speed that maps to intensity 1.0.
    full_scale_speed: float = 0.5
    #: Picture residual (0-1) below which a pair counts as static content.
    static_residual: float = 0.01

    def __post_init__(self) -> None:
        _finite_positive(
            self,
            "min_confidence",
            "static_speed",
            "pan_speed",
            "zoom_rate",
            "shake_residual",
            "full_scale_speed",
            "static_residual",
        )
        _require(self.min_pairs >= 1, "min_pairs must be at least 1")
        _require(
            self.smooth_samples >= 3 and self.smooth_samples % 2 == 1,
            "smooth_samples must be odd and >= 3",
        )
        _require(0.5 < self.consistency <= 1.0, "consistency must be within (0.5, 1]")
        _require(self.static_speed < self.pan_speed, "static_speed must be below pan_speed")

    def to_config(self) -> dict[str, JsonValue]:
        return _config(self)


@dataclass(frozen=True, slots=True)
class QualitySettings:
    """Thresholds that turn raw picture metrics into descriptive reason codes."""

    #: Sharpness (gradient measure relative to tonal spread) below this reads as soft.
    low_sharpness: float = 0.15
    #: Luma noise sigma (0-1) above this reads as noisy.
    high_noise: float = 0.02
    #: Rms frame-to-frame luma flutter (0-1) above this reads as flicker.
    flicker: float = 0.01
    #: Share of clipped / crushed pixels above which the reason is reported.
    clipped_fraction: float = 0.02
    crushed_fraction: float = 0.3
    #: Tonal spread (p99 - p1) below this WITH blacks above ``flat_black_floor`` is flat or
    #: log-encoded footage: exposure is not judged, so flat footage is not called underexposed.
    #: (Underexposed footage has low spread too, but its blacks sit at zero.)
    flat_spread: float = 0.25
    flat_black_floor: float = 0.08
    dark_median: float = 0.15
    bright_median: float = 0.85
    #: Transfer characteristics whose code values are not display-referred luma.
    non_display_transfers: tuple[str, ...] = ("smpte2084", "arib-std-b67")
    #: Fewer sampled frames than this leave a shot's quality unknown.
    min_samples: int = 2

    def __post_init__(self) -> None:
        _finite_positive(
            self,
            "low_sharpness",
            "high_noise",
            "flicker",
            "clipped_fraction",
            "crushed_fraction",
            "flat_spread",
            "flat_black_floor",
            "dark_median",
            "bright_median",
        )
        _require(self.dark_median < self.bright_median, "dark_median must be below bright_median")
        _require(self.min_samples >= 1, "min_samples must be at least 1")

    def to_config(self) -> dict[str, JsonValue]:
        config = _config(self)
        config["non_display_transfers"] = list(self.non_display_transfers)
        return config


@dataclass(frozen=True, slots=True)
class TrackingSettings:
    """How detections in successive analysed frames become tracks."""

    #: A detection continues a track when their boxes overlap at least this much ...
    match_iou: float = 0.3
    #: ... or, for fast movers between sparse frames, when their centres are this close
    #: (fraction of the picture diagonal).
    center_gate: float = 0.12
    #: A track survives this long without a detection (occlusion, a missed frame).
    max_gap_seconds: float = 1.5
    #: Tracks with fewer detections are noise and are dropped.
    min_detections: int = 2
    #: Only detections at least this confident can START a track (ByteTrack style: weaker ones
    #: may still continue one).
    start_confidence: float = 0.5

    def __post_init__(self) -> None:
        for name in ("match_iou", "center_gate", "start_confidence"):
            _require(0.0 < getattr(self, name) < 1.0, f"{name} must be within (0, 1)")
        _finite_positive(self, "max_gap_seconds")
        _require(self.min_detections >= 1, "min_detections must be at least 1")

    def to_config(self) -> dict[str, JsonValue]:
        return _config(self)


@dataclass(frozen=True, slots=True)
class FaceSettings:
    """How face measurements become visible cues. Angles in degrees; the rest are 0-1 cues."""

    #: Eye contact with the camera: head turned no more than this and iris near the centre.
    eye_contact_yaw: float = 18.0
    eye_contact_pitch: float = 18.0
    gaze_offset: float = 0.35
    #: An eye counts as closed below this openness; a smile cue counts from this value.
    eyes_closed: float = 0.35
    smile: float = 0.5
    #: Mouth activity: window (seconds) and the spread of the jaw opening that reads as speaking.
    speaking_window_seconds: float = 2.0
    speaking_activity: float = 0.03
    #: A detector confidence below this is reported as a possible occlusion.
    occlusion_confidence: float = 0.65
    #: Faces smaller than this (fraction of picture height) are too small for the cues.
    min_face_height: float = 0.04
    #: Face crops with a sharpness below this are called soft.
    face_soft_sharpness: float = 0.1

    def __post_init__(self) -> None:
        _finite_positive(
            self,
            "eye_contact_yaw",
            "eye_contact_pitch",
            "gaze_offset",
            "speaking_window_seconds",
            "speaking_activity",
            "min_face_height",
            "face_soft_sharpness",
        )
        for name in ("eyes_closed", "smile", "occlusion_confidence"):
            _require(0.0 < getattr(self, name) < 1.0, f"{name} must be within (0, 1)")

    def to_config(self) -> dict[str, JsonValue]:
        return _config(self)


@dataclass(frozen=True, slots=True)
class TextSettings:
    """How OCR rows become tracked text, overlays and screen-content events."""

    min_confidence: float = 0.5
    #: Boxes of the same text overlap at least this much between frames.
    same_text_iou: float = 0.4
    #: A lower third sits below this height, is at most this tall and stays at most this long.
    lower_third_top: float = 0.6
    lower_third_max_height: float = 0.25
    lower_third_max_seconds: float = 12.0
    #: Captions: text inside the bottom band that changes while its place stays the same.
    caption_top: float = 0.68
    caption_min_changes: int = 2
    #: A watermark is text in the same place in at least this share of the analysed frames of
    #: the whole video (and in at least ``watermark_min_frames`` of them).
    watermark_share: float = 0.6
    watermark_min_frames: int = 4
    #: A seam: a straight line across this share of the picture, near the middle band, in this
    #: share of the analysed frames.
    seam_share: float = 0.7
    #: Text that looks like code has at least this share of programming symbols.
    code_symbol_ratio: float = 0.08
    #: Whole-picture change that, with different text, reads as the screen content changing.
    screen_change_diff: float = 0.08

    def __post_init__(self) -> None:
        for name in (
            "min_confidence",
            "same_text_iou",
            "lower_third_top",
            "lower_third_max_height",
            "caption_top",
            "watermark_share",
            "seam_share",
            "code_symbol_ratio",
            "screen_change_diff",
        ):
            _require(0.0 < getattr(self, name) < 1.0, f"{name} must be within (0, 1)")
        _finite_positive(self, "lower_third_max_seconds")
        _require(self.caption_min_changes >= 1, "caption_min_changes must be at least 1")
        _require(self.watermark_min_frames >= 2, "watermark_min_frames must be at least 2")

    def to_config(self) -> dict[str, JsonValue]:
        return _config(self)


@dataclass(frozen=True, slots=True)
class MeaningSettings:
    """How embeddings, saliency and tracks become scenes, relations and visual meaning."""

    #: Adjacent shots whose embeddings are at least this similar belong to one scene.
    scene_similarity: float = 0.82
    #: Shots at least this similar (and similarly framed) are retakes / near duplicates.
    retake_similarity: float = 0.9
    near_duplicate_similarity: float = 0.985
    #: Person tracks whose appearance embeddings are at least this similar get one identity cluster.
    identity_similarity: float = 0.88
    #: Retakes must also show similar framing: subject box size and position this close.
    retake_framing_tolerance: float = 0.15
    #: The best zero-shot label must beat the runner-up by this much, else it is only a guess.
    label_margin: float = 0.01
    #: Softmax temperature turning cosine similarity into label scores (CLIP uses about 100).
    label_temperature: float = 100.0
    #: A label is reported as a content type from this score; the indicator flags need this score.
    content_min_score: float = 0.35
    indicator_min_score: float = 0.6
    #: Saliency peaks must rise this far above the shot's median peak to be attention events.
    attention_peak_rise: float = 0.25
    #: Motion peaks must be this many times the shot's median residual energy.
    motion_peak_ratio: float = 3.0
    #: Frames per second one event may span: events closer than this many seconds are merged.
    event_merge_seconds: float = 0.5

    def __post_init__(self) -> None:
        for name in (
            "scene_similarity",
            "retake_similarity",
            "near_duplicate_similarity",
            "identity_similarity",
            "content_min_score",
            "indicator_min_score",
            "retake_framing_tolerance",
        ):
            _require(0.0 < getattr(self, name) <= 1.0, f"{name} must be within (0, 1]")
        _require(
            self.scene_similarity <= self.retake_similarity <= self.near_duplicate_similarity,
            "scene_similarity <= retake_similarity <= near_duplicate_similarity",
        )
        _finite_positive(
            self,
            "label_margin",
            "label_temperature",
            "attention_peak_rise",
            "motion_peak_ratio",
            "event_merge_seconds",
        )

    def to_config(self) -> dict[str, JsonValue]:
        return _config(self)


@dataclass(frozen=True, slots=True)
class CinemaSettings:
    """How subjects and picture geometry become framing, composition and lighting measurements."""

    #: Face height (fraction of the picture height) from which a shot is an extreme close-up,
    #: a close-up, or (down to ``medium_face``) a medium shot; with no face, the person's box.
    extreme_close_up_face: float = 0.5
    close_up_face: float = 0.28
    medium_face: float = 0.1
    medium_person: float = 0.5
    close_up_person: float = 0.95
    #: Headroom (gap above the subject, fraction of the picture height) that reads as balanced.
    headroom_low: float = 0.03
    headroom_high: float = 0.18
    #: Subject centre within this distance of a third line / of the centre counts as on it.
    thirds_tolerance: float = 0.08
    center_tolerance: float = 0.06
    #: Share of edge pixels from which the background reads as cluttered.
    clutter_edge_density: float = 0.14
    #: A subject box touching the frame within this margin is cut by the frame edge.
    edge_margin: float = 0.01
    #: Crop-safe windows for these display aspect ratios (width:height) and the margin kept
    #: around the subject (fraction of the window).
    crop_aspects: tuple[str, ...] = ("16:9", "9:16", "1:1")
    crop_margin: float = 0.05
    #: Backlight: the subject is this much darker than its surroundings (luma ratio); a shadow
    #: contrast above ``harsh_shadow`` is harsh.
    backlight_ratio: float = 0.6
    harsh_shadow: float = 0.55
    #: Tilt (degrees) below which the horizon counts as level, and the support it needs.
    level_tolerance: float = 1.0
    tilt_min_support: float = 0.2

    def __post_init__(self) -> None:
        _require(
            self.medium_face < self.close_up_face < self.extreme_close_up_face <= 1.0,
            "medium_face < close_up_face < extreme_close_up_face <= 1",
        )
        _require(
            self.medium_person < self.close_up_person <= 1.0, "medium_person < close_up_person <= 1"
        )
        _require(
            0.0 <= self.headroom_low < self.headroom_high < 1.0, "headroom_low < headroom_high"
        )
        _finite_positive(
            self,
            "thirds_tolerance",
            "center_tolerance",
            "clutter_edge_density",
            "edge_margin",
            "crop_margin",
            "backlight_ratio",
            "harsh_shadow",
            "level_tolerance",
            "tilt_min_support",
        )
        _require(bool(self.crop_aspects), "at least one crop aspect is needed")
        for aspect in self.crop_aspects:
            width, _, height = aspect.partition(":")
            _require(
                width.isdigit() and height.isdigit() and int(width) > 0 < int(height),
                f"bad aspect {aspect!r}",
            )

    def to_config(self) -> dict[str, JsonValue]:
        config = _config(self)
        config["crop_aspects"] = list(self.crop_aspects)
        return config


@dataclass(frozen=True, slots=True)
class ContentTypeProfile:
    """How scores are conditioned on what kind of footage this is. A data table, not code.

    A shaky run-and-gun vlog shot must not be penalised like a shaky interview shot: the
    tolerance, the prior that shake is deliberate and the weights live here. Adding a content
    type is adding an entry to ``CONTENT_PROFILES`` (and, to select it, a vocabulary mapping).
    """

    name: str
    version: int
    #: Camera shake (fraction of frame width, rms) at which the stability score is 0.5.
    shake_half: float
    #: How likely handheld shake is deliberate for this kind of footage (0-1).
    handheld_intentional: float
    #: Weights of (sharpness, noise, exposure) in the visual-quality score.
    quality_weights: tuple[float, float, float]
    #: A visible subject is part of what this footage is for (else subject visibility is n/a).
    subject_expected: bool
    #: Weights of (motion, attention, on-screen content) in the visual-interest score.
    interest_weights: tuple[float, float, float]

    def __post_init__(self) -> None:
        _require(bool(self.name) and self.version >= 1, "a content profile needs name and version")
        _finite_positive(self, "shake_half")
        _require(0.0 <= self.handheld_intentional <= 1.0, "handheld_intentional within [0, 1]")
        for weights in (self.quality_weights, self.interest_weights):
            _require(
                all(w >= 0 for w in weights) and sum(weights) > 0,
                "weights are three non-negative numbers with a positive sum",
            )

    def to_config(self) -> dict[str, JsonValue]:
        config = _config(self)
        config["quality_weights"] = list(self.quality_weights)
        config["interest_weights"] = list(self.interest_weights)
        return config


#: Version of the scoring rubrics. Never change what a score MEANS without bumping it: scores
#: of different rubric versions are not comparable.
RUBRIC_VERSION = 1
DEFAULT_CONTENT_PROFILE = "generic"
CONTENT_PROFILES: Mapping[str, ContentTypeProfile] = {
    c.name: c
    for c in (
        ContentTypeProfile("generic", 1, 0.005, 0.3, (0.4, 0.3, 0.3), False, (0.4, 0.3, 0.3)),
        ContentTypeProfile("talking_head", 1, 0.002, 0.1, (0.4, 0.3, 0.3), True, (0.2, 0.5, 0.3)),
        ContentTypeProfile("vlog", 1, 0.012, 0.6, (0.3, 0.3, 0.4), True, (0.5, 0.3, 0.2)),
        ContentTypeProfile(
            "tutorial_screen", 1, 0.001, 0.05, (0.6, 0.1, 0.3), False, (0.2, 0.2, 0.6)
        ),
    )
}


def content_profile(name: str) -> ContentTypeProfile:
    """The named content profile; an unknown name falls back to the generic one."""
    return CONTENT_PROFILES.get(name) or CONTENT_PROFILES[DEFAULT_CONTENT_PROFILE]


@dataclass(frozen=True, slots=True)
class ProcessingProfile:
    """One named, versioned way of analysing footage."""

    name: str
    version: int
    description: str
    analyzers: tuple[AnalyzerId, ...]
    measurement: MeasurementSettings
    shots: ShotSettings = ShotSettings()
    motion: MotionSettings = MotionSettings()
    quality: QualitySettings = QualitySettings()
    tracking: TrackingSettings = TrackingSettings()
    faces: FaceSettings = FaceSettings()
    text: TextSettings = TextSettings()
    meaning: MeaningSettings = MeaningSettings()
    cinema: CinemaSettings = CinemaSettings()

    def __post_init__(self) -> None:
        _require(bool(self.name) and self.version >= 1, "a profile needs a name and a version")
        _require(AnalyzerId.SHOTS in self.analyzers, "every profile includes the shots analyzer")
        _require(len(set(self.analyzers)) == len(self.analyzers), "analyzers must be unique")

    def interpretation_config(self) -> dict[str, JsonValue]:
        """Everything that decides how stored signals become the result."""
        return {
            "profile": self.name,
            "profile_version": self.version,
            "analyzers": [a.value for a in self.analyzers],
            "shots": self.shots.to_config(),
            "motion": self.motion.to_config(),
            "quality": self.quality.to_config(),
            "tracking": self.tracking.to_config(),
            "faces": self.faces.to_config(),
            "text": self.text.to_config(),
            "meaning": self.meaning.to_config(),
            "cinema": self.cinema.to_config(),
            "rubric_version": RUBRIC_VERSION,
            "content_profiles": {n: c.to_config() for n, c in CONTENT_PROFILES.items()},
        }


_TRIAGE_SAMPLING = MeasurementSettings(candidate_fps=6.0, static_gap_seconds=0.5)
_CLASSICAL = (AnalyzerId.SHOTS, AnalyzerId.QUALITY, AnalyzerId.MOTION, AnalyzerId.SALIENCY)
_STANDARD = (
    *_CLASSICAL,
    AnalyzerId.GEOMETRY,
    AnalyzerId.ENTITIES,
    AnalyzerId.FACES,
    AnalyzerId.TEXT,
    AnalyzerId.EMBEDDINGS,
)

PROFILES: Mapping[str, ProcessingProfile] = {
    p.name: p
    for p in (
        ProcessingProfile(
            name="triage",
            version=2,
            description="Cheapest: shots and picture quality, for assessing original footage.",
            analyzers=(AnalyzerId.SHOTS, AnalyzerId.QUALITY),
            measurement=_TRIAGE_SAMPLING,
        ),
        ProcessingProfile(
            name="fast",
            version=2,
            description="Shots, picture quality, camera motion and saliency; no models.",
            analyzers=_CLASSICAL,
            measurement=_TRIAGE_SAMPLING,
        ),
        ProcessingProfile(
            name="standard",
            version=2,
            description=(
                "Everything the classical analyzers measure plus detection, faces, on-screen "
                "text and embeddings. Analyzers whose model is not installed report "
                "not_available."
            ),
            analyzers=_STANDARD,
            measurement=MeasurementSettings(candidate_fps=12.0),
        ),
        ProcessingProfile(
            name="deep",
            version=2,
            description=(
                "Densest sampling at higher resolution, plus body and hands, anonymous identity "
                "clusters and keyframe descriptions by a vision-language model."
            ),
            analyzers=(
                *_STANDARD,
                AnalyzerId.BODY,
                AnalyzerId.APPEARANCE,
                AnalyzerId.DESCRIPTIONS,
            ),
            measurement=MeasurementSettings(
                candidate_fps=24.0, sample_width=640, rgb_width=960, rgb_fps=6.0
            ),
        ),
    )
}

DEFAULT_PROFILE = "standard"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """How a run executes. It never changes the result beyond numerical tolerance."""

    #: ``auto`` and ``cpu`` run on the CPU; ``gpu`` falls back to the CPU with a warning for as
    #: long as no installed engine can use one.
    device: DeviceKind = DeviceKind.AUTO
    #: A model that is not on disk may be downloaded (once, with a pinned checksum). Off by default:
    #: the analysis never needs a network, and an analyzer without its model is ``not_available``.
    allow_model_download: bool = False


def get_profile(name: str) -> ProcessingProfile:
    """The named profile, or ``InvalidProfile`` listing the valid names."""
    found = PROFILES.get(name)
    if found is None:
        raise InvalidProfile(f"unknown profile {name!r}; choose one of {', '.join(PROFILES)}")
    return found
