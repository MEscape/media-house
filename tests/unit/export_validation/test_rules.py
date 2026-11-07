"""The validation rules: what makes a file acceptable, how serious each problem is, who owns the
fix - and that a check that was not made can never be a PASS."""

from dataclasses import replace

import pytest

from media_house.core.domain import Rational
from media_house.modules.export_validation.domain.facts import (
    AudioFacts,
    DecodeFacts,
    Expected,
    FileFacts,
    FrameSample,
    Intentional,
    Lineage,
    LoudnessFacts,
    Span,
    VideoFacts,
)
from media_house.modules.export_validation.domain.profile import (
    DELIVERY_PROFILES,
    DeliveryProfile,
    InvalidDeliveryProfile,
    UnknownDeliveryProfile,
    get_delivery_profile,
)
from media_house.modules.export_validation.domain.report import Finding
from media_house.modules.export_validation.domain.rules import Evaluation, evaluate
from media_house.modules.export_validation.domain.values import (
    Category,
    Certainty,
    CheckState,
    NextAction,
    Route,
    Severity,
    Status,
)

RATE = Rational(25, 1)
PROFILE = get_delivery_profile("youtube_16_9")


def expected(**changes: object) -> Expected:
    return replace(
        Expected(1920, 1080, RATE, 8.0, True, "mp4", "h264", "yuv420p", "aac", 48000, 2, (16, 9)),
        **changes,  # type: ignore[arg-type]
    )


def video(**changes: object) -> VideoFacts:
    return replace(
        VideoFacts(
            "h264",
            1920,
            1080,
            RATE,
            RATE,
            "yuv420p",
            Rational(16, 9),
            8.0,
            200,
            0,
            0,
            0,
            False,
            ("space", "range"),
        ),
        **changes,  # type: ignore[arg-type]
    )


def audio(**changes: object) -> AudioFacts:
    return replace(AudioFacts("aac", 48000, 2, 8.0, 0.0, 0.0), **changes)  # type: ignore[arg-type]


def frames(*similarities: float | None) -> tuple[FrameSample, ...]:
    kinds = ["opening", *["section"] * (len(similarities) - 2), "ending"]
    return tuple(
        FrameSample(f"{k} {i}", k, float(i), f"s{i}", sim, 0.0)
        for i, (k, sim) in enumerate(zip(kinds, similarities, strict=True))
    )


def good(**changes: object) -> FileFacts:
    return replace(
        FileFacts(
            exists=True, readable=True, size_bytes=400_000, extension=".mp4", file_name="v.mp4",
            duration=8.0, inspection_error=False, video=video(), audio=audio(),
            tags=(("title", "A video"),), decode=DecodeFacts(True, 200, 8.0, ()),
            black=(), frozen=(), silent=(),
            loudness=LoudnessFacts(-14.0, 6.0, -2.0, -4.0, True),
            frames=frames(1.0, 1.0, 1.0), reference_used=True,
        ),
        **changes,  # type: ignore[arg-type]
    )  # fmt: skip


def lineage(**changes: object) -> Lineage:
    return replace(
        Lineage("tl", 3, "h3", "sess", True, "r1", "tl", 3, "h3", "sess", True, "cfg", "cfg"),
        **changes,  # type: ignore[arg-type]
    )


def run(
    facts: FileFacts | None = None,
    *,
    exp: Expected | None = None,
    profile: DeliveryProfile = PROFILE,
    lin: Lineage | None = None,
    intentional: tuple[Intentional, ...] = (),
) -> Evaluation:
    return evaluate(facts or good(), exp or expected(), profile, lin or lineage(), intentional)


def mandatory(evaluation: Evaluation) -> list[str]:
    return [n.name for n in evaluation.not_checked if n.mandatory]


def codes(evaluation: Evaluation) -> set[str]:
    return {f.code for f in evaluation.findings}


def finding(evaluation: Evaluation, code: str) -> Finding:
    return next(f for f in evaluation.findings if f.code == code)


# --- a good file ---------------------------------------------------------------------------------


