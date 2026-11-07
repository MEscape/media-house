"""The final export, with the real review, the real render manager and the real validator (the
render backend and the media measurements are stand-ins): a person's decisions are the only way in,
and what failed is never delivered."""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.core.application.ports import DecodeReport
from media_house.modules.export_validation.application.store import ValidationStore
from media_house.modules.export_validation.application.validate import ValidateExport
from media_house.modules.resolve_export.application.approvals import ExportApprovals
from media_house.modules.resolve_export.application.inputs import ExportInputs, InputsReader
from media_house.modules.resolve_export.application.run_export import ExportRunner, RunExportCommand
from media_house.modules.resolve_export.application.store import ExportStore
from media_house.modules.resolve_export.domain.errors import DeliveryRefused, NotReadyForExport
from media_house.modules.resolve_export.domain.readiness import (
    IssueFact,
    PolishProblemFact,
    ReadinessFacts,
)
from media_house.modules.resolve_export.domain.values import DeliveryStatus, ExportStatus
from media_house.modules.resolve_export.infrastructure.delivery_store import FilesystemDeliveryStore
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ArtifactKind,
    NotAuthorized,
    NotPermitted,
    ReviewSession,
    ReviewState,
    TimelineRef,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from tests.support.layer6_fakes import FakeAnalyzer
from tests.unit.render_manager.test_render import World as RenderWorld

CTX = JobContext.detached()
ADA, SYS = Actor.human("Ada"), Actor.system("pipeline")
PROFILE = "youtube_16_9"


class NoMixes:
    def latest_result(self, anchor: str, timeline_id: str | None) -> Ok[None]:
        return Ok(None)

    def latest_spec(self, anchor: str, timeline_id: str | None) -> Ok[None]:
        return Ok(None)

    def find_spec(self, anchor: str, content_hash: str) -> Ok[None]:
        return Ok(None)


class FakeResolve:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command: object, ctx: object) -> Result[object, object]:
        self.calls += 1
        return Ok(None)


class StubInputs(InputsReader):
    """The reader, with the reports of the review given instead of looked up."""

    def __init__(self, world: "World") -> None:
        self.world = world

    def read(
        self, session_id: str, profile_id: str, *, high_issues_block: bool | None = None
    ) -> Result[ExportInputs, NotReadyForExport]:
        w = self.world
        session = w.r.sessions.get(session_id).value  # type: ignore[union-attr]
        timeline = w.r.catalog.latest(w.r.anchor).value[0]  # type: ignore[union-attr,index]
        resolved = w.r.profiles.resolve(profile_id, anchor_asset_id=w.r.anchor)
        resolution = resolved.value if isinstance(resolved, Ok) else None
        facts = replace(
            w.facts,
            timeline_hash=timeline.build.build_hash,
            review_hash=session.timeline.content_hash,
            profile_known=isinstance(resolved, Ok),
            profile_problems=resolution.problems if resolution else (),
        )
        return Ok(ExportInputs(session, timeline, None, None, None, None, resolution, facts))


