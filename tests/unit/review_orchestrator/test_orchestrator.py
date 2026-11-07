"""The orchestrator service: it reads the stored timeline, keeps one open review per timeline,
stores every step with its history, and publishes what happened - once."""

from dataclasses import replace

import pytest

from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ApprovalKind,
    ArtifactKind,
    AuthorizeExportCommand,
    CheckpointCommand,
    DecideCommand,
    DecisionRecorded,
    OpenSessionCommand,
    ReviewState,
    ReviewStateChanged,
    TimelineVersionBound,
    Verdict,
)
from media_house.modules.review_orchestrator.application.orchestrator import ReviewOrchestrator
from media_house.modules.review_orchestrator.domain.errors import NotAuthorized, StaleTimeline
from media_house.modules.review_orchestrator.domain.session import ReviewSession
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline
from media_house.shared.errors import ConflictError, Err, Ok
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.review_fakes import InMemoryReviewStore, StubTimelineCatalog
from tests.unit.timeline_builder import fixtures as bf

ADA = Actor.human("Ada")
QC = Actor.system("quality_control")


def revised(timeline: BuiltTimeline, version: int, *, change: bool = True) -> BuiltTimeline:
    """The next version of a timeline; ``change`` moves its first video clip by a frame."""
    tracks = timeline.tracks
    if change:
        out = []
        moved = False
        for track in tracks:
            if track.video and not moved:
                first = replace(track.video[0], end_frame=track.video[0].end_frame - 1)
                track = replace(track, video=(first, *track.video[1:]))
                moved = True
            out.append(track)
        tracks = tuple(out)
    build = replace(
        timeline.build,
        build_version=version,
        build_hash=f"hash-{version}" if change else timeline.build.build_hash,
    )
    return replace(timeline, tracks=tracks, build=build)


class World:
    def __init__(self) -> None:
        self.first = bf.built()
        self.catalog = StubTimelineCatalog(self.first)
        self.store = InMemoryReviewStore()
        self.events = RecordingPublisher()
        self.clock = FixedClock()
        self.orchestrator = ReviewOrchestrator(
            self.store,
            self.catalog,
            self.clock,
            self.events,
        )
        self.anchor = self.first.anchor_asset_id

    def open(self) -> str:
        result = self.orchestrator.open(OpenSessionCommand(self.anchor, "Ada"))
        assert isinstance(result, Ok)
        return result.value.session.session_id

    def session(self, session_id: str) -> ReviewSession:
        found = self.orchestrator.get(session_id)
        assert isinstance(found, Ok)
        return found.value


@pytest.fixture
def world() -> World:
    return World()


def test_opening_binds_the_review_to_the_stored_timeline(world: World) -> None:
    result = world.orchestrator.open(OpenSessionCommand(world.anchor, "Ada", project_id="p1"))

    assert isinstance(result, Ok) and result.value.created
    session = result.value.session
    assert session.state is ReviewState.CREATED and session.project_id == "p1"
    assert session.timeline.content_hash == world.first.build.build_hash
    assert session.timeline.build_asset_id == "build-1"
    assert {s.section_id for s in session.sections}  # one record per section of the timeline


def test_opening_twice_gives_the_same_review(world: World) -> None:
    first = world.open()
    again = world.orchestrator.open(OpenSessionCommand(world.anchor, "Ada"))

    assert isinstance(again, Ok) and not again.value.created
    assert again.value.session.session_id == first
    assert len(world.orchestrator.find(world.anchor)) == 1


