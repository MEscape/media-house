"""Rendering through the service: what is allowed, what is reused, what is reported - and the
human-approval barrier in front of a final render."""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.media_inspection.application.contracts import MediaInspection
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.render_manager.application.catalog import RenderHistory
from media_house.modules.render_manager.application.profiles import RenderProfileBook
from media_house.modules.render_manager.application.queue import RenderQueue
from media_house.modules.render_manager.application.render import (
    RenderAuthorization,
    RenderOutcome,
    RenderRequest,
    RenderService,
)
from media_house.modules.render_manager.application.store import RenderStore
from media_house.modules.render_manager.domain.errors import (
    NotControllable,
    RenderRefused,
    UnknownRender,
)
from media_house.modules.render_manager.domain.values import (
    UNMEASURED,
    JobState,
    RenderType,
    Verification,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ApprovalKind,
    ArtifactKind,
    AuthorizeExportCommand,
    DecideCommand,
    NotAuthorized,
    OpenSessionCommand,
    ReviewState,
    Verdict,
)
from media_house.modules.review_orchestrator.application.orchestrator import ReviewOrchestrator
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import ConflictError, Err, Ok
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.layer6_fakes import (
    FakeBackend,
    FakeCutter,
    FakeDisk,
    FakeInspector,
    FileLibrary,
    timeline_inspection,
)
from tests.support.review_fakes import InMemoryReviewStore, StubTimelineCatalog
from tests.unit.review_orchestrator.test_orchestrator import revised
from tests.unit.timeline_builder import fixtures as bf

CTX = JobContext.detached()
ADA = Actor.human("Ada")
QC = Actor.system("quality_control")


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.timeline = bf.built()
        self.anchor = self.timeline.anchor_asset_id
        self.catalog = StubTimelineCatalog(self.timeline)
        self.library = FileLibrary(tmp_path / "library")
        self.library.add_original(self.anchor)
        (tmp_path / "out").mkdir()
        self.backend = FakeBackend(self.library, tmp_path / "out")
        self.backend.payload = b"\x00" * 400_000
        self.cutter = FakeCutter()
        self.inspector = FakeInspector(default=self._inspection_of, library=self.library)
        self.paths = AppPaths.under_root(tmp_path / "app")
        self.events = RecordingPublisher()
        self.clock = FixedClock()
        self.sessions = ReviewOrchestrator(
            InMemoryReviewStore(), self.catalog, self.clock, RecordingPublisher()
        )
        self.store = RenderStore(self.library.contract(), self.paths)
        self.service = RenderService(
            self.library.contract(), self.catalog, self.backend, self.cutter, FakeDisk(),
            self.inspector, self.sessions, self.store, self.paths, self.clock, self.events,
        )  # fmt: skip
        self.history = RenderHistory(self.store)
        self.profiles = RenderProfileBook(self.catalog)

    def _inspection_of(self, asset: MediaAssetDto) -> MediaInspection:
        """A section preview lasts what it was cut for; everything else is the whole timeline."""
        derivation = asset.derivation
        if derivation is not None and derivation.operation == "render_section_preview":
            config = derivation.config
            frames = int(config["end_frame"]) - int(config["start_frame"])  # type: ignore[arg-type]
            return timeline_inspection(self.timeline, duration=frames / 25)
        return timeline_inspection(self.timeline)

    def review(self, **changes: object) -> RenderRequest:
        base = RenderRequest(self.anchor, RenderType.REVIEW, "review_fast")
        return replace(base, **changes)  # type: ignore[arg-type]

    def render(self, request: RenderRequest | None = None, ctx: JobContext = CTX) -> RenderOutcome:
        done = self.service.render(request or self.review(), ctx)
        assert isinstance(done, Ok), done
        return done.value

    def open_session(self) -> str:
        opened = self.sessions.open(OpenSessionCommand(self.anchor, "Ada"))
        assert isinstance(opened, Ok)
        return opened.value.session.session_id

    def authorize(self, profile_id: str = "youtube_16_9") -> RenderAuthorization:
        """A review that went through quality control, polish and a person's approvals."""
        sid = self.open_session()
        o = self.sessions
        ref = self.sessions.get(sid).value.timeline  # type: ignore[union-attr]
        o.advance(sid, ReviewState.ANALYSIS_PENDING, actor=QC)
        o.attach(sid, ArtifactKind.QUALITY_REPORT, "qr1", timeline=ref, actor=QC)
        o.advance(sid, ReviewState.REVIEW_READY, actor=QC)
        o.advance(sid, ReviewState.FINAL_POLISH_PENDING, actor=QC)
        o.attach(sid, ArtifactKind.FINAL_POLISH_REPORT, "fp1", timeline=ref, actor=QC)
        o.advance(sid, ReviewState.AWAITING_FINAL_APPROVAL, actor=QC)
        o.decide(DecideCommand(sid, ApprovalKind.FINAL, "final", Verdict.APPROVED, ADA, ref))
        config_hash = self.profiles.config_hash(profile_id, anchor_asset_id=self.anchor)
        assert isinstance(config_hash, Ok)
        done = o.authorize_export(
            AuthorizeExportCommand(sid, ADA, ref, profile_id, config_hash.value, "qr1", "fp1")
        )
        assert isinstance(done, Ok)
        return RenderAuthorization(sid, done.value.export_authorization_id, ref)

    def final(
        self, authorization: RenderAuthorization | None, profile: str = "youtube_16_9"
    ) -> RenderRequest:
        return RenderRequest(
            self.anchor, RenderType.FINAL, profile, authorization=authorization,
            review_session_id=authorization.session_id if authorization else "",
        )  # fmt: skip

    def publish_change(self) -> None:
        self.catalog.publish(revised(self.timeline, 2))


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def refusal(done: object) -> RenderRefused:
    assert isinstance(done, Err) and isinstance(done.error, RenderRefused), done
    return done.error


