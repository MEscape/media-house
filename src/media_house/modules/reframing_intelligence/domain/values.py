"""Vocabulary, versions and the meaning of every score of reframing intelligence.

Coordinates and time (stated once, used everywhere):

* A position is a fraction (0-1) of the source picture in DISPLAY orientation (rotation applied,
  pixel shape corrected), x to the right, y downwards, origin top-left. A crop is the window of
  that picture that becomes the output: ``Crop(x, y, w, h)`` with its top-left corner as anchor.
  The window is scaled uniformly to the output resolution; nothing is ever stretched.
* A time is exact seconds on the recording's common clock (the video analysis' clock plus the
  alignment's video correction, 0 when none exists). Source times and timeline places are never
  mixed: this module knows only source times, and the editing stage places them.

Scores are in [0, 1] or ``None`` (could not be stated, which is NOT the same as fine). Each answers
one question and none is a calibrated probability:

=============================  ==========  =================================================
score                          basis       question (higher = better unless stated)
=============================  ==========  =================================================
``subject_visibility_score``   measured    smallest share, over the sampled moments, of a
                                           required element's area that lies inside the crop
``composition_score``          heuristic   how close the subject lands to the profile's
                                           placement (anchor, headroom). A preference, not proof
``crop_feasibility_score``     measured    0 when the crop is geometrically invalid, else the
                                           lower of visibility and (1 - clipping risk / 2)
``effective_resolution_score`` estimated   1 when the crop needs no upscaling, 0 at the
                                           profile's largest tolerated upscale (the enlargement
                                           is measured; what it costs in quality is a policy)
``tracking_confidence``        observed    mean confidence of the track points the crop rests on
                                           (the upstream measurement strength)
``edge_clipping_risk``         measured    LOWER is better: the largest shortfall of a required
                                           element's margin to a crop edge (1 = cut by the edge)
``multi_subject_visibility``   measured    share of required elements inside the crop at every
                                           sampled moment; ``None`` with fewer than two
``caption_safe_area_score``    measured    1 minus the largest share of a required element that
                                           lies in a reserved area; ``None`` without safe areas
``continuity_compatibility``   heuristic   how well the crop sits with the neighbouring shot's
                                           (subject size and place); ``None`` without a neighbour
``motion_smoothness_score``    measured    1 for a still crop; falls with crop speed against the
                                           limit and with the number of framing changes
``reframing_need_score``       heuristic   HIGHER = more need: how much the original picture
                                           fails the target (aspect mismatch, clipped subject)
=============================  ==========  =================================================

``basis`` says what kind of statement a score is: ``observed`` (an upstream measurement passed
on), ``measured`` (geometry computed here from observed boxes), ``estimated`` (a modelled
quality), ``heuristic`` (a design preference). The recommendation's ``confidence`` is separate: it
is how well the evidence covers the shot, an ordinal heuristic, never a probability.
"""

from enum import StrEnum
from typing import Final

ANALYZER_VERSION: Final = 1
SCHEMA_VERSION: Final = 1
VOCABULARY_VERSION: Final = 1
PROFILE_VERSION: Final = 1
DOCUMENT_TYPE: Final = "reframing_analysis"
RESULT_OPERATION: Final = "reframing_analysis"

#: Largest relative difference between a crop's aspect (in display pixels) and the target's. The
#: output is an integer pixel grid, so a crop of a 1080p frame is exact only to about half a pixel.
ASPECT_TOLERANCE: Final = 0.002
#: Slack when checking that a number lies in [0, 1] (stored values are rounded to 6 decimals).
EPSILON: Final = 1e-6


