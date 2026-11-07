"""Validate an exported file: look at the file itself, against the timeline that was approved.

The validation trusts nothing the render said about itself. It inspects the file, decodes it to the
end, measures black frames, frozen frames, silence and loudness, samples pictures and compares them
with the same moments of the approved review render, and reads the stored approved build (not the
latest) for what the file must be. The verdict comes from the pure rules; this class only gathers
the facts. It never changes the file, never re-edits or re-renders, and never advances the review:
``resolve_export`` reads the report and records the verdict.

An unchanged file validated again for the same approved version and delivery profile is not measured
again: the stored report is served.
"""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from media_house.core.application.ports import Clock, MediaAnalyzer
from media_house.core.domain import Rational, json_codec
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.export_validation.application.events import ExportValidated
from media_house.modules.export_validation.application.store import ValidationStore
from media_house.modules.export_validation.application.views import (
    TimelineFacts,
    audio_facts,
    decode_facts,
    inspection_failed,
    loudness_facts,
    spans,
    timeline_facts,
    video_facts,
)
from media_house.modules.export_validation.domain.facts import (
    Expected,
    FileFacts,
    FrameSample,
    Intentional,
    Lineage,
)
from media_house.modules.export_validation.domain.profile import (
    DeliveryProfile,
    UnknownDeliveryProfile,
    get_delivery_profile,
)
from media_house.modules.export_validation.domain.report import (
    ApprovedReference,
    ExportValidationReport,
)
from media_house.modules.export_validation.domain.rules import Evaluation, evaluate
from media_house.modules.export_validation.domain.values import VALIDATOR_VERSION, Status
from media_house.modules.media_inspection.application.contracts import (
    Depth,
    InspectionConfig,
    InspectionResult,
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.render_manager.application.contracts import (
    RenderCatalog,
    RenderProfile,
    RenderProfiles,
    RenderReport,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ArtifactKind,
    ReviewSessions,
    TimelineRef,
    ValidationStatus,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    DomainError,
    Err,
    MediaHouseError,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)
type ValidationFailure = DomainError | MediaError | NotFoundError | ValidationError


@dataclass(frozen=True, slots=True)
class IntentionalSpan:
    """A stretch the plan declared on purpose: ``kind`` is ``black``, ``silence`` or ``freeze``."""

    kind: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class ValidateExportCommand:
    #: The exported file (a library asset).
    output_asset_id: str
    #: The version the person approved; its stored build says what the file must be.
    approved: TimelineRef
    delivery_id: str
    review_session_id: str = ""
    export_id: str = ""
    #: The output configuration the export was authorized for (from the render manager's hash).
    authorized_config_hash: str = ""
    #: The approved review render the exported pictures are compared with.
    reference_render_asset_id: str = ""
    intentional: tuple[IntentionalSpan, ...] = ()
    #: Measure again even if this exact file was validated before.
    force: bool = False
    #: A custom delivery profile (its id must not be a built-in's).
    profile: DeliveryProfile | None = None


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    report: ExportValidationReport
    report_asset: MediaAssetDto
    #: A stored validation of the same file and approval was served; nothing was measured.
    reused: bool
    #: The report was recorded in the review (``False`` if the review moved on in the meantime).
    recorded_in_review: bool

    @property
    def status(self) -> Status:
        return self.report.validation_status

    @property
    def review_status(self) -> ValidationStatus:
        """The verdict in the review orchestrator's words (``resolve_export`` records it)."""
        return ValidationStatus(self.report.validation_status.value)


class ValidateExport:
    """Implements ``ExportValidator``."""

    def __init__(
        self,
        library: MediaLibrary,
        catalog: BuiltTimelineCatalog,
        inspector: MediaInspector,
        analyzer: MediaAnalyzer,
        renders: RenderCatalog,
        profiles: RenderProfiles,
        mixes: MixCatalog,
        sessions: ReviewSessions,
        store: ValidationStore,
        paths: AppPaths,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._library = library
        self._catalog = catalog
        self._inspector = inspector
        self._analyzer = analyzer
        self._renders = renders
        self._profiles = profiles
        self._mixes = mixes
        self._sessions = sessions
        self._store = store
        self._paths = paths
        self._clock = clock
        self._events = events

    def execute(
        self, command: ValidateExportCommand, ctx: JobContext
    ) -> Result[ValidationOutcome, ValidationFailure]:
        try:
            delivery = command.profile or get_delivery_profile(command.delivery_id)
        except UnknownDeliveryProfile as exc:
            return Err(exc)
        output = self._library.get(command.output_asset_id)
        if isinstance(output, Err):
            return output
        render_profile = self._render_profile(delivery)
        if isinstance(render_profile, Err):
            return render_profile
        approved = self._approved(command.approved)
        if isinstance(approved, Err):
            return approved
        anchor_id = command.approved.anchor_asset_id
        lineage_report = self._renders.for_output(anchor_id, output.value.id)
        reference = self._reference(command)
        identity = self._analyzer.capabilities().identity
        key = self._cache_key(command, output.value, delivery, identity, lineage_report, reference)
        if not command.force:
            stored = self._store.find(anchor_id, key)
            if isinstance(stored, Err):
                return stored
            if stored.value is not None:
                report, asset = stored.value
                _log.info("Stored export validation served", status=report.validation_status.value)
                return Ok(ValidationOutcome(report, asset, reused=True, recorded_in_review=False))
        ctx.raise_if_cancelled()
        facts = self._facts(command, output.value, approved.value, delivery, reference, ctx)
        expected = _expected(approved.value, render_profile.value)
        lineage = _lineage(command, lineage_report)
        intentional = tuple(
            Intentional(i.kind, i.start_seconds, i.end_seconds) for i in command.intentional
        )
        evaluation = evaluate(facts, expected, delivery, lineage, intentional)
        report = self._report(
            command, output.value, approved.value, delivery, identity, key, lineage_report,
            facts, evaluation,
        )  # fmt: skip
        stored_asset = self._store.save(report)
        if isinstance(stored_asset, Err):
            return stored_asset
        recorded = self._tell_session(command, report)
        self._events.publish(
            ExportValidated(
                occurred_at=self._clock.now(),
                export_validation_id=report.export_validation_id,
                export_id=report.export_id,
                timeline_version=report.timeline_version,
                status=report.validation_status.value,
            )
        )
        _log.info(
            "Export validated",
            status=report.validation_status.value,
            problems=len(report.detected_problems),
            not_checked=len(report.not_checked),
        )
        return Ok(
            ValidationOutcome(report, stored_asset.value, reused=False, recorded_in_review=recorded)
        )

    # --- inputs ---------------------------------------------------------------------------------
    def _render_profile(
        self, delivery: DeliveryProfile
    ) -> Result[RenderProfile, ValidationFailure]:
        found = self._profiles.profile(delivery.render_profile_id)
        if isinstance(found, Err):
            return found
        if found.value.version != delivery.render_profile_version:
            return Err(
                ValidationError(
                    "render profile version differs",
                    user_message=(
                        f"Delivery profile '{delivery.delivery_id}' is made for render profile "
                        f"version {delivery.render_profile_version}, which is now version "
                        f"{found.value.version}. Update the delivery profile."
                    ),
                )
            )
        return Ok(found.value)

    def _approved(self, ref: TimelineRef) -> Result[TimelineFacts, ValidationFailure]:
        """The approved version, from its stored build: never the latest, which may differ."""
        if not ref.build_asset_id:
            return Err(
                ValidationError(
                    "the approved version has no stored build",
                    user_message=(
                        "The approved timeline version has no stored build to compare with."
                    ),
                )
            )
        loaded = self._catalog.load(ref.build_asset_id)
        if isinstance(loaded, Err):
            return loaded
        if loaded.value.build.build_hash != ref.content_hash:
            return Err(
                ValidationError(
                    "the stored build is not the approved content",
                    user_message=(
                        "The stored timeline build does not match the approved version; the "
                        "approval cannot be checked against it."
                    ),
                )
            )
        return Ok(timeline_facts(loaded.value))

    def _reference(self, command: ValidateExportCommand) -> Path | None:
        if not command.reference_render_asset_id:
            return None
        path = self._library.local_path(command.reference_render_asset_id)
        return path.value if isinstance(path, Ok) else None

    def _cache_key(
        self,
        command: ValidateExportCommand,
        output: MediaAssetDto,
        delivery: DeliveryProfile,
        identity: str,
        lineage_report: RenderReport | None,
        reference: Path | None,
    ) -> str:
        reference_checksum = ""
        if reference is not None:
            ref_asset = self._library.get(command.reference_render_asset_id)
            reference_checksum = ref_asset.value.checksum if isinstance(ref_asset, Ok) else ""
        return json_codec.digest(
            {
                "file": output.checksum,
                "delivery": json_codec.canon(delivery),
                "approved": [
                    command.approved.timeline_id,
                    command.approved.version,
                    command.approved.content_hash,
                ],
                "analyzer": identity,
                "validator": VALIDATOR_VERSION,
                "intentional": json_codec.canon(command.intentional),
                "reference": reference_checksum,
                "authorized": command.authorized_config_hash,
                "session": command.review_session_id,
                "render": lineage_report.report_id if lineage_report else "",
                "file_name": output.original_filename,
                "force": new_id() if command.force else "",
            }
        )

    # --- the file -------------------------------------------------------------------------------
    def _facts(
        self,
        command: ValidateExportCommand,
        output: MediaAssetDto,
        timeline: TimelineFacts,
        delivery: DeliveryProfile,
        reference: Path | None,
        ctx: JobContext,
    ) -> FileFacts:
        path = self._library.local_path(output.id)
        if isinstance(path, Err) or not path.value.is_file():
            return FileFacts(
                exists=False, readable=False, size_bytes=0, extension=output.extension,
                file_name=output.original_filename, duration=None, inspection_error=False,
                video=None, audio=None,
            )  # fmt: skip
        file = path.value
        inspection = self._inspect(output, ctx)
        token = ctx.cancellation
        caps = self._analyzer.capabilities().operations
        decode = self._analyzer.decode(file, cancellation=token)
        black = frozen = silent = None
        if "black_spans" in caps:
            black = spans(
                self._analyzer.black_spans(
                    file,
                    min_seconds=delivery.black_min_seconds,
                    picture_threshold=delivery.black_picture_threshold,
                    pixel_threshold=delivery.black_pixel_threshold,
                    cancellation=token,
                )
            )
        if "frozen_spans" in caps:
            frozen = spans(
                self._analyzer.frozen_spans(
                    file,
                    min_seconds=delivery.freeze_min_seconds,
                    noise_db=delivery.freeze_noise_db,
                    cancellation=token,
                )
            )
        if "silent_spans" in caps:
            silent = spans(
                self._analyzer.silent_spans(
                    file,
                    min_seconds=delivery.silence_min_seconds,
                    noise_db=delivery.silence_noise_db,
                    cancellation=token,
                )
            )
        loudness = None
        if "loudness" in caps:
            loudness = loudness_facts(self._analyzer.loudness(file, span=None, cancellation=token))
        frames, unreadable = self._frames(file, reference, timeline, delivery, ctx)
        return FileFacts(
            exists=True,
            readable=inspection is not None and inspection.inspection.observed.integrity.readable,
            inspected=inspection is not None,
            size_bytes=output.file_size,
            extension=output.extension,
            file_name=output.original_filename,
            duration=inspection.inspection.container.duration if inspection else None,
            inspection_error=inspection is not None and inspection_failed(inspection),
            video=video_facts(inspection) if inspection else None,
            audio=audio_facts(inspection) if inspection else None,
            tags=tuple(sorted(inspection.inspection.container.tags.items())) if inspection else (),
            decode=decode_facts(decode),
            black=black,
            frozen=frozen,
            silent=silent,
            loudness=loudness,
            frames=frames,
            reference_used=reference is not None and frames is not None,
            unreadable_frames=unreadable,
        )

    def _inspect(self, output: MediaAssetDto, ctx: JobContext) -> InspectionResult | None:
        """The inspection of the file, or ``None`` when it cannot be had: the streams are then
        not checked (and the verdict says so), never assumed missing."""
        try:
            inspected = self._inspector.execute(
                InspectMediaCommand(output.id, InspectionConfig(depth=Depth.PROBE)), ctx
            )
        except MediaHouseError as exc:
            _log.warning("The exported file could not be inspected", reason=str(exc))
            return None
        return inspected.value if isinstance(inspected, Ok) else None

    def _frames(
        self,
        file: Path,
        reference: Path | None,
        timeline: TimelineFacts,
        delivery: DeliveryProfile,
        ctx: JobContext,
    ) -> tuple[tuple[FrameSample, ...] | None, int]:
        """Pictures of the file at the opening, the ending and inside each section, compared with
        the same moments of the approved review render (``None``: nothing to compare with)."""
        if reference is None:
            return None, 0
        points = _sample_points(timeline, delivery.max_section_samples)
        times = [moment for _, _, moment, _ in points]
        with self._paths.temporary_directory(prefix="export-validation-") as tmp:
            ours = self._analyzer.sample_frames(
                file, times, into=tmp / "file", cancellation=ctx.cancellation
            )
            theirs = self._analyzer.sample_frames(
                reference, times, into=tmp / "reference", cancellation=ctx.cancellation
            )
            have = {s.requested: s for s in ours}
            other = {s.requested: s for s in theirs}
            samples: list[FrameSample] = []
            for kind, label, moment, section_id in points:
                mine, ref = have.get(moment), other.get(moment)
                if mine is None:
                    continue
                similarity = None
                if ref is not None and mine.path is not None and ref.path is not None:
                    similarity = self._analyzer.compare_frames(mine.path, ref.path).similarity
                samples.append(
                    FrameSample(
                        label, kind, float(moment), section_id, similarity, mine.black_fraction
                    )
                )
        return tuple(samples), len(times) - len(ours)

    # --- the report -----------------------------------------------------------------------------
    def _report(
        self,
        command: ValidateExportCommand,
        output: MediaAssetDto,
        timeline: TimelineFacts,
        delivery: DeliveryProfile,
        identity: str,
        key: str,
        lineage_report: RenderReport | None,
        facts: FileFacts,
        evaluation: Evaluation,
    ) -> ExportValidationReport:
        ref = command.approved
        warnings = _warnings(command, facts)
        return ExportValidationReport(
            export_validation_id=new_id(),
            export_id=command.export_id,
            render_id=lineage_report.render_id if lineage_report else "",
            anchor_asset_id=ref.anchor_asset_id,
            project_id=timeline.project_id,
            timeline_id=timeline.timeline_id,
            timeline_version=timeline.version,
            approved_version_reference=ApprovedReference(
                ref.timeline_id,
                ref.version,
                ref.content_hash,
                ref.build_asset_id or "",
                command.review_session_id,
            ),
            output_asset_id=output.id,
            file_path=output.original_filename,
            file_checksum=output.checksum,
            delivery_profile_id=delivery.delivery_id,
            delivery_profile_version=delivery.version,
            validation_status=evaluation.status,
            severity=evaluation.severity,
            recommended_next_action=evaluation.next_action,
            created_at=self._clock.now(),
            analyzer_identity=identity,
            area_status=evaluation.area_status,
            checks=evaluation.checks,
            detected_problems=evaluation.findings,
            not_checked=evaluation.not_checked,
            measurements=self._measurements(ref, facts),
            warnings=warnings,
            cache_key=key,
            review_session_id=command.review_session_id,
            inputs=(
                ("analyzer", identity),
                ("delivery_profile", f"{delivery.delivery_id}@{delivery.version}"),
                ("approved_build", ref.build_asset_id or ""),
                ("reference_render", command.reference_render_asset_id),
            ),
        )

    def _measurements(self, ref: TimelineRef, facts: FileFacts) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = [
            ("size_bytes", str(facts.size_bytes)),
            ("duration_seconds", "unknown" if facts.duration is None else f"{facts.duration:.3f}"),
        ]
        if facts.video:
            v = facts.video
            values += [
                ("video", f"{v.codec} {v.width}x{v.height} {v.pixel_format}"),
                ("frame_rate", str(v.frame_rate)),
            ]
        if facts.audio:
            values.append(("audio", f"{facts.audio.codec} {facts.audio.sample_rate} Hz"))
        if facts.loudness and facts.loudness.has_audio:
            ln = facts.loudness
            values += [
                ("integrated_lufs", _num(ln.integrated_lufs)),
                ("true_peak_dbtp", _num(ln.true_peak_dbtp)),
                ("loudness_range_lu", _num(ln.loudness_range_lu)),
            ]
        mix = self._mixes.latest_result(ref.anchor_asset_id, ref.timeline_id)
        if isinstance(mix, Ok) and mix.value is not None and mix.value[0].mix is not None:
            m = mix.value[0].mix.measurement
            values += [
                ("audio_mix_integrated_lufs (supporting, not the file)", _num(m.integrated_lufs)),
                ("audio_mix_true_peak_dbtp (supporting, not the file)", _num(m.true_peak_dbtp)),
            ]
        return tuple(values)

    def _tell_session(self, command: ValidateExportCommand, report: ExportValidationReport) -> bool:
        if not command.review_session_id:
            return False
        attached = self._sessions.attach(
            command.review_session_id,
            ArtifactKind.EXPORT_VALIDATION_REPORT,
            report.export_validation_id,
            timeline=command.approved,
            actor=Actor.system("export_validation"),
        )
        if isinstance(attached, Err):
            _log.warning("Validation report not recorded in the review", reason=str(attached.error))
            return False
        return True


# --- pure helpers ------------------------------------------------------------------------------


def _num(value: float | None) -> str:
    return "unmeasured" if value is None else f"{value:.2f}"


def _warnings(command: ValidateExportCommand, facts: FileFacts) -> tuple[str, ...]:
    found: list[str] = []
    if command.reference_render_asset_id and not facts.reference_used:
        found.append("the reference render could not be used, so pictures were not compared")
    return tuple(found)


def _expected(timeline: TimelineFacts, profile: RenderProfile) -> Expected:
    v, a = profile.video, profile.audio
    return Expected(
        width=v.width or timeline.width,
        height=v.height or timeline.height,
        frame_rate=v.frame_rate or Rational(timeline.frame_rate_num, timeline.frame_rate_den),
        duration_seconds=timeline.duration_seconds,
        has_audio=timeline.has_sound and profile.audio_required,
        container=v.container,
        video_codec=v.codec,
        pixel_format=v.pixel_format,
        audio_codec=a.codec,
        sample_rate=a.sample_rate,
        channels=a.channels,
        aspect=v.aspect,
    )


def _lineage(command: ValidateExportCommand, report: RenderReport | None) -> Lineage:
    ref = command.approved
    if report is None:
        return Lineage(
            approved_timeline_id=ref.timeline_id,
            approved_version=ref.version,
            approved_hash=ref.content_hash,
            approved_session_id=command.review_session_id,
            render_found=False,
            authorized_config_hash=command.authorized_config_hash,
        )
    return Lineage(
        approved_timeline_id=ref.timeline_id,
        approved_version=ref.version,
        approved_hash=ref.content_hash,
        approved_session_id=command.review_session_id,
        render_found=True,
        render_id=report.render_id,
        render_timeline_id=report.timeline_id,
        render_version=report.timeline_version,
        render_hash=report.timeline_content_hash,
        render_session_id=report.review_session_id,
        render_succeeded=report.succeeded,
        render_config_hash=report.output_config_hash,
        authorized_config_hash=command.authorized_config_hash,
    )


def _sample_points(timeline: TimelineFacts, limit: int) -> list[tuple[str, str, Fraction, str]]:
    """Where to look: the first frame, the last, and the middle of each section."""
    frames = timeline.duration_frames
    points: list[tuple[str, str, Fraction, str]] = [
        ("opening", "opening", timeline.moment(0), ""),
        ("ending", "ending", timeline.moment(max(0, frames - 2)), ""),
    ]
    sections = list(timeline.sections)
    if len(sections) > limit:
        step = len(sections) / limit
        sections = [sections[int(i * step)] for i in range(limit)]
    for section_id, start, end in sections:
        middle = (start + end) / 2
        if 0 < middle < frames - 1:
            points.append(("section", f"section {section_id}", timeline.moment(middle), section_id))
    unique: dict[Fraction, tuple[str, str, Fraction, str]] = {}
    for p in points:
        unique.setdefault(p[2], p)
    return list(unique.values())
