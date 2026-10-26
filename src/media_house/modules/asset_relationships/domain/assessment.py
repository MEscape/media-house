"""The grade of a claim: a deterministic function of its type and current evidence.

A grade is ordinal. Nothing here is a probability, an average or a weighted sum, and a grade never
makes a claim trusted lineage: a person's decision does that.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from media_house.modules.asset_relationships.domain.evidence import (
    CLOCK_TRUST_UNVERIFIED,
    RelationshipEvidence,
)
from media_house.modules.asset_relationships.domain.taxonomy import spec_of
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    RelationshipType,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class Assessment:
    grade: Grade
    #: Evidence of defined kinds that opposes the claim or disagrees with other evidence.
    contradicted: bool = False


def assess(
    relationship_type: RelationshipType, evidence: Sequence[RelationshipEvidence]
) -> Assessment:
    """Grade the current (not superseded) evidence of a stored claim."""
    if not spec_of(relationship_type).storable:
        raise InvariantViolation(f"{relationship_type.value} is a projection and has no grade here")
    current = [e for e in evidence if not e.superseded]
    if not current:
        raise InvariantViolation("A claim needs evidence")
    if relationship_type is RelationshipType.TEMPORALLY_OVERLAPS_WITH:
        return _overlap(current)
    return _take(current)


def _of(evidence: Sequence[RelationshipEvidence], kind: EvidenceKind) -> list[RelationshipEvidence]:
    return [e for e in evidence if e.kind is kind]


def _overlap(evidence: Sequence[RelationshipEvidence]) -> Assessment:
    wall = _of(evidence, EvidenceKind.WALL_CLOCK_OVERLAP)
    timecode = _of(evidence, EvidenceKind.TIMECODE_OVERLAP)
    # Clocks are only trusted when something other than the file's own tags says they agree.
    wall_grade = (
        Grade.INDICATED
        if any(e.detail["clock_trust"] != CLOCK_TRUST_UNVERIFIED for e in wall)
        else Grade.SUGGESTIVE
    )
    if wall and timecode:
        agreement = _offsets_agree(wall, timecode)
        if agreement is True:
            return Assessment(Grade.CORROBORATED)
        if agreement is False:
            return Assessment(Grade.SUGGESTIVE, contradicted=True)
    if wall:
        return Assessment(wall_grade)
    return Assessment(Grade.SUGGESTIVE)  # timecode alone, or only a person's assertion


def _offsets_agree(
    wall: Sequence[RelationshipEvidence], timecode: Sequence[RelationshipEvidence]
) -> bool | None:
    """Whether the offsets the two kinds imply agree within their uncertainty.

    ``None`` when that cannot be established (an offset or an uncertainty is missing).
    """
    a = next((e.offset for e in wall if e.offset is not None), None)
    b = next((e.offset for e in timecode if e.offset is not None), None)
    if a is None or b is None or a.uncertainty is None or b.uncertainty is None:
        return None
    return abs(a.seconds - b.seconds) <= a.uncertainty + b.uncertainty


def _norm(value: object) -> str:
    return str(value).strip().casefold()


def _take(evidence: Sequence[RelationshipEvidence]) -> Assessment:
    tags = _of(evidence, EvidenceKind.PRODUCTION_TAG_MATCH)
    if not tags:
        return Assessment(Grade.SUGGESTIVE)  # only a person's assertion
    for tag in tags:
        same_scene = _norm(tag.detail["scene_a"]) == _norm(tag.detail["scene_b"])
        different_take = _norm(tag.detail["take_a"]) != _norm(tag.detail["take_b"])
        if not (same_scene and different_take):
            return Assessment(Grade.SUGGESTIVE, contradicted=True)
    same_source = any(
        t.detail.get("same_device") is True or t.detail.get("same_reel") is True for t in tags
    )
    return Assessment(Grade.CORROBORATED if same_source else Grade.INDICATED)