# --- a review render, reported and reused ----------------------------------------------------


def test_a_review_render_is_reported_with_what_was_made_and_checked(world: World) -> None:
    outcome = world.render()

    report = outcome.report
    assert outcome.succeeded and report.status is JobState.COMPLETED
    assert report.verification_status is Verification.VERIFIED
    assert (report.render_profile_id, report.render_profile_version) == ("review_fast", 1)
    assert (report.render_backend, report.render_backend_version) == ("fake_resolve", "19.0 (fake)")
    assert (
        report.timeline_version == 1
        and report.timeline_content_hash == world.timeline.build.build_hash
    )
    assert (report.width, report.height, report.container, report.codec) == (
        1920,
        1080,
        "mp4",
        "h264",
    )
    assert report.audio is not None and report.audio.codec == "aac"
    assert report.output_asset_id == outcome.output.id  # type: ignore[union-attr]
    assert report.output_config_hash and report.content_hash and report.file_size == 400_000
    assert report.resource_usage.cpu == UNMEASURED and report.resource_usage.vram == UNMEASURED
    assert report.resource_usage.disk_free_before_bytes == 10_000_000_000
    assert world.history.history(world.anchor)[0].report_id == report.report_id


def test_unavailable_settings_are_reported_never_dropped(world: World) -> None:
    report = world.render().report

    assert {"bitrate", "audio_bitrate", "sample_rate", "channels", "color"} & {
        lim.setting for lim in report.limitations
    }
    assert all("backend does not set this" in lim.reason for lim in report.limitations)


def test_an_identical_request_is_served_from_the_stored_render(world: World) -> None:
    first = world.render()
    second = world.render()

    assert len(world.backend.requests) == 1
    assert second.reused and second.report.report_id == first.report.report_id
    assert len(world.history.history(world.anchor)) == 1


def test_a_changed_timeline_is_a_new_render_and_a_rebuild_of_the_same_content_is_not(
    world: World,
) -> None:
    first = world.render()
    world.catalog.publish(revised(world.timeline, 2, change=False))  # rebuilt, nothing changed
    same = world.render()
    world.publish_change()  # the video changed
    changed = world.render()

    assert same.reused and not changed.reused
    assert changed.report.render_id != first.report.render_id
    assert changed.report.timeline_version == 2
    assert len(world.backend.requests) == 2


def test_overwrite_renders_again_and_keeps_both(world: World) -> None:
    world.render()
    again = world.render(world.review(overwrite=True))

    assert not again.reused and len(world.backend.requests) == 2
    assert world.backend.requests[1].reuse is False
    assert len(world.history.history(world.anchor)) == 2


