"""Scores, rubric v1: a judgment of a property of the footage, derived from stored raw metrics.

Each score has a value in [0, 1], reason codes, a confidence and the rubric version, and is
CONDITIONED on the content type through ``ContentTypeProfile`` (a data table): a shaky vlog shot is
not penalised like a shaky interview. Scores describe how well a property holds, never what to
do. ``technical_usability`` is technical fitness only ("can this footage technically be used"),
not "should it be used".

NEVER change what a score means without bumping ``profiles.RUBRIC_VERSION``: scores of different
rubric versions are not comparable, and later style learning depends on stable measurements.

Rubric v1, in one place:

* stability            1 / (1 + (shake / shake_half)^2); shake is the camera path's rms deviation.
* visual_quality       weighted mean of sharpness (sharp / 2*soft limit, capped), noise
                       (1 / (1 + (sigma / noise limit)^2)) and exposure (1 - clipping and
                       crushing penalties); exposure is left out on flat / HDR footage.
* subject_visibility   share of analysed frames with the main subject x its size (to 25 % of the
                       picture height) x edge-cut and face-visibility factors.
* composition          mean of thirds/centre alignment, headroom in range and background calm.
* framing              half subject-size consistency, half "not cut by the frame edge".
* visual_interest      weighted mean of motion energy, attention strength and on-screen content.
* technical_usability  0.5 visual_quality + 0.3 stability + 0.2 min(sharp, exposure), scaled down
                       when a covered lens / black picture / slate indicator is present.
"""

import math
from collections.abc import Sequence

from media_house.modules.video_intelligence.domain.observations import (
    Assessed,
    CameraObservation,
    CompositionObservation,
    Evidence,
    FaceObservation,
    FramingObservation,
    IndicatorObservation,
    MotionObservation,
    QualityAssessment,
    Score,
    TextObservation,
    VisualMeaningObservation,
)
from media_house.modules.video_intelligence.domain.profiles import (
    RUBRIC_VERSION,
    CinemaSettings,
    ContentTypeProfile,
    QualitySettings,
)
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerState,
    CameraMovement,
    ScoreName,
)

_SMOOTH_MOVES = frozenset(
    {CameraMovement.PAN, CameraMovement.TILT, CameraMovement.PUSH_IN, CameraMovement.PULL_OUT}
)
_SUBJECT_FULL_HEIGHT = 0.25
_MOTION_FULL_ENERGY = 0.05
_CONTENT_FULL_ITEMS = 5
_USABILITY_PENALTY = 0.2
_BLUR_SEPARATION_LIKELY = 0.6


class ShotMeasures:
    """Everything the scores of one shot are derived from (its observations)."""

    def __init__(
        self,
        *,
        camera: CameraObservation,
        motion: MotionObservation,
        quality: QualityAssessment,
        faces: FaceObservation,
        framing: FramingObservation,
        composition: CompositionObservation,
        meaning: VisualMeaningObservation,
        text: TextObservation,
        indicators: IndicatorObservation,
        subject_frames: int,
        analysed_frames: int,
        subject_height_variation: float | None,
    ) -> None:
        self.camera, self.motion, self.quality = camera, motion, quality
        self.faces, self.framing, self.composition = faces, framing, composition
        self.meaning, self.text, self.indicators = meaning, text, indicators
        self.subject_frames, self.analysed_frames = subject_frames, analysed_frames
        self.subject_height_variation = subject_height_variation


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _score(
    name: ScoreName,
    profile: ContentTypeProfile,
    value: float,
    confidence: float,
    evidence: Sequence[Evidence],
    reasons: Sequence[str],
    intentional: float | None = None,
) -> Score:
    return Score(
        state=AnalyzerState.OK,
        confidence=_unit(confidence),
        evidence=tuple(evidence),
        reasons=tuple(reasons),
        name=name,
        value=_unit(value),
        rubric_version=RUBRIC_VERSION,
        content_profile=profile.name,
        intentional_likelihood=intentional,
    )


def _missing(
    name: ScoreName, profile: ContentTypeProfile, state: AnalyzerState, reason: str
) -> Score:
    return Score(
        state=state,
        reasons=(reason,),
        name=name,
        rubric_version=RUBRIC_VERSION,
        content_profile=profile.name,
    )


def _from(source: Assessed, reason: str) -> str:
    return source.reasons[0] if source.reasons else reason


def stability(m: ShotMeasures, profile: ContentTypeProfile) -> Score:
    camera = m.camera
    if not camera.ok or camera.shake_residual is None or camera.confidence is None:
        return _missing(
            ScoreName.STABILITY, profile, camera.state, _from(camera, "camera_not_measured")
        )
    shake = camera.shake_residual
    value = 1.0 / (1.0 + (shake / profile.shake_half) ** 2)
    reasons = [
        r for r in camera.reasons if r in {"handheld_shake", "measured_on_stabilized_footage"}
    ]
    intentional: float | None = None
    if camera.movement in _SMOOTH_MOVES and shake < profile.shake_half:
        intentional = 0.9
    elif "handheld_shake" in reasons or camera.movement is CameraMovement.HANDHELD:
        intentional = profile.handheld_intentional
    return _score(
        ScoreName.STABILITY,
        profile,
        value,
        camera.confidence,
        camera.evidence,
        reasons,
        intentional,
    )


