"""The review workspace: reads and changes a review ONLY through existing contracts.

* sessions, history, feedback, section locks: ``review_orchestrator`` (``ReviewCatalog`` /
  ``ReviewSessions``)
* proposals and decisions on them: ``revision_engine`` (``RevisionCatalog`` / ``RevisionDecider``),
  which records the decision in the review itself
* export readiness and authorization: ``resolve_export`` (``ExportApprovalService``); the output
  profiles come from ``render_manager`` (``RenderProfiles``). Authorizing exports nothing.
* the anchor asset picker: ``media_library`` (``MediaLibrary.search``)

Every call is blocking (local databases): run them as jobs. Expected refusals come back as
``Err(user message)``.
"""

from dataclasses import dataclass

from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaLibrary,
    MediaQuery,
    MediaType,
)
from media_house.modules.render_manager.application.contracts import Purpose, RenderProfiles
from media_house.modules.resolve_export.application.contracts import (
    ExportApprovalService,
    ReadinessReport,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    AuditEntry,
    ReviewCatalog,
    ReviewSession,
    ReviewSessions,
    SectionStatus,
    TimelineRef,
    Verdict,
)
from media_house.modules.revision_engine.application.contracts import (
    ChangeStatus,
    DecideRevisionCommand,
    ProposalDecision,
    RevisionCatalog,
    RevisionDecider,
    RevisionPlan,
)
from media_house.shared.errors import Err, Ok, Result

__all__ = [
    "OPEN_PROPOSALS",
    "AnchorAsset",
    "ChangeStatus",
    "ExportProfile",
    "ReadinessReport",
    "ReviewSession",
    "ReviewSnapshot",
    "ReviewWorkspace",
    "RevisionPlan",
    "SectionStatus",
    "Verdict",
]

#: Proposals a person can still decide on (a conflict needs an explicit override to approve).
OPEN_PROPOSALS = frozenset({ChangeStatus.PROPOSED, ChangeStatus.CONFLICT})

#: Profiles a final export can be authorized for (previews/intermediates are not deliverables).
_EXPORT_PURPOSES = frozenset({Purpose.DELIVERY, Purpose.MASTER})


@dataclass(frozen=True, slots=True)
class AnchorAsset:
    asset_id: str
    name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class ExportProfile:
    profile_id: str
    name: str
    purpose: str


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    session: ReviewSession
    history: tuple[AuditEntry, ...]
    plans: tuple[RevisionPlan, ...]
    #: Why the plans could not be read (empty: they were).
    plans_error: str = ""


class ReviewWorkspace:
    def __init__(
        self,
        *,
        library: MediaLibrary,
        reviews: ReviewCatalog,
        sessions: ReviewSessions,
        revisions: RevisionCatalog,
        decider: RevisionDecider,
        approvals: ExportApprovalService,
        profiles: RenderProfiles,
    ) -> None:
        self._library = library
        self._reviews = reviews
        self._sessions = sessions
        self._revisions = revisions
        self._decider = decider
        self._approvals = approvals
        self._profiles = profiles

    # --- reads ------------------------------------------------------------------------------
    def anchors(self) -> list[AnchorAsset]:
        """Original audio/video recordings: the assets a review is anchored to."""
        page = self._library.search(
            MediaQuery(
                media_types=frozenset({MediaType.VIDEO, MediaType.AUDIO}),
                roles=frozenset({AssetRole.ORIGINAL}),
                page_size=MediaQuery.MAX_PAGE_SIZE,
            )
        )
        return [AnchorAsset(a.id, a.display_name, a.media_type.value) for a in page.items]

    def sessions(self, anchor_asset_id: str) -> list[ReviewSession]:
        found = self._reviews.find(anchor_asset_id, active_only=False)
        return sorted(found, key=lambda s: s.updated_at, reverse=True)

    def snapshot(self, session_id: str) -> Result[ReviewSnapshot, str]:
        got = self._reviews.get(session_id)
        if isinstance(got, Err):
            return Err(got.error.user_message)
        session = got.value
        history = tuple(self._reviews.history(session_id))
        plans = self._revisions.plans(session.anchor_asset_id, session_id=session_id)
        if isinstance(plans, Err):
            return Ok(ReviewSnapshot(session, history, (), plans.error.user_message))
        return Ok(ReviewSnapshot(session, history, tuple(plan for plan, _ in plans.value)))

    def export_profiles(self) -> list[ExportProfile]:
        return [
            ExportProfile(p.profile_id, p.name, p.purpose.value)
            for p in self._profiles.profiles()
            if p.purpose in _EXPORT_PURPOSES
        ]

    def readiness(self, session_id: str, profile_id: str) -> Result[ReadinessReport, str]:
        found = self._approvals.readiness(session_id, profile_id)
        return Err(found.error.user_message) if isinstance(found, Err) else found

    # --- changes (each one is a person's act) ------------------------------------------------
    def add_feedback(
        self, session_id: str, text: str, *, author: str, section_id: str | None = None
    ) -> Result[None, str]:
        if not text.strip():
            return Err("Write the feedback first.")
        done = self._sessions.add_feedback(
            session_id, text.strip(), author=author, section_id=section_id
        )
        return Err(done.error.user_message) if isinstance(done, Err) else Ok(None)

    def lock_section(
        self, session_id: str, section_id: str, *, timeline: TimelineRef, reviewer: str, note: str
    ) -> Result[None, str]:
        done = self._sessions.lock_section(
            session_id, section_id, timeline=timeline, actor=Actor.human(reviewer), note=note
        )
        return Err(done.error.user_message) if isinstance(done, Err) else Ok(None)

    def unlock_section(
        self, session_id: str, section_id: str, *, reviewer: str, reason: str
    ) -> Result[None, str]:
        if not reason.strip():
            return Err("Give a reason for unlocking the section.")
        done = self._sessions.unlock_section(
            session_id, section_id, actor=Actor.human(reviewer), reason=reason.strip()
        )
        return Err(done.error.user_message) if isinstance(done, Err) else Ok(None)

    def decide_proposal(
        self,
        session_id: str,
        plan_id: str,
        proposal_id: str,
        verdict: Verdict,
        *,
        timeline: TimelineRef,
        reviewer: str,
        note: str = "",
    ) -> Result[None, str]:
        if verdict is Verdict.MODIFIED and not note.strip():
            return Err("Describe the modification you approve.")
        done = self._decider.execute(
            DecideRevisionCommand(
                review_session_id=session_id,
                revision_plan_id=plan_id,
                actor=Actor.human(reviewer),
                timeline=timeline,
                decisions=(ProposalDecision(proposal_id, verdict, note.strip()),),
            )
        )
        return Err(done.error.user_message) if isinstance(done, Err) else Ok(None)

    def authorize_export(
        self, session_id: str, *, timeline: TimelineRef, reviewer: str, profile_id: str
    ) -> Result[str, str]:
        """Record a person's authorization of the final export. Nothing is exported here."""
        done = self._approvals.authorize(session_id, Actor.human(reviewer), timeline, profile_id)
        return Err(done.error.user_message) if isinstance(done, Err) else done