class Strategy(StrEnum):
    ORIGINAL = "original_framing"
    STATIC_CROP = "static_crop"
    REPOSITIONED_CROP = "static_crop_repositioned"
    DYNAMIC_CROP = "dynamic_crop"
    PUSH_IN = "push_in"
    PULL_OUT = "pull_out"
    WIDER_SOURCE = "wider_source"
    ALTERNATIVE_RANGE = "alternative_range"
    FILL_TREATMENT = "fill_treatment"
    ALTERNATING_CROPS = "alternating_crops"
    UNREFRAMEABLE = "unreframeable"

    @property
    def has_crop(self) -> bool:
        """Whether the strategy is carried by a crop window of this recording."""
        return self in (
            Strategy.ORIGINAL,
            Strategy.STATIC_CROP,
            Strategy.REPOSITIONED_CROP,
            Strategy.DYNAMIC_CROP,
            Strategy.PUSH_IN,
            Strategy.PULL_OUT,
        )


class CameraMotion(StrEnum):
    NONE = "none"
    FOLLOW = "follow"
    PUSH_IN = "push_in"
    PULL_OUT = "pull_out"
    MIXED = "mixed"


class Feasibility(StrEnum):
    #: The crop is valid, keeps every required element and meets the quality policy.
    FEASIBLE = "feasible"
    #: Valid and quality is acceptable, but a stated trade-off remains (clipped margin, off-target).
    TRADEOFFS = "feasible_with_tradeoffs"
    #: Geometrically valid, but the picture would be upscaled or soft beyond the policy.
    LOW_QUALITY = "low_quality"
    #: No crop of the target shape keeps what is required.
    IMPOSSIBLE = "impossible"
    #: Evidence is missing, so the crop is a placeholder that must not be applied unseen.
    UNCERTAIN = "uncertain"


class Importance(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    CONTEXTUAL = "contextual"


class Interpolation(StrEnum):
    #: The crop of this keyframe stays until the next keyframe, then jumps.
    HOLD = "hold"
    #: x, y, w and h move linearly in time to the next keyframe's (the aspect is preserved).
    LINEAR = "linear"


class IntervalState(StrEnum):
    TRACKED = "tracked"
    GAP = "gap"
    LOW_CONFIDENCE = "low_confidence"


class ScoreName(StrEnum):
    SUBJECT_VISIBILITY = "subject_visibility_score"
    COMPOSITION = "composition_score"
    CROP_FEASIBILITY = "crop_feasibility_score"
    EFFECTIVE_RESOLUTION = "effective_resolution_score"
    TRACKING_CONFIDENCE = "tracking_confidence"
    EDGE_CLIPPING_RISK = "edge_clipping_risk"
    MULTI_SUBJECT_VISIBILITY = "multi_subject_visibility_score"
    CAPTION_SAFE_AREA = "caption_safe_area_score"
    CONTINUITY_COMPATIBILITY = "continuity_compatibility_score"
    MOTION_SMOOTHNESS = "motion_smoothness_score"
    REFRAMING_NEED = "reframing_need_score"


class ScoreBasis(StrEnum):
    OBSERVED = "observed"
    MEASURED = "measured"
    ESTIMATED = "estimated"
    HEURISTIC = "heuristic"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    UNRESOLVED = "unresolved"
    ERROR = "error"


class IssueKind(StrEnum):
    ELEMENT_CLIPPED = "element_clipped"
    EDGE_PROXIMITY = "edge_proximity"
    SOURCE_EDGE = "source_edge"
    TRACKING_GAP = "tracking_gap"
    LOW_TRACKING_CONFIDENCE = "low_tracking_confidence"
    LOW_QUALITY = "low_quality"
    UPSCALE = "upscale"
    SPEED_LIMIT = "speed_limit"
    ELEMENT_OMITTED = "element_omitted"
    NOT_TRACKED = "not_tracked"
    SAFE_AREA = "safe_area"
    ADJACENT_CROP = "adjacent_crop"
    CONTINUITY_RISK = "continuity_risk"
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_GEOMETRY = "invalid_geometry"
    SOURCE_GEOMETRY = "source_geometry"
    UNREFRAMEABLE = "unreframeable"


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