def test_a_new_version_is_noticed_and_what_it_voids_is_recorded(world: World) -> None:
    sid = world.open()
    world.orchestrator.advance(sid, ReviewState.ANALYSIS_PENDING, actor=QC)
    session = world.session(sid)
    world.orchestrator.attach(
        sid, ArtifactKind.QUALITY_REPORT, "qr1", timeline=session.timeline, actor=QC
    )
    world.orchestrator.advance(sid, ReviewState.REVIEW_READY, actor=QC)
    world.catalog.publish(
        revised(world.first, 2)
    )  # a person edited in Resolve and it was read back

    synced = world.orchestrator.sync_timeline(sid)

    assert isinstance(synced, Ok)
    assert synced.value.timeline.version == 2
    assert synced.value.state is ReviewState.REVIEW_AGAIN_REQUIRED
    assert synced.value.quality_report_id == ""
    history = [e.kind for e in world.orchestrator.history(sid)]
    assert "timeline.changed" in history and history.index("opened") == 0
    assert [e.sequence for e in world.orchestrator.history(sid)] == list(range(1, len(history) + 1))


def test_a_rebuild_that_changed_nothing_leaves_the_decisions_alone(world: World) -> None:
    sid = world.open()
    world.catalog.publish(revised(world.first, 2, change=False))

    synced = world.orchestrator.sync_timeline(sid)

    assert isinstance(synced, Ok) and synced.value.timeline.version == 2
    assert synced.value.state is ReviewState.CREATED
    assert any(isinstance(e, TimelineVersionBound) and not e.material for e in world.events.events)


def test_a_decision_made_on_a_version_that_is_gone_is_refused(world: World) -> None:
    sid = world.open()
    seen = world.session(sid).timeline
    world.catalog.publish(revised(world.first, 2))
    world.orchestrator.sync_timeline(sid)
    section = world.session(sid).sections[0].section_id

    result = world.orchestrator.decide(
        DecideCommand(sid, ApprovalKind.SECTION, section, Verdict.APPROVED, ADA, seen)
    )

    assert isinstance(result, Err) and isinstance(result.error, StaleTimeline)


def test_decisions_and_moves_are_published_once_and_a_repeat_publishes_nothing(
    world: World,
) -> None:
    sid = world.open()
    ref = world.session(sid).timeline
    section = world.session(sid).sections[0].section_id
    command = DecideCommand(sid, ApprovalKind.SECTION, section, Verdict.APPROVED, ADA, ref)

    world.orchestrator.decide(command)
    published = len(world.events.events)
    world.orchestrator.decide(command)  # the same decision again

    assert len(world.events.events) == published
    assert sum(isinstance(e, DecisionRecorded) for e in world.events.events) == 1
    world.orchestrator.advance(sid, ReviewState.ANALYSIS_PENDING, actor=QC)
    assert any(
        isinstance(e, ReviewStateChanged) and e.target == "analysis_pending"
        for e in world.events.events
    )


def test_a_refused_step_is_an_err_and_leaves_the_review_untouched(world: World) -> None:
    sid = world.open()
    before = len(world.orchestrator.history(sid))

    result = world.orchestrator.advance(sid, ReviewState.FINAL_APPROVED, actor=ADA)

    assert isinstance(result, Err)
    assert world.session(sid).state is ReviewState.CREATED
    assert len(world.orchestrator.history(sid)) == before


def test_an_unknown_review_is_not_found(world: World) -> None:
    assert isinstance(world.orchestrator.get("nope"), Err)
    assert isinstance(
        world.orchestrator.advance("nope", ReviewState.ANALYSIS_PENDING, actor=QC), Err
    )


def test_a_write_that_loses_a_race_is_applied_again_to_the_newer_review(world: World) -> None:
    sid = world.open()
    real_save = world.store.save
    calls = {"n": 0}

    def racing(session, entries, *, expected_revision):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConflictError("lost", user_message="lost")
        return real_save(session, entries, expected_revision=expected_revision)

    world.store.save = racing  # type: ignore[method-assign]

    result = world.orchestrator.add_feedback(sid, "tighten the intro", author="Ada")

    assert isinstance(result, Ok) and calls["n"] == 2
    assert len(world.session(sid).feedback) == 1  # once, not twice


