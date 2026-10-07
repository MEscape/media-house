"""Relationships between assets: facts where the library knows them, candidates where it guesses.

Only attributes the Media Library already holds are compared (duration, size, container,
codec, checksum, derivation). A relationship that is not recorded is never stated as fact: it is
a ``RelationCandidate`` with a confidence and the evidence it rests on, and the confidence never
reaches 1.0 for a guess. Re-evaluated on demand, never cached (the library changes).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from media_house.modules.media_inspection.domain.values import Certainty, JsonValue

#: Durations this close are "the same length" to a frame (about 1/25 s).
SAME_DURATION_SECONDS = 0.04
#: ...and this close are "nearly" the same (trimmed heads and tails, container rounding).
NEAR_DURATION_SECONDS = 0.5
_ASPECT_TOLERANCE = 0.01
_PROXY_AREA_RATIO = 0.7
#: A guess is never certain.
_MAX_GUESS_CONFIDENCE = 0.9


class RelationKind(StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"  # same bytes
    SOURCE = "source"  # the other asset is what this one was derived from
    DERIVATIVE = "derivative"  # the other asset was derived from this one
    POSSIBLE_DUPLICATE = "possible_duplicate"  # same picture size and length, different bytes
    POSSIBLE_PROXY = "possible_proxy"  # same length and shape, different resolution
    POSSIBLE_COMPANION = "possible_companion"  # audio-only and video-only of the same length


@dataclass(frozen=True, slots=True)
class MediaSummary:
    """The attributes of an asset that relationships are judged by."""

    asset_id: str
    checksum: str
    has_video: bool
    has_audio: bool
    duration: float | None
    width: int | None
    height: int | None
    container: tuple[str, ...] = ()
    video_codec: str | None = None
    source_asset_id: str | None = None


@dataclass(frozen=True, slots=True)
class RelationCandidate:
    kind: RelationKind
    asset_id: str
    #: ``MEASURED`` for recorded facts (derivation, identical checksum), ``POSSIBLE`` otherwise.
    certainty: Certainty
    #: 1.0 only for facts; at most 0.9 for a guess.
    confidence: float
    evidence: Mapping[str, JsonValue] = field(default_factory=dict)


def find_relations(
    subject: MediaSummary, others: Sequence[MediaSummary]
) -> tuple[RelationCandidate, ...]:
    """Everything that plausibly relates ``others`` to ``subject``, strongest first."""
    found: list[RelationCandidate] = []
    for other in others:
        if other.asset_id == subject.asset_id:
            continue
        found.extend(_relations_to(subject, other))
    return tuple(sorted(found, key=lambda c: (-c.confidence, c.kind.value, c.asset_id)))


def _relations_to(subject: MediaSummary, other: MediaSummary) -> list[RelationCandidate]:
    if other.checksum == subject.checksum:
        return [_fact(RelationKind.EXACT_DUPLICATE, other, {"checksum": subject.checksum})]
    facts: list[RelationCandidate] = []
    if subject.source_asset_id == other.asset_id:
        facts.append(_fact(RelationKind.SOURCE, other, {}))
    if other.source_asset_id == subject.asset_id:
        facts.append(_fact(RelationKind.DERIVATIVE, other, {}))
    if facts:
        return facts
    return [
        c
        for c in (_duplicate(subject, other), _proxy(subject, other), _companion(subject, other))
        if c
    ]


def _fact(
    kind: RelationKind, other: MediaSummary, evidence: Mapping[str, JsonValue]
) -> RelationCandidate:
    return RelationCandidate(kind, other.asset_id, Certainty.MEASURED, 1.0, evidence)


def _difference(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else abs(a - b)


def _same_size(subject: MediaSummary, other: MediaSummary) -> bool:
    return (
        subject.width is not None
        and subject.width == other.width
        and subject.height is not None
        and subject.height == other.height
    )


def _guess(
    kind: RelationKind,
    other: MediaSummary,
    confidence: float,
    evidence: Mapping[str, JsonValue],
) -> RelationCandidate:
    return RelationCandidate(
        kind,
        other.asset_id,
        Certainty.POSSIBLE,
        round(min(confidence, _MAX_GUESS_CONFIDENCE), 2),
        evidence,
    )


def _duplicate(subject: MediaSummary, other: MediaSummary) -> RelationCandidate | None:
    """Same length and picture size but different bytes: probably a re-encode of the same take."""
    gap = _difference(subject.duration, other.duration)
    if not (subject.has_video and other.has_video) or gap is None or gap > NEAR_DURATION_SECONDS:
        return None
    if not _same_size(subject, other):
        return None
    # length and picture size alone are weak evidence; every further match adds a little
    score = 0.3 + (0.3 if gap <= SAME_DURATION_SECONDS else 0.1)
    score += 0.1 if subject.container and subject.container == other.container else 0.0
    score += 0.1 if subject.video_codec and subject.video_codec == other.video_codec else 0.0
    score += 0.1 if subject.has_audio == other.has_audio else 0.0
    return _guess(
        RelationKind.POSSIBLE_DUPLICATE,
        other,
        score,
        {"duration_difference_seconds": round(gap, 4), "size": [subject.width, subject.height]},
    )


def _area(width: int | None, height: int | None) -> int | None:
    return width * height if width and height else None


def _proxy(subject: MediaSummary, other: MediaSummary) -> RelationCandidate | None:
    """Same length and shape at a clearly different resolution: a proxy or its full version."""
    gap = _difference(subject.duration, other.duration)
    if not (subject.has_video and other.has_video) or gap is None or gap > SAME_DURATION_SECONDS:
        return None
    mine, theirs = _area(subject.width, subject.height), _area(other.width, other.height)
    if mine is None or theirs is None or mine == theirs:
        return None
    assert subject.width and subject.height and other.width and other.height  # noqa: PT018, S101
    shape = (subject.width / subject.height) / (other.width / other.height)
    if abs(shape - 1) > _ASPECT_TOLERANCE:
        return None
    ratio = theirs / mine
    if min(ratio, 1 / ratio) > _PROXY_AREA_RATIO:
        return None
    return _guess(
        RelationKind.POSSIBLE_PROXY,
        other,
        0.6 + (0.1 if gap == 0 else 0.0),
        {
            "other_is": "lower_resolution" if ratio < 1 else "higher_resolution",
            "duration_difference_seconds": round(gap, 4),
            "area_ratio": round(ratio, 4),
        },
    )


def _companion(subject: MediaSummary, other: MediaSummary) -> RelationCandidate | None:
    """A silent video and an audio-only file of the same length: parts of one recording."""
    gap = _difference(subject.duration, other.duration)
    if gap is None or gap > SAME_DURATION_SECONDS * 2:
        return None
    complementary = (
        subject.has_video and not subject.has_audio and other.has_audio and not other.has_video
    ) or (subject.has_audio and not subject.has_video and other.has_video and not other.has_audio)
    if not complementary:
        return None
    return _guess(
        RelationKind.POSSIBLE_COMPANION,
        other,
        0.5,
        {"duration_difference_seconds": round(gap, 4)},
    )