def test_each_output_has_its_own_versioned_name(world: World) -> None:
    world.render()
    world.render(world.review(overwrite=True))

    names = [r.file_name for r in world.backend.requests]
    assert len(set(names)) == 2 and names[0].endswith("_001") and names[1].endswith("_002")


def test_the_report_is_recorded_in_the_review(world: World) -> None:
    sid = world.open_session()

    outcome = world.render(world.review(review_session_id=sid))

    session = world.sessions.get(sid)
    assert isinstance(session, Ok)
    assert session.value.render_report_ids == (outcome.report.report_id,)


# --- a completed job is not a valid file -----------------------------------------------------


def test_a_failed_render_is_never_reported_as_success(world: World) -> None:
    world.backend.state = "failed"
    world.backend.errors = ("Resolve refused the codec",)

    outcome = world.render()

    assert not outcome.succeeded and outcome.output is None
    assert outcome.report.status is JobState.FAILED
    assert outcome.report.verification_status is Verification.NOT_VERIFIED
    assert outcome.report.errors == ("Resolve refused the codec",)
    assert outcome.report.output_asset_id == ""


def test_a_completed_job_whose_file_fails_its_checks_is_not_a_success(world: World) -> None:
    world.inspector.default = lambda _a: timeline_inspection(world.timeline, width=1280, height=720)

    outcome = world.render()

    assert outcome.report.status is JobState.COMPLETED
    assert not outcome.succeeded
    assert outcome.report.verification_status is Verification.FAILED
    assert any(c.name == "resolution" and c.state.value == "failed" for c in outcome.report.checks)


def test_a_file_without_the_timelines_sound_fails_the_check(world: World) -> None:
    world.inspector.default = lambda _a: timeline_inspection(world.timeline, audio=False)

    outcome = world.render()

    assert not outcome.succeeded
    assert any(
        c.name == "audio stream" and c.state.value == "failed" for c in outcome.report.checks
    )


def test_a_file_that_cannot_be_inspected_is_not_verified_and_not_a_success(world: World) -> None:
    world.inspector.raises = ConflictError("a derived asset already exists", user_message="x")

    outcome = world.render()

    assert outcome.report.status is JobState.COMPLETED and outcome.output is not None
    assert outcome.report.verification_status is Verification.NOT_VERIFIED
    assert not outcome.succeeded
    assert [c.name for c in outcome.report.checks if c.state.value == "not_checked"] == [
        "output inspected"
    ]


def test_a_backend_that_claims_success_without_a_file_is_a_failure(world: World) -> None:
    world.backend.render = lambda request, ctx: __import__(  # type: ignore[method-assign]
        "media_house.modules.render_manager.application.ports", fromlist=["BackendResult"]
    ).BackendResult("completed", None)

    outcome = world.render()

    assert outcome.report.status is JobState.FAILED and not outcome.succeeded
    assert "no file" in " ".join(outcome.report.errors)


def test_a_failed_render_can_be_retried_and_keeps_its_id(world: World) -> None:
    world.backend.state = "failed"
    failed = world.render()
    world.backend.state = "completed"

    retried = world.service.retry(world.anchor, failed.report.render_id, CTX)

    assert isinstance(retried, Ok) and retried.value.succeeded
    assert retried.value.report.render_id == failed.report.render_id
    assert retried.value.report.attempt == 2
    assert [r.attempt for r in world.history.history(world.anchor)] == [2, 1]


def test_a_render_that_succeeded_is_not_retried(world: World) -> None:
    done = world.render()

    assert (
        "nothing to retry"
        in refusal(world.service.retry(world.anchor, done.report.render_id, CTX)).reasons[0]
    )
    unknown = world.service.retry(world.anchor, "r-nothing", CTX)
    assert isinstance(unknown, Err) and isinstance(unknown.error, UnknownRender)


def test_cancelling_a_running_render_ends_it_as_cancelled(world: World) -> None:
    planned = world.service.plan(world.review())
    assert isinstance(planned, Ok)

    def cancel_now(_request: object) -> None:
        world.service.cancel(planned.value.render_id)

    world.backend.on_render = cancel_now

    done = world.service.execute(planned.value, CTX)

    assert isinstance(done, Ok)
    assert done.value.report.status is JobState.CANCELLED and not done.value.succeeded