class World:
    def __init__(self, tmp_path: Path, *, review_render: bool = True) -> None:
        self.r = RenderWorld(tmp_path)
        self.analyzer = FakeAnalyzer()
        self.resolve = FakeResolve()
        self.validator = ValidateExport(
            self.r.library.contract(), self.r.catalog, self.r.inspector, self.analyzer,
            self.r.history, self.r.profiles, NoMixes(), self.r.sessions,
            ValidationStore(self.r.library.contract(), self.r.paths), self.r.paths,
            self.r.clock, self.r.events,
        )  # fmt: skip
        self.exports = ExportStore(self.r.library.contract(), self.r.paths)
        self.delivery_root = tmp_path / "deliveries"
        self.delivery = FilesystemDeliveryStore(self.delivery_root)
        h = self.r.timeline.build.build_hash
        self.facts = ReadinessFacts(
            timeline_hash=h, review_hash=h, quality_report_id="qr1", quality_hash=h,
            quality_issues=(), polish_report_id="fp1", polish_hash=h,
            polish_readiness="ready_for_final_review", polish_problems=(), mix_state="completed",
            mix_errors=(), offline_sources=(), resolve_hash=h, resolve_conflicts=(),
            profile_known=True, profile_problems=(), high_issues_block=True,
        )  # fmt: skip
        self.inputs = StubInputs(self)
        self.approvals = ExportApprovals(
            self.inputs,
            self.r.sessions,
            self.resolve,  # type: ignore[arg-type]
        )
        self.runner = ExportRunner(
            self.r.library.contract(),
            self.inputs,
            self.r.sessions,
            self.r.service,
            self.r.history,
            self.validator,
            self.exports,
            self.delivery,
            self.r.events,
            self.r.clock,
        )
        if review_render:
            assert self.r.render().succeeded
        self.sid = self.r.open_session()
        self.ref: TimelineRef = self.r.sessions.get(self.sid).value.timeline  # type: ignore[union-attr]
        o = self.r.sessions
        o.advance(self.sid, ReviewState.ANALYSIS_PENDING, actor=SYS)
        o.attach(self.sid, ArtifactKind.QUALITY_REPORT, "qr1", timeline=self.ref, actor=SYS)
        o.advance(self.sid, ReviewState.REVIEW_READY, actor=SYS)
        o.advance(self.sid, ReviewState.FINAL_POLISH_PENDING, actor=SYS)
        o.attach(self.sid, ArtifactKind.FINAL_POLISH_REPORT, "fp1", timeline=self.ref, actor=SYS)

    @property
    def state(self) -> ReviewState:
        return self.r.sessions.get(self.sid).value.state  # type: ignore[union-attr]

    def session(self) -> ReviewSession:
        found = self.r.sessions.get(self.sid)
        assert isinstance(found, Ok)
        return found.value

    def approve_and_authorize(self, profile: str = PROFILE) -> str:
        assert isinstance(self.approvals.present(self.sid, profile, CTX), Ok)
        assert isinstance(self.approvals.approve_final(self.sid, ADA, self.ref), Ok)
        authorized = self.approvals.authorize(self.sid, ADA, self.ref, profile)
        assert isinstance(authorized, Ok), authorized
        return authorized.value

    def command(self, authorization: str, **changes: object) -> RunExportCommand:
        return replace(RunExportCommand(self.sid, authorization, ADA, PROFILE, self.ref), **changes)  # type: ignore[arg-type]

    def finals(self) -> int:
        return sum(
            1 for q in self.r.backend.requests if "final" in q.file_name or "master" in q.file_name
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def run(world: World, command: RunExportCommand):  # type: ignore[no-untyped-def]
    done = world.runner.execute(command, CTX)
    assert isinstance(done, Ok), done
    return done.value


# --- the way in: a person's decisions ----------------------------------------------------------


def test_the_final_review_is_prepared_for_a_person_and_waits(world: World) -> None:
    review = world.approvals.present(world.sid, PROFILE, CTX)

    assert isinstance(review, Ok)
    assert review.value.state is ReviewState.AWAITING_FINAL_APPROVAL
    assert review.value.readiness.clear and review.value.resolve_is_current
    assert world.state is ReviewState.AWAITING_FINAL_APPROVAL


def test_a_good_report_a_ready_polish_and_a_review_render_are_not_an_authorization(
    world: World,
) -> None:
    world.approvals.present(world.sid, PROFILE, CTX)

    early = world.approvals.authorize(world.sid, ADA, world.ref, PROFILE)

    assert isinstance(early, Err)  # nobody approved the final video
    assert world.finals() == 0 and world.state is ReviewState.AWAITING_FINAL_APPROVAL


def test_only_a_person_approves_the_final_video_and_authorizes_the_export(world: World) -> None:
    world.approvals.present(world.sid, PROFILE, CTX)

    by_system = world.approvals.approve_final(world.sid, SYS, world.ref)
    assert isinstance(by_system, Err) and isinstance(by_system.error, NotPermitted)
    assert isinstance(world.approvals.approve_final(world.sid, ADA, world.ref), Ok)
    refused = world.approvals.authorize(world.sid, Actor.preconfigured("rule"), world.ref, PROFILE)

    assert isinstance(refused, Err) and isinstance(refused.error, NotPermitted)
    assert world.state is ReviewState.FINAL_APPROVED


def test_a_hard_blocker_stops_the_authorization_and_no_override_changes_that(world: World) -> None:
    world.facts = replace(
        world.facts,
        quality_issues=(IssueFact("q1", "video.black_frames", "critical", "black", True),),
    )
    world.approvals.present(world.sid, PROFILE, CTX)
    world.approvals.approve_final(world.sid, ADA, world.ref)

    world.approvals.override(world.sid, ADA, "q1", world.ref, note="I know")
    refused = world.approvals.authorize(world.sid, ADA, world.ref, PROFILE)

    assert isinstance(refused, Err) and isinstance(refused.error, NotReadyForExport)
    assert "black" in str(refused.error) and world.state is ReviewState.FINAL_APPROVED


def test_a_high_issue_needs_a_persons_override_on_record_and_then_the_export_may_go(
    world: World,
) -> None:
    world.facts = replace(
        world.facts, quality_issues=(IssueFact("q2", "caption.overlap", "high", "overlap", False),)
    )
    world.approvals.present(world.sid, PROFILE, CTX)
    world.approvals.approve_final(world.sid, ADA, world.ref)

    unresolved = world.approvals.authorize(world.sid, ADA, world.ref, PROFILE)
    by_system = world.approvals.override(world.sid, SYS, "q2", world.ref, note="x")
    overridden = world.approvals.override(world.sid, ADA, "q2", world.ref, note="known, accepted")
    authorized = world.approvals.authorize(world.sid, ADA, world.ref, PROFILE)

    assert isinstance(unresolved, Err) and isinstance(unresolved.error, NotAuthorized)
    assert isinstance(by_system, Err) and isinstance(overridden, Ok) and isinstance(authorized, Ok)


def test_a_polish_problem_is_overridden_by_the_id_the_review_can_name(world: World) -> None:
    world.facts = replace(
        world.facts,
        polish_problems=(PolishProblemFact("placeholder.caption_text", "TODO", "", 40),),
    )
    world.approvals.present(world.sid, PROFILE, CTX)
    world.approvals.approve_final(world.sid, ADA, world.ref)

    assert isinstance(world.approvals.authorize(world.sid, ADA, world.ref, PROFILE), Err)
    world.approvals.override(
        world.sid, ADA, "polish:placeholder.caption_text:40", world.ref, note="ok"
    )

    assert isinstance(world.approvals.authorize(world.sid, ADA, world.ref, PROFILE), Ok)


# --- the export ------------------------------------------------------------------------------


def test_an_authorized_export_is_rendered_checked_recorded_and_technically_ready(
    world: World,
) -> None:
    auth = world.approve_and_authorize()

    result = run(world, world.command(auth))

    report = result.report
    assert result.technically_ready and report.export_status is ExportStatus.VALIDATED
    assert world.finals() == 1 and world.state is ReviewState.EXPORT_VALIDATED
    assert report.validation_status == "pass" and report.validation_report_reference
    assert (report.codec, report.container, report.resolution) == ("h264", "mp4", "1920x1080")
    assert report.export_authorized_by == "Ada" and report.authorization_id == auth
    assert report.delivery_status is DeliveryStatus.AWAITING_APPROVAL
    assert report.output_asset_id and str(world.delivery_root) not in report.output_name
    stored = world.exports.find(world.r.anchor, report.export_id)
    assert isinstance(stored, Ok) and stored.value is not None and stored.value[0] == report


def test_nothing_is_rendered_without_a_valid_authorization(world: World) -> None:
    world.approve_and_authorize()

    done = world.runner.execute(world.command("not-an-authorization"), CTX)

    assert isinstance(done, Err) and world.finals() == 0
    assert world.state is ReviewState.EXPORT_AUTHORIZED


def test_an_authorization_is_for_one_output_not_another(world: World) -> None:
    auth = world.approve_and_authorize(PROFILE)

    done = world.runner.execute(world.command(auth, profile_id="shorts_9_16"), CTX)

    assert isinstance(done, Err) and world.finals() == 0


def test_a_timeline_change_after_authorization_stops_the_export_before_anything_renders(
    world: World,
) -> None:
    auth = world.approve_and_authorize()
    world.r.publish_change()

    done = world.runner.execute(world.command(auth), CTX)

    assert isinstance(done, Err) and isinstance(done.error, NotAuthorized)
    assert world.finals() == 0 and world.state is ReviewState.REVIEW_AGAIN_REQUIRED


def test_a_render_that_fails_is_a_failed_export_never_delivered_and_the_authorization_stands(
    world: World,
) -> None:
    auth = world.approve_and_authorize()
    world.r.backend.state = "failed"

    result = run(world, world.command(auth))

    assert result.report.export_status is ExportStatus.FAILED and not result.technically_ready
    assert result.report.delivery_status is DeliveryStatus.BLOCKED and result.report.errors
    assert world.state is ReviewState.EXPORT_FAILED
    delivered = world.runner.deliver(world.sid, ADA, result.report.export_id)
    assert isinstance(delivered, Err) and isinstance(delivered.error, DeliveryRefused)
    world.r.backend.state = "completed"
    again = world.runner.retry(world.sid, ADA, world.command(auth), CTX)
    assert isinstance(again, Ok) and again.value.report.export_status is ExportStatus.VALIDATED


def test_a_file_that_fails_its_validation_blocks_delivery(world: World) -> None:
    auth = world.approve_and_authorize()
    world.analyzer.decode_report = DecodeReport(False, 10, None, ("error while decoding",))

    result = run(world, world.command(auth))

    assert result.report.export_status is ExportStatus.FAILED
    assert result.report.validation_status == "fail" and result.report.errors
    assert world.state is ReviewState.EXPORT_FAILED
    assert isinstance(world.runner.deliver(world.sid, ADA, result.report.export_id), Err)


def test_a_validation_that_cannot_decide_waits_for_a_person_and_is_never_a_pass(
    tmp_path: Path,
) -> None:
    world = World(tmp_path, review_render=False)  # nothing to compare the exported pictures with
    auth = world.approve_and_authorize()

    result = run(world, world.command(auth))

    assert (
        result.report.export_status is ExportStatus.MANUAL_REVIEW and not result.technically_ready
    )
    assert world.state is ReviewState.EXPORT_VALIDATION_PENDING
    assert isinstance(world.runner.deliver(world.sid, ADA, result.report.export_id), Err)
    by_system = world.runner.settle_manual_review(
        world.sid, SYS, result.report.export_id, accept=True, note=""
    )
    assert isinstance(by_system, Err)
    settled = world.runner.settle_manual_review(
        world.sid, ADA, result.report.export_id, accept=True, note="I watched it"
    )
    assert isinstance(settled, Ok)
    assert settled.value.report.export_status is ExportStatus.VALIDATED
    assert world.session().state is ReviewState.EXPORT_VALIDATED


# --- delivery --------------------------------------------------------------------------------


def test_delivery_needs_a_persons_approval_and_replaces_nothing(world: World) -> None:
    auth = world.approve_and_authorize()
    exported = run(world, world.command(auth)).report

    unapproved = world.runner.deliver(world.sid, ADA, exported.export_id)
    assert isinstance(unapproved, Err) and "approved" in str(unapproved.error)
    assert isinstance(
        world.approvals.approve_delivery(world.sid, ADA, exported.export_id, world.ref), Ok
    )
    first = world.runner.deliver(world.sid, ADA, exported.export_id)

    assert isinstance(first, Ok), first
    placed = first.value.report.delivered_as
    assert placed.endswith("_v001_youtube_16_9_001.mp4") or "youtube_16_9_001" in placed
    assert (world.delivery_root / placed).is_file() and (
        world.delivery_root / (placed + ".json")
    ).is_file()
    assert first.value.report.delivery_status is DeliveryStatus.DELIVERED
    assert world.state is ReviewState.DELIVERY_READY
    assert isinstance(world.runner.deliver(world.sid, ADA, exported.export_id), Err)  # not twice


def test_a_delivered_master_is_never_replaced_even_by_a_file_of_the_same_name(world: World) -> None:
    auth = world.approve_and_authorize()
    exported = run(world, world.command(auth)).report
    world.approvals.approve_delivery(world.sid, ADA, exported.export_id, world.ref)
    world.delivery_root.mkdir(parents=True, exist_ok=True)
    taken = world.delivery_root / "video_v001_youtube_16_9_001.mp4"
    taken.write_bytes(b"an earlier master")

    delivered = world.runner.deliver(world.sid, ADA, exported.export_id)

    assert isinstance(delivered, Ok)
    assert taken.read_bytes() == b"an earlier master"  # untouched
    assert delivered.value.report.delivered_as != taken.name


def test_the_export_history_is_listed_newest_first(world: World) -> None:
    auth = world.approve_and_authorize()
    exported = run(world, world.command(auth)).report

    listed = world.exports.exports(world.r.anchor, session_id=world.sid)

    assert isinstance(listed, Ok) and [r.export_id for r, _ in listed.value] == [exported.export_id]