def test_a_file_that_is_what_the_approved_timeline_and_the_delivery_ask_passes() -> None:
    result = run()

    assert result.status is Status.PASS and result.findings == () and mandatory(result) == []
    # what pictures cannot show is said, not hidden
    assert [n.name for n in result.not_checked] == ["text clipping, safe areas, placeholders"]
    assert result.next_action is NextAction.DELIVER and result.severity is Severity.INFO
    assert {c.state for c in result.checks} == {CheckState.PASSED, CheckState.NOT_CHECKED}
    assert [c.name for c in result.checks if c.state is CheckState.NOT_CHECKED] == [
        "text clipping, safe areas, placeholders"
    ]
    assert {a.category for a in result.area_status} == set(Category)


# --- integrity: a probe is not a decode ----------------------------------------------------------


def test_a_file_that_is_not_there_cannot_be_delivered() -> None:
    result = run(good(exists=False))

    assert result.status is Status.FAIL and codes(result) == {"file.missing"}
    assert finding(result, "file.missing").next_action is NextAction.REEXPORT


def test_a_file_that_does_not_decode_to_the_end_fails() -> None:
    result = run(good(decode=DecodeFacts(False, 40, 1.5, ("error while decoding MB 3 4",))))

    f = finding(result, "file.decode_failed")
    assert result.status is Status.FAIL and f.route_to is Route.RENDER_MANAGER
    assert f.evidence[0][1] == "error while decoding MB 3 4"


def test_a_container_that_promises_more_than_decodes_is_a_truncated_file() -> None:
    result = run(good(decode=DecodeFacts(True, 100, 4.0, ())))

    assert result.status is Status.FAIL and "file.truncated" in codes(result)


def test_a_file_that_was_never_decoded_is_not_a_pass() -> None:
    result = run(good(decode=None))

    assert result.status is Status.MANUAL_REVIEW_REQUIRED
    assert mandatory(result) == ["complete decode"]


def test_an_unreadable_or_empty_file_is_critical() -> None:
    assert run(good(readable=False)).status is Status.FAIL
    assert "file.empty" in codes(run(good(size_bytes=10)))
    assert "file.unreadable" in codes(run(good(inspection_error=True)))


# --- video: the render's own settings go back to the renderer ------------------------------------


@pytest.mark.parametrize(
    ("facts", "code"),
    [
        (
            good(video=video(width=1280, height=720, display_aspect=Rational(16, 9))),
            "video.resolution",
        ),
        (
            good(video=video(frame_rate=Rational(30, 1), average_frame_rate=Rational(30, 1))),
            "video.frame_rate",
        ),
        (good(video=video(codec="hevc")), "video.codec"),
        (good(video=video(display_aspect=Rational(4, 3))), "video.aspect"),
        (good(video=video(dropped_frames=3)), "video.dropped_frames"),
        (good(video=video(discontinuities=1)), "video.discontinuities"),
        (good(video=None), "video.missing"),
    ],
)  # fmt: skip
def test_a_render_setting_problem_goes_back_to_the_renderer_not_to_editing(
    facts: FileFacts, code: str
) -> None:
    result = run(facts)

    f = finding(result, code)
    assert result.status is Status.FAIL
    assert (f.route_to, f.next_action) == (Route.RENDER_MANAGER, NextAction.RERENDER)
    assert f.certainty is Certainty.CONFIRMED


def test_a_declared_rate_that_the_frames_do_not_keep_is_reported() -> None:
    result = run(good(video=video(average_frame_rate=Rational(23, 1))))

    assert finding(result, "video.variable_rate").severity is Severity.MEDIUM
    assert result.status is Status.WARNING


def test_untagged_colour_is_only_a_possible_low_issue() -> None:
    result = run(good(video=video(color_tags=())))

    assert finding(result, "video.color_untagged").severity is Severity.LOW
    assert result.status is Status.WARNING


# --- black, frozen and silence: intended is not an error -----------------------------------------


def test_black_in_the_middle_that_the_plan_did_not_declare_fails() -> None:
    result = run(good(black=(Span(2.0, 3.0),)))

    f = finding(result, "video.black_frames")
    assert result.status is Status.FAIL and f.severity is Severity.HIGH
    # the cause is not known from the file: look at the timeline before choosing a fix
    assert (f.route_to, f.next_action) == (Route.QUALITY_CONTROL, NextAction.INVESTIGATE)


