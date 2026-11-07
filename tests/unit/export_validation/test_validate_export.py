"""Validating an exported file through the service, against the real render manager's reports:
the verdict, the stored report, the cache, and the refusals that keep an unchecked file from
passing."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from media_house.core.application.ports import DecodeReport, LoudnessReading, TimeSpan
from media_house.modules.export_validation.application.catalog import ValidationHistory
from media_house.modules.export_validation.application.store import ValidationStore
from media_house.modules.export_validation.application.validate import (
    IntentionalSpan,
    ValidateExport,
    ValidateExportCommand,
    ValidationOutcome,
)
from media_house.modules.export_validation.domain.profile import get_delivery_profile
from media_house.modules.export_validation.domain.report import report_to_json
from media_house.modules.export_validation.domain.values import (
    Category,
    NextAction,
    Route,
    Severity,
    Status,
)
from media_house.modules.render_manager.application.render import RenderAuthorization
from media_house.modules.render_manager.domain.values import RenderType
from media_house.modules.review_orchestrator.application.contracts import (
    ArtifactKind,
    TimelineRef,
    ValidationStatus,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import ConflictError, Err, Ok, ValidationError
from tests.support.layer6_fakes import FakeAnalyzer, timeline_inspection
from tests.unit.render_manager.test_render import World as RenderWorld

CTX = JobContext.detached()


class NoMixes:
    def latest_result(self, anchor: str, timeline_id: str | None) -> Ok[None]:
        return Ok(None)

    def latest_spec(self, anchor: str, timeline_id: str | None) -> Ok[None]:
        return Ok(None)

    def find_spec(self, anchor: str, content_hash: str) -> Ok[None]:
        return Ok(None)


class World:
    """A timeline that was approved, a review render of it, and the final export of it."""

    def __init__(self, tmp_path: Path) -> None:
        self.r = RenderWorld(tmp_path)
        self.analyzer = FakeAnalyzer()
        self.store = ValidationStore(self.r.library.contract(), self.r.paths)
        self.validator = ValidateExport(
            self.r.library.contract(), self.r.catalog, self.r.inspector, self.analyzer,
            self.r.history, self.r.profiles, NoMixes(), self.r.sessions, self.store,
            self.r.paths, self.r.clock, self.r.events,
        )  # fmt: skip
        self.history = ValidationHistory(self.store)
        review = self.r.render()
        assert review.output is not None
        self.review_asset = review.output.id
        self.authorization = self.r.authorize()
        final = self.r.render(self.r.final(self.authorization))
        assert final.succeeded and final.output is not None
        self.export = final.output
        self.config_hash = final.report.output_config_hash

    def command(self, **changes: object) -> ValidateExportCommand:
        base = ValidateExportCommand(
            output_asset_id=self.export.id,
            approved=self.authorization.timeline,
            delivery_id="youtube_16_9",
            review_session_id=self.authorization.session_id,
            export_id="exp1",
            authorized_config_hash=self.config_hash,
            reference_render_asset_id=self.review_asset,
        )
        return replace(base, **changes)  # type: ignore[arg-type]

    def validate(self, command: ValidateExportCommand | None = None) -> ValidationOutcome:
        done = self.validator.execute(command or self.command(), CTX)
        assert isinstance(done, Ok), done
        return done.value

    def problem_codes(self, outcome: ValidationOutcome) -> set[str]:
        return {f.code for f in outcome.report.detected_problems}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# --- the verdicts ----------------------------------------------------------------------------


def test_the_file_that_was_approved_and_rendered_correctly_passes(world: World) -> None:
    outcome = world.validate()

    report = outcome.report
    assert outcome.status is Status.PASS and report.detected_problems == ()
    assert report.recommended_next_action is NextAction.DELIVER
    assert outcome.review_status is ValidationStatus.PASS
    assert report.timeline_version == 1 and report.export_id == "exp1"
    assert report.render_id.startswith("r-") and report.delivery_profile_id == "youtube_16_9"
    assert (
        report.approved_version_reference.content_hash == world.authorization.timeline.content_hash
    )
    assert report.area(Category.INTEGRITY).value == "passed"
    assert dict(report.measurements)["integrated_lufs"] == "-14.00"


def test_the_report_is_stored_found_and_recorded_in_the_review(world: World) -> None:
    outcome = world.validate()

    anchor = world.r.anchor
    assert outcome.recorded_in_review
    session = world.r.sessions.get(world.authorization.session_id)
    assert isinstance(session, Ok)
    assert session.value.export_validation_id == outcome.report.export_validation_id
    assert world.history.get(anchor, outcome.report.export_validation_id) == outcome.report
    assert world.history.latest_for_export(anchor, "exp1") == outcome.report
    assert world.history.history(anchor, output_asset_id=world.export.id) == [outcome.report]
    assert [type(e).__name__ for e in world.r.events.events].count("ExportValidated") == 1


def test_the_report_names_the_file_never_a_path(world: World, tmp_path: Path) -> None:
    text = report_to_json(world.validate().report)

    assert str(tmp_path) not in text
    assert world.export.original_filename in text


def test_an_unchanged_file_is_not_measured_again(world: World) -> None:
    first = world.validate()
    calls = list(world.analyzer.calls)

    second = world.validate()

    assert second.reused and second.report == first.report
    assert world.analyzer.calls == calls  # nothing was decoded or measured again
    forced = world.validate(world.command(force=True))
    assert not forced.reused and len(world.analyzer.calls) > len(calls)


def test_another_approved_version_or_delivery_is_a_new_validation(world: World) -> None:
    first = world.validate()

    other = world.validate(world.command(delivery_id="master"))

    assert (
        not other.reused and other.report.export_validation_id != first.report.export_validation_id
    )


# --- a validation that cannot decide is never a pass -----------------------------------------


def test_without_the_approved_review_render_the_file_needs_a_person(world: World) -> None:
    outcome = world.validate(world.command(reference_render_asset_id=""))

    assert outcome.status is Status.MANUAL_REVIEW_REQUIRED
    assert outcome.review_status is ValidationStatus.MANUAL_REVIEW_REQUIRED
    assert [n.name for n in outcome.report.not_checked if n.mandatory] == [
        "pictures compared with the approved review render"
    ]
    assert outcome.report.recommended_next_action is NextAction.MANUAL_REVIEW


def test_a_measurement_the_analyzer_cannot_make_is_not_checked_not_passed(world: World) -> None:
    world.analyzer.operations.discard("loudness")

    outcome = world.validate()

    assert outcome.status is Status.MANUAL_REVIEW_REQUIRED
    assert [n.name for n in outcome.report.not_checked if n.mandatory] == ["loudness"]


def test_a_render_nobody_can_account_for_needs_a_person(world: World, tmp_path: Path) -> None:
    stranger = tmp_path / "stranger.mp4"
    stranger.write_bytes(b"\x00" * 400_000)
    world.r.library.add_file("stranger", stranger)

    outcome = world.validate(world.command(output_asset_id="stranger"))

    assert outcome.status is Status.MANUAL_REVIEW_REQUIRED
    assert "lineage.unknown_render" in world.problem_codes(outcome)


# --- the file is judged on its own -----------------------------------------------------------


def test_a_truncated_file_fails_even_though_the_render_said_it_succeeded(world: World) -> None:
    world.analyzer.decode_report = DecodeReport(False, 40, Fraction(3, 2), ("moov atom not found",))

    outcome = world.validate()

    assert outcome.status is Status.FAIL and "file.decode_failed" in world.problem_codes(outcome)
    assert outcome.report.severity is Severity.CRITICAL


def test_a_wrong_resolution_and_missing_audio_route_back_to_the_renderer(world: World) -> None:
    world.r.inspector.answers[world.export.id] = timeline_inspection(
        world.r.timeline, width=1280, height=720, audio=False
    )

    outcome = world.validate()

    assert outcome.status is Status.FAIL
    codes = world.problem_codes(outcome)
    assert {"video.resolution", "audio.missing"} <= codes
    for f in outcome.report.detected_problems:
        assert f.route_to is Route.RENDER_MANAGER and f.next_action is NextAction.RERENDER


def test_black_frames_in_the_output_fail_unless_the_plan_declared_them(world: World) -> None:
    world.analyzer.black = (TimeSpan(Fraction(2), Fraction(3)),)

    undeclared = world.validate()
    declared = world.validate(world.command(intentional=(IntentionalSpan("black", 1.9, 3.1),)))

    assert undeclared.status is Status.FAIL and "video.black_frames" in world.problem_codes(
        undeclared
    )
    assert declared.status is Status.PASS and not declared.report.detected_problems


def test_loudness_off_target_goes_to_the_mix_and_a_hot_peak_fails(world: World) -> None:
    world.analyzer.loudness_reading = LoudnessReading(
        -19.0, 6.0, -2.0, -4.0, 384000, 48000, 2, "aac"
    )
    off = world.validate()
    world.analyzer.loudness_reading = LoudnessReading(
        -14.0, 6.0, 0.5, -0.5, 384000, 48000, 2, "aac"
    )
    hot = world.validate(world.command(force=True))

    assert off.status is Status.FAIL
    assert (
        next(f for f in off.report.detected_problems if f.code == "audio.loudness_far").route_to
        is Route.AUDIO_MIX_ENGINE
    )
    assert hot.status is Status.FAIL and "audio.true_peak" in world.problem_codes(hot)


def test_pictures_that_are_not_the_approved_ones_are_called_out(world: World) -> None:
    export_path = world.r.library.files[world.export.id]
    world.analyzer.looks[export_path] = lambda t: "different" if t > 0 else "same"

    outcome = world.validate()

    assert outcome.status is Status.MANUAL_REVIEW_REQUIRED
    assert world.problem_codes(outcome) == {"timeline.section_differs", "timeline.ending_differs"}


def test_pictures_that_could_not_be_decoded_are_reported(world: World) -> None:
    world.analyzer.undecodable = {Fraction(0)}

    outcome = world.validate()

    assert "visual.frames_unreadable" in world.problem_codes(outcome)
    assert outcome.status is Status.MANUAL_REVIEW_REQUIRED


def test_a_file_whose_streams_cannot_be_inspected_is_never_a_false_failure_or_a_pass(
    world: World,
) -> None:
    world.r.inspector.raises = ConflictError("a derived asset already exists", user_message="x")

    outcome = world.validate()

    assert outcome.status is Status.MANUAL_REVIEW_REQUIRED
    assert not outcome.report.detected_problems  # nothing is wrong that was seen
    assert {"container readable", "video stream facts", "audio stream facts"} <= {
        n.name for n in outcome.report.not_checked
    }


def test_sound_that_is_missing_is_still_seen_when_the_inspection_is_unavailable(
    world: World,
) -> None:
    world.r.inspector.raises = ConflictError("a derived asset already exists", user_message="x")
    world.analyzer.loudness_reading = LoudnessReading(
        None, None, None, None, None, None, None, "", False
    )

    outcome = world.validate()

    assert outcome.status is Status.FAIL and "audio.missing" in world.problem_codes(outcome)


def test_a_file_that_vanished_from_disk_fails_as_missing(world: World) -> None:
    world.r.library.files[world.export.id].unlink()

    outcome = world.validate()

    assert outcome.status is Status.FAIL and world.problem_codes(outcome) == {"file.missing"}
    assert world.analyzer.calls == []  # nothing to measure


def test_a_delivery_limit_the_timeline_exceeds_is_a_revision_not_a_rerender(world: World) -> None:
    short = replace(
        get_delivery_profile("youtube_16_9"), delivery_id="short_only", max_duration_seconds=5.0
    )

    outcome = world.validate(world.command(delivery_id="short_only", profile=short))

    f = next(p for p in outcome.report.detected_problems if p.code == "delivery.too_long")
    assert (f.route_to, f.next_action) == (Route.REVISION_ENGINE, NextAction.REVISE_TIMELINE)


# --- which timeline the file is of -----------------------------------------------------------


def test_a_render_made_for_another_timeline_version_fails(world: World) -> None:
    from tests.unit.review_orchestrator.test_orchestrator import revised

    world.r.catalog.publish(revised(world.r.timeline, 2))  # the timeline moved on
    newer = TimelineRef(
        world.authorization.timeline.timeline_id, 2, "hash-2", world.r.anchor, "build-2"
    )

    outcome = world.validate(world.command(approved=newer))

    assert outcome.status is Status.FAIL
    assert "lineage.wrong_version" in world.problem_codes(outcome)
    assert outcome.report.timeline_version == 2


def test_an_export_rendered_with_other_output_settings_than_the_authorized_ones_fails(
    world: World,
) -> None:
    outcome = world.validate(world.command(authorized_config_hash="not-what-was-authorized"))

    assert outcome.status is Status.FAIL
    assert "lineage.other_output_settings" in world.problem_codes(outcome)


def test_the_approved_version_is_read_from_its_stored_build_not_the_latest(world: World) -> None:
    from tests.unit.review_orchestrator.test_orchestrator import revised

    world.r.catalog.publish(revised(world.r.timeline, 2))  # the latest is now longer/different

    outcome = world.validate()  # still the approved version 1

    assert outcome.status is Status.PASS and outcome.report.timeline_version == 1


# --- refusals --------------------------------------------------------------------------------


def test_an_approval_without_a_stored_build_cannot_be_checked(world: World) -> None:
    unstored = replace(world.authorization.timeline, build_asset_id=None)

    done = world.validator.execute(world.command(approved=unstored), CTX)

    assert isinstance(done, Err) and isinstance(done.error, ValidationError)


def test_a_stored_build_that_is_not_the_approved_content_is_refused(world: World) -> None:
    wrong = replace(world.authorization.timeline, content_hash="not-the-approved-content")

    done = world.validator.execute(world.command(approved=wrong), CTX)

    assert isinstance(done, Err) and "does not match" in done.error.user_message


def test_an_unknown_delivery_profile_and_an_unknown_file_are_errors(world: World) -> None:
    assert isinstance(world.validator.execute(world.command(delivery_id="vimeo"), CTX), Err)
    assert isinstance(world.validator.execute(world.command(output_asset_id="nope"), CTX), Err)


def test_a_delivery_made_for_another_render_profile_version_is_refused(world: World) -> None:
    stale = replace(
        get_delivery_profile("youtube_16_9"), delivery_id="old_one", render_profile_version=2
    )

    done = world.validator.execute(world.command(delivery_id="old_one", profile=stale), CTX)

    assert isinstance(done, Err) and "version" in done.error.user_message


def test_a_render_report_that_did_not_succeed_is_noted_not_trusted(world: World) -> None:
    world.r.backend.state = "failed"
    failed = world.r.render(world.r.review(profile_id="review_proxy"))
    assert not failed.succeeded
    # the same file served under another render: lineage reads the report of THIS file only
    outcome = world.validate()

    assert outcome.status is Status.PASS


def test_the_final_render_type_is_what_made_the_export(world: World) -> None:
    report = world.r.history.for_output(world.r.anchor, world.export.id)

    assert report is not None and report.render_type is RenderType.FINAL
    assert world.validate().report.render_id == report.render_id
    assert ArtifactKind.EXPORT_VALIDATION_REPORT.value == "export_validation_report"
    assert isinstance(world.authorization, RenderAuthorization)