def test_a_cancelled_job_context_cancels_the_render(world: World) -> None:
    token = CancellationToken()
    token.cancel()

    outcome = world.render(ctx=JobContext("j", token, CTX.progress))

    assert outcome.report.status is JobState.CANCELLED and world.backend.requests == []


# --- what a kind of render may use -----------------------------------------------------------


def test_a_preview_never_spends_a_masters_time(world: World) -> None:
    reasons = refusal(world.service.plan(world.review(profile_id="master_hq"))).reasons

    assert "preview profile" in reasons[0]
    assert world.backend.requests == []


def test_a_backend_that_cannot_realise_a_profile_is_asked_for_nothing(world: World) -> None:
    world.backend.capabilities = replace(
        world.backend.capabilities, profiles=frozenset({"youtube"})
    )

    reasons = refusal(
        world.service.plan(RenderRequest(world.anchor, RenderType.INTERMEDIATE, "intermediate"))
    ).reasons

    assert "cannot realise" in " ".join(reasons)


def test_an_unknown_profile_and_a_missing_timeline_are_errors_not_renders(world: World) -> None:
    assert isinstance(world.service.render(world.review(profile_id="tiktok"), CTX), Err)
    assert isinstance(
        world.service.render(replace(world.review(), anchor_asset_id="nope"), CTX), Err
    )


def test_a_whole_timeline_backend_refuses_a_frame_range_for_a_review(world: World) -> None:
    reasons = refusal(
        world.service.plan(world.review(start_frame=10, end_frame=50, padding_seconds=0.0))
    ).reasons

    assert "whole timeline only" in " ".join(reasons)


# --- the human approval barrier --------------------------------------------------------------


def test_no_final_render_starts_without_an_authorization(world: World) -> None:
    reasons = refusal(world.service.render(world.final(None), CTX)).reasons

    assert "authorization" in reasons[0]
    assert world.backend.requests == []


def test_a_final_render_with_a_valid_authorization_runs_at_final_quality(world: World) -> None:
    authorization = world.authorize()

    outcome = world.render(world.final(authorization))

    assert outcome.succeeded and outcome.report.render_type is RenderType.FINAL
    assert outcome.report.authorization_id == authorization.authorization_id
    request = world.backend.requests[0]
    assert request.require_final_quality and request.native_profile == "youtube"


def test_an_authorization_for_another_output_cannot_start_the_render(world: World) -> None:
    authorization = world.authorize("youtube_16_9")  # authorized for youtube, not for the master

    done = world.service.render(world.final(authorization, profile="master_hq"), CTX)

    assert isinstance(done, Err) and isinstance(done.error, NotAuthorized)
    assert world.backend.requests == []


def test_an_authorization_for_an_older_version_cannot_render_a_changed_timeline(
    world: World,
) -> None:
    authorization = world.authorize()
    world.publish_change()  # the person edited after approving

    done = world.service.render(world.final(authorization), CTX)

    assert isinstance(done, Err)
    assert "changed" in " ".join(refusal(done).reasons) or isinstance(done.error, NotAuthorized)
    assert world.backend.requests == []


def test_a_final_render_never_overwrites_or_renders_from_proxies(world: World) -> None:
    authorization = world.authorize()

    over = refusal(world.service.plan(replace(world.final(authorization), overwrite=True)))
    assert "never overwrites" in " ".join(over.reasons)
    custom = replace(
        __import__(
            "media_house.modules.render_manager.domain.profile", fromlist=["get_profile"]
        ).get_profile("youtube_16_9"),
        profile_id="youtube_with_proxies",
        allow_proxy_sources=True,
    )
    proxies = refusal(
        world.service.plan(
            replace(world.final(authorization), profile=custom, profile_id=custom.profile_id)
        )
    )
    assert "proxies" in " ".join(proxies.reasons)


def test_a_profile_for_another_aspect_is_refused_for_the_final_render(world: World) -> None:
    authorization = world.authorize("youtube_16_9")

    reasons = refusal(world.service.plan(world.final(authorization, profile="shorts_9_16"))).reasons

    assert "9:16" in " ".join(reasons)