def test_black_the_plan_declared_is_not_flagged() -> None:
    result = run(good(black=(Span(2.0, 3.0),)), intentional=(Intentional("black", 1.9, 3.1),))

    assert result.status is Status.PASS and result.findings == ()


def test_a_fade_in_from_black_at_the_start_is_a_warning_unless_declared() -> None:
    start = good(black=(Span(0.0, 0.5),))

    assert run(start).status is Status.WARNING and "video.black_edge" in codes(run(start))
    assert run(start, intentional=(Intentional("black", 0.0, 0.6),)).status is Status.PASS
    ending = good(black=(Span(7.5, None),))
    assert "video.black_edge" in codes(run(ending))


def test_a_declaration_of_another_kind_does_not_excuse_black() -> None:
    result = run(good(black=(Span(2.0, 3.0),)), intentional=(Intentional("silence", 2.0, 3.0),))

    assert result.status is Status.FAIL


def test_a_frozen_stretch_is_judged_unless_declared_or_black() -> None:
    held = good(frozen=(Span(4.0, 7.0),))

    assert "video.frozen" in codes(run(held)) and run(held).status is Status.WARNING
    assert run(held, intentional=(Intentional("freeze", 4.0, 7.0),)).status is Status.PASS
    # a black stretch is also "frozen"; it is reported once, as black
    both = run(good(black=(Span(2.0, 3.0),), frozen=(Span(2.0, 3.0),)))
    assert "video.frozen" not in codes(both) and "video.black_frames" in codes(both)


def test_unexpected_silence_is_a_likely_problem_and_a_mostly_silent_file_fails() -> None:
    quiet = run(good(silent=(Span(3.0, 5.0),)))
    dead = run(good(silent=(Span(0.0, None),)))

    assert (
        quiet.status is Status.WARNING
        and finding(quiet, "audio.silence").severity is Severity.MEDIUM
    )
    assert dead.status is Status.FAIL and "audio.mostly_silent" in codes(dead)
    assert (
        run(good(silent=(Span(3.0, 5.0),)), intentional=(Intentional("silence", 3.0, 5.0),)).status
        is Status.PASS
    )


# --- audio ---------------------------------------------------------------------------------------


def test_missing_sound_fails_when_the_timeline_has_sound_and_not_when_it_has_none() -> None:
    assert "audio.missing" in codes(run(good(audio=None, loudness=None)))
    silent_timeline = run(
        good(audio=None, loudness=LoudnessFacts(None, None, None, None, False), silent=None),
        exp=expected(has_audio=False),
    )
    assert "audio.missing" not in codes(silent_timeline)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"sample_rate": 44100}, "audio.sample_rate"),
        ({"channels": 1}, "audio.channels"),
        ({"codec": "opus"}, "audio.codec"),
        ({"duration": 6.5}, "audio.duration_mismatch"),
        ({"start_offset": 0.2}, "audio.sync"),
    ],
)
def test_the_audio_format_length_and_sync_are_the_renderers_problem(
    changes: dict[str, object], code: str
) -> None:
    result = run(good(audio=audio(**changes)))

    f = finding(result, code)
    assert result.status is Status.FAIL and f.route_to is Route.RENDER_MANAGER


def test_an_early_ending_sound_is_called_an_abrupt_cut() -> None:
    result = run(good(audio=audio(duration=6.0)))

    assert "abrupt cut" in finding(result, "audio.duration_mismatch").message


def test_loudness_is_judged_by_tolerance_and_a_level_fix_goes_to_the_mix() -> None:
    near = run(good(loudness=LoudnessFacts(-15.5, 6.0, -2.0, -4.0, True)))
    far = run(good(loudness=LoudnessFacts(-22.0, 6.0, -2.0, -4.0, True)))

    assert near.status is Status.WARNING and "audio.loudness" in codes(near)
    assert far.status is Status.FAIL
    f = finding(far, "audio.loudness_far")
    assert (f.route_to, f.next_action) == (Route.AUDIO_MIX_ENGINE, NextAction.REMIX_AUDIO)
    assert run(good(loudness=LoudnessFacts(-14.4, 6.0, -2.0, -4.0, True))).status is Status.PASS