def visual_quality(
    m: ShotMeasures, profile: ContentTypeProfile, settings: QualitySettings
) -> Score:
    quality = m.quality
    metrics = quality.metrics
    if (
        not quality.ok
        or metrics.sharpness is None
        or metrics.noise_sigma is None
        or quality.confidence is None
    ):
        return _missing(
            ScoreName.VISUAL_QUALITY, profile, quality.state, _from(quality, "quality_not_measured")
        )
    sharp = _unit(metrics.sharpness / (2 * settings.low_sharpness))
    noise = 1.0 / (1.0 + (metrics.noise_sigma / settings.high_noise) ** 2)
    judged = not {"flat_or_log_footage", "exposure_not_judged_non_display_transfer"} & set(
        quality.reasons
    )
    exposure: float | None = None
    if judged and metrics.clipped is not None and metrics.crushed is not None:
        penalty = 8 * metrics.clipped + 1.5 * metrics.crushed
        penalty += 0.3 if {"dark_exposure", "bright_exposure"} & set(quality.reasons) else 0.0
        exposure = _unit(1.0 - penalty)
    parts = [(sharp, profile.quality_weights[0]), (noise, profile.quality_weights[1])]
    if exposure is not None:
        parts.append((exposure, profile.quality_weights[2]))
    weight = sum(w for _, w in parts)
    value = sum(v * w for v, w in parts) / weight
    intentional: float | None = None
    if "soft_focus" in quality.reasons:
        separation = m.composition.subject_separation
        intentional = separation if separation and separation >= _BLUR_SEPARATION_LIKELY else 0.2
    elif "dark_exposure" in quality.reasons:
        intentional = 0.3
    return _score(
        ScoreName.VISUAL_QUALITY,
        profile,
        value,
        quality.confidence,
        quality.evidence,
        quality.reasons,
        intentional,
    )


def subject_visibility(m: ShotMeasures, profile: ContentTypeProfile) -> Score:
    if not profile.subject_expected:
        return _missing(
            ScoreName.SUBJECT_VISIBILITY,
            profile,
            AnalyzerState.NOT_APPLICABLE,
            f"subject_not_expected_for_{profile.name}",
        )
    framing = m.framing
    if (
        m.analysed_frames == 0
        or not framing.ok
        or framing.subject_height is None
        or framing.confidence is None
    ):
        return _missing(
            ScoreName.SUBJECT_VISIBILITY,
            profile,
            framing.state,
            _from(framing, "no_subject_measured"),
        )
    coverage = _unit(m.subject_frames / m.analysed_frames)
    size = _unit(framing.subject_height / _SUBJECT_FULL_HEIGHT)
    cut = 0.7 if framing.cut_by_frame_edge else 1.0
    face = 1.0
    reasons: list[str] = []
    if m.faces.ok and m.analysed_frames:
        face = 0.7 + 0.3 * _unit(m.faces.face_frames / m.analysed_frames)
        if m.faces.reasons and "no_usable_face_found" in m.faces.reasons:
            reasons.append("no_usable_face_found")
    if coverage < 0.5:
        reasons.append("subject_present_in_less_than_half_of_the_frames")
    if size < 1.0:
        reasons.append("small_in_frame")
    if framing.cut_by_frame_edge:
        reasons.append("cut_by_frame_edge")
    return _score(
        ScoreName.SUBJECT_VISIBILITY,
        profile,
        coverage * size * cut * face,
        framing.confidence,
        framing.evidence,
        reasons,
    )


def composition_score(
    m: ShotMeasures, profile: ContentTypeProfile, settings: CinemaSettings
) -> Score:
    c = m.composition
    if not c.ok or c.thirds_offset is None or c.confidence is None:
        return _missing(ScoreName.COMPOSITION, profile, c.state, _from(c, "no_subject_measured"))
    reasons: list[str] = []
    parts = [
        1.0 if c.centered else _unit(1.0 - c.thirds_offset / (2.5 * settings.thirds_tolerance))
    ]
    if c.headroom is not None:
        low, high = settings.headroom_low, settings.headroom_high
        gap = (
            0.0 if low <= c.headroom <= high else min(abs(c.headroom - low), abs(c.headroom - high))
        )
        parts.append(_unit(1.0 - gap / 0.2))
        if c.headroom < low:
            reasons.append("tight_headroom")
        elif c.headroom > high:
            reasons.append("loose_headroom")
    if c.background_clutter is not None:
        parts.append(_unit(1.0 - c.background_clutter / (2 * settings.clutter_edge_density)))
        if c.background_clutter >= settings.clutter_edge_density:
            reasons.append("busy_background")
    return _score(
        ScoreName.COMPOSITION, profile, sum(parts) / len(parts), c.confidence, c.evidence, reasons
    )