def test_a_queued_final_render_is_asked_again_when_it_runs(world: World) -> None:
    authorization = world.authorize()
    queue = RenderQueue(world.service)
    queued = queue.enqueue(world.final(authorization))
    assert isinstance(queued, Ok)
    world.publish_change()  # the timeline changes while the render waits

    done = queue.run_next(CTX)

    assert done is not None and isinstance(done, Err) and isinstance(done.error, NotAuthorized)
    assert world.backend.requests == []


# --- section previews ------------------------------------------------------------------------


def test_a_section_preview_is_cut_from_a_whole_review_render_with_context(world: World) -> None:
    request = RenderRequest(
        world.anchor, RenderType.SECTION_PREVIEW, "review_fast",
        start_frame=50, end_frame=100, padding_seconds=1.0,
    )  # fmt: skip

    outcome = world.render(request)

    assert outcome.succeeded
    assert len(world.backend.requests) == 1  # the whole-timeline review it was cut from
    assert world.cutter.cuts == [(1.0, 5.0)]  # frames 25-125 at 25 fps
    assert (outcome.report.start_frame, outcome.report.end_frame) == (25, 125)
    assert outcome.report.verification_status is not Verification.FAILED


def test_a_second_preview_reuses_the_whole_review_render(world: World) -> None:
    def preview(a: int, b: int) -> RenderRequest:
        return RenderRequest(
            world.anchor, RenderType.SECTION_PREVIEW, "review_fast",
            start_frame=a, end_frame=b, padding_seconds=0.0,
        )  # fmt: skip

    world.render(preview(0, 50))
    second = world.render(preview(100, 150))

    assert second.succeeded and len(world.backend.requests) == 1
    assert world.cutter.cuts == [(0.0, 2.0), (4.0, 6.0)]


def test_a_section_by_name_and_one_that_does_not_exist(world: World) -> None:
    named = world.render(
        RenderRequest(
            world.anchor,
            RenderType.SECTION_PREVIEW,
            "review_fast",
            section_id="s1",
            padding_seconds=0.0,
        )
    )
    missing = refusal(
        world.service.plan(
            RenderRequest(
                world.anchor, RenderType.SECTION_PREVIEW, "review_fast", section_id="nope"
            )
        )
    )

    assert named.succeeded and (named.report.start_frame, named.report.end_frame) == (0, 200)
    assert "no section 'nope'" in missing.reasons[0]


def test_a_preview_whose_source_render_fails_is_a_failure(world: World) -> None:
    world.backend.state = "failed"

    outcome = world.render(
        RenderRequest(
            world.anchor, RenderType.SECTION_PREVIEW, "review_fast",
            start_frame=0, end_frame=50, padding_seconds=0.0,
        )
    )  # fmt: skip

    assert not outcome.succeeded and world.cutter.cuts == []


# --- the queue and control -------------------------------------------------------------------


def test_the_queue_runs_the_highest_priority_first_and_oldest_on_a_tie(world: World) -> None:
    queue = RenderQueue(world.service)
    low = world.review(priority=0)
    high = world.review(profile_id="review_proxy", priority=5)
    other_low = replace(
        world.review(), render_type=RenderType.INTERMEDIATE, profile_id="intermediate", priority=0
    )
    for request in (low, high, other_low):
        assert isinstance(queue.enqueue(request), Ok)

    order = []
    while (done := queue.run_next(CTX)) is not None:
        assert isinstance(done, Ok)
        order.append(done.value.report.render_profile_id)

    assert order == ["review_proxy", "review_fast", "intermediate"]
    assert queue.pending() == []


def test_the_same_request_is_queued_once(world: World) -> None:
    queue = RenderQueue(world.service)

    first, second = queue.enqueue(world.review()), queue.enqueue(world.review())

    assert isinstance(first, Ok) and isinstance(second, Ok)
    assert first.value.render_id == second.value.render_id
    assert len(queue.pending()) == 1


def test_a_held_render_waits_and_a_withdrawn_one_never_runs(world: World) -> None:
    queue = RenderQueue(world.service)
    a = queue.enqueue(world.review())
    b = queue.enqueue(world.review(profile_id="review_proxy"))
    assert isinstance(a, Ok) and isinstance(b, Ok)
    assert isinstance(queue.hold(a.value.render_id), Ok)

    done = queue.run_next(CTX)

    assert isinstance(done, Ok) and done.value.report.render_profile_id == "review_proxy"
    assert queue.run_next(CTX) is None  # the held one waits
    assert isinstance(queue.release(a.value.render_id), Ok)
    assert isinstance(queue.withdraw(a.value.render_id), Ok)
    assert queue.run_next(CTX) is None
    assert isinstance(queue.withdraw("r-none"), Err)


