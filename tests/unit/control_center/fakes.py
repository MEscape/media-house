"""In-memory stand-ins for the contracts the Control Center reads (no Qt, no files, no Resolve)."""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import cast

from media_house.modules.control_center.application.review_workspace import ReviewWorkspace
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    AssetStatus,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaQuery,
    MediaType,
    Page,
    SourceType,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    ImportedSound,
    ImportSoundLibraryCommand,
    ImportSoundLibraryReport,
    Licence,
    LicenceState,
    SoundAsset,
    SoundIndex,
    SoundKind,
    SoundMatch,
    SoundQuery,
)
from media_house.modules.render_manager.application.contracts import (
    PROFILES,
    RenderProfile,
    RenderProfiles,
)
from media_house.modules.resolve_adapter.application.contracts import (
    REGISTRY,
    CapabilityMatrix,
    CapabilityState,
    ConnectionState,
    ConnectionStatus,
    DetectedCapability,
    ResolveEnvironment,
    ResolveInspection,
    ResolveVersion,
)
from media_house.modules.resolve_export.application.contracts import (
    Blocker,
    BlockerKind,
    ExportApprovalService,
    ExportReadiness,
    ReadinessReport,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ActorKind,
    Approval,
    ApprovalKind,
    AuditEntry,
    ReviewCatalog,
    ReviewSession,
    ReviewSessions,
    ReviewState,
    SectionRecord,
    SectionStatus,
    TimelineFacts,
    TimelineRef,
    Verdict,
)
from media_house.modules.revision_engine.application.contracts import (
    ChangeKind,
    ChangeStatus,
    DecideRevisionCommand,
    Level,
    PlanStatus,
    ProposedChange,
    RevisionCatalog,
    RevisionDecider,
    RevisionPlan,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result, ValidationError

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


# --- resolve ----------------------------------------------------------------------------------
def _matrix(state: CapabilityState, reason: str) -> CapabilityMatrix:
    return CapabilityMatrix(
        tuple(
            DetectedCapability(
                s.operation,
                CapabilityState.UNSUPPORTED if s.known_gap else state,
                reason,
                s.mechanism,
                s.summary,
            )
            for s in REGISTRY
        )
    )


def offline_inspection() -> ResolveInspection:
    return ResolveInspection(
        ConnectionStatus(ConnectionState.NOT_RUNNING, "Resolve is not running", True, False),
        _matrix(CapabilityState.NOT_VERIFIED, "no connection: nothing was checked"),
        (),
        None,
    )


def connected_inspection() -> ResolveInspection:
    env = ResolveEnvironment(
        product="DaVinci Resolve Studio",
        version=ResolveVersion(19, 0, 3, 2),
        edition="studio",
        operating_system="windows",
        scripting_module="fusionscript",
    )
    return ResolveInspection(
        ConnectionStatus(ConnectionState.CONNECTED, "", True, True, env),
        _matrix(CapabilityState.NOT_VERIFIED, "the method exists"),
        ("Demo", "Other"),
        "Demo",
    )


class FakeInspector:
    def __init__(self, inspection: ResolveInspection | None = None) -> None:
        self.inspection = inspection or offline_inspection()
        self.calls = 0

    def execute(self) -> ResolveInspection:
        self.calls += 1
        return self.inspection


# --- sounds -----------------------------------------------------------------------------------
def sound(
    asset_id: str,
    title: str,
    *,
    commercial: bool | None,
    kind: str = "music",
    attribution: str | None = None,
    author: str = "",
) -> SoundAsset:
    return SoundAsset(
        asset_id=asset_id,
        kind=SoundKind(kind),
        title=title,
        categories=(),
        tags=("calm",),
        duration_seconds=125.4,
        checksum=asset_id,
        licence=Licence(
            name="Custom",
            url="",
            commercial_use=commercial,
            attribution_required=attribution is not None,
            attribution_text=attribution,
            author=author,
            source="Owner",
        ),
    )


class FakeSoundCatalog:
    def __init__(self, sounds: Sequence[SoundAsset]) -> None:
        self.items = list(sounds)

    def sounds(self, kind: SoundKind | None = None) -> list[SoundAsset]:
        return [s for s in self.items if kind is None or s.kind is kind]

    def get(self, asset_id: str) -> SoundAsset | None:
        return next((s for s in self.items if s.asset_id == asset_id), None)

    def index(self) -> SoundIndex:  # unused by the control center
        raise NotImplementedError

    def search(self, query: SoundQuery) -> list[SoundMatch]:  # unused by the control center
        raise NotImplementedError


class FakeImporter:
    def __init__(self, *, fail: bool = False, catalog: FakeSoundCatalog | None = None) -> None:
        self.fail = fail
        self.catalog = catalog
        self.commands: list[ImportSoundLibraryCommand] = []

    def execute(
        self, command: ImportSoundLibraryCommand, ctx: JobContext
    ) -> Result[ImportSoundLibraryReport, ValidationError]:
        self.commands.append(command)
        if self.fail:
            return Err(ValidationError("bad", user_message="The manifest is not valid JSON."))
        if self.catalog is not None:
            self.catalog.items.append(sound("new", "Imported", commercial=False))
        return Ok(
            ImportSoundLibraryReport(
                sounds=(
                    ImportedSound(
                        "x.wav", "new", SoundKind.MUSIC, True, False, LicenceState.NOT_COMMERCIAL
                    ),
                ),
                not_cleared=("x.wav",),
            )
        )


# --- review -----------------------------------------------------------------------------------
REF = TimelineRef("tl-1", 3, "abcdef0123456789", "asset-1")


def session(**changes: object) -> ReviewSession:
    base = ReviewSession(
        session_id="s-1",
        project_id=None,
        anchor_asset_id="asset-1",
        timeline=REF,
        facts=TimelineFacts(REF, 1920, 1080, "25", 2500, ()),
        state=ReviewState.AWAITING_HUMAN_REVIEW,
        review_version=2,
        created_at=NOW,
        updated_at=NOW,
        reviewer="Ada",
        quality_report_id="qc-1",
        sections=(
            SectionRecord("intro", "Intro", SectionStatus.UNREVIEWED),
            SectionRecord("main", "Main", SectionStatus.LOCKED, decided_by="Ada"),
        ),
        approvals=(
            Approval(
                "ap-1",
                ApprovalKind.SECTION,
                "main",
                Verdict.APPROVED,
                "Ada",
                ActorKind.HUMAN,
                REF,
                NOW,
                2,
            ),
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def plan(*, status: ChangeStatus = ChangeStatus.PROPOSED) -> RevisionPlan:
    change = ProposedChange(
        proposal_id="p1",
        proposal_key="k1",
        kind=ChangeKind.TRIM_CLIP,
        owner_module="timeline_builder",
        operation="trim",
        level=Level.SUGGESTED,
        title="Trim the pause at 0:12",
        description="Intro: a 1.4 s pause; trim it.",
        status=status,
    )
    return RevisionPlan(
        revision_plan_id="plan-1",
        revision_id="r-1",
        plan_version=1,
        previous_revision_reference=None,
        project_id=None,
        timeline_id="tl-1",
        source_quality_report_id="qc-1",
        source_timeline_version=3,
        source_timeline_hash=REF.content_hash,
        target_timeline_version=None,
        review_session_id="s-1",
        created_at=NOW.isoformat(),
        anchor_asset_id="asset-1",
        proposed_changes=(change,),
        instructions=(),
        skipped=(),
        affected_sections=("intro",),
        affected_clips=(),
        problem_references=(),
        responsible_modules=("timeline_builder",),
        expected_improvements=(),
        potential_side_effects=(),
        dependencies=(),
        approval_requirement=Level.SUGGESTED,
        approval_status="awaiting",
        execution_status=PlanStatus.AWAITING_APPROVAL,
        checkpoint_reference="",
        new_revision_reference=None,
        validation_results=None,
        warnings=(),
        provenance=(),
    )


def asset(asset_id: str, name: str) -> MediaAssetDto:
    return MediaAssetDto(
        id=asset_id,
        media_type=MediaType.VIDEO,
        mime_type="video/mp4",
        extension="mp4",
        original_filename=f"{name}.mp4",
        display_name=name,
        file_size=1,
        checksum=asset_id,
        role=AssetRole.ORIGINAL,
        status=AssetStatus.ACTIVE,
        is_favorite=False,
        source_type=SourceType.LOCAL_FILE,
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class ReviewBackend:
    """One fake for every review-side contract; records each call."""

    sessions_by_id: dict[str, ReviewSession] = field(default_factory=lambda: {"s-1": session()})
    plans: list[RevisionPlan] = field(default_factory=lambda: [plan()])
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    refuse: str = ""
    soft: tuple[Blocker, ...] = ()
    hard: tuple[Blocker, ...] = ()

    # MediaLibrary.search
    def search(self, query: MediaQuery | None = None) -> Page[MediaAssetDto]:
        items = [asset("asset-1", "Interview")]
        return Page(items=items, total=len(items), page=1, page_size=50)

    # ReviewCatalog
    def get(self, session_id: str) -> Result[ReviewSession, ValidationError]:
        found = self.sessions_by_id.get(session_id)
        return Ok(found) if found else Err(ValidationError("x", user_message="No such review."))

    def find(
        self, anchor_asset_id: str, *, timeline_id: str | None = None, active_only: bool = True
    ) -> list[ReviewSession]:
        return [s for s in self.sessions_by_id.values() if s.anchor_asset_id == anchor_asset_id]

    def history(self, session_id: str, *, after: int = 0, limit: int = 500) -> list[AuditEntry]:
        return [AuditEntry("opened", NOW, "Ada", ActorKind.HUMAN, "Review opened", 3)]

    # ReviewSessions (the parts the control center calls)
    def _record(self, name: str, *args: object) -> Result[ReviewSession, ValidationError]:
        self.calls.append((name, args))
        if self.refuse:
            return Err(ValidationError("x", user_message=self.refuse))
        return Ok(self.sessions_by_id["s-1"])

    def add_feedback(
        self, session_id: str, text: str, *, author: str, section_id: str | None = None
    ) -> Result[ReviewSession, ValidationError]:
        return self._record("add_feedback", session_id, text, author, section_id)

    def lock_section(
        self,
        session_id: str,
        section_id: str,
        *,
        timeline: TimelineRef,
        actor: Actor,
        note: str = "",
    ) -> Result[ReviewSession, ValidationError]:
        return self._record("lock_section", session_id, section_id, timeline, actor, note)

    def unlock_section(
        self, session_id: str, section_id: str, *, actor: Actor, reason: str
    ) -> Result[ReviewSession, ValidationError]:
        return self._record("unlock_section", session_id, section_id, actor, reason)

    # RevisionCatalog / RevisionDecider
    def plans_for(self) -> list[tuple[RevisionPlan, MediaAssetDto]]:
        return [(p, asset("doc", "plan")) for p in self.plans]

    # ExportApprovalService
    def readiness(self, session_id: str, profile_id: str) -> Result[ReadinessReport, MediaError]:
        self.calls.append(("readiness", (session_id, profile_id)))
        return Ok(
            ReadinessReport(ExportReadiness(self.hard, self.soft, ()), profile_id, "cfg-hash", REF)
        )

    def authorize(
        self, session_id: str, actor: Actor, timeline: TimelineRef, profile_id: str
    ) -> Result[str, ValidationError]:
        self.calls.append(("authorize", (session_id, actor, timeline, profile_id)))
        return Ok("auth-1")

    # RenderProfiles
    def profiles(self) -> tuple[RenderProfile, ...]:
        return tuple(PROFILES)


class RevisionCatalogFake:
    def __init__(self, backend: ReviewBackend) -> None:
        self._b = backend

    def plans(
        self, anchor_asset_id: str, *, session_id: str | None = None
    ) -> Result[list[tuple[RevisionPlan, MediaAssetDto]], MediaError]:
        return Ok(self._b.plans_for())

    def find(self, anchor_asset_id: str, plan_id: str, *, session_id: str | None = None) -> object:
        raise NotImplementedError


class RevisionDeciderFake:
    def __init__(self, backend: ReviewBackend) -> None:
        self._b = backend

    def execute(self, command: DecideRevisionCommand) -> Result[object, ValidationError]:
        self._b.calls.append(("decide", (command,)))
        return Ok(object())


def blocker(message: str, *, hard: bool) -> Blocker:
    return Blocker(f"b-{message}", BlockerKind.CRITICAL_ISSUE, message, hard)


def build_workspace(backend: ReviewBackend) -> ReviewWorkspace:
    # The fakes implement only the methods the control center calls; cast for the type checker.
    return ReviewWorkspace(
        library=cast(MediaLibrary, backend),
        reviews=cast(ReviewCatalog, backend),
        sessions=cast(ReviewSessions, backend),
        revisions=cast(RevisionCatalog, RevisionCatalogFake(backend)),
        decider=cast(RevisionDecider, RevisionDeciderFake(backend)),
        approvals=cast(ExportApprovalService, backend),
        profiles=cast(RenderProfiles, backend),
    )
