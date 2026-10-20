"""What the module remembers: which artifact each slot serves, and what is known about it.

Bytes, checksums, sizes and derivation live in the Media Library and are never copied here; the
records only reference library assets by id.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from media_house.modules.proxy_manager.domain.values import Condition, Evidence, Verdict


class ArtifactState(StrEnum):
    LIVE = "live"
    PRUNING = "pruning"  # being deleted: nothing may adopt or activate it


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """A library derived asset this module created, with the verdict it was last given."""

    asset_id: str
    source_asset_id: str
    profile: str
    spec_fingerprint: str
    attempt_id: str
    verdict: Verdict
    evidence: Evidence
    validator_version: int
    validated_at: datetime
    state: ArtifactState = ArtifactState.LIVE


@dataclass(frozen=True, slots=True)
class Slot:
    """``(source, profile)``: one stable place a proxy is served from, with a rollback target."""

    source_asset_id: str
    profile: str
    active_asset_id: str
    previous_asset_id: str | None
    #: ``seq`` of the attempt whose artifact is active (the fencing token).
    activated_seq: int
    updated_at: datetime


def assess(
    *,
    has_slot: bool,
    asset_present: bool,
    file_ok: bool,
    verdict: Verdict | None,
    validator_version: int | None,
    artifact_fingerprint: str | None,
    wanted_fingerprint: str,
    validator_current: int,
) -> Condition:
    """Where a slot stands. Order matters: a missing file outranks everything else."""
    if not has_slot:
        return Condition.NONE
    if not asset_present or not file_ok:
        return Condition.MISSING
    if verdict is None or not verdict.usable:
        return Condition.INVALID
    if validator_version != validator_current:
        return Condition.NEEDS_REVALIDATION
    if artifact_fingerprint != wanted_fingerprint:
        return Condition.STALE
    return Condition.READY