def test_peaks_over_the_ceiling_or_at_full_scale_are_reported() -> None:
    over = run(good(loudness=LoudnessFacts(-14.0, 6.0, -0.2, -3.0, True)))
    clipped = run(good(loudness=LoudnessFacts(-14.0, 6.0, -3.0, 0.0, True)))

    assert over.status is Status.FAIL and "audio.true_peak" in codes(over)
    assert clipped.status is Status.WARNING and "audio.clipping" in codes(clipped)


def test_a_programme_that_never_rises_above_the_gate_is_not_a_pass() -> None:
    result = run(good(loudness=LoudnessFacts(None, None, -80.0, -80.0, True)))

    assert "audio.silent_programme" in codes(result)
    assert result.status is Status.MANUAL_REVIEW_REQUIRED  # HIGH but only likely


def test_loudness_that_was_not_measured_is_not_a_pass() -> None:
    result = run(good(loudness=None))

    assert result.status is Status.MANUAL_REVIEW_REQUIRED
    assert mandatory(result) == ["loudness"]


def test_a_delivery_without_a_loudness_target_does_not_judge_loudness() -> None:
    master = get_delivery_profile("master")

    result = run(good(loudness=LoudnessFacts(-9.0, 12.0, -2.0, -4.0, True)), profile=master)

    assert result.status is Status.PASS


# --- pictures against the approved review render -------------------------------------------------


def test_without_a_review_render_to_compare_with_the_file_is_not_a_pass() -> None:
    result = run(good(frames=None, reference_used=False))

    assert result.status is Status.MANUAL_REVIEW_REQUIRED
    assert result.not_checked[0].mandatory
    assert "approved review render" in result.not_checked[0].reason


def test_a_picture_that_is_not_the_approved_one_needs_a_person_and_says_where() -> None:
    result = run(good(frames=frames(1.0, 0.2, 1.0)))

    f = finding(result, "timeline.section_differs")
    assert result.status is Status.MANUAL_REVIEW_REQUIRED
    assert f.next_action is NextAction.INVESTIGATE and f.start_seconds == 1.0
    assert ("section", "s1") in f.evidence


def test_a_wrong_opening_or_ending_is_named() -> None:
    opening = run(good(frames=frames(0.1, 1.0, 1.0)))
    ending = run(good(frames=frames(1.0, 1.0, 0.1)))

    assert "timeline.opening_differs" in codes(opening)
    assert "timeline.ending_differs" in codes(ending)


def test_pictures_that_could_not_be_decoded_are_not_a_clean_pass() -> None:
    result = run(good(unreadable_frames=2))

    assert result.status is Status.MANUAL_REVIEW_REQUIRED and "visual.frames_unreadable" in codes(
        result
    )


# --- the approved timeline -----------------------------------------------------------------------


def test_a_render_of_another_timeline_version_fails_even_if_it_looks_fine() -> None:
    result = run(lin=lineage(render_version=4, render_hash="h4"))

    f = finding(result, "lineage.wrong_version")
    assert result.status is Status.FAIL and f.severity is Severity.CRITICAL
    assert f.route_to is Route.RESOLVE_EXPORT and "latest is not" in f.recommended_action


def test_a_render_of_another_timeline_or_other_output_settings_fails() -> None:
    assert "lineage.other_timeline" in codes(run(lin=lineage(render_timeline_id="other")))
    other = run(lin=lineage(render_config_hash="different"))
    assert other.status is Status.FAIL and "lineage.other_output_settings" in codes(other)
    assert "lineage.other_review" in codes(run(lin=lineage(render_session_id="elsewhere")))


def test_a_file_nobody_can_trace_to_a_render_needs_a_person() -> None:
    result = run(lin=Lineage("tl", 3, "h3", "sess", render_found=False))

    assert result.status is Status.MANUAL_REVIEW_REQUIRED
    assert finding(result, "lineage.unknown_render").needs_review


def test_a_render_report_that_did_not_succeed_is_noted() -> None:
    result = run(lin=lineage(render_succeeded=False))

    assert "lineage.render_not_successful" in codes(result) and result.status is Status.WARNING


def test_a_duration_that_is_not_the_timelines_is_investigated_not_assumed() -> None:
    short = good(
        duration=6.0,
        decode=DecodeFacts(True, 150, 6.0, ()),
        audio=audio(duration=6.0),
        video=video(duration=6.0, declared_frames=150),
    )
    result = run(short)

    f = finding(result, "timeline.duration")
    assert result.status is Status.FAIL and f.next_action is NextAction.INVESTIGATE