def framing_score(m: ShotMeasures, profile: ContentTypeProfile) -> Score:
    f = m.framing
    if not f.ok or f.confidence is None:
        return _missing(ScoreName.FRAMING, profile, f.state, _from(f, "no_subject_measured"))
    variation = m.subject_height_variation
    consistency = 1.0 if variation is None else _unit(1.0 - 3.0 * variation)
    value = 0.5 * consistency + 0.5 * (0.0 if f.cut_by_frame_edge else 1.0)
    reasons = []
    if f.cut_by_frame_edge:
        reasons.append("cut_by_frame_edge")
    if consistency < 0.7:
        reasons.append("framing_changes_during_the_shot")
    return _score(ScoreName.FRAMING, profile, value, f.confidence, f.evidence, reasons)


def visual_interest(m: ShotMeasures, profile: ContentTypeProfile) -> Score:
    parts: list[tuple[float, float]] = []
    evidence: list[Evidence] = []
    confidences: list[float] = []
    if m.motion.ok and m.motion.mean_energy is not None and m.motion.confidence is not None:
        parts.append(
            (_unit(m.motion.mean_energy / _MOTION_FULL_ENERGY), profile.interest_weights[0])
        )
        evidence.extend(m.motion.evidence)
        confidences.append(m.motion.confidence)
    attention = m.meaning.main_attention
    if m.meaning.ok and attention is not None and m.meaning.confidence is not None:
        parts.append((_unit(attention.strength), profile.interest_weights[1]))
        evidence.extend(m.meaning.evidence)
        confidences.append(m.meaning.confidence)
    if m.text.ok and m.text.confidence is not None:
        items = len(m.text.items) + (len(m.text.items) and 0)
        parts.append((_unit(items / _CONTENT_FULL_ITEMS), profile.interest_weights[2]))
        evidence.extend(m.text.evidence)
        confidences.append(m.text.confidence)
    weight = sum(w for _, w in parts)
    if not parts or weight <= 0:
        return _missing(
            ScoreName.VISUAL_INTEREST, profile, AnalyzerState.NOT_ANALYZED, "no_interest_evidence"
        )
    value = sum(v * w for v, w in parts) / weight
    return _score(
        ScoreName.VISUAL_INTEREST, profile, value, sum(confidences) / len(confidences), evidence, ()
    )


def technical_usability(
    scores: dict[ScoreName, Score], m: ShotMeasures, profile: ContentTypeProfile
) -> Score:
    quality, steady = scores[ScoreName.VISUAL_QUALITY], scores[ScoreName.STABILITY]
    if quality.value is None or steady.value is None or quality.confidence is None:
        state = quality.state if quality.value is None else steady.state
        return _missing(
            ScoreName.TECHNICAL_USABILITY, profile, state, "quality_or_stability_not_measured"
        )
    metrics = m.quality.metrics
    sharp = _unit((metrics.sharpness or 0.0) / 0.3)
    value = 0.5 * quality.value + 0.3 * steady.value + 0.2 * min(sharp, quality.value)
    reasons = list(dict.fromkeys((*quality.reasons, *steady.reasons)))
    if m.indicators.flags:
        value *= _USABILITY_PENALTY
        reasons.extend(f"possible_{flag}" for flag in m.indicators.flags)
    confidence = min(quality.confidence, steady.confidence or quality.confidence)
    return _score(
        ScoreName.TECHNICAL_USABILITY,
        profile,
        value,
        confidence,
        [*quality.evidence, *steady.evidence],
        reasons,
        quality.intentional_likelihood,
    )


def score_shot(
    m: ShotMeasures,
    profile: ContentTypeProfile,
    quality_settings: QualitySettings,
    cinema: CinemaSettings,
) -> tuple[Score, ...]:
    scores = {
        ScoreName.STABILITY: stability(m, profile),
        ScoreName.VISUAL_QUALITY: visual_quality(m, profile, quality_settings),
        ScoreName.SUBJECT_VISIBILITY: subject_visibility(m, profile),
        ScoreName.COMPOSITION: composition_score(m, profile, cinema),
        ScoreName.FRAMING: framing_score(m, profile),
        ScoreName.VISUAL_INTEREST: visual_interest(m, profile),
    }
    scores[ScoreName.TECHNICAL_USABILITY] = technical_usability(scores, m, profile)
    return tuple(scores[name] for name in ScoreName)


def variation(values: Sequence[float]) -> float | None:
    """Coefficient of variation (std / mean), or ``None`` for fewer than two values."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) / mean
