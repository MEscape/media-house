"""Use case: propose claims from facts media inspection already recorded (caller-driven, resumable).

``analyze`` looks at ONE asset (the unit of work, also used by the pipeline); ``detect`` walks the
library's originals in ``(created_at, id)`` order under a budget and analyses each, remembering
where it stopped. Nothing is probed: a missing inspection makes the asset ``unavailable`` and
touches no claim. Proposals go through ``ProposeRelationship``, so every rule about decided claims
(never revived, never overwritten) is the one claims already obey, and a detector cannot confirm,
reject or delete anything.

Two cursors are kept in ``scan_state``. The FEED cursor only moves forward, so a job after new
imports sees just the new assets. A periodic full SWEEP restarts from the beginning to find what
the feed cannot: a late commit with an older timestamp, a restored asset, a changed inspection.
The feed is a latency optimisation; correctness is the sweep plus idempotent analysis.

An asset's analysis order makes the pair discoverable from either end, even when both are
analysed at the same moment: facts are committed BEFORE candidates are searched, and the mark (the
record that the work is done) is written LAST.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.commands import (
    EvidenceSubmission,
    ProposeRelationshipCommand,
)
from media_house.modules.asset_relationships.application.inspection_facts import inspection_facts
from media_house.modules.asset_relationships.application.ports import (
    InspectionSource,
    LibrarySearch,
)
from media_house.modules.asset_relationships.application.propose_relationship import (
    ProposeRelationship,
)
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.detection import (
    DETECTORS,
    Detection,
    Detector,
    InspectionFacts,
    Lookup,
    Observed,
)
from media_house.modules.asset_relationships.domain.detection_store import (
    DetectionStore,
    Mark,
    MarkOutcome,
)
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.repository import (
    RelationshipCriteria,
    RelationshipRepository,
)
from media_house.modules.asset_relationships.domain.values import (
    RelationshipOrigin,
    RelationshipStatus,
)
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    AssetStatus,
    MediaAssetDto,
    MediaQuery,
    MediaType,
    SortField,
    SortOrder,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    Err,
    InfrastructureError,
    InvariantViolation,
    Ok,
    OperationCancelledError,
    Result,
    ValidationError,
)
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_FEED = "detect_feed_cursor"
_SWEEP = "detect_sweep_cursor"
_LAST_FULL = "detect_last_full_sweep"
#: A failure happened before the first complete pass: that pass must not count as a sweep.
_DIRTY = "detect_first_pass_dirty"
_LAST_RUN = "detect_last_run"
_PAGE = MediaQuery.MAX_PAGE_SIZE
_CLAIM_PAGE = 200
#: This many assets failing in a row means the store or library is down, not one bad asset.
_OUTAGE_AFTER = 3


class DetectStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class DetectReason(StrEnum):
    BUDGET = "budget"
    TIE_GROUP_TOO_LARGE = "tie_group_too_large"
    #: Some assets could not be analysed; the rest were. They are retried by the next full sweep.
    ASSET_FAILURES = "asset_failures"
    CANCELLED = "cancelled"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class DetectionSettings:
    """How a job is bounded. All provisional and unmeasured on a real library."""

    #: Assets per cursor commit.
    batch_assets: int = 100
    #: The most assets one job analyses; a longer walk continues in the next job.
    max_assets_per_job: int = 1_000
    #: Minimum time between the end of one full sweep and the start of the next.
    full_sweep_interval: timedelta = timedelta(hours=24)
    #: Library pages of 200 read to get past assets sharing one creation microsecond.
    max_tie_pages: int = 10
    #: Candidates proposed per asset and detector; beyond it the outcome is ``capped`` and
    #: ``DetectorResult.capped`` counts AT LEAST one more (the search reads one candidate past it).
    max_candidates_per_asset: int = 25

    def __post_init__(self) -> None:
        if min(self.batch_assets, self.max_assets_per_job, self.max_candidates_per_asset) < 1:
            raise InvariantViolation("Detection limits must be at least 1")
        if self.max_tie_pages < 1:
            raise InvariantViolation("max_tie_pages must be at least 1")


@dataclass(frozen=True, slots=True)
class AnalyzeAssetCommand:
    asset_id: str
    #: Detector ids to run. Nothing runs unless asked: there is no hidden "enabled" setting.
    detectors: frozenset[str]


@dataclass(frozen=True, slots=True)
class DetectCommand:
    detectors: frozenset[str]
    #: Start a full sweep now instead of waiting for ``full_sweep_interval``.
    full: bool = False


@dataclass(frozen=True, slots=True)
class DetectorResult:
    detector: str
    outcome: MarkOutcome
    #: The asset had been analysed on these inputs and this detector version already.
    unchanged: bool = False
    proposed: int = 0
    capped: int = 0
    made_stale: int = 0
    flagged_outdated: int = 0
    #: Candidates dropped because the other asset or the domain refused the claim.
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class AssetAnalysis:
    asset_id: str
    #: ``False``: there is no stored inspection to read (nothing was probed).
    inspection_available: bool
    results: tuple[DetectorResult, ...]


@dataclass(frozen=True, slots=True)
class DetectOutcome:
    status: DetectStatus
    reason: DetectReason | None
    assets_visited: int
    analyzed: int
    unchanged: int
    unavailable: int
    proposed: int
    capped: int
    made_stale: int
    flagged_outdated: int
    sweep_completed: bool
    feed_cursor: str | None
    sweep_cursor: str | None
    started_at: datetime
    finished_at: datetime
    #: Assets whose analysis failed (left unmarked, so a later sweep tries them again).
    failed: int = 0
    first_error: str | None = None

    def to_json(self) -> str:
        data = asdict(self)
        data["status"] = self.status.value
        data["reason"] = None if self.reason is None else self.reason.value
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat()
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "DetectOutcome":
        data = json.loads(text)
        reason = data.pop("reason")
        return cls(
            status=DetectStatus(data.pop("status")),
            reason=None if reason is None else DetectReason(reason),
            started_at=datetime.fromisoformat(data.pop("started_at")),
            finished_at=datetime.fromisoformat(data.pop("finished_at")),
            **data,
        )


@dataclass
class _Counters:
    visited: int = 0
    analyzed: int = 0
    unchanged: int = 0
    unavailable: int = 0
    proposed: int = 0
    capped: int = 0
    made_stale: int = 0
    flagged_outdated: int = 0
    failed: int = 0

    def count(self, analysis: AssetAnalysis) -> None:
        self.visited += 1
        if not analysis.inspection_available:
            self.unavailable += 1
            return
        if analysis.results and all(r.unchanged for r in analysis.results):
            self.unchanged += 1
        else:
            self.analyzed += 1
        for r in analysis.results:
            self.proposed += r.proposed
            self.capped += r.capped
            self.made_stale += r.made_stale
            self.flagged_outdated += r.flagged_outdated


@dataclass
class _Job:
    started: datetime
    counters: _Counters = field(default_factory=_Counters)
    budget: int = 0
    reason: DetectReason | None = None
    superseded: bool = False
    sweep_completed: bool = False
    first_error: str | None = None

    @property
    def stopped_cleanly(self) -> bool:
        return self.reason is None and not self.superseded


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _key(asset: MediaAssetDto) -> str:
    return f"{_stamp(asset.created_at)}|{asset.id}"


def _parse(key: str | None) -> tuple[datetime, str] | None:
    if not key:
        return None
    stamp, _, asset_id = key.partition("|")
    try:
        return datetime.fromisoformat(stamp), asset_id
    except ValueError:
        return None  # a corrupt cursor restarts the walk: costs time, never correctness


class DetectRelationships:
    """Implements ``RelationshipDetection``."""

    def __init__(
        self,
        store: DetectionStore,
        inspections: InspectionSource,
        library: LibrarySearch,
        propose: ProposeRelationship,
        repository: RelationshipRepository,
        clock: Clock,
        settings: DetectionSettings | None = None,
        detectors: Mapping[str, Detector] = DETECTORS,
    ) -> None:
        self._store = store
        self._inspections = inspections
        self._library = library
        self._propose = propose
        self._repository = repository
        self._clock = clock
        self._settings = settings or DetectionSettings()
        self._detectors = detectors

    # ------------------------------------------------------------------ public
    def detector_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._detectors))

    def last_run(self) -> DetectOutcome | None:
        text = self._store.get_state(_LAST_RUN)
        if text is None:
            return None
        try:
            return DetectOutcome.from_json(text)
        except (ValueError, KeyError, TypeError) as exc:
            _log.warning("The recorded detection outcome is unreadable", reason=str(exc))
            return None

    def analyze(
        self, command: AnalyzeAssetCommand, ctx: JobContext
    ) -> Result[AssetAnalysis, ValidationError]:
        chosen = self._choose(command.detectors)
        if isinstance(chosen, Err):
            return chosen
        ctx.raise_if_cancelled()
        return Ok(self._analyze(command.asset_id, chosen.value))

    def detect(
        self, command: DetectCommand, ctx: JobContext
    ) -> Result[DetectOutcome, ValidationError]:
        chosen = self._choose(command.detectors)
        if isinstance(chosen, Err):
            return chosen
        job = _Job(self._clock.now(), budget=self._settings.max_assets_per_job)
        try:
            self._run(job, chosen.value, command.full, ctx)
        except OperationCancelledError:
            self._finish(job, DetectStatus.INTERRUPTED, DetectReason.CANCELLED, swallow=True)
            raise
        except Exception:
            self._finish(job, DetectStatus.FAILED, DetectReason.UNEXPECTED, swallow=True)
            raise
        if job.superseded:
            return Ok(self._finish(job, DetectStatus.SUPERSEDED, None, swallow=False))
        if job.reason is not None:
            return Ok(self._finish(job, DetectStatus.PARTIAL, job.reason, swallow=False))
        return Ok(self._finish(job, DetectStatus.COMPLETED, None, swallow=False))

    # ------------------------------------------------------------------ the walk
    def _run(self, job: _Job, detectors: tuple[Detector, ...], full: bool, ctx: JobContext) -> None:
        self._walk(_FEED, job, detectors, ctx)
        if not job.stopped_cleanly:
            return
        if (
            self._store.get_state(_LAST_FULL) is None
            and self._store.get_state(_SWEEP) is None
            and self._store.get_state(_DIRTY) is None
        ):
            # The feed started at the first asset and has now reached the end: that was the
            # first complete pass, so a sweep right behind it would only visit everything twice.
            self._finish_sweep(job)
            return
        if self._sweep_due(job.started, full):
            if self._store.get_state(_SWEEP) is None and not self._store.cas_state(
                _SWEEP, None, ""
            ):
                job.superseded = True
                return
            self._walk(_SWEEP, job, detectors, ctx)
            if job.stopped_cleanly:
                self._finish_sweep(job)

    def _finish_sweep(self, job: _Job) -> None:
        self._store.cas_state(_LAST_FULL, self._store.get_state(_LAST_FULL), _stamp(job.started))
        self._store.cas_state(_SWEEP, self._store.get_state(_SWEEP), None)
        self._store.cas_state(_DIRTY, self._store.get_state(_DIRTY), None)
        job.sweep_completed = True

    def _sweep_due(self, now: datetime, full: bool) -> bool:
        if self._store.get_state(_SWEEP) is not None or full:
            return True  # a sweep in progress always continues
        last = self._store.get_state(_LAST_FULL)
        if last is None:
            return True
        try:
            return now - datetime.fromisoformat(last) >= self._settings.full_sweep_interval
        except ValueError:
            return True

    def _walk(
        self, cursor_key: str, job: _Job, detectors: tuple[Detector, ...], ctx: JobContext
    ) -> None:
        self._walk_batches(cursor_key, job, detectors, ctx)
        if job.counters.failed:
            if self._store.get_state(_LAST_FULL) is None:
                self._store.cas_state(_DIRTY, self._store.get_state(_DIRTY), "1")
            if job.reason is None and not job.superseded:
                job.reason = DetectReason.ASSET_FAILURES

    def _walk_batches(
        self, cursor_key: str, job: _Job, detectors: tuple[Detector, ...], ctx: JobContext
    ) -> None:
        in_a_row = 0
        while job.budget > 0:
            ctx.raise_if_cancelled()
            stored = self._store.get_state(cursor_key)
            batch, blocked = self._next_batch(
                _parse(stored), min(self._settings.batch_assets, job.budget)
            )
            if not batch:
                if blocked:
                    _log.warning("Detection stopped: too many assets share one creation time")
                    job.reason = DetectReason.TIE_GROUP_TOO_LARGE
                return
            for asset in batch:
                ctx.raise_if_cancelled()
                try:
                    analysis = self._analyze(asset.id, detectors)
                except InfrastructureError as exc:
                    # One asset that cannot be analysed must not block the walk for good. It is
                    # left without a mark, so a later sweep tries it again; but several in a row
                    # mean an outage, and then the job fails with the cursor where it was.
                    in_a_row += 1
                    job.counters.failed += 1
                    job.first_error = job.first_error or f"{asset.id}: {exc}"
                    _log.warning("Detection skipped an asset", asset_id=asset.id, reason=str(exc))
                    if in_a_row >= _OUTAGE_AFTER:
                        raise
                    continue
                in_a_row = 0
                job.counters.count(analysis)
            job.budget -= len(batch)
            if not self._store.cas_state(cursor_key, stored, _key(batch[-1])):
                job.superseded = True
                return
        job.reason = DetectReason.BUDGET

    def _next_batch(
        self, cursor: tuple[datetime, str] | None, limit: int
    ) -> tuple[list[MediaAssetDto], bool]:
        """The next ``limit`` eligible originals after ``cursor``; and whether ties blocked it.

        ``created_from`` is inclusive and the library breaks ties by id, so dropping everything at
        or before the cursor leaves exactly the next rows, as long as the tie group at the cursor's
        timestamp is read through (at most ``max_tie_pages`` pages).
        """
        after = None if cursor is None else (_stamp(cursor[0]), cursor[1])
        out: list[MediaAssetDto] = []
        page = 0
        while len(out) < limit and page < self._settings.max_tie_pages + limit // _PAGE + 1:
            page += 1
            result = self._library.search(
                MediaQuery(
                    media_types=frozenset({MediaType.VIDEO, MediaType.AUDIO}),
                    roles=frozenset({AssetRole.ORIGINAL}),
                    statuses=frozenset({AssetStatus.ACTIVE}),
                    created_from=None if cursor is None else cursor[0],
                    sort_by=SortField.CREATED_AT,
                    sort_order=SortOrder.ASC,
                    page=page,
                    page_size=_PAGE,
                )
            )
            for asset in result.items:
                if after is not None and (_stamp(asset.created_at), asset.id) <= after:
                    continue
                out.append(asset)
                if len(out) == limit:
                    break
            if not result.has_next:
                return out, False
        return out, not out

    # ------------------------------------------------------------------ one asset
    def _choose(self, ids: Iterable[str]) -> Result[tuple[Detector, ...], ValidationError]:
        wanted = sorted(set(ids))
        unknown = [d for d in wanted if d not in self._detectors]
        if not wanted or unknown:
            known = ", ".join(sorted(self._detectors))
            return Err(
                ValidationError(
                    f"Unknown or missing detectors: {unknown or 'none given'}",
                    field="detectors",
                    user_message=f"Choose one or more detectors from: {known}.",
                )
            )
        return Ok(tuple(self._detectors[d] for d in wanted))

    def _analyze(self, asset_id: str, detectors: tuple[Detector, ...]) -> AssetAnalysis:
        found = self._inspections.find(asset_id)
        now = _stamp(self._clock.now())
        if found is None:
            # Not "none": no inspection says nothing about relationships. Looked at again later.
            for d in detectors:
                previous = self._store.get_mark(asset_id, d.id)
                if previous is None or previous.outcome is not MarkOutcome.UNAVAILABLE:
                    self._store.put_mark(
                        Mark(asset_id, d.id, d.version, "", MarkOutcome.UNAVAILABLE, 0),
                        analyzed_at=now,
                    )
            return AssetAnalysis(
                asset_id,
                False,
                tuple(DetectorResult(d.id, MarkOutcome.UNAVAILABLE) for d in detectors),
            )
        facts = inspection_facts(asset_id, found)
        return AssetAnalysis(
            asset_id, True, tuple(self._run_detector(d, facts, now) for d in detectors)
        )

    def _run_detector(self, d: Detector, facts: InspectionFacts, now: str) -> DetectorResult:
        mark = self._store.get_mark(facts.asset_id, d.id)
        if (
            mark is not None
            and mark.detector_version == d.version
            and mark.input_identity == facts.identity
            and mark.outcome is not MarkOutcome.UNAVAILABLE
        ):
            return DetectorResult(d.id, mark.outcome, unchanged=True)
        fact = d.fact_of(facts)
        self._store.replace_fact(
            facts.asset_id, d.id, fact, identity=facts.identity, inspection_id=facts.inspection_id
        )
        proposed_ids: set[str] = set()
        proposed = skipped = capped = 0
        if fact is not None:
            own = Observed(facts.asset_id, facts.identity, facts.inspection_id, fact)
            candidates = self._candidates(d, own)
            capped = max(0, len(candidates) - self._settings.max_candidates_per_asset)
            for other in candidates[: self._settings.max_candidates_per_asset]:
                detection = d.match(own, other)
                if detection is None:
                    continue
                relationship_id = self._submit(d, detection, own, other)
                if relationship_id is None:
                    skipped += 1
                else:
                    proposed += 1
                    proposed_ids.add(relationship_id)
        stale, outdated = self._supersede(d, facts, proposed_ids)
        outcome = (
            MarkOutcome.CAPPED if capped else MarkOutcome.FOUND if proposed else MarkOutcome.NONE
        )
        self._store.put_mark(
            Mark(facts.asset_id, d.id, d.version, facts.identity, outcome, proposed),
            analyzed_at=now,
        )
        return DetectorResult(d.id, outcome, False, proposed, capped, stale, outdated, skipped)

    def _candidates(self, d: Detector, own: Observed) -> list[Observed]:
        limit = self._settings.max_candidates_per_asset + 1
        fact = own.fact
        if d.lookup is Lookup.OVERLAP and fact.start_us is not None and fact.end_us is not None:
            return list(
                self._store.overlapping(
                    d.id, fact.start_us, fact.end_us, exclude=own.asset_id, limit=limit
                )
            )
        if d.lookup is Lookup.GROUP:
            return list(
                self._store.in_group(d.id, fact.group_key, exclude=own.asset_id, limit=limit)
            )
        return []

    def _submit(
        self, d: Detector, detection: Detection, own: Observed, other: Observed
    ) -> str | None:
        result = self._propose.execute(
            ProposeRelationshipCommand(
                relationship_type=detection.relationship_type,
                asset_a=detection.asset_a,
                asset_b=detection.asset_b,
                origin=RelationshipOrigin.DETECTOR,
                evidence=tuple(
                    EvidenceSubmission(e.kind, d.id, d.version, dict(e.detail), e.offset)
                    for e in detection.evidence
                ),
                basis=Basis(
                    d.id,
                    d.version,
                    {own.asset_id: own.identity, other.asset_id: other.identity},
                ),
                a_interval=detection.a_interval,
                b_interval=detection.b_interval,
                offset=detection.offset,
            )
        )
        if isinstance(result, Err):
            # The other asset left the library, or the domain refused the claim: not a failure of
            # this asset's analysis.
            _log.info("A detected pair was not proposed", reason=str(result.error))
            return None
        return result.value.relationship_id

    def _supersede(
        self, d: Detector, facts: InspectionFacts, proposed: set[str]
    ) -> tuple[int, int]:
        """Claims of this detector on this asset that the new analysis no longer proposes.

        An undecided one becomes ``STALE`` and a decided one gets ``outdated_since`` ONLY when this
        asset's inputs changed since the claim was made; otherwise the claim still holds on what it
        was built from. Nothing is ever deleted, and no decision is touched.
        """
        stale = outdated = 0
        after: str | None = None
        now = self._clock.now()
        while True:
            claims = self._repository.find(
                RelationshipCriteria(
                    facts.asset_id,
                    types=frozenset({d.relationship_type}),
                    origins=frozenset({RelationshipOrigin.DETECTOR}),
                    after=after,
                    limit=_CLAIM_PAGE,
                )
            )
            if not claims:
                return stale, outdated
            after = claims[-1].id.value
            for claim in claims:
                if (
                    claim.id.value in proposed
                    or claim.basis.detector != d.id
                    or claim.basis.inputs.get(facts.asset_id) == facts.identity
                ):
                    continue
                changed = self._flag(claim, now)
                stale += changed == "stale"
                outdated += changed == "outdated"

    def _flag(self, claim: AssetRelationship, now: datetime) -> str | None:
        result: list[str] = []

        def change(existing: AssetRelationship | None) -> AssetRelationship | None:
            if existing is None:
                return None
            if existing.status is RelationshipStatus.PROPOSED:
                existing.mark_stale(now=now)
                result.append("stale")
                return existing
            if existing.decided and existing.mark_outdated(now=now):
                result.append("outdated")
                return existing
            return None

        self._repository.update(claim.id, change)
        return result[0] if result else None

    # ------------------------------------------------------------------ bookkeeping
    def _finish(
        self, job: _Job, status: DetectStatus, reason: DetectReason | None, *, swallow: bool
    ) -> DetectOutcome:
        c = job.counters
        outcome = DetectOutcome(
            status,
            reason,
            c.visited,
            c.analyzed,
            c.unchanged,
            c.unavailable,
            c.proposed,
            c.capped,
            c.made_stale,
            c.flagged_outdated,
            job.sweep_completed,
            self._store.get_state(_FEED),
            self._store.get_state(_SWEEP),
            job.started,
            self._clock.now(),
            c.failed,
            job.first_error,
        )
        try:
            self._store.cas_state(_LAST_RUN, self._store.get_state(_LAST_RUN), outcome.to_json())
        except Exception:
            if not swallow:
                raise
            _log.warning("The detection outcome could not be recorded", exc_info=True)
        _log.info(
            "Relationship detection finished",
            status=status.value,
            visited=c.visited,
            proposed=c.proposed,
            capped=c.capped,
        )
        return outcome