def test_streams_that_could_not_be_inspected_are_said_not_assumed_missing() -> None:
    result = run(good(inspected=False, video=None, audio=None))

    assert result.status is Status.MANUAL_REVIEW_REQUIRED and result.findings == ()
    assert {"container readable", "video stream facts", "audio stream facts"} <= {
        n.name for n in result.not_checked
    }


# --- the delivery --------------------------------------------------------------------------------


def test_a_delivery_that_is_too_long_is_a_problem_of_the_timeline() -> None:
    shorts = get_delivery_profile("youtube_shorts")

    result = run(good(duration=200.0), exp=expected(duration_seconds=200.0), profile=shorts)

    f = finding(result, "delivery.too_long")
    assert (f.route_to, f.next_action) == (Route.REVISION_ENGINE, NextAction.REVISE_TIMELINE)


def test_the_container_size_metadata_and_name_rules_of_a_delivery() -> None:
    strict = replace(
        PROFILE, max_size_bytes=1000, required_metadata=("title", "creation_time"),
        name_pattern=r"[a-z]+_final\.mp4",
    )  # fmt: skip

    result = run(good(extension=".mov"), profile=strict)

    assert {
        "delivery.container",
        "delivery.too_large",
        "delivery.metadata",
        "delivery.name",
    } <= codes(result)
    assert "creation_time" in finding(result, "delivery.metadata").message
    assert finding(result, "delivery.metadata").route_to is Route.RENDER_MANAGER


# --- the verdict is deterministic ----------------------------------------------------------------


def test_the_same_facts_always_give_the_same_verdict_and_order() -> None:
    facts = good(
        black=(Span(2.0, 3.0),), silent=(Span(3.0, 5.0),),
        loudness=LoudnessFacts(-15.5, 6.0, -2.0, -4.0, True),
    )  # fmt: skip

    first, second = run(facts), run(facts)

    assert first == second
    severities = [f.severity for f in first.findings]
    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    assert severities == sorted(severities, key=order.index)


def test_the_worst_problem_decides_the_next_action() -> None:
    result = run(good(video=video(codec="hevc"), silent=(Span(3.0, 5.0),)))

    assert result.status is Status.FAIL and result.next_action is NextAction.RERENDER
    assert result.severity is Severity.HIGH


def test_every_finding_names_an_owner_and_a_next_step() -> None:
    result = run(
        good(
            video=video(width=1280, height=720, display_aspect=Rational(16, 9)),
            black=(Span(2.0, 3.0),), audio=audio(sample_rate=44100),
            loudness=LoudnessFacts(-22.0, 6.0, 0.0, 0.0, True),
        ),
        lin=lineage(render_hash="other"),
    )  # fmt: skip

    assert result.findings
    for f in result.findings:
        assert f.route_to is not Route.NONE and f.recommended_action and f.message
    # a render-setting problem is never sent to editing and a timeline problem never to the renderer
    for f in result.findings:
        if f.code in {"video.resolution", "audio.sample_rate", "video.codec"}:
            assert f.next_action is NextAction.RERENDER
        if f.code.startswith("lineage."):
            assert f.next_action is not NextAction.REVISE_TIMELINE


# --- profiles ------------------------------------------------------------------------------------


def test_delivery_profiles_are_valid_data_that_names_a_render_profile() -> None:
    ids = [p.delivery_id for p in DELIVERY_PROFILES]

    assert len(ids) == len(set(ids))
    assert {"youtube_16_9", "youtube_shorts", "instagram_reels", "square", "master"} <= set(ids)
    assert all(p.render_profile_id and p.render_profile_version >= 1 for p in DELIVERY_PROFILES)
    with pytest.raises(UnknownDeliveryProfile):
        get_delivery_profile("vimeo")
    with pytest.raises(InvalidDeliveryProfile):
        DeliveryProfile("bad id", 1, "x", "youtube_16_9")
    with pytest.raises(InvalidDeliveryProfile):
        DeliveryProfile("ok_id", 1, "x", "youtube_16_9", name_pattern="(")
