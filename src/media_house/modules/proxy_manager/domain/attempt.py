"""One generation attempt: its lifecycle states and the only legal moves between them.

``seq`` is assigned by the store when the attempt is created and is the FENCING TOKEN: it orders
requests by SUBMISSION, not by completion. A slot only accepts an activation from an attempt
whose ``seq`` is higher than that of the attempt that produced its active proxy.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from itertools import pairwise

from media_house.shared.errors import InvariantViolation

#: An attempt of another process with no sign of life for this long is dead.
DEFAULT_LEASE_SECONDS = 120.0


class AttemptState(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    READY = "ready"  # activated: the slot now serves this attempt's artifact
    SUPERSEDED = "superseded"  # produced a valid artifact, but a newer request already won
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the owning process ended without finishing

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_STATES


class FailureKind(StrEnum):
    TRANSIENT = "transient"  # trying again later may work (disk space, tool crash, timeout)
    PERMANENT = "permanent"  # the same request will fail the same way
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {
        AttemptState.READY,
        AttemptState.SUPERSEDED,
        AttemptState.FAILED,
        AttemptState.CANCELLED,
        AttemptState.INTERRUPTED,
    }
)
ACTIVE_STATES = frozenset(AttemptState) - TERMINAL_STATES
#: States that carry a failure kind (and only they do).
FAILURE_STATES = frozenset({AttemptState.FAILED, AttemptState.CANCELLED, AttemptState.INTERRUPTED})

_STAGES = (
    AttemptState.QUEUED,
    AttemptState.GENERATING,
    AttemptState.VALIDATING,
    AttemptState.PUBLISHING,
)
#: Every legal move. The SQL migration seeds an identical table (a test keeps them equal).
TRANSITIONS: frozenset[tuple[AttemptState, AttemptState]] = frozenset(
    set(pairwise(_STAGES))
    # reusing or adopting a stored artifact skips encoding (and, when it is trusted, validating)
    | {
        (AttemptState.QUEUED, AttemptState.VALIDATING),
        (AttemptState.QUEUED, AttemptState.PUBLISHING),
    }
    | {
        (AttemptState.PUBLISHING, AttemptState.READY),
        (AttemptState.PUBLISHING, AttemptState.SUPERSEDED),
    }
    | {(stage, end) for stage in _STAGES for end in FAILURE_STATES}
)


@dataclass(frozen=True, slots=True)
class Attempt:
    id: str
    source_asset_id: str
    profile: str
    profile_version: int
    spec_fingerprint: str
    owner_token: str
    created_at: datetime
    #: Assigned by the store on creation (``0`` until then).
    seq: int = 0
    state: AttemptState = AttemptState.QUEUED
    #: Name of the scratch directory this attempt owns (proof of ownership for cleanup).
    scratch_name: str | None = None
    reserved_bytes: int = 0
    reserved_volume: str | None = None
    #: Library asset this attempt replaces because it was unusable (same derivation fingerprint).
    replaces_asset_id: str | None = None
    artifact_asset_id: str | None = None
    encoder: str | None = None
    fallback: str | None = None
    toolchain: str | None = None
    failure: FailureKind | None = None
    error_message: str | None = None
    finished_at: datetime | None = None
    #: Last sign of life of the owner. A foreign attempt whose beat is older than the lease is dead.
    heartbeat_at: datetime | None = None

    def advance(self, to: AttemptState, *, now: datetime, **changes: object) -> "Attempt":
        """The attempt in state ``to``; an illegal move is a bug and raises."""
        if (self.state, to) not in TRANSITIONS:
            raise InvariantViolation(
                f"Illegal attempt transition {self.state.value} -> {to.value}",
                details={"attempt_id": self.id},
            )
        finished = now if to.terminal else None
        return replace(self, state=to, finished_at=finished, **changes)  # type: ignore[arg-type]

    def fail(self, kind: FailureKind, message: str, *, now: datetime) -> "Attempt":
        to = {
            FailureKind.CANCELLED: AttemptState.CANCELLED,
        }.get(kind, AttemptState.FAILED)
        return self.advance(to, now=now, failure=kind, error_message=message[:500])
