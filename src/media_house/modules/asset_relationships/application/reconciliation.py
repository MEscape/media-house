"""Use case: reconcile the stored claims with the live library (caller-driven, bounded, resumable).

One job walks the module's OWN claims by id in batches, asks the library about the endpoints it
meets, and applies the rules of ``domain.reconciliation`` to each claim in its own transaction.
It stores nothing about lineage and never calls the lineage projection.

How a job ends (``ReconcileOutcome.status`` and ``reason``):

* ``COMPLETED``: the sweep reached the last claim.
* ``PARTIAL``: ``budget`` (claims-per-job limit), ``transient_library`` (the library failed after
  the retries) or ``suspicious_library`` (a batch looks like the library lost its data). Batches
  committed earlier stay committed; the failing batch wrote nothing and its cursor did not move,
  so the next attempt resumes there once the backoff allows.
* ``INTERRUPTED``: cancelled; recorded, then ``OperationCancelledError`` propagates.
* ``FAILED``: an unexpected exception; recorded, then it propagates.
* ``NOT_DUE`` (the interval or the failure backoff has not passed) and ``SUPERSEDED`` (another run
  moved the cursor first).
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.ports import AssetLookup, BasisCheck
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.errors import CorruptClaim
from media_house.modules.asset_relationships.domain.reconciliation import (
    ClaimRef,
    ClaimVerdict,
    PurgeReason,
    ReconcilePolicy,
    ReconciliationStore,
    ScanState,
    looks_emptied,
    reconcile_claim,
)
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    InfrastructureError,
    InvariantViolation,
    OperationCancelledError,
)
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class OutcomeStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    NOT_DUE = "not_due"
    SUPERSEDED = "superseded"


class OutcomeReason(StrEnum):
    BUDGET = "budget"
    TRANSIENT_LIBRARY = "transient_library"
    SUSPICIOUS_LIBRARY = "suspicious_library"
    BACKOFF = "backoff"
    INTERVAL = "interval"
    CANCELLED = "cancelled"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class ReconciliationSettings:
    """How a job is bounded and how long claims whose assets are gone are kept. All provisional."""

    #: Claims per batch: at most twice as many endpoints, which is one library call.
    batch_claims: int = 250
    #: The most claims one job handles; a longer sweep continues in the next job.
    max_claims_per_job: int = 10_000
    #: Minimum time between the end of one sweep and the start of the next.
    min_sweep_interval: timedelta = timedelta(hours=24)
    undecided_orphan_grace: timedelta = timedelta(days=7)
    decided_retention: timedelta = timedelta(days=365)
    #: Waits before each RETRY of a failed library call (so up to len + 1 attempts per batch).
    retry_delays_seconds: tuple[float, ...] = (1.0, 4.0, 16.0)
    failure_backoff_base: timedelta = timedelta(minutes=30)
    failure_backoff_cap: timedelta = timedelta(hours=24)
    suspicious_min_claims: int = 20
    suspicious_ratio: float = 0.5
    suspicious_recheck_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.batch_claims < 1 or self.max_claims_per_job < 1:
            raise InvariantViolation("Reconciliation batch and job limits must be at least 1")
        if not 0 < self.suspicious_ratio <= 1:
            raise InvariantViolation("suspicious_ratio must be in (0, 1]")

    @property
    def policy(self) -> ReconcilePolicy:
        return ReconcilePolicy(self.undecided_orphan_grace, self.decided_retention)


@dataclass(frozen=True, slots=True)
class ReconcileCommand:
    """``force`` skips the minimum interval between sweeps, never the failure backoff."""

    force: bool = False


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    status: OutcomeStatus
    reason: OutcomeReason | None
    claims_checked: int
    claims_changed: int
    marked_missing: int
    restored: int
    purged_undecided: int
    purged_decided: int
    marked_stale: int
    marked_outdated: int
    skipped_corrupt: int
    batches: int
    retries: int
    #: Sweeps completed so far (this job's included).
    sweep_generation: int
    #: Where the next job resumes (opaque); ``None`` between sweeps.
    cursor: str | None
    started_at: datetime
    finished_at: datetime
    next_attempt_after: datetime | None

    def to_json(self) -> str:
        data = asdict(self)
        data["status"] = self.status.value
        data["reason"] = None if self.reason is None else self.reason.value
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat()
        data["next_attempt_after"] = (
            None if self.next_attempt_after is None else self.next_attempt_after.isoformat()
        )
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "ReconcileOutcome":
        data = json.loads(text)
        reason = data.pop("reason")
        next_attempt = data.pop("next_attempt_after")
        return cls(
            status=OutcomeStatus(data.pop("status")),
            reason=None if reason is None else OutcomeReason(reason),
            started_at=datetime.fromisoformat(data.pop("started_at")),
            finished_at=datetime.fromisoformat(data.pop("finished_at")),
            next_attempt_after=None
            if next_attempt is None
            else datetime.fromisoformat(next_attempt),
            **data,
        )


@dataclass
class _Counters:
    claims_checked: int = 0
    claims_changed: int = 0
    marked_missing: int = 0
    restored: int = 0
    purged_undecided: int = 0
    purged_decided: int = 0
    marked_stale: int = 0
    marked_outdated: int = 0
    skipped_corrupt: int = 0
    batches: int = 0
    retries: int = 0

    def count(self, verdict: ClaimVerdict) -> None:
        if verdict.purge is PurgeReason.UNDECIDED_ORPHAN:
            self.purged_undecided += 1
        elif verdict.purge is PurgeReason.RETENTION_EXPIRED:
            self.purged_decided += 1
        self.marked_missing += verdict.marked_missing
        self.restored += verdict.restored
        self.marked_stale += verdict.marked_stale
        self.marked_outdated += verdict.marked_outdated
        if verdict.changed or verdict.purge is not None:
            self.claims_changed += 1


@dataclass
class _Job:
    state: ScanState
    started_at: datetime
    counters: _Counters = field(default_factory=_Counters)
    cursor: str | None = None
    generation: int = 0


class ReconcileRelationships:
    """Keeps stored claims truthful against the library. Implements ``RelationshipMaintenance``."""

    def __init__(
        self,
        store: ReconciliationStore,
        library: AssetLookup,
        clock: Clock,
        checks: Sequence[BasisCheck] = (),
        settings: ReconciliationSettings | None = None,
    ) -> None:
        self._store = store
        self._library = library
        self._clock = clock
        self._checks = tuple(checks)
        self._settings = settings or ReconciliationSettings()

    # ------------------------------------------------------------------ public
    def last_run(self) -> ReconcileOutcome | None:
        """The outcome of the last real run (``NOT_DUE`` answers are never recorded)."""
        text = self._store.load_state().last_run
        if text is None:
            return None
        try:
            return ReconcileOutcome.from_json(text)
        except (ValueError, KeyError, TypeError) as exc:
            _log.warning("The recorded reconciliation outcome is unreadable", reason=str(exc))
            return None

    def reconcile(self, command: ReconcileCommand, ctx: JobContext) -> ReconcileOutcome:
        started = self._clock.now()
        state = self._store.load_state()
        job = _Job(state, started, cursor=state.claim_cursor, generation=state.sweep_generation)
        not_due = self._not_due(state, started, command.force)
        if not_due is not None:
            return self._outcome(job, OutcomeStatus.NOT_DUE, not_due, state.next_attempt_after)
        try:
            outcome = self._sweep(job, ctx)
        except OperationCancelledError:
            self._finish(
                job,
                self._outcome(job, OutcomeStatus.INTERRUPTED, OutcomeReason.CANCELLED),
                swallow=True,
            )
            raise
        except Exception:
            self._finish(
                job,
                self._outcome(job, OutcomeStatus.FAILED, OutcomeReason.UNEXPECTED),
                swallow=True,
            )
            raise
        return self._finish(job, outcome, swallow=False)

    # ------------------------------------------------------------------ gating
    def _not_due(self, state: ScanState, now: datetime, force: bool) -> OutcomeReason | None:
        if state.next_attempt_after is not None and now < state.next_attempt_after:
            return OutcomeReason.BACKOFF
        if state.claim_cursor is not None or force or state.last_sweep_completed_at is None:
            return None  # a sweep in progress always continues
        if now - state.last_sweep_completed_at >= self._settings.min_sweep_interval:
            return None
        return OutcomeReason.INTERVAL

    # ------------------------------------------------------------------ the sweep
    def _sweep(self, job: _Job, ctx: JobContext) -> ReconcileOutcome:
        settings = self._settings
        budget = settings.max_claims_per_job
        while True:
            ctx.raise_if_cancelled()
            batch = self._store.claims_after(job.cursor, settings.batch_claims)
            if not batch:
                if not self._store.complete_sweep(job.cursor, now=self._clock.now()):
                    return self._outcome(job, OutcomeStatus.SUPERSEDED, None)
                job.cursor = None
                job.generation += 1
                return self._outcome(job, OutcomeStatus.COMPLETED, None)

            endpoints = sorted({a for ref in batch for a in (ref.asset_a, ref.asset_b)})
            present = self._lookup(ctx, endpoints, job)
            if present is None:
                return self._outcome(job, OutcomeStatus.PARTIAL, OutcomeReason.TRANSIENT_LIBRARY)
            absent = set(endpoints) - present
            if self._suspicious(len(batch), len(absent), len(endpoints)):
                if ctx.cancellation.wait(settings.suspicious_recheck_seconds):
                    ctx.raise_if_cancelled()
                present = self._lookup(ctx, endpoints, job)
                if present is None:
                    return self._outcome(
                        job, OutcomeStatus.PARTIAL, OutcomeReason.TRANSIENT_LIBRARY
                    )
                absent = set(endpoints) - present
                if self._suspicious(len(batch), len(absent), len(endpoints)):
                    # no write for this batch and its cursor does not move: earlier batches stay
                    _log.warning(
                        "Reconciliation stopped: the library looks emptied",
                        claims=len(batch),
                        absent_endpoints=len(absent),
                        endpoints=len(endpoints),
                    )
                    return self._outcome(
                        job, OutcomeStatus.PARTIAL, OutcomeReason.SUSPICIOUS_LIBRARY
                    )

            now = self._clock.now()
            for ref in batch:
                # a healthy claim whose endpoints are present and whose basis is current needs no
                # transaction at all; whatever is acted on is judged again on the stored claim
                if self._needs_attention(ref, absent):
                    self._apply(ref.relationship_id, absent, now, job.counters)
            if not self._store.advance_cursor(job.cursor, batch[-1].relationship_id, now=now):
                return self._outcome(job, OutcomeStatus.SUPERSEDED, None)
            job.cursor = batch[-1].relationship_id
            job.counters.batches += 1
            job.counters.claims_checked += len(batch)
            ctx.progress.report(job.counters.claims_checked, None, "Reconciling relationships")
            budget -= len(batch)
            if budget <= 0:
                return self._outcome(job, OutcomeStatus.PARTIAL, OutcomeReason.BUDGET)

    def _suspicious(self, claims: int, absent: int, endpoints: int) -> bool:
        return looks_emptied(
            claims=claims,
            absent_endpoints=absent,
            endpoints=endpoints,
            minimum=self._settings.suspicious_min_claims,
            ratio=self._settings.suspicious_ratio,
        )

    def _lookup(self, ctx: JobContext, endpoints: list[str], job: _Job) -> set[str] | None:
        """Which endpoints the library has; ``None`` if it kept failing (not an absence)."""
        delays = self._settings.retry_delays_seconds
        for attempt in range(len(delays) + 1):
            try:
                return {asset.id for asset in self._library.get_many(endpoints)}
            except InfrastructureError as exc:
                if attempt == len(delays):
                    _log.warning("The library kept failing during reconciliation", reason=exc.code)
                    return None
                job.counters.retries += 1
                if ctx.cancellation.wait(delays[attempt]):
                    ctx.raise_if_cancelled()
        return None

    def _needs_attention(self, ref: ClaimRef, absent: set[str]) -> bool:
        if ref.marked or ref.asset_a in absent or ref.asset_b in absent:
            return True
        if not self._checks:
            return False
        try:
            return self._basis_current(Basis.from_canonical(ref.basis)) is False
        except InvariantViolation:
            return True  # an unreadable basis: the claim's own transaction judges (and skips) it

    def _apply(
        self, relationship_id: str, absent: set[str], now: datetime, counters: _Counters
    ) -> None:
        def decide(claim: AssetRelationship) -> ClaimVerdict:
            return reconcile_claim(
                claim,
                absent=absent,
                basis_current=self._basis_current(claim.basis),
                now=now,
                policy=self._settings.policy,
            )

        try:
            verdict = self._store.reconcile_claim(relationship_id, decide, now=now)
        except CorruptClaim as exc:
            counters.skipped_corrupt += 1
            _log.warning("A stored claim is unreadable and was skipped", reason=exc.code)
            return
        if verdict is not None:
            counters.count(verdict)

    def _basis_current(self, basis: Basis) -> bool | None:
        for check in self._checks:
            answer = check.is_current(basis)
            if answer is not None:
                return answer
        return None

    # ------------------------------------------------------------------ outcome
    def _outcome(
        self,
        job: _Job,
        status: OutcomeStatus,
        reason: OutcomeReason | None,
        next_attempt_after: datetime | None = None,
    ) -> ReconcileOutcome:
        c = job.counters
        return ReconcileOutcome(
            status=status,
            reason=reason,
            claims_checked=c.claims_checked,
            claims_changed=c.claims_changed,
            marked_missing=c.marked_missing,
            restored=c.restored,
            purged_undecided=c.purged_undecided,
            purged_decided=c.purged_decided,
            marked_stale=c.marked_stale,
            marked_outdated=c.marked_outdated,
            skipped_corrupt=c.skipped_corrupt,
            batches=c.batches,
            retries=c.retries,
            sweep_generation=job.generation,
            cursor=job.cursor,
            started_at=job.started_at,
            finished_at=self._clock.now(),
            next_attempt_after=next_attempt_after,
        )

    def _finish(self, job: _Job, outcome: ReconcileOutcome, *, swallow: bool) -> ReconcileOutcome:
        """Record the run, work out the failure backoff, and log it."""
        state = job.state
        failing = outcome.status is OutcomeStatus.FAILED or outcome.reason in {
            OutcomeReason.TRANSIENT_LIBRARY,
            OutcomeReason.SUSPICIOUS_LIBRARY,
        }
        if failing:
            failures = state.consecutive_failures + 1
            delay = min(
                self._settings.failure_backoff_base * 2 ** (failures - 1),
                self._settings.failure_backoff_cap,
            )
            next_attempt: datetime | None = outcome.finished_at + delay
        elif outcome.status in {OutcomeStatus.INTERRUPTED, OutcomeStatus.SUPERSEDED}:
            failures, next_attempt = state.consecutive_failures, state.next_attempt_after
        else:
            failures, next_attempt = 0, None
        result = replace(outcome, next_attempt_after=next_attempt)
        try:
            self._store.record_run(
                last_run=result.to_json(),
                consecutive_failures=failures,
                next_attempt_after=next_attempt,
                now=result.finished_at,
            )
        except InfrastructureError:
            if not swallow:
                raise
            _log.warning("The reconciliation outcome could not be recorded")
        _log.info(
            "Reconciliation finished",
            status=result.status.value,
            reason=None if result.reason is None else result.reason.value,
            claims_checked=result.claims_checked,
            claims_changed=result.claims_changed,
            marked_missing=result.marked_missing,
            restored=result.restored,
            purged=result.purged_undecided + result.purged_decided,
            marked_stale=result.marked_stale,
            marked_outdated=result.marked_outdated,
            skipped_corrupt=result.skipped_corrupt,
            batches=result.batches,
            retries=result.retries,
        )
        return result
