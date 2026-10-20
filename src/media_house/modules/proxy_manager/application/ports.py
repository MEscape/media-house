"""Ports owned by the proxy manager's application layer.

* :class:`ProxyStore`: the module's own memory (slots, artifacts, attempts); never media bytes.
* :class:`ProxyEncoder`: makes the proxy file from a source file and a ResolvedSpec.
* :class:`MediaProbe`: reads facts from a media file (the fallback when no inspection is stored,
  and the independent view of the OUTPUT that validation judges).
* :class:`DiskSpace` and :class:`Scratch`: free space and the module's own scratch directories.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from media_house.modules.proxy_manager.domain.attempt import Attempt
from media_house.modules.proxy_manager.domain.records import ArtifactRecord, Slot
from media_house.modules.proxy_manager.domain.source import SourceFacts
from media_house.modules.proxy_manager.domain.spec import ResolvedSpec
from media_house.modules.proxy_manager.domain.validation import OutputFacts
from media_house.modules.proxy_manager.domain.values import Evidence, Verdict
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True)
class EncodeRequest:
    source: Path
    destination: Path
    spec: ResolvedSpec
    facts: SourceFacts
    #: Hard ceiling for the output; the encoder must stop writing beyond it.
    max_bytes: int


@dataclass(frozen=True, slots=True)
class EncodeOutcome:
    #: The concrete encoder that produced the file (provenance, not part of the fingerprint).
    encoder: str
    fallback: str | None = None


class ProxyEncoder(Protocol):
    @property
    def identity(self) -> str:
        """Version of the tool chain that encodes (recorded; drift is reported, not fatal)."""
        ...

    def encode(
        self,
        request: EncodeRequest,
        cancellation: CancellationToken,
        on_progress: Callable[[float], None],
    ) -> EncodeOutcome:
        """Write the proxy. Raises ``ProcessFailedError`` / ``OperationCancelledError``."""
        ...


class MediaProbe(Protocol):
    def probe_source(self, path: Path, cancellation: CancellationToken) -> SourceFacts: ...

    def probe_output(
        self, path: Path, cancellation: CancellationToken, *, decode: bool
    ) -> OutputFacts:
        """Facts of a finished file; ``decode`` also decodes it to the end."""
        ...


class DiskSpace(Protocol):
    def free_bytes(self, path: Path) -> int: ...

    def volume_of(self, path: Path) -> str:
        """Stable identifier of the volume ``path`` lives on."""
        ...

    def size_bytes(self, path: Path) -> int | None:
        """Size of the file, or ``None`` if it does not exist."""
        ...


class Scratch(Protocol):
    """Scratch directories this module owns, named ``proxy-<attempt id>``."""

    @property
    def root(self) -> Path: ...

    def create(self, name: str) -> Path: ...

    def remove(self, name: str) -> None:
        """Remove an owned scratch directory; a missing one is not an error."""
        ...

    def names(self) -> list[str]:
        """Names of the ``proxy-*`` directories that exist (to report ones nobody owns)."""
        ...


class PublishOutcome(StrEnum):
    ACTIVATED = "activated"
    SUPERSEDED = "superseded"  # a newer request already won the slot
    PRUNING = "pruning"  # the artifact is being deleted and cannot be activated


class ProxyStore(Protocol):
    @property
    def owner_token(self) -> str:
        """Identifies this process's attempts; attempts of other owners are dead ones."""
        ...

    def begin_attempt(
        self,
        attempt: Attempt,
        *,
        free_bytes: int,
        margin_bytes: int,
        now: datetime,
        lease_seconds: float,
    ) -> Attempt:
        """Record the attempt and reserve its disk in one transaction.

        Raises ``AttemptInFlight`` when the identical generation is already running and its
        owner is alive; one whose owner is another process with an expired lease is retired
        (INTERRUPTED) and replaced. Raises ``InsufficientSpace`` when free space minus live
        reservations cannot hold it.
        """
        ...

    def touch_attempt(self, attempt_id: str, *, now: datetime) -> bool:
        """Record a sign of life of this owner's live attempt (its lease starts over).

        ``False`` means the attempt is not live and this owner's any more, e.g. it was retired
        after its lease ran out: the caller must stop working for it.
        """
        ...

    def expire_lease(self, attempt_id: str, *, now: datetime, lease_seconds: float) -> bool:
        """Retire a live attempt of another owner whose lease ran out. ``True`` if it did."""
        ...

    def save_attempt(self, attempt: Attempt) -> None:
        """Persist the attempt's new state. Raises ``LeaseLost`` if it was retired meanwhile."""
        ...

    def get_attempt(self, attempt_id: str) -> Attempt | None: ...

    def has_attempt_for(self, source_asset_id: str, spec_fingerprint: str) -> bool:
        """Proof that this module once tried to produce that exact derivation."""
        ...

    def get_slot(self, source_asset_id: str, profile: str) -> Slot | None: ...

    def get_artifact(self, asset_id: str) -> ArtifactRecord | None: ...

    def find_artifact(
        self, source_asset_id: str, spec_fingerprint: str
    ) -> ArtifactRecord | None: ...

    def update_verdict(
        self, asset_id: str, verdict: Verdict, evidence: Evidence, validator_version: int,
        *, now: datetime,
    ) -> None:  # fmt: skip
        ...

    def publish(
        self, attempt: Attempt, artifact: ArtifactRecord, *, now: datetime
    ) -> tuple[Attempt, PublishOutcome]:
        """Record the artifact and, if this attempt is the newest, make it the slot's active one.

        One transaction. ``attempt`` must be PUBLISHING (in the database too: an attempt retired
        after its lease ran out raises ``LeaseLost``); it ends READY or SUPERSEDED. If
        ``attempt.replaces_asset_id`` is set, that unusable artifact's record is removed in the
        same transaction. The previous active artifact is kept as rollback target only if usable.
        """
        ...

    def interrupt_foreign_attempts(
        self, *, now: datetime, lease_seconds: float
    ) -> Sequence[Attempt]:
        """Mark live attempts of other owners whose lease ran out INTERRUPTED."""
        ...

    # --- removing and pruning ------------------------------------------------------------
    def list_artifacts(self) -> Sequence[ArtifactRecord]: ...

    def unreferenced_artifacts(self) -> Sequence[ArtifactRecord]:
        """Live artifacts no slot serves or keeps, whose slot has no live attempt."""
        ...

    def claim_unreferenced(self) -> Sequence[ArtifactRecord]:
        """``unreferenced_artifacts`` marked PRUNING in one transaction: nothing can adopt them."""
        ...

    def claim_slot(self, source_asset_id: str, profile: str) -> Sequence[ArtifactRecord]:
        """Dissolve a slot: delete it and mark its artifacts PRUNING in one transaction.

        Raises ``AttemptInFlight`` if the slot has a live attempt.
        """
        ...

    def pruning_artifacts(self) -> Sequence[ArtifactRecord]: ...

    def finish_prune(self, asset_id: str) -> None:
        """Forget a PRUNING artifact whose library asset is gone."""
        ...

    def abandon_prune(self, asset_id: str) -> None:
        """Return a PRUNING artifact that could not be deleted to LIVE."""
        ...

    def drop_record(self, asset_id: str) -> bool:
        """Forget an unreferenced artifact record whose library asset vanished."""
        ...

    def purge_source(self, source_asset_id: str) -> int:
        """Forget every slot and artifact record of a source the library no longer has."""
        ...

    def known_sources(self) -> Sequence[str]: ...

    def attempts_with_scratch(self) -> Sequence[Attempt]:
        """Terminal attempts that still name a scratch directory (to be swept)."""
        ...

    def clear_scratch(self, attempt_id: str) -> None: ...
