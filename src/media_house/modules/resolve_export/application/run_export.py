"""Running an authorized final export, checking the file, and delivering it.

The export starts only when the review confirms - at that moment, for exactly this timeline version
and this output - that a person authorized it; the render manager asks again before it renders. The
file that comes out is then checked by ``export_validation`` (not by this module and not by the
render's own success): a failed validation stops delivery, a validation that could not decide waits
for a person, and only a validated file can be delivered, on a person's approval when the project
says so. Delivery copies the file into the delivery folder under a new name; it never replaces an
earlier master and never uploads or publishes anything.
"""

from dataclasses import dataclass, replace

from media_house.core.application.ports import Clock
from media_house.core.domain import json_codec
from media_house.modules.export_validation.application.contracts import (
    ExportValidator,
    IntentionalSpan,
    ValidateExportCommand,
    ValidationOutcome,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.render_manager.application.contracts import (
    RenderAuthorization,
    RenderCatalog,
    RenderManager,
    RenderOutcome,
    RenderRequest,
    RenderType,
)
from media_house.modules.resolve_export.application.events import (
    DeliveryReady,
    ExportCompleted,
    ExportStarted,
)
from media_house.modules.resolve_export.application.inputs import ExportInputs, InputsReader
from media_house.modules.resolve_export.application.ports import DeliveryStore
from media_house.modules.resolve_export.application.store import ExportStore
from media_house.modules.resolve_export.domain.errors import (
    DeliveryRefused,
    ExportRefused,
    NotReadyForExport,
)
from media_house.modules.resolve_export.domain.naming import delivery_name
from media_house.modules.resolve_export.domain.report import ExportReport
from media_house.modules.resolve_export.domain.values import DeliveryStatus, ExportStatus
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ApprovalKind,
    ReviewFailure,
    ReviewSession,
    ReviewSessions,
    ReviewState,
    TimelineRef,
    ValidationStatus,
    Verdict,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)
_SYSTEM = Actor.system("resolve_export")

type ExportFailure = (
    NotReadyForExport | ExportRefused | DeliveryRefused | MediaError | ReviewFailure
)


@dataclass(frozen=True, slots=True)
class RunExportCommand:
    review_session_id: str
    authorization_id: str
    actor: Actor
    profile_id: str
    #: The version the person authorized. Another version is refused.
    timeline: TimelineRef
    #: Black frames, silence or freezes the plan declared on purpose (not errors in the file).
    intentional: tuple[IntentionalSpan, ...] = ()
    #: The delivery profile the file is checked against (default: the output profile's id).
    delivery_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExportResult:
    report: ExportReport
    asset: MediaAssetDto
    #: The file passed its checks (PASS or WARNING) and the review holds it as validated.
    technically_ready: bool


