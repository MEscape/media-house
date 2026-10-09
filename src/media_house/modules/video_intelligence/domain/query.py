"""Ask a finished analysis for shots: filter on what was measured, rank by what was measured.

A small query over the typed result, not a search engine. A filter condition on a value that was
not measured (state other than ``ok``) EXCLUDES the shot: an unknown value never matches.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from media_house.modules.video_intelligence.domain.result import Shot, VideoAnalysis
from media_house.modules.video_intelligence.domain.values import CameraMovement


class RankBy(StrEnum):
    """What orders the matches. Always best-first for the named property."""

    SHARPNESS = "sharpness"
    STEADINESS = "steadiness"  # least camera shake first
    DURATION = "duration"  # longest first
    CHRONOLOGICAL = "chronological"


@dataclass(frozen=True, slots=True)
class ShotFilter:
    """Every given condition must hold."""

    min_seconds: float | None = None
    max_seconds: float | None = None
    camera_movements: frozenset[CameraMovement] | None = None
    max_shake_residual: float | None = None
    min_sharpness: float | None = None
    #: Quality reason codes the shot must have / must not have.
    with_reasons: frozenset[str] = frozenset()
    without_reasons: frozenset[str] = frozenset()


def _sharpness(shot: Shot) -> float | None:
    return shot.quality.metrics.sharpness if shot.quality.ok else None


def _shake(shot: Shot) -> float | None:
    return shot.camera.shake_residual if shot.camera.ok else None


def _steadiness_key(shot: Shot) -> float:
    shake = _shake(shot)
    return float("inf") if shake is None else shake


def _matches(shot: Shot, f: ShotFilter) -> bool:
    if f.min_seconds is not None and shot.seconds < f.min_seconds:
        return False
    if f.max_seconds is not None and shot.seconds > f.max_seconds:
        return False
    if f.camera_movements is not None and (
        not shot.camera.ok or shot.camera.movement not in f.camera_movements
    ):
        return False
    if f.max_shake_residual is not None and (
        (shake := _shake(shot)) is None or shake > f.max_shake_residual
    ):
        return False
    if f.min_sharpness is not None and (
        (sharpness := _sharpness(shot)) is None or sharpness < f.min_sharpness
    ):
        return False
    reasons = set(shot.quality.reasons) if shot.quality.ok else set()
    return f.with_reasons <= reasons and not (f.without_reasons & reasons)


_KEYS: dict[RankBy, Callable[[Shot], float]] = {
    RankBy.SHARPNESS: lambda s: -(_sharpness(s) or 0.0),
    RankBy.STEADINESS: _steadiness_key,
    RankBy.DURATION: lambda s: -s.seconds,
    RankBy.CHRONOLOGICAL: lambda s: float(s.index),
}


def find_shots(
    analysis: VideoAnalysis,
    condition: ShotFilter | None = None,
    rank_by: RankBy = RankBy.CHRONOLOGICAL,
) -> tuple[Shot, ...]:
    """The matching shots, best first for ``rank_by`` (ties keep chronological order)."""
    wanted = condition or ShotFilter()
    matching = [s for s in analysis.shots if _matches(s, wanted)]
    return tuple(sorted(matching, key=lambda s: (_KEYS[rank_by](s), s.index)))
