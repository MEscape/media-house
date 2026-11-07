"""The rules of a review: states, version binding, who may decide, what a change voids."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from media_house.modules.review_orchestrator.domain import workflow as w
from media_house.modules.review_orchestrator.domain.errors import (
    InvalidTransition,
    LockedSection,
    NotAuthorized,
    NotPermitted,
    StaleTimeline,
    UnknownSubject,
)
from media_house.modules.review_orchestrator.domain.permissions import (
    Authority,
    Operation,
    PermissionPolicy,
)
from media_house.modules.review_orchestrator.domain.session import ReviewSession
from media_house.modules.review_orchestrator.domain.timeline import TimelineFacts
from media_house.modules.review_orchestrator.domain.values import (
    ActorKind,
    ApprovalKind,
    ArtifactKind,
    ExportStatus,
    ReviewState,
    SectionStatus,
    ValidationStatus,
    Verdict,
)
from tests.support.fakes import T0
from tests.support.review_fakes import make_facts

S = ReviewState
H = ActorKind.HUMAN
ADA = "Ada"


class Clockwork:
    """A session driven step by step; every step gets the next minute."""

    def __init__(self, session: ReviewSession) -> None:
        self.session = session
        self.audit: list[str] = []
        self._tick = 0

    @property
    def now(self) -> datetime:
        self._tick += 1
        return T0 + timedelta(minutes=self._tick)

    def state(self) -> ReviewState:
        return self.session.state

    def run(self, step: w.Update) -> ReviewSession:
        self.session = step.session
        self.audit += [e.kind for e in step.entries]
        return self.session

    def to(self, state: ReviewState, actor_kind: ActorKind = ActorKind.SYSTEM) -> ReviewSession:
        return self.run(
            w.advance(self.session, state, now=self.now, actor="t", actor_kind=actor_kind)
        )

    def bind(self, facts: TimelineFacts) -> ReviewSession:
        return self.run(
            w.bind_timeline(
                self.session, facts, now=self.now, actor="sync", actor_kind=ActorKind.SYSTEM
            )
        )

    def attach(self, kind: ArtifactKind, ident: str) -> ReviewSession:
        return self.run(
            w.attach_artifact(
                self.session, kind, ident, timeline=self.session.timeline, now=self.now,
                actor="qc", actor_kind=ActorKind.SYSTEM,
            )
        )  # fmt: skip

    def decide(
        self, kind: ApprovalKind, subject: str, verdict: Verdict = Verdict.APPROVED,
        actor_kind: ActorKind = H, **kw: object,
    ) -> ReviewSession:  # fmt: skip
        return self.run(
            w.decide(
                self.session, kind=kind, subject_id=subject, verdict=verdict, actor=ADA,
                actor_kind=actor_kind, timeline=self.session.timeline, now=self.now, **kw,  # type: ignore[arg-type]
            )
        )  # fmt: skip


def opened(policy: PermissionPolicy | None = None, facts: TimelineFacts | None = None) -> Clockwork:
    update = w.open_session(
        session_id="s1", project_id="p", facts=facts or make_facts(1), reviewer=ADA,
        policy=policy or PermissionPolicy(), resolve_project_ref="rp", resolve_timeline_ref="rt",
        now=T0,
    )  # fmt: skip
    return Clockwork(update.session)


def awaiting_revision_approval(c: Clockwork) -> Clockwork:
    c.to(S.ANALYSIS_PENDING)
    c.attach(ArtifactKind.QUALITY_REPORT, "qr1")
    c.to(S.REVISION_PROPOSED)
    c.attach(ArtifactKind.REVISION_PLAN, "plan1")
    c.to(S.AWAITING_REVISION_APPROVAL)
    return c


def at_final_approval(c: Clockwork) -> Clockwork:
    c.to(S.ANALYSIS_PENDING)
    c.attach(ArtifactKind.QUALITY_REPORT, "qr1")
    c.to(S.REVIEW_READY)
    c.to(S.FINAL_POLISH_PENDING)
    c.attach(ArtifactKind.FINAL_POLISH_REPORT, "fp1")
    c.to(S.AWAITING_FINAL_APPROVAL)
    return c


def authorized(c: Clockwork, *, blocking: tuple[str, ...] = ()) -> Clockwork:
    at_final_approval(c)
    c.decide(ApprovalKind.FINAL, "final")
    c.run(
        w.authorize_export(
            c.session, timeline=c.session.timeline, export_profile_id="youtube",
            output_config_hash="cfg", quality_report_id="qr1", polish_report_id="fp1",
            blocking_unresolved=blocking, actor=ADA, actor_kind=H, now=c.now,
        )
    )  # fmt: skip
    return c


# --- the state machine -----------------------------------------------------------------------


def test_a_review_starts_created_with_every_section_unreviewed() -> None:
    session = opened().session

    assert session.state is S.CREATED
    assert [s.status for s in session.sections] == [SectionStatus.UNREVIEWED] * 3
    assert session.timeline.version == 1 and session.timeline_history == (session.timeline,)


def test_the_normal_path_to_human_review_is_open() -> None:
    c = opened()
    c.to(S.ANALYSIS_PENDING)
    c.attach(ArtifactKind.QUALITY_REPORT, "qr1")

    assert c.state() is S.ANALYSIS_COMPLETE  # the report moved it on
    c.to(S.REVIEW_READY)
    c.to(S.AWAITING_HUMAN_REVIEW)
    assert c.state() is S.AWAITING_HUMAN_REVIEW


def test_a_move_the_table_does_not_list_is_refused_and_nothing_changes() -> None:
    c = opened()

    with pytest.raises(InvalidTransition):
        c.to(S.REVIEW_READY)
    assert c.state() is S.CREATED


@pytest.mark.parametrize(
    "state",
    [
        S.REVISION_APPROVED, S.REVISION_REJECTED, S.FINAL_APPROVED, S.EXPORT_AUTHORIZED,
        S.EXPORT_IN_PROGRESS, S.EXPORT_VALIDATED, S.EXPORT_FAILED, S.DELIVERY_READY,
    ],
)  # fmt: skip
def test_states_that_stand_for_a_decision_cannot_be_entered_by_a_plain_move(
    state: ReviewState,
) -> None:
    c = opened()

    with pytest.raises(InvalidTransition, match="own operation"):
        w.advance(c.session, state, now=T0, actor="x", actor_kind=ActorKind.SYSTEM)


def test_a_cancelled_review_goes_nowhere() -> None:
    c = opened()
    c.run(w.cancel(c.session, now=c.now, actor=ADA, actor_kind=H, reason="dropped"))

    with pytest.raises(InvalidTransition):
        c.to(S.ANALYSIS_PENDING)


def test_final_review_needs_a_polish_report_for_this_version() -> None:
    c = opened()
    c.to(S.ANALYSIS_PENDING)
    c.attach(ArtifactKind.QUALITY_REPORT, "qr1")
    c.to(S.REVIEW_READY)
    c.to(S.FINAL_POLISH_PENDING)

    with pytest.raises(InvalidTransition, match="polish"):
        c.to(S.FINAL_REVIEW_READY)


def test_a_report_for_another_version_is_refused() -> None:
    c = opened()
    c.to(S.ANALYSIS_PENDING)

    with pytest.raises(StaleTimeline):
        w.attach_artifact(
            c.session, ArtifactKind.QUALITY_REPORT, "old",
            timeline=make_facts(7, content="other").ref,
            now=T0, actor="qc", actor_kind=ActorKind.SYSTEM,
        )  # fmt: skip


def test_the_same_report_twice_is_one_report() -> None:
    c = opened()
    c.to(S.ANALYSIS_PENDING)
    c.attach(ArtifactKind.QUALITY_REPORT, "qr1")

    again = w.attach_artifact(
        c.session, ArtifactKind.QUALITY_REPORT, "qr1", timeline=c.session.timeline, now=T0,
        actor="qc", actor_kind=ActorKind.SYSTEM,
    )  # fmt: skip

    assert not again.changed and again.entries == ()


# --- decisions are bound to a version and to who may take them -------------------------------


def test_a_decision_on_an_older_version_is_refused() -> None:
    c = awaiting_revision_approval(opened())
    seen = c.session.timeline
    c.bind(make_facts(2, sections=(("intro", "d-intro"), ("body", "CHANGED"), ("end", "d-end"))))

    with pytest.raises(StaleTimeline, match="version 1"):
        w.decide(
            c.session, kind=ApprovalKind.REVISION, subject_id="plan1", verdict=Verdict.APPROVED,
            actor=ADA, actor_kind=H, timeline=seen, now=T0,
        )  # fmt: skip


def test_the_system_never_decides_for_a_person() -> None:
    c = awaiting_revision_approval(opened())

    with pytest.raises(NotPermitted):
        c.decide(ApprovalKind.REVISION, "plan1", actor_kind=ActorKind.SYSTEM)


def test_a_suggested_revision_cannot_be_approved_by_automation() -> None:
    c = awaiting_revision_approval(opened())

    with pytest.raises(NotPermitted, match="needs a person"):
        c.decide(
            ApprovalKind.REVISION, "plan1", actor_kind=ActorKind.PRECONFIGURED,
            operation=Operation.LOCAL_CREATIVE_REVISION,
        )  # fmt: skip


def test_a_technical_repair_the_project_authorized_in_advance_may_run_unattended() -> None:
    policy = PermissionPolicy(grants=((Operation.TECHNICAL_REPAIR, Authority.AUTOMATIC),))
    c = awaiting_revision_approval(opened(policy))

    c.decide(
        ApprovalKind.REVISION, "plan1", actor_kind=ActorKind.PRECONFIGURED,
        operation=Operation.TECHNICAL_REPAIR,
    )  # fmt: skip

    assert c.state() is S.REVISION_APPROVED
    assert c.session.approvals[-1].manual is False


def test_no_grant_can_make_the_final_export_automatic() -> None:
    policy = PermissionPolicy(grants=((Operation.FINAL_EXPORT, Authority.AUTOMATIC),))

    assert policy.authority(Operation.FINAL_EXPORT) is Authority.MANUAL_ONLY
    assert policy.authority(Operation.SECTION_UNLOCK) is Authority.MANUAL_ONLY
    assert not policy.allows_unattended(Operation.FINAL_EXPORT)


def test_a_modified_approval_must_say_what_changed() -> None:
    c = awaiting_revision_approval(opened())

    with pytest.raises(NotPermitted, match="say what"):
        c.decide(ApprovalKind.REVISION, "plan1", Verdict.MODIFIED)


def test_the_same_decision_twice_is_recorded_once() -> None:
    c = awaiting_revision_approval(opened())
    c.decide(ApprovalKind.PROPOSAL, "plan1:p1")
    again = w.decide(
        c.session, kind=ApprovalKind.PROPOSAL, subject_id="plan1:p1", verdict=Verdict.APPROVED,
        actor=ADA, actor_kind=H, timeline=c.session.timeline, now=T0,
    )  # fmt: skip

    assert not again.changed and len(c.session.approvals) == 1


def test_a_rejected_proposal_is_remembered_so_it_is_not_made_again() -> None:
    c = awaiting_revision_approval(opened())
    c.decide(ApprovalKind.PROPOSAL, "plan1:p1", Verdict.REJECTED, note="I like it as it is")
    c.decide(ApprovalKind.PROPOSAL, "plan1:p2", Verdict.APPROVED)

    assert c.session.rejected_subjects() == {"plan1:p1"}


# --- revisions -------------------------------------------------------------------------------


def test_an_approved_revision_runs_and_its_approval_is_used_up() -> None:
    c = awaiting_revision_approval(opened())
    c.decide(ApprovalKind.REVISION, "plan1", sections=("body",))
    assert c.state() is S.REVISION_APPROVED

    c.run(w.begin_revision(c.session, "plan1", now=c.now, actor="rev", actor_kind=ActorKind.SYSTEM))
    c.run(
        w.record_revision_result(
            c.session, "plan1",
            make_facts(2, sections=(("intro", "d-intro"), ("body", "NEW"), ("end", "d-end"))),
            now=c.now, actor="rev", actor_kind=ActorKind.SYSTEM,
        )
    )  # fmt: skip

    assert c.state() is S.REVISION_COMPLETE and c.session.timeline.version == 2
    plan_approval = c.session.decisions_on("plan1", ApprovalKind.REVISION)[0]
    assert plan_approval.consumed_by_version == 2 and plan_approval.valid


def test_a_rejected_revision_does_not_execute() -> None:
    c = awaiting_revision_approval(opened())
    c.decide(ApprovalKind.REVISION, "plan1", Verdict.REJECTED)

    assert c.state() is S.REVISION_REJECTED
    with pytest.raises(InvalidTransition):
        w.begin_revision(c.session, "plan1", now=T0, actor="rev", actor_kind=ActorKind.SYSTEM)


def test_a_revision_never_starts_without_a_current_approval_of_that_plan() -> None:
    c = awaiting_revision_approval(opened())
    c.decide(ApprovalKind.REVISION, "plan1")

    with pytest.raises(NotAuthorized, match="plan2"):
        w.begin_revision(c.session, "plan2", now=T0, actor="rev", actor_kind=ActorKind.SYSTEM)


def test_a_timeline_change_after_approval_makes_the_approved_plan_stale() -> None:
    c = awaiting_revision_approval(opened())
    c.decide(ApprovalKind.REVISION, "plan1", sections=("body",))

    c.bind(
        make_facts(
            2, sections=(("intro", "d-intro"), ("body", "a person moved a clip"), ("end", "d-end"))
        )
    )

    assert c.state() is S.REVIEW_AGAIN_REQUIRED
    assert not c.session.decisions_on("plan1")[0].valid
    assert c.session.quality_report_id == ""  # the report described another video
    assert "approval.invalidated" in c.audit and "timeline.changed" in c.audit


def test_a_modified_plan_goes_back_to_be_proposed_again() -> None:
    c = awaiting_revision_approval(opened())

    c.decide(ApprovalKind.REVISION, "plan1", Verdict.MODIFIED, modification="keep the second shot")

    assert c.state() is S.REVISION_PROPOSED


def test_a_failed_revision_returns_to_a_person_not_to_success() -> None:
    c = awaiting_revision_approval(opened())
    c.decide(ApprovalKind.REVISION, "plan1")
    c.run(w.begin_revision(c.session, "plan1", now=c.now, actor="rev", actor_kind=ActorKind.SYSTEM))

    c.run(
        w.revision_failed(
            c.session, "plan1", "no media", now=c.now, actor="rev", actor_kind=ActorKind.SYSTEM
        )
    )

    assert c.state() is S.AWAITING_HUMAN_REVIEW


# --- sections --------------------------------------------------------------------------------


def test_a_changed_section_loses_its_approval_and_the_others_keep_theirs() -> None:
    c = opened()
    c.to(S.ANALYSIS_PENDING)
    c.decide(ApprovalKind.SECTION, "intro")
    c.decide(ApprovalKind.SECTION, "body")

    c.bind(make_facts(2, sections=(("intro", "d-intro"), ("body", "changed"), ("end", "d-end"))))

    assert c.session.section("intro").status is SectionStatus.APPROVED  # type: ignore[union-attr]
    body = c.session.section("body")
    assert body is not None and body.status is SectionStatus.UNREVIEWED
    assert c.session.current_approval("intro", ApprovalKind.SECTION) is not None
    assert c.session.current_approval("body", ApprovalKind.SECTION) is None


def test_a_rebuild_with_the_same_content_keeps_every_decision() -> None:
    c = opened()
    c.decide(ApprovalKind.SECTION, "intro")

    c.bind(make_facts(2, content="hash-1"))  # a new version number, the same video

    assert c.session.timeline.version == 2
    assert c.session.current_approval("intro", ApprovalKind.SECTION) is not None
    assert c.audit[-1] == "timeline.rebound"


def test_binding_the_version_the_review_already_has_does_nothing() -> None:
    c = opened()

    again = w.bind_timeline(
        c.session, make_facts(1), now=T0, actor="s", actor_kind=ActorKind.SYSTEM
    )

    assert not again.changed


def test_a_locked_section_keeps_automation_out() -> None:
    c = opened()
    c.run(w.lock_section(c.session, "intro", timeline=c.session.timeline, now=c.now, actor=ADA))

    with pytest.raises(LockedSection, match="intro"):
        w.guard_locked(c.session, ("body", "intro"), "a caption change")
    c.to(S.ANALYSIS_PENDING)
    c.attach(ArtifactKind.QUALITY_REPORT, "qr1")
    c.to(S.REVISION_PROPOSED)
    c.to(S.AWAITING_REVISION_APPROVAL)
    with pytest.raises(LockedSection):
        c.decide(ApprovalKind.REVISION, "plan1", sections=("intro",))


def test_a_person_can_approve_a_revision_of_a_locked_section_by_unlocking_it_on_record() -> None:
    c = awaiting_revision_approval(opened())
    c.run(w.lock_section(c.session, "intro", timeline=c.session.timeline, now=c.now, actor=ADA))

    c.decide(ApprovalKind.REVISION, "plan1", sections=("intro",), unlock_sections=("intro",))

    assert c.session.section("intro").status is SectionStatus.UNDER_REVIEW  # type: ignore[union-attr]
    assert "section.unlocked" in c.audit


def test_only_a_person_unlocks() -> None:
    c = opened()
    c.run(w.lock_section(c.session, "intro", timeline=c.session.timeline, now=c.now, actor=ADA))

    with pytest.raises(NotPermitted):
        w.unlock_section(
            c.session, "intro", now=T0, actor="rev", actor_kind=ActorKind.PRECONFIGURED, reason="x"
        )


def test_a_lock_survives_a_change_but_says_the_section_moved() -> None:
    c = opened()
    c.run(w.lock_section(c.session, "body", timeline=c.session.timeline, now=c.now, actor=ADA))

    c.bind(
        make_facts(
            2, sections=(("intro", "d-intro"), ("body", "moved in Resolve"), ("end", "d-end"))
        )
    )

    body = c.session.section("body")
    assert body is not None and body.status is SectionStatus.LOCKED and body.drift
    assert "body" in c.session.locked_sections()


def test_an_unknown_section_is_named() -> None:
    c = opened()

    with pytest.raises(UnknownSubject):
        w.lock_section(c.session, "nope", timeline=c.session.timeline, now=T0, actor=ADA)


def test_feedback_is_kept_as_said_until_a_plan_takes_it_up() -> None:
    c = opened()
    c.to(S.ANALYSIS_PENDING)
    c.attach(ArtifactKind.QUALITY_REPORT, "qr1")
    c.to(S.REVIEW_READY)

    c.run(
        w.add_feedback(
            c.session, text="The intro is too long.", author=ADA, section_id="intro", now=c.now
        )
    )

    assert c.state() is S.FEEDBACK_RECEIVED
    item = c.session.pending_feedback()[0]
    assert item.text == "The intro is too long." and item.timeline_version == 1
    c.run(w.feedback_handled(c.session, (item.feedback_id,), "plan1", now=c.now))
    assert c.session.pending_feedback() == ()


# --- the final export ------------------------------------------------------------------------


def test_the_final_approval_is_a_persons_and_only_in_the_right_state() -> None:
    c = at_final_approval(opened())

    with pytest.raises(NotPermitted):
        c.decide(ApprovalKind.FINAL, "final", actor_kind=ActorKind.PRECONFIGURED)
    c.decide(ApprovalKind.FINAL, "final")

    assert c.state() is S.FINAL_APPROVED and c.session.final_approval_id


def test_a_final_approval_cannot_be_given_before_the_final_review_exists() -> None:
    c = opened()

    with pytest.raises(InvalidTransition):
        c.decide(ApprovalKind.FINAL, "final")


def test_the_export_is_authorized_only_by_a_person_for_exactly_this_video() -> None:
    c = at_final_approval(opened())
    c.decide(ApprovalKind.FINAL, "final")

    with pytest.raises(NotPermitted):
        w.authorize_export(
            c.session, timeline=c.session.timeline, export_profile_id="youtube",
            output_config_hash="cfg", quality_report_id="qr1", polish_report_id="fp1",
            blocking_unresolved=(), actor="auto", actor_kind=ActorKind.PRECONFIGURED, now=T0,
        )  # fmt: skip
    authorized(opened())  # and the person's path works


def test_a_high_quality_score_is_not_an_authorization() -> None:
    c = at_final_approval(opened())  # reports are there, nobody approved the video

    with pytest.raises(InvalidTransition):
        w.authorize_export(
            c.session, timeline=c.session.timeline, export_profile_id="youtube",
            output_config_hash="cfg", quality_report_id="qr1", polish_report_id="fp1",
            blocking_unresolved=(), actor=ADA, actor_kind=H, now=T0,
        )  # fmt: skip


def test_an_issue_that_blocks_the_export_needs_a_persons_override() -> None:
    c = at_final_approval(opened())
    c.decide(ApprovalKind.FINAL, "final")
    kwargs: dict[str, Any] = {
        "timeline": c.session.timeline,
        "export_profile_id": "youtube",
        "output_config_hash": "cfg",
        "quality_report_id": "qr1",
        "polish_report_id": "fp1",
        "blocking_unresolved": ("issue-9",),
        "actor": ADA,
        "actor_kind": H,
        "now": T0,
    }

    with pytest.raises(NotAuthorized, match="issue-9"):
        w.authorize_export(c.session, **kwargs)
    c.decide(ApprovalKind.OVERRIDE, "issue-9", note="known and accepted")
    w.authorize_export(c.session, **kwargs)


def test_authorization_names_the_profile_the_output_and_the_version() -> None:
    c = authorized(opened())
    ident = c.session.export_authorization_id
    ref = c.session.timeline

    assert c.state() is S.EXPORT_AUTHORIZED
    assert (
        w.verify_export_authorization(
            c.session, ident, timeline=ref, export_profile_id="youtube", output_config_hash="cfg"
        ).authorized_by
        == ADA
    )
    with pytest.raises(NotAuthorized, match="profile"):
        w.verify_export_authorization(
            c.session, ident, timeline=ref, export_profile_id="master", output_config_hash="cfg"
        )
    with pytest.raises(NotAuthorized, match="output settings"):
        w.verify_export_authorization(
            c.session, ident, timeline=ref, export_profile_id="youtube", output_config_hash="other"
        )
    with pytest.raises(NotAuthorized, match="another version"):
        w.verify_export_authorization(
            c.session, ident, timeline=make_facts(5, content="x").ref,
            export_profile_id="youtube", output_config_hash="cfg",
        )  # fmt: skip


def test_an_approval_of_an_old_version_cannot_authorize_a_changed_video() -> None:
    c = authorized(opened())
    ident = c.session.export_authorization_id
    old = c.session.timeline

    c.bind(
        make_facts(2, sections=(("intro", "d-intro"), ("body", "d-body"), ("end", "a new ending")))
    )

    assert c.state() is S.REVIEW_AGAIN_REQUIRED
    assert c.session.final_approval_id == "" and c.session.export_status is ExportStatus.NONE
    assert not c.session.authorizations[0].valid
    with pytest.raises(NotAuthorized):
        w.verify_export_authorization(
            c.session, ident, timeline=old, export_profile_id="youtube", output_config_hash="cfg"
        )


def test_the_export_lifecycle_to_a_validated_file() -> None:
    c = authorized(opened())
    ident = c.session.export_authorization_id
    c.run(
        w.begin_export(c.session, ident, "exp1", now=c.now, actor="re", actor_kind=ActorKind.SYSTEM)
    )
    c.run(w.export_rendered(c.session, now=c.now, actor="re", actor_kind=ActorKind.SYSTEM))
    c.run(
        w.record_export_validation(
            c.session,
            ValidationStatus.PASS,
            "ev1",
            now=c.now,
            actor="ev",
            actor_kind=ActorKind.SYSTEM,
        )
    )

    assert c.state() is S.EXPORT_VALIDATED and c.session.export_status is ExportStatus.VALIDATED
    with pytest.raises(NotAuthorized, match="delivery"):
        w.mark_delivery_ready(c.session, now=T0, actor="re", actor_kind=ActorKind.SYSTEM)
    c.decide(ApprovalKind.DELIVERY, "exp1")
    c.run(w.mark_delivery_ready(c.session, now=c.now, actor="re", actor_kind=ActorKind.SYSTEM))
    assert c.state() is S.DELIVERY_READY


def test_delivery_needs_no_second_approval_when_the_project_says_so() -> None:
    c = authorized(opened(PermissionPolicy(require_delivery_approval=False)))
    c.run(
        w.begin_export(
            c.session,
            c.session.export_authorization_id,
            "exp1",
            now=c.now,
            actor="re",
            actor_kind=ActorKind.SYSTEM,
        )
    )
    c.run(w.export_rendered(c.session, now=c.now, actor="re", actor_kind=ActorKind.SYSTEM))
    c.run(
        w.record_export_validation(
            c.session,
            ValidationStatus.WARNING,
            "ev1",
            now=c.now,
            actor="ev",
            actor_kind=ActorKind.SYSTEM,
        )
    )

    c.run(w.mark_delivery_ready(c.session, now=c.now, actor="re", actor_kind=ActorKind.SYSTEM))

    assert c.state() is S.DELIVERY_READY


def test_a_failed_validation_blocks_delivery() -> None:
    c = authorized(opened())
    c.run(
        w.begin_export(
            c.session,
            c.session.export_authorization_id,
            "exp1",
            now=c.now,
            actor="re",
            actor_kind=ActorKind.SYSTEM,
        )
    )
    c.run(w.export_rendered(c.session, now=c.now, actor="re", actor_kind=ActorKind.SYSTEM))

    c.run(
        w.record_export_validation(
            c.session,
            ValidationStatus.FAIL,
            "ev1",
            now=c.now,
            actor="ev",
            actor_kind=ActorKind.SYSTEM,
        )
    )

    assert c.state() is S.EXPORT_FAILED
    with pytest.raises(InvalidTransition):
        w.mark_delivery_ready(c.session, now=T0, actor="re", actor_kind=ActorKind.SYSTEM)


def test_a_validation_that_cannot_decide_waits_for_a_person_and_is_never_a_pass() -> None:
    c = authorized(opened(PermissionPolicy(require_delivery_approval=False)))
    c.run(
        w.begin_export(
            c.session,
            c.session.export_authorization_id,
            "exp1",
            now=c.now,
            actor="re",
            actor_kind=ActorKind.SYSTEM,
        )
    )
    c.run(w.export_rendered(c.session, now=c.now, actor="re", actor_kind=ActorKind.SYSTEM))

    c.run(
        w.record_export_validation(
            c.session,
            ValidationStatus.MANUAL_REVIEW_REQUIRED,
            "ev1",
            now=c.now,
            actor="ev",
            actor_kind=ActorKind.SYSTEM,
        )
    )

    assert c.state() is S.EXPORT_VALIDATION_PENDING
    assert c.session.export_status is ExportStatus.MANUAL_REVIEW
    with pytest.raises(InvalidTransition):
        w.mark_delivery_ready(c.session, now=T0, actor="re", actor_kind=ActorKind.SYSTEM)
    with pytest.raises(NotPermitted):
        w.resolve_manual_validation(
            c.session, True, now=T0, actor="re", actor_kind=ActorKind.SYSTEM, note=""
        )
    c.run(
        w.resolve_manual_validation(
            c.session, True, now=c.now, actor=ADA, actor_kind=H, note="looks right"
        )
    )
    assert c.state() is S.EXPORT_VALIDATED


def test_one_authorization_starts_one_export_and_a_failed_one_may_be_retried() -> None:
    c = authorized(opened())
    ident = c.session.export_authorization_id
    system = ActorKind.SYSTEM
    c.run(w.begin_export(c.session, ident, "exp1", now=c.now, actor="re", actor_kind=system))
    c.run(w.export_failed(c.session, "render error", now=c.now, actor="re", actor_kind=system))

    c.run(w.retry_export(c.session, now=c.now, actor="re", actor_kind=system))

    assert c.state() is S.EXPORT_AUTHORIZED
    c.run(w.begin_export(c.session, ident, "exp2", now=c.now, actor="re", actor_kind=system))
    # a second export on the same authorization, while exp2 holds it, is refused
    again = replace(c.session, state=S.EXPORT_AUTHORIZED)
    with pytest.raises(NotAuthorized, match="already started"):
        w.begin_export(again, ident, "exp3", now=T0, actor="re", actor_kind=system)


def test_an_export_cannot_start_without_authorization() -> None:
    c = at_final_approval(opened())

    with pytest.raises(InvalidTransition):
        w.begin_export(c.session, "none", "exp1", now=T0, actor="re", actor_kind=ActorKind.SYSTEM)