def test_a_checkpoint_reads_the_stored_build_back_and_says_whether_it_is_usable(
    world: World,
) -> None:
    sid = world.open()

    made = world.orchestrator.create_checkpoint(
        CheckpointCommand(sid, "initial", "before review", mix_overrides_hash="m0"), actor=ADA
    )

    assert isinstance(made, Ok)
    checkpoint = made.value.checkpoints[0]
    assert checkpoint.verified and checkpoint.timeline.version == 1
    assert checkpoint.mix_overrides_hash == "m0"


def test_a_checkpoint_of_a_build_that_no_longer_matches_is_not_verified(world: World) -> None:
    sid = world.open()
    world.catalog._builds["build-1"] = revised(world.first, 1)  # the stored build changed under it

    made = world.orchestrator.create_checkpoint(CheckpointCommand(sid, "x"), actor=ADA)

    assert isinstance(made, Ok) and not made.value.checkpoints[0].verified


def _approved_for_export(world: World) -> tuple[str, AuthorizeExportCommand]:
    sid = world.open()
    o = world.orchestrator
    ref = world.session(sid).timeline
    o.advance(sid, ReviewState.ANALYSIS_PENDING, actor=QC)
    o.attach(sid, ArtifactKind.QUALITY_REPORT, "qr1", timeline=ref, actor=QC)
    o.advance(sid, ReviewState.REVIEW_READY, actor=QC)
    o.advance(sid, ReviewState.FINAL_POLISH_PENDING, actor=QC)
    o.attach(sid, ArtifactKind.FINAL_POLISH_REPORT, "fp1", timeline=ref, actor=QC)
    o.advance(sid, ReviewState.AWAITING_FINAL_APPROVAL, actor=QC)
    o.decide(DecideCommand(sid, ApprovalKind.FINAL, "final", Verdict.APPROVED, ADA, ref))
    return sid, AuthorizeExportCommand(sid, ADA, ref, "youtube", "cfg", "qr1", "fp1")


def test_the_export_is_authorized_and_verified_for_exactly_that_output(world: World) -> None:
    sid, command = _approved_for_export(world)

    authorized = world.orchestrator.authorize_export(command)

    assert isinstance(authorized, Ok) and authorized.value.state is ReviewState.EXPORT_AUTHORIZED
    ident = authorized.value.export_authorization_id
    ref = authorized.value.timeline
    good = world.orchestrator.verify_export(
        sid, ident, timeline=ref, export_profile_id="youtube", output_config_hash="cfg"
    )
    wrong = world.orchestrator.verify_export(
        sid, ident, timeline=ref, export_profile_id="youtube", output_config_hash="different"
    )
    assert isinstance(good, Ok) and isinstance(wrong, Err)
    assert isinstance(wrong.error, NotAuthorized)


def test_an_edit_after_authorization_is_noticed_when_the_export_asks_to_start(
    world: World,
) -> None:
    sid, command = _approved_for_export(world)
    authorized = world.orchestrator.authorize_export(command)
    assert isinstance(authorized, Ok)
    world.catalog.publish(revised(world.first, 2))  # the person changed the timeline afterwards

    result = world.orchestrator.verify_export(
        sid,
        authorized.value.export_authorization_id,
        timeline=authorized.value.timeline,
        export_profile_id="youtube",
        output_config_hash="cfg",
    )

    assert isinstance(result, Err) and isinstance(result.error, NotAuthorized)
    assert world.session(sid).state is ReviewState.REVIEW_AGAIN_REQUIRED


def test_authorizing_looks_at_the_stored_timeline_first(world: World) -> None:
    sid, command = _approved_for_export(world)
    world.catalog.publish(revised(world.first, 2))  # changed after the person approved

    result = world.orchestrator.authorize_export(command)

    assert isinstance(result, Err)
    assert world.session(sid).state is ReviewState.REVIEW_AGAIN_REQUIRED