def test_a_running_render_cannot_be_paused_by_a_backend_that_cannot(world: World) -> None:
    planned = world.service.plan(world.review())
    assert isinstance(planned, Ok)
    seen: list[object] = []
    world.backend.on_render = lambda _r: seen.append(world.service.pause(planned.value.render_id))

    world.service.execute(planned.value, CTX)

    result = seen[0]
    assert isinstance(result, Err) and isinstance(result.error, NotControllable)
    assert "cannot pause" in result.error.user_message


def test_a_backend_that_can_pause_pauses_and_resumes(world: World) -> None:
    world.backend.capabilities = replace(world.backend.capabilities, pause_resume=True)
    planned = world.service.plan(world.review())
    assert isinstance(planned, Ok)
    seen: list[object] = []

    def during(_r: object) -> None:
        seen.append(world.service.pause(planned.value.render_id))
        seen.append(world.service.status(planned.value.render_id))
        seen.append(world.service.resume(planned.value.render_id))

    world.backend.on_render = during
    world.service.execute(planned.value, CTX)

    paused, status, resumed = seen
    assert isinstance(paused, Ok) and paused.value.state is JobState.PAUSED
    assert isinstance(status, Ok) and status.value.state is JobState.PAUSED
    assert isinstance(resumed, Ok) and resumed.value.state is JobState.RUNNING
    assert isinstance(world.service.pause("r-none"), Err)


def test_progress_and_status_of_a_finished_render(world: World) -> None:
    outcome = world.render()

    status = world.service.status(outcome.report.render_id)

    assert isinstance(status, Ok)
    assert status.value.state is JobState.COMPLETED and status.value.progress == 100.0


# --- profiles and history --------------------------------------------------------------------


def test_the_output_hash_for_an_authorization_comes_from_the_timeline(world: World) -> None:
    a = world.profiles.config_hash("youtube_16_9", anchor_asset_id=world.anchor)
    b = world.profiles.config_hash("master_hq", anchor_asset_id=world.anchor)
    resolution = world.profiles.resolve("youtube_16_9", anchor_asset_id=world.anchor)

    assert isinstance(a, Ok) and isinstance(b, Ok) and isinstance(resolution, Ok)
    assert a.value != b.value and a.value == resolution.value.config_hash
    assert (resolution.value.settings.width, resolution.value.settings.height) == (1920, 1080)
    assert isinstance(world.profiles.config_hash("tiktok", anchor_asset_id=world.anchor), Err)


def test_history_finds_the_latest_render_of_one_video_and_the_report_of_a_file(
    world: World,
) -> None:
    first = world.render()
    world.publish_change()
    second = world.render()

    old = world.history.latest(world.anchor, content_hash=world.timeline.build.build_hash)
    newest = world.history.latest(world.anchor)
    owner = world.history.for_output(world.anchor, second.output.id)  # type: ignore[union-attr]
    stored = world.history.get(world.anchor, first.report.report_id)

    assert old is not None and old.report_id == first.report.report_id
    assert newest is not None and newest.report_id == second.report.report_id
    assert owner is not None and owner.report_id == second.report.report_id
    assert isinstance(stored, Ok) and stored.value is not None
    assert world.history.latest(world.anchor, render_type=RenderType.FINAL) is None


def test_a_finished_render_publishes_one_event(world: World) -> None:
    world.render()
    world.render()  # served from the store: nothing new happened

    finished = [e for e in world.events.events if type(e).__name__ == "RenderFinished"]
    assert len(finished) == 1 and finished[0].succeeded  # type: ignore[attr-defined]


def test_the_stored_report_round_trips(world: World) -> None:
    outcome = world.render()
    assert outcome.report_asset is not None

    loaded = world.store.load(outcome.report_asset)

    assert loaded == outcome.report


def asset_of(world: World, outcome: RenderOutcome) -> MediaAssetDto:
    assert outcome.output is not None
    return outcome.output
