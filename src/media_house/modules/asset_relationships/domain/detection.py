"""Detectors: pure rules that turn facts media inspection already recorded into proposed claims.

A detector never probes, never grades and never decides. It says which assets MIGHT be related and
why; the grade is ``assess(type, evidence)`` (a function of the evidence alone) and a person's
decision is the only thing that makes a claim trusted. Nothing here reads a filename, a duration
or a file size as evidence of a relationship, and there is no score or probability anywhere.

Two rules exist, because only two are justified by what inspection records:

* ``wall_clock_overlap``: two recordings whose AWARE creation timestamps and durations overlap by
  more than the combined timestamp uncertainty.
* ``production_tag_takes``: the same scene tag with a different take tag, on the same device or
  reel.

Timecode is deliberately not a detector: many cameras restart timecode at zero on every clip, so it
would link every clip to every other.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Final, Protocol

from media_house.modules.asset_relationships.domain.basis import MANUAL_DETECTOR, Basis
from media_house.modules.asset_relationships.domain.intervals import (
    ClockOffset,
    TimeCoordinates,
    TimeInterval,
)
from media_house.modules.asset_relationships.domain.values import (
    ClockDomain,
    EvidenceKind,
    JsonValue,
    OffsetOrigin,
    RelationshipType,
)

_US: Final = 1_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
#: Container creation times are commonly whole seconds, so a second is the honest resolution.
DEFAULT_MARGIN_US: Final = 1 * _US
#: Cameras with an unset clock write the epoch of their container format (1904, 1970): such a
#: timestamp is not a time of recording and would "overlap" every other unset clock. Anything before
#: this is skipped. A heuristic, to be checked against ground truth.
EARLIEST_PLAUSIBLE: Final = datetime(2000, 1, 1, tzinfo=UTC)
#: One continuous camera file longer than this is more likely a metadata error than a recording; its
#: span would overlap everything around it. Also a heuristic.
MAX_PLAUSIBLE_SPAN_S: Final = 48 * 3600
RECORDING_START = TimeCoordinates(ClockDomain.CONTAINER, "creation_time_assumed_start")


def _clean(text: str | None) -> str | None:
    return text.strip() or None if text is not None else None


@dataclass(frozen=True, slots=True)
class InspectionFacts:
    """The few values of one inspection the rules read, copied by the application layer."""

    asset_id: str
    #: The stored inspection document (a derived asset) and its derivation fingerprint: together
    #: they say WHICH inspection of WHICH bytes these facts came from.
    inspection_id: str
    inspection_fingerprint: str
    duration_seconds: float | None
    creation_time: datetime | None
    device: str | None
    reel_id: str | None
    scene: str | None
    take: str | None

    @property
    def identity(self) -> str:
        return f"{self.inspection_id}:{self.inspection_fingerprint}"


@dataclass(frozen=True, slots=True)
class Fact:
    """What a detector indexes about one asset so others can find it without reading everything."""

    #: ``wall_span`` or ``production_tags``.
    kind: str
    group_key: str = ""
    #: UTC microseconds since the epoch, half-open ``[start, end)``; ``None`` for tags.
    start_us: int | None = None
    end_us: int | None = None
    detail: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Observed:
    """An indexed fact together with whose it is and which inspection it came from."""

    asset_id: str
    identity: str
    inspection_id: str
    fact: Fact


class Lookup(StrEnum):
    OVERLAP = "overlap"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class DetectedEvidence:
    kind: EvidenceKind
    detail: Mapping[str, JsonValue]
    offset: ClockOffset | None = None


@dataclass(frozen=True, slots=True)
class Detection:
    """A proposed claim: its type, endpoints and evidence. No grade, no score."""

    relationship_type: RelationshipType
    asset_a: str
    asset_b: str
    evidence: tuple[DetectedEvidence, ...]
    a_interval: TimeInterval | None = None
    b_interval: TimeInterval | None = None
    offset: ClockOffset | None = None


class Detector(Protocol):
    id: str
    version: str
    relationship_type: RelationshipType
    lookup: Lookup

    def fact_of(self, subject: InspectionFacts) -> Fact | None:
        """The fact to index for this asset, or ``None`` when its inspection has nothing usable."""
        ...

    def match(self, one: Observed, other: Observed) -> Detection | None: ...


class WallClockOverlap:
    id = "wall_clock_overlap"
    version = "1"
    relationship_type = RelationshipType.TEMPORALLY_OVERLAPS_WITH
    lookup = Lookup.OVERLAP

    def __init__(self, margin_us: int = DEFAULT_MARGIN_US) -> None:
        self.margin_us = margin_us

    def fact_of(self, subject: InspectionFacts) -> Fact | None:
        created = subject.creation_time
        duration = subject.duration_seconds
        # A naive timestamp has no zone and none is invented; no usable duration, no span.
        if created is None or created.tzinfo is None or duration is None:
            return None
        if not math.isfinite(duration) or not 0 < duration <= MAX_PLAUSIBLE_SPAN_S:
            return None
        try:
            created_utc = created.astimezone(UTC)
        except (OverflowError, ValueError):
            return None  # an offset that cannot be expressed in UTC is not a usable time
        if created_utc < EARLIEST_PLAUSIBLE:
            return None
        start = (created_utc - _EPOCH) // timedelta(microseconds=1)
        end = start + round(duration * _US)
        return Fact(
            "wall_span",
            start_us=start,
            end_us=end,
            detail={"device": _clean(subject.device)},
        )

    @property
    def needed_overlap_us(self) -> int:
        """Back-to-back clips of one recording touch or differ by a rounding: not an overlap."""
        return 2 * self.margin_us

    def match(self, one: Observed, other: Observed) -> Detection | None:
        a, b = one.fact, other.fact
        if a.start_us is None or a.end_us is None or b.start_us is None or b.end_us is None:
            return None
        overlap = min(a.end_us, b.end_us) - max(a.start_us, b.start_us)
        if overlap <= self.needed_overlap_us:
            return None
        device = a.detail.get("device")
        same_device = isinstance(device, str) and device == b.detail.get("device")
        start = max(a.start_us, b.start_us)
        end = min(a.end_us, b.end_us)
        offset = ClockOffset(
            Fraction(a.start_us - b.start_us, _US),
            Fraction(self.needed_overlap_us, _US),
            OffsetOrigin.WALL_CLOCK_DIFFERENCE,
        )
        evidence = DetectedEvidence(
            EvidenceKind.WALL_CLOCK_OVERLAP,
            {
                "clock_trust": "same_device" if same_device else "unverified",
                "creation_time_semantics": "assumed_start",
                # What was calculated: file timestamps overlap. Not proof of one event.
                "evidence_scope": "timestamp_overlap_only",
                "overlap_us": overlap,
                "margin_us": self.margin_us,
                "inspection_a": one.inspection_id,
                "inspection_b": other.inspection_id,
            },
            offset,
        )
        return Detection(
            self.relationship_type,
            one.asset_id,
            other.asset_id,
            (evidence,),
            a_interval=TimeInterval(
                Fraction(start - a.start_us, _US), Fraction(end - a.start_us, _US), RECORDING_START
            ),
            b_interval=TimeInterval(
                Fraction(start - b.start_us, _US), Fraction(end - b.start_us, _US), RECORDING_START
            ),
            offset=offset,
        )


class ProductionTakes:
    id = "production_tag_takes"
    version = "1"
    relationship_type = RelationshipType.ALTERNATE_TAKE_OF
    lookup = Lookup.GROUP

    def fact_of(self, subject: InspectionFacts) -> Fact | None:
        scene, take = _clean(subject.scene), _clean(subject.take)
        if scene is None or take is None:
            return None
        return Fact(
            "production_tags",
            group_key=scene.casefold(),
            detail={
                "scene": scene,
                "take": take,
                "device": _clean(subject.device),
                "reel_id": _clean(subject.reel_id),
            },
        )

    def match(self, one: Observed, other: Observed) -> Detection | None:
        a, b = one.fact.detail, other.fact.detail
        take_a, take_b = str(a["take"]), str(b["take"])
        if take_a.casefold() == take_b.casefold() or one.fact.group_key != other.fact.group_key:
            return None
        # Two named devices that differ contradict the reading "one camera made both".
        if (
            a.get("device") is not None
            and b.get("device") is not None
            and a["device"] != b["device"]
        ):
            return None
        same_device = a.get("device") is not None and a.get("device") == b.get("device")
        same_reel = a.get("reel_id") is not None and a.get("reel_id") == b.get("reel_id")
        # A generic label such as "1" is shared by unrelated shoots: the scene and take tags alone
        # are not enough, the same device or reel has to agree too.
        if not (same_device or same_reel):
            return None
        evidence = DetectedEvidence(
            EvidenceKind.PRODUCTION_TAG_MATCH,
            {
                "scene_a": str(a["scene"]),
                "scene_b": str(b["scene"]),
                "take_a": take_a,
                "take_b": take_b,
                "same_device": same_device,
                "same_reel": same_reel,
                "inspection_a": one.inspection_id,
                "inspection_b": other.inspection_id,
            },
        )
        return Detection(self.relationship_type, one.asset_id, other.asset_id, (evidence,))


DETECTORS: Mapping[str, Detector] = {d.id: d for d in (WallClockOverlap(), ProductionTakes())}


class DetectorVersions:
    """The ``BasisCheck`` for detections: current when the version equals the detector's now.

    In memory and O(1); it knows every registered detector whether or not a run enabled it, so a
    version bump invalidates old claims either way. Anything else (including ``manual``) is unknown.
    """

    def __init__(self, detectors: Mapping[str, Detector] = DETECTORS) -> None:
        self._versions = {d.id: d.version for d in detectors.values()}

    def is_current(self, basis: Basis) -> bool | None:
        if basis.detector == MANUAL_DETECTOR or basis.detector not in self._versions:
            return None
        return basis.version == self._versions[basis.detector]