class ExportRunner:
    def __init__(
        self,
        library: MediaLibrary,
        inputs: InputsReader,
        sessions: ReviewSessions,
        renders: RenderManager,
        render_history: RenderCatalog,
        validator: ExportValidator,
        store: ExportStore,
        delivery: DeliveryStore,
        events: EventPublisher,
        clock: Clock,
    ) -> None:
        self._library = library
        self._inputs = inputs
        self._sessions = sessions
        self._renders = renders
        self._render_history = render_history
        self._validator = validator
        self._store = store
        self._delivery = delivery
        self._events = events
        self._clock = clock

    # --- the export --------------------------------------------------------------------------
    def execute(
        self, command: RunExportCommand, ctx: JobContext
    ) -> Result[ExportResult, ExportFailure]:
        read = self._inputs.read(command.review_session_id, command.profile_id)
        if isinstance(read, Err):
            return read
        data = read.value
        if not data.config_hash:
            return Err(
                NotReadyForExport(
                    list(data.facts.profile_problems) or ["The output profile cannot be used."]
                )
            )
        verified = self._sessions.verify_export(
            command.review_session_id,
            command.authorization_id,
            timeline=command.timeline,
            export_profile_id=command.profile_id,
            output_config_hash=data.config_hash,
        )
        if isinstance(verified, Err):
            return verified
        authorization = verified.value
        anchor = self._library.get(data.session.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        export_id = new_id()
        begun = self._sessions.begin_export(
            command.review_session_id, command.authorization_id, export_id, actor=command.actor
        )
        if isinstance(begun, Err):
            return begun
        report = self._initial(data, export_id, authorization.authorized_by, command)
        self._events.publish(
            ExportStarted(
                occurred_at=self._clock.now(),
                export_id=export_id,
                review_session_id=command.review_session_id,
                timeline_version=report.timeline_version,
                profile_id=command.profile_id,
            )
        )
        rendered = self._render(data, command, ctx)
        if isinstance(rendered, Err):
            return self._fail(
                anchor.value, report, command, f"The render was refused: {rendered.error}"
            )
        outcome = rendered.value
        if not outcome.succeeded or outcome.output is None:
            reasons = (
                "; ".join(outcome.report.errors) or "the render did not produce a verified file"
            )
            return self._fail(anchor.value, _with_render(report, outcome), command, reasons)
        report = _with_render(report, outcome)
        step = self._sessions.export_rendered(command.review_session_id, actor=_SYSTEM)
        if isinstance(step, Err):
            return self._fail(anchor.value, report, command, str(step.error.user_message))
        report = self._touch(replace(report, export_status=ExportStatus.VALIDATING, progress=90.0))
        validated = self._validate(data, command, outcome.output.id, export_id, ctx)
        if isinstance(validated, Err):
            return self._fail(
                anchor.value, report, command, f"The file could not be validated: {validated.error}"
            )
        return self._conclude(anchor.value, report, command, validated.value)

    def retry(
        self, session_id: str, actor: Actor, command: RunExportCommand, ctx: JobContext
    ) -> Result[ExportResult, ExportFailure]:
        """After a failed export the same authorization is used again if it still stands."""
        again = self._sessions.retry_export(session_id, actor=actor)
        if isinstance(again, Err):
            return again
        return self.execute(command, ctx)

    def cancel(self, render_id: str) -> Result[None, ExportFailure]:
        cancelled = self._renders.cancel(render_id)
        if isinstance(cancelled, Err):
            return Err(ExportRefused(str(cancelled.error.user_message)))
        return Ok(None)

    def settle_manual_review(
        self, session_id: str, actor: Actor, export_id: str, *, accept: bool, note: str
    ) -> Result[ExportResult, ExportFailure]:
        """A validation that could not decide is decided by a person looking at the file."""
        settled = self._sessions.resolve_manual_validation(
            session_id, accept=accept, actor=actor, note=note
        )
        if isinstance(settled, Err):
            return settled
        session = settled.value
        found = self._store.find(session.anchor_asset_id, export_id)
        if isinstance(found, Err):
            return found
        if found.value is None:
            return Err(ExportRefused(f"There is no export {export_id}."))
        report = found.value[0]
        updated = self._touch(
            replace(
                report,
                export_status=ExportStatus.VALIDATED if accept else ExportStatus.FAILED,
                delivery_status=DeliveryStatus.AWAITING_APPROVAL
                if accept
                else DeliveryStatus.BLOCKED,
                warnings=(
                    *report.warnings,
                    f"A person {'accepted' if accept else 'rejected'} the file: {note}",
                ),
            )
        )
        return self._save(updated, session.anchor_asset_id, technically_ready=accept)

    # --- delivery ----------------------------------------------------------------------------
    def deliver(
        self, session_id: str, actor: Actor, export_id: str
    ) -> Result[ExportResult, ExportFailure]:
        """Place the validated file in the delivery folder and mark the delivery ready. It needs
        the validated state and, when the project requires it, a person's delivery approval; it
        replaces nothing and publishes nothing."""
        data = self._reviews_session(session_id)
        if isinstance(data, Err):
            return data
        session = data.value
        found = self._store.find(session.anchor_asset_id, export_id)
        if isinstance(found, Err):
            return found
        if found.value is None:
            return Err(DeliveryRefused(f"There is no export {export_id}."))
        report = found.value[0]
        if (
            report.export_status is not ExportStatus.VALIDATED
            or session.state is not ReviewState.EXPORT_VALIDATED
        ):
            return Err(DeliveryRefused("Only a validated export can be delivered."))
        if session.policy.require_delivery_approval:
            approval = session.current_approval(export_id, ApprovalKind.DELIVERY)
            if approval is None or approval.verdict is not Verdict.APPROVED:
                return Err(DeliveryRefused("A person has not approved this export for delivery."))
        located = self._library.local_path(report.output_asset_id)
        if isinstance(located, Err):
            return located
        number = 1 + sum(
            1
            for r, _ in self._exports_of(session)
            if r.delivery_status is DeliveryStatus.DELIVERED
            and r.export_profile_id == report.export_profile_id
        )
        name = delivery_name(
            report.project_id or "video",
            report.timeline_version,
            report.export_profile_id,
            number,
            report.container or "mp4",
        )
        while self._delivery.exists(name):  # never replace an earlier master
            number += 1
            name = delivery_name(
                report.project_id or "video", report.timeline_version,
                report.export_profile_id, number, report.container or "mp4",
            )  # fmt: skip
        try:
            placed = self._delivery.place(
                located.value,
                name,
                {
                    "export_id": report.export_id,
                    "timeline_id": report.timeline_id,
                    "timeline_version": str(report.timeline_version),
                    "timeline_content_hash": report.timeline_content_hash,
                    "profile": report.export_profile_id,
                    "validation": report.validation_status,
                    "authorized_by": report.export_authorized_by,
                },
            )
        except DeliveryRefused as exc:
            return Err(exc)
        ready = self._sessions.mark_delivery_ready(session_id, actor=actor)
        if isinstance(ready, Err):
            return ready
        updated = self._touch(
            replace(report, delivery_status=DeliveryStatus.DELIVERED, delivered_as=placed)
        )
        self._events.publish(
            DeliveryReady(
                occurred_at=self._clock.now(),
                export_id=export_id,
                review_session_id=session_id,
                timeline_version=report.timeline_version,
                delivered_as=placed,
            )
        )
        return self._save(updated, session.anchor_asset_id, technically_ready=True)

    # --- steps -------------------------------------------------------------------------------
    def _render(
        self, data: ExportInputs, command: RunExportCommand, ctx: JobContext
    ) -> Result[RenderOutcome, object]:
        return self._renders.render(
            RenderRequest(
                anchor_asset_id=data.session.anchor_asset_id,
                render_type=RenderType.FINAL,
                profile_id=command.profile_id,
                timeline_id=data.session.timeline.timeline_id,
                review_session_id=data.session.session_id,
                authorization=RenderAuthorization(
                    session_id=data.session.session_id,
                    authorization_id=command.authorization_id,
                    timeline=command.timeline,
                ),
            ),
            ctx,
        )

    def _validate(
        self,
        data: ExportInputs,
        command: RunExportCommand,
        output_asset_id: str,
        export_id: str,
        ctx: JobContext,
    ) -> Result[ValidationOutcome, object]:
        reference = self._render_history.latest(
            data.session.anchor_asset_id,
            timeline_id=data.session.timeline.timeline_id,
            content_hash=data.session.timeline.content_hash,
            render_type=RenderType.REVIEW,
        )
        return self._validator.execute(
            ValidateExportCommand(
                output_asset_id=output_asset_id,
                approved=data.session.timeline,
                delivery_id=command.delivery_id or command.profile_id,
                review_session_id=data.session.session_id,
                export_id=export_id,
                authorized_config_hash=data.config_hash,
                reference_render_asset_id=reference.output_asset_id if reference else "",
                intentional=command.intentional,
            ),
            ctx,
        )

    def _conclude(
        self,
        anchor: MediaAssetDto,
        report: ExportReport,
        command: RunExportCommand,
        outcome: ValidationOutcome,
    ) -> Result[ExportResult, ExportFailure]:
        recorded = self._sessions.record_export_validation(
            command.review_session_id,
            outcome.review_status,
            outcome.report.export_validation_id,
            actor=_SYSTEM,
        )
        if isinstance(recorded, Err):
            return self._fail(anchor, report, command, str(recorded.error.user_message))
        status = outcome.review_status
        ready = status in (ValidationStatus.PASS, ValidationStatus.WARNING)
        export_status = {
            ValidationStatus.PASS: ExportStatus.VALIDATED,
            ValidationStatus.WARNING: ExportStatus.VALIDATED,
            ValidationStatus.FAIL: ExportStatus.FAILED,
            ValidationStatus.MANUAL_REVIEW_REQUIRED: ExportStatus.MANUAL_REVIEW,
        }[status]
        updated = self._touch(
            replace(
                report,
                export_status=export_status,
                progress=100.0,
                validation_report_reference=outcome.report.export_validation_id,
                validation_status=outcome.report.validation_status.value,
                delivery_status=DeliveryStatus.AWAITING_APPROVAL
                if ready
                else (
                    DeliveryStatus.BLOCKED
                    if status is ValidationStatus.FAIL
                    else DeliveryStatus.NOT_DELIVERED
                ),
                errors=tuple(p.message for p in outcome.report.detected_problems)
                if status is ValidationStatus.FAIL
                else (),
            )
        )
        self._events.publish(
            ExportCompleted(
                occurred_at=self._clock.now(),
                export_id=report.export_id,
                review_session_id=report.review_session_id,
                timeline_version=report.timeline_version,
                status=export_status.value,
                validation_status=outcome.report.validation_status.value,
            )
        )
        return self._save(updated, anchor.id, technically_ready=ready)

    def _fail(
        self,
        anchor: MediaAssetDto,
        report: ExportReport,
        command: RunExportCommand,
        reason: str,
    ) -> Result[ExportResult, ExportFailure]:
        """A failed export is a report with its reason, and the review is told: nothing that failed
        is ever delivered, and the authorization stands for a retry."""
        failed = self._sessions.export_failed(command.review_session_id, reason, actor=_SYSTEM)
        if isinstance(failed, Err):
            _log.warning("Review not told of the failed export", reason=str(failed.error))
        updated = self._touch(
            replace(
                report,
                export_status=ExportStatus.FAILED,
                delivery_status=DeliveryStatus.BLOCKED,
                errors=(*report.errors, reason),
            )
        )
        self._events.publish(
            ExportCompleted(
                occurred_at=self._clock.now(),
                export_id=report.export_id,
                review_session_id=report.review_session_id,
                timeline_version=report.timeline_version,
                status=ExportStatus.FAILED.value,
                validation_status=report.validation_status,
            )
        )
        return self._save(updated, anchor.id, technically_ready=False)

    # --- the record --------------------------------------------------------------------------
    def _initial(
        self, data: ExportInputs, export_id: str, authorized_by: str, command: RunExportCommand
    ) -> ExportReport:
        session = data.session
        now = self._clock.now().isoformat()
        authorization = session.authorization(command.authorization_id)
        return ExportReport(
            export_id=export_id,
            project_id=session.project_id,
            timeline_id=session.timeline.timeline_id,
            timeline_version=session.timeline.version,
            timeline_content_hash=session.timeline.content_hash,
            anchor_asset_id=session.anchor_asset_id,
            review_session_id=session.session_id,
            approval_reference=authorization.final_approval_id if authorization else "",
            authorization_id=command.authorization_id,
            export_authorized_by=authorized_by,
            export_profile_id=command.profile_id,
            output_config_hash=data.config_hash,
            resolve_project_reference=session.resolve_project_ref,
            resolve_timeline_reference=session.resolve_timeline_ref,
            resolve_bound_version=data.binding.build_version if data.binding else None,
            render_id="",
            output_asset_id="",
            output_name="",
            codec="",
            container="",
            resolution="",
            frame_rate="",
            audio_configuration="",
            export_status=ExportStatus.RENDERING,
            progress=0.0,
            validation_report_reference="",
            validation_status="",
            delivery_status=DeliveryStatus.NOT_DELIVERED,
            delivered_as="",
            warnings=(),
            errors=(),
            created_at=now,
            updated_at=now,
        )

    def _touch(self, report: ExportReport) -> ExportReport:
        stamped = replace(report, updated_at=self._clock.now().isoformat(), content_hash="")
        return replace(
            stamped, content_hash=json_codec.digest(replace(stamped, created_at="", updated_at=""))
        )

    def _save(
        self, report: ExportReport, anchor_id: str, *, technically_ready: bool
    ) -> Result[ExportResult, ExportFailure]:
        anchor = self._library.get(anchor_id)
        if isinstance(anchor, Err):
            return anchor
        saved = self._store.save(anchor.value, report)
        if isinstance(saved, Err):
            return saved
        return Ok(ExportResult(report, saved.value, technically_ready))

    def _reviews_session(self, session_id: str) -> Result[ReviewSession, ReviewFailure]:
        return self._sessions.sync_timeline(session_id)

    def _exports_of(self, session: ReviewSession) -> list[tuple[ExportReport, MediaAssetDto]]:
        listed = self._store.exports(session.anchor_asset_id, session_id=session.session_id)
        return listed.value if isinstance(listed, Ok) else []


def _with_render(report: ExportReport, outcome: RenderOutcome) -> ExportReport:
    r = outcome.report
    audio = r.audio
    return replace(
        report,
        render_id=r.render_id,
        output_asset_id=r.output_asset_id,
        output_name=r.output_name,
        codec=r.codec,
        container=r.container,
        resolution=f"{r.width}x{r.height}" if r.width and r.height else "",
        frame_rate=f"{r.frame_rate.numerator}/{r.frame_rate.denominator}" if r.frame_rate else "",
        audio_configuration=f"{audio.codec} {audio.sample_rate} Hz {audio.channels} ch"
        if audio
        else "",
        progress=80.0,
        warnings=tuple(r.warnings),
    )
