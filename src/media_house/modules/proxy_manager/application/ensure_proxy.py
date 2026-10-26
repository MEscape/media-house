"""Use case: make sure a valid proxy of a profile exists for a source asset and is the one served.

    facts -> spec -> library fingerprint -> reuse? -> reserve -> encode -> validate -> register
          -> activate (fenced)

* The source is never modified; the proxy is a library derived asset registered as ``PREVIEW``.
* Identical requests are answered from the library: the derivation fingerprint covers the source
  content and every output-affecting value of the spec. A valid stored proxy costs no encoding
  and no write.
* Nothing is published unless an independent look at the finished file says it keeps its promises.
* The last known-good proxy stays active until a validated replacement is committed. A slot only
  accepts an activation from the newest REQUEST that finishes (fencing by attempt ``seq``).
* A library asset that already owns the derivation fingerprint but is unusable can only be
  replaced if this module provably created it; otherwise the request fails and nothing is deleted.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    AssetStatus,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.proxy_manager.application.commands import EnsureProxyCommand
from media_house.modules.proxy_manager.application.dto import ProxyDto
from media_house.modules.proxy_manager.application.inspection_facts import known_source_facts
from media_house.modules.proxy_manager.application.lookup import ProxyLookup
from media_house.modules.proxy_manager.application.maintain_proxies import release_pruned
from media_house.modules.proxy_manager.application.ports import (
    DiskSpace,
    EncodeRequest,
    MediaProbe,
    ProxyEncoder,
    ProxyStore,
    PublishOutcome,
    Scratch,
)
from media_house.modules.proxy_manager.application.provenance import ProxyProvenance
from media_house.modules.proxy_manager.domain.attempt import (
    DEFAULT_LEASE_SECONDS,
    Attempt,
    AttemptState,
    FailureKind,
)
from media_house.modules.proxy_manager.domain.errors import (
    ArtifactBeingRemoved,
    ArtifactUnrepairable,
    AttemptInFlight,
    InsufficientSpace,
    LeaseLost,
    OutputExceedsReservation,
    ProxyError,
    SharedAttemptFailed,
    Superseded,
    Unverifiable,
    ValidationFailed,
    WaitTimedOut,
)
from media_house.modules.proxy_manager.domain.profile import ProxyProfile, resolve_profile
from media_house.modules.proxy_manager.domain.records import ArtifactRecord, ArtifactState
from media_house.modules.proxy_manager.domain.source import SourceFacts
from media_house.modules.proxy_manager.domain.spec import (
    ResolvedSpec,
    reservation_bytes,
    resolve_spec,
)
from media_house.modules.proxy_manager.domain.validation import ValidationReport, validate
from media_house.modules.proxy_manager.domain.values import (
    PROXY_OPERATION,
    SPEC_VERSION,
    VALIDATOR_VERSION,
    Condition,
    Evidence,
    Verdict,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    Err,
    InfrastructureError,
    NotFoundError,
    Ok,
    OperationCancelledError,
    PersistenceError,
    Result,
    ValidationError,
)
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)

type ProxyManagerError = MediaError | ProxyError

DEFAULT_MARGIN_BYTES = 1024 * 1024 * 1024
#: How often a running attempt proves it is alive (well inside the lease).
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_POLL_SECONDS = 0.5
#: The longest one call waits for an identical request that is still running.
DEFAULT_WAIT_TIMEOUT_SECONDS = 3600.0
#: Waits one call may chain (a request that finishes can be followed by another identical one).
_MAX_WAITS = 5
_HEARTBEAT_JOIN_SECONDS = 5.0
_PERCENT = 100
#: Share of the progress bar the encode itself covers (it is nearly all of the time).
_ENCODE_START, _ENCODE_END = 10, 85
_NAME_LIMIT = 255
_MESSAGE_LIMIT = 500


class _Kind(StrEnum):
    GENERATE = "generate"  # nothing owns the derivation yet
    REPAIR = "repair"  # an unusable asset we created owns it: replace it
    REUSE = "reuse"  # a usable stored proxy: only (re)activate it
    ADOPT = "adopt"  # registered by a crashed attempt, never committed: verified, then adopted


@dataclass(frozen=True, slots=True)
class _Plan:
    kind: _Kind
    existing: MediaAssetDto | None = None
    record: ArtifactRecord | None = None
    report: ValidationReport | None = None
    #: Id of a record this attempt supersedes in the same transaction (the library asset may be
    #: gone already, but its record would collide with the new one's fingerprint).
    replaces: str | None = None

    @property
    def encodes(self) -> bool:
        return self.kind in (_Kind.GENERATE, _Kind.REPAIR)


@dataclass(slots=True)
class _Run:
    attempt: Attempt
    #: Set by the heartbeat when the store says this attempt was retired.
    lost: threading.Event = field(default_factory=threading.Event)


class EnsureProxy:
    """One source asset in, one served and verified proxy out; reuse when nothing changed."""

    def __init__(
        self,
        library: MediaLibrary,
        inspections: InspectionCatalog | None,
        probe: MediaProbe,
        encoder: ProxyEncoder,
        store: ProxyStore,
        disk: DiskSpace,
        scratch: Scratch,
        clock: Clock,
        *,
        margin_bytes: int = DEFAULT_MARGIN_BYTES,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        wait_timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    ) -> None:
        self._library = library
        self._inspections = inspections
        self._probe = probe
        self._encoder = encoder
        self._store = store
        self._disk = disk
        self._scratch = scratch
        self._clock = clock
        self._margin = margin_bytes
        self._lease = lease_seconds
        self._heartbeat = heartbeat_seconds
        self._poll = poll_seconds
        self._wait_timeout = wait_timeout_seconds
        self._lookup = ProxyLookup(library, store, disk)
        self._recovered = False
        self._recovery_lock = threading.Lock()

    # ----------------------------------------------------------------------------------------
    def recover(self) -> None:
        """Retire attempts of a process that stopped responding and sweep their scratch directories.

        An attempt of another process is dead only once its lease ran out; until then it is
        respected (several processes may share the data directory).

        Idempotent. Runs once per instance before the first request. A registered-but-never-
        committed proxy needs no sweep: the next identical request finds it by fingerprint,
        verifies it and adopts it (see ``_plan``).
        """
        with self._recovery_lock:
            if self._recovered:
                return
            ended = self._store.interrupt_foreign_attempts(
                now=self._clock.now(), lease_seconds=self._lease
            )
            swept = 0
            for attempt in self._store.attempts_with_scratch():
                if attempt.scratch_name is None:
                    continue
                try:
                    self._scratch.remove(attempt.scratch_name)
                    self._store.clear_scratch(attempt.id)
                    swept += 1
                except InfrastructureError:
                    _log.warning("Scratch directory not removed yet", attempt_id=attempt.id)
            self._recovered = True
            if ended or swept:
                _log.info("Proxy recovery", interrupted=len(ended), swept=swept)

    def execute(
        self, command: EnsureProxyCommand, ctx: JobContext
    ) -> Result[ProxyDto, ProxyManagerError]:
        started = time.perf_counter()
        self.recover()
        try:
            profile = resolve_profile(command.profile, command.overrides)
        except ProxyError as exc:
            return Err(exc)
        source = self._library.get(command.source_asset_id)
        if isinstance(source, Err):
            return source
        if source.value.media_type is not MediaType.VIDEO:
            return Err(
                ValidationError(
                    f"Asset {source.value.id} is {source.value.media_type.value}, not video",
                    field="source_asset_id",
                    user_message="Only video files can be given a proxy.",
                )
            )
        path = self._library.local_path(source.value.id)
        if isinstance(path, Err):
            return path
        try:
            result = self._ensure_waiting(source.value, path.value, profile, ctx)
        except ProxyError as exc:
            return Err(exc)
        if isinstance(result, Ok):
            _log.info(
                "Proxy ready",
                source_asset_id=source.value.id,
                profile=profile.name,
                reused=result.value.reused,
                verdict=result.value.verdict.value,
                evidence=result.value.evidence.value,
                seconds=round(time.perf_counter() - started, 1),
            )
        return result

    # ----------------------------------------------------------------------------------------
    def _ensure_waiting(
        self, asset: MediaAssetDto, source_path: Path, profile: ProxyProfile, ctx: JobContext
    ) -> Result[ProxyDto, ProxyManagerError]:
        """``_ensure``, but an identical request already running is waited for, not refused.

        When it succeeds (or is superseded) the request is evaluated again, so it is answered
        from the stored result; when it fails or is cancelled the waiter reports that failure
        instead of silently encoding the same thing again; when its owner stopped responding the
        waiter takes over.
        """
        waited_for = ""
        for _ in range(_MAX_WAITS):
            try:
                return self._ensure(asset, source_path, profile, ctx)
            except AttemptInFlight as busy:
                waited_for = busy.attempt_id
                finished = self._await(waited_for, ctx)
                if finished is not None and finished.state in (
                    AttemptState.FAILED,
                    AttemptState.CANCELLED,
                ):
                    raise SharedAttemptFailed(
                        finished.id, finished.state.value, finished.error_message or ""
                    ) from busy
        raise WaitTimedOut(waited_for, self._wait_timeout)

    def _await(self, attempt_id: str, ctx: JobContext) -> Attempt | None:
        """Block (cancellably) until the attempt ends; retire it if its owner stopped responding."""
        deadline = time.monotonic() + self._wait_timeout
        ctx.progress.report(1, _PERCENT, "Waiting for an identical request")
        while True:
            attempt = self._store.get_attempt(attempt_id)
            if attempt is None or attempt.state.terminal:
                return attempt
            if self._store.expire_lease(
                attempt_id, now=self._clock.now(), lease_seconds=self._lease
            ):
                continue
            if time.monotonic() >= deadline:
                raise WaitTimedOut(attempt_id, self._wait_timeout)
            if ctx.cancellation.wait(self._poll):
                ctx.raise_if_cancelled()

    def _ensure(
        self, asset: MediaAssetDto, source_path: Path, profile: ProxyProfile, ctx: JobContext
    ) -> Result[ProxyDto, ProxyManagerError]:
        ctx.progress.report(1, _PERCENT, "Reading the source")
        facts = known_source_facts(self._inspections, asset.id)
        if facts is None:
            facts = self._probe.probe_source(source_path, ctx.cancellation)
        spec = resolve_spec(profile, facts)
        config = spec.to_config()
        fingerprint = self._library.fingerprint(asset.id, PROXY_OPERATION, config, SPEC_VERSION)
        if isinstance(fingerprint, Err):
            return fingerprint
        wanted = fingerprint.value
        served = self._lookup.served(
            asset.id, profile.name, wanted, decoded=profile.full_decode_validation
        )
        if served is not None:
            _log.info("Proxy reused", asset_id=asset.id, profile=profile.name, cache_hit=True)
            return Ok(served)

        existing = self._library.find_derived_asset(asset.id, wanted)
        plan = self._plan(asset.id, profile, spec, facts, wanted, existing, ctx)
        run = self._begin(asset, source_path, profile, spec, facts, wanted, plan)
        try:
            with self._alive(run):
                result = self._drive(
                    run, plan, asset, source_path, profile, spec, facts, wanted, ctx
                )
            if isinstance(result, Err) and not run.attempt.state.terminal:
                self._end(run, FailureKind.PERMANENT, str(result.error))
            return result
        except OperationCancelledError:
            self._end(run, FailureKind.CANCELLED, "cancelled")
            raise
        except ProxyError as exc:
            transient = isinstance(exc, InsufficientSpace | ArtifactBeingRemoved | LeaseLost)
            self._end(run, FailureKind.TRANSIENT if transient else FailureKind.PERMANENT, str(exc))
            raise
        except Exception as exc:  # recorded on the attempt, then re-raised unchanged
            if self._retired(run):
                # whatever broke (its scratch directory may have been swept, its disk taken by the
                # request that replaced it) is a consequence of being retired, and says so
                raise LeaseLost(run.attempt.id) from exc
            self._end(run, FailureKind.TRANSIENT, self._explain(exc))
            raise
        finally:
            self._cleanup(run)

    # ----------------------------------------------------------------------------------------
    def _plan(
        self,
        source_id: str,
        profile: ProxyProfile,
        spec: ResolvedSpec,
        facts: SourceFacts,
        fingerprint: str,
        existing: MediaAssetDto | None,
        ctx: JobContext,
    ) -> _Plan:
        """What to do about a library asset that may already own this derivation."""
        if existing is None:
            stale = self._store.find_artifact(source_id, fingerprint)
            return _Plan(_Kind.GENERATE, replaces=stale.asset_id if stale else None)
        if existing.status is AssetStatus.ARCHIVED:
            raise ArtifactUnrepairable(existing.id, "it is archived; restore or delete it first")
        record = self._store.get_artifact(existing.id)
        if record is None and not self._store.has_attempt_for(source_id, fingerprint):
            raise ArtifactUnrepairable(existing.id, "it was not created by this module")
        if record is not None and record.state is ArtifactState.PRUNING:
            # a removal that stopped half way: finish it rather than fail until somebody prunes
            release_pruned(self._library, self._store, record)
            still = self._store.get_artifact(existing.id)
            if still is not None and still.state is ArtifactState.PRUNING:
                raise ArtifactBeingRemoved(existing.id)
            found = self._library.find_derived_asset(source_id, fingerprint)
            return self._plan(source_id, profile, spec, facts, fingerprint, found, ctx)
        if not self._lookup.file_ok(existing):
            return _Plan(_Kind.REPAIR, existing, record, replaces=existing.id)
        if record is not None and record.verdict.usable:
            trusted = record.validator_version == VALIDATOR_VERSION and (
                not profile.full_decode_validation or record.evidence is Evidence.DECODED
            )
            if trusted:
                return _Plan(_Kind.REUSE, existing, record)
            # an outdated verdict, or weaker evidence than asked for, is checked against the file
            report = self._check_stored(existing, spec, facts, ctx)
            if not report.verdict.usable:
                return _Plan(_Kind.REPAIR, existing, record, replaces=existing.id)
            refreshed = replace(
                record,
                verdict=report.verdict,
                evidence=report.evidence,
                validator_version=report.validator_version,
            )
            self._store.update_verdict(
                existing.id,
                report.verdict,
                report.evidence,
                report.validator_version,
                now=self._clock.now(),
            )
            return _Plan(_Kind.REUSE, existing, refreshed)
        if record is None:
            report = self._check_stored(existing, spec, facts, ctx)
            if report.verdict.usable:
                return _Plan(_Kind.ADOPT, existing, None, report)
        return _Plan(_Kind.REPAIR, existing, record, replaces=existing.id)

    def _check_stored(
        self, asset: MediaAssetDto, spec: ResolvedSpec, facts: SourceFacts, ctx: JobContext
    ) -> ValidationReport:
        """Judge the file the library actually serves, decoded to the end (rare, so thorough)."""
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            raise ArtifactUnrepairable(asset.id, "its file is not readable")
        output = self._probe.probe_output(path.value, ctx.cancellation, decode=True)
        return validate(
            spec, facts, output, reserved_bytes=reservation_bytes(spec, facts), require_decode=True
        )

    # ----------------------------------------------------------------------------------------
    def _begin(
        self,
        asset: MediaAssetDto,
        source_path: Path,
        profile: ProxyProfile,
        spec: ResolvedSpec,
        facts: SourceFacts,
        fingerprint: str,
        plan: _Plan,
    ) -> _Run:
        reserved, volume = 0, None
        free = 0
        if plan.encodes:
            volume = self._disk.volume_of(self._scratch.root)
            same_volume = volume == self._disk.volume_of(source_path)
            # encoding writes the file once, registering copies it: both on one volume = twice
            reserved = reservation_bytes(spec, facts) * (2 if same_volume else 1)
            free = self._disk.free_bytes(self._scratch.root)
        attempt = Attempt(
            id=new_id(),
            source_asset_id=asset.id,
            profile=profile.name,
            profile_version=profile.version,
            spec_fingerprint=fingerprint,
            owner_token=self._store.owner_token,
            created_at=self._clock.now(),
            heartbeat_at=self._clock.now(),
            reserved_bytes=reserved,
            reserved_volume=volume,
            # recorded BEFORE anything is deleted, so a crash can finish or report the repair
            replaces_asset_id=plan.replaces,
        )
        return _Run(
            self._store.begin_attempt(
                attempt,
                free_bytes=free,
                margin_bytes=self._margin,
                now=self._clock.now(),
                lease_seconds=self._lease,
            )
        )

    def _move(self, run: _Run, to: AttemptState, **changes: object) -> None:
        self._check_lost(run)
        now = self._clock.now()
        run.attempt = run.attempt.advance(to, now=now, heartbeat_at=now, **changes)
        self._store.save_attempt(run.attempt)

    def _retired(self, run: _Run) -> bool:
        """Has another request retired this attempt (its lease ran out) behind its back?"""
        if run.lost.is_set():
            return True
        try:
            stored = self._store.get_attempt(run.attempt.id)
        except PersistenceError:
            return False
        return (
            stored is not None and stored.state.terminal and stored.state is not run.attempt.state
        )

    @staticmethod
    def _check_lost(run: _Run) -> None:
        """Stop working for an attempt another request has retired."""
        if run.lost.is_set():
            raise LeaseLost(run.attempt.id)

    @contextmanager
    def _alive(self, run: _Run) -> Iterator[None]:
        """Prove the attempt is alive on a timer, whatever it is busy with.

        Encode progress alone is not enough: validating by decoding, or copying a big file into the
        library, can take longer than the lease without reporting anything.
        """
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(self._heartbeat):
                try:
                    if not self._store.touch_attempt(run.attempt.id, now=self._clock.now()):
                        run.lost.set()
                        return
                except PersistenceError:
                    _log.warning("Heartbeat not recorded", attempt_id=run.attempt.id)

        thread = threading.Thread(target=beat, name="proxy-heartbeat", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=_HEARTBEAT_JOIN_SECONDS)

    def _end(self, run: _Run, kind: FailureKind, message: str) -> None:
        if run.attempt.state.terminal:
            return
        run.attempt = run.attempt.fail(kind, message, now=self._clock.now())
        try:
            self._store.save_attempt(run.attempt)
        except LeaseLost:
            _log.warning("Attempt was retired before it could end", attempt_id=run.attempt.id)
        except PersistenceError:
            _log.exception("Could not record the end of an attempt", attempt_id=run.attempt.id)

    def _cleanup(self, run: _Run) -> None:
        name = run.attempt.scratch_name
        if name is None:
            return
        try:
            self._scratch.remove(name)
            self._store.clear_scratch(run.attempt.id)
        except (InfrastructureError, PersistenceError):
            # the owning attempt row keeps the name, so the next recovery sweeps it
            _log.warning("Scratch directory not removed", attempt_id=run.attempt.id)

    def _explain(self, exc: Exception) -> str:
        text = f"{type(exc).__name__}: {exc}"[:_MESSAGE_LIMIT]
        try:
            if self._disk.free_bytes(self._scratch.root) < self._margin:
                text += " (free disk space is low)"
        except InfrastructureError:
            pass
        return text

    # ----------------------------------------------------------------------------------------
    def _drive(
        self,
        run: _Run,
        plan: _Plan,
        asset: MediaAssetDto,
        source_path: Path,
        profile: ProxyProfile,
        spec: ResolvedSpec,
        facts: SourceFacts,
        fingerprint: str,
        ctx: JobContext,
    ) -> Result[ProxyDto, ProxyManagerError]:
        if plan.kind is _Kind.REUSE and plan.existing and plan.record:
            self._move(run, AttemptState.PUBLISHING)
            return self._publish(run, plan.existing, plan.record)
        if plan.kind is _Kind.ADOPT and plan.existing and plan.report:
            self._move(run, AttemptState.VALIDATING)
            self._move(run, AttemptState.PUBLISHING)
            record = self._record(
                run, plan.existing.id, asset.id, profile, fingerprint, plan.report
            )
            return self._publish(run, plan.existing, record)

        reserved = reservation_bytes(spec, facts)
        self._move(run, AttemptState.GENERATING, scratch_name=f"proxy-{run.attempt.id}")
        assert run.attempt.scratch_name is not None  # noqa: S101 - set on the line above
        destination = self._scratch.create(run.attempt.scratch_name) / f"proxy.{spec.container}"
        ctx.raise_if_cancelled()

        def encoding(done: float) -> None:
            ctx.progress.report(
                _ENCODE_START + int(done * (_ENCODE_END - _ENCODE_START)),
                _PERCENT,
                f"Encoding {int(done * _PERCENT)}%",
            )

        outcome = self._encoder.encode(
            EncodeRequest(source_path, destination, spec, facts, reserved),
            ctx.cancellation,
            encoding,
        )
        self._move(
            run, AttemptState.VALIDATING, encoder=outcome.encoder, fallback=outcome.fallback,
            toolchain=self._encoder.identity,
        )  # fmt: skip
        ctx.progress.report(90, _PERCENT, "Verifying the proxy")
        decode = profile.full_decode_validation
        output = self._probe.probe_output(destination, ctx.cancellation, decode=decode)
        if output.size_bytes > reserved:
            raise OutputExceedsReservation(output.size_bytes, reserved)
        report = validate(spec, facts, output, reserved_bytes=reserved, require_decode=decode)
        if report.verdict is Verdict.INVALID:
            raise ValidationFailed(report.summary())
        if report.verdict is Verdict.UNVERIFIABLE:
            raise Unverifiable(report.summary())

        self._move(run, AttemptState.PUBLISHING)
        ctx.raise_if_cancelled()  # the last point at which a cancel stops the request cleanly
        self._require_library_space(source_path, output.size_bytes)
        self._check_lost(run)  # a retired attempt must not delete or register anything
        if plan.kind is _Kind.REPAIR and plan.existing is not None:
            self._release_derivation(plan.existing)
        ctx.progress.report(95, _PERCENT, "Saving the proxy")
        provenance = ProxyProvenance(
            source_asset_id=asset.id, profile=profile.name, profile_version=profile.version,
            spec_fingerprint=fingerprint, spec=spec.to_config(), verdict=report.verdict,
            evidence=report.evidence, validator_version=report.validator_version,
            transformations=report.transformations, encoder=outcome.encoder,
            toolchain=self._encoder.identity, facts_origin=facts.origin,
            attempt_id=run.attempt.id, warnings=report.warnings,
        )  # fmt: skip
        registered = self._library.register_derived(
            asset.id,
            destination,
            operation=PROXY_OPERATION,
            config=spec.to_config(),
            version=SPEC_VERSION,
            display_name=f"{asset.display_name} (proxy {profile.name})"[:_NAME_LIMIT],
            metadata=provenance.to_metadata(report),
            role=AssetRole.PREVIEW,
        )
        if isinstance(registered, Err):
            return registered
        stored = registered.value.asset
        replaced_in_place = plan.existing is not None and stored.id == plan.existing.id
        if not registered.value.created or replaced_in_place:
            # The library did not take OUR file (it already owned the derivation). Never assume it
            # did: judge the bytes it actually serves, decoded to the end.
            report = self._check_stored(stored, spec, facts, ctx)
            if not report.verdict.usable:
                raise ArtifactUnrepairable(
                    stored.id, f"the stored file fails validation: {report.summary()}"
                )
        record = self._record(run, stored.id, asset.id, profile, fingerprint, report)
        return self._publish(run, stored, record)

    def _record(
        self,
        run: _Run,
        asset_id: str,
        source_id: str,
        profile: ProxyProfile,
        fingerprint: str,
        report: ValidationReport,
    ) -> ArtifactRecord:
        return ArtifactRecord(
            asset_id=asset_id,
            source_asset_id=source_id,
            profile=profile.name,
            spec_fingerprint=fingerprint,
            attempt_id=run.attempt.id,
            verdict=report.verdict,
            evidence=report.evidence,
            validator_version=report.validator_version,
            validated_at=self._clock.now(),
        )

    def _publish(
        self, run: _Run, stored: MediaAssetDto, record: ArtifactRecord
    ) -> Result[ProxyDto, ProxyManagerError]:
        done, outcome = self._store.publish(run.attempt, record, now=self._clock.now())
        run.attempt = done
        if outcome is PublishOutcome.SUPERSEDED:
            raise Superseded(done.id)
        if outcome is PublishOutcome.PRUNING:
            raise ArtifactBeingRemoved(stored.id)
        return Ok(self._lookup.dto(stored, record, Condition.READY, reused=not run.attempt.encoder))

    def _release_derivation(self, unusable: MediaAssetDto) -> None:
        """Delete the unusable asset that owns the derivation so the fresh one can take its place.

        Only called for assets this module provably created (``_plan``). ``include_derived=False``
        refuses when anything else was derived from it, so nobody's data is cascaded away.
        """
        deleted = self._library.delete(unusable.id, include_derived=False)
        if isinstance(deleted, Err) and not isinstance(deleted.error, NotFoundError):
            raise ArtifactUnrepairable(unusable.id, "other assets were derived from it")

    def _require_library_space(self, source_path: Path, size: int) -> None:
        free = self._disk.free_bytes(source_path)
        if free < size + self._margin:
            raise InsufficientSpace(size + self._margin, free)
