"""The validation contract: what each verdict needs, and what no verdict may claim."""

from collections.abc import Callable
from dataclasses import replace

import pytest

from media_house.modules.proxy_manager.domain.profile import resolve_profile
from media_house.modules.proxy_manager.domain.records import assess
from media_house.modules.proxy_manager.domain.source import SourceFacts
from media_house.modules.proxy_manager.domain.spec import (
    ResolvedSpec,
    reservation_bytes,
    resolve_spec,
)
from media_house.modules.proxy_manager.domain.validation import (
    OutputFacts,
    ValidationReport,
    validate,
)
from media_house.modules.proxy_manager.domain.values import (
    VALIDATOR_VERSION,
    Condition,
    Evidence,
    Verdict,
)
from tests.support.proxy_fakes import (
    audio_facts,
    perfect_output,
    source_facts,
    video_facts,
)


def setup(
    profile: str = "analysis", facts: SourceFacts | None = None
) -> tuple[ResolvedSpec, SourceFacts]:
    facts = facts or source_facts()
    return resolve_spec(resolve_profile(profile), facts), facts


def judge(
    spec: ResolvedSpec, facts: SourceFacts, output: OutputFacts, *, decode: bool = False
) -> ValidationReport:
    return validate(
        spec, facts, output, reserved_bytes=reservation_bytes(spec, facts), require_decode=decode
    )


def with_video(output: OutputFacts, **changes: object) -> OutputFacts:
    assert output.video is not None
    return replace(output, video=replace(output.video, **changes))  # type: ignore[arg-type]


def test_a_faithful_output_is_valid_and_rests_on_packets_not_on_decoding() -> None:
    spec, facts = setup()

    report = judge(spec, facts, perfect_output(spec, facts))

    assert report.verdict is Verdict.VALID
    assert report.evidence is Evidence.PACKETS  # complete and consistent, NOT proven decodable
    assert not report.failures and not report.unevaluated


def test_a_decoded_clean_output_reaches_decoded_evidence() -> None:
    spec, facts = setup()
    output = replace(perfect_output(spec, facts), decode_errors=0)

    report = judge(spec, facts, output, decode=True)

    assert report.verdict is Verdict.VALID and report.evidence is Evidence.DECODED


def test_requiring_a_decode_that_was_not_done_is_unverifiable_never_valid() -> None:
    spec, facts = setup()

    report = judge(spec, facts, perfect_output(spec, facts), decode=True)

    assert report.verdict is Verdict.UNVERIFIABLE
    assert [c.name for c in report.unevaluated] == ["decodable"]


def test_decoder_complaints_make_the_output_invalid_even_when_not_required() -> None:
    spec, facts = setup()
    output = replace(perfect_output(spec, facts), decode_errors=3)

    assert judge(spec, facts, output).verdict is Verdict.INVALID


def test_a_deliberate_departure_from_the_source_timeline_is_valid_with_transforms() -> None:
    late = source_facts(video_facts(first_pts=1.5), [audio_facts(first=1.5)])
    spec, facts = setup(facts=late)

    report = judge(spec, facts, perfect_output(spec, facts))

    assert report.verdict is Verdict.VALID_WITH_TRANSFORMS
    assert report.transformations == ("start_time_rebased",)


def test_a_start_the_spec_promised_to_keep_must_be_kept() -> None:
    late = source_facts(video_facts(first_pts=1.5), [audio_facts(first=1.5)])
    spec, facts = setup(facts=late)
    keeps_start = replace(spec, transformations=())  # a spec that promises no rebasing

    kept = perfect_output(keeps_start, facts)  # start 1.5 preserved
    assert judge(keeps_start, facts, kept).verdict is Verdict.VALID

    assert kept.video is not None
    moved = with_video(kept, first_pts=0.0, end_pts=kept.video.end_pts - 1.5)
    assert "start_time" in [c.name for c in judge(keeps_start, facts, moved).failures]


@pytest.mark.parametrize(
    ("mutate", "failed"),
    [
        (lambda o: with_video(o, frames=o.video.frames - 1), "frame_count"),
        (lambda o: with_video(o, width=o.video.width + 2), "geometry"),
        (lambda o: with_video(o, rotation=90), "rotation"),
        (lambda o: with_video(o, codec="mpeg4"), "codec"),
        (lambda o: with_video(o, end_pts=o.video.end_pts - 1.0), "video_duration"),
        (lambda o: replace(o, audio=()), "streams"),
        (lambda o: replace(o, other_streams=1), "streams"),
        (lambda o: replace(o, video=None), "streams"),
        (lambda o: replace(o, timecode=None), "timecode"),
        (lambda o: replace(o, size_bytes=10), "size"),
    ],
)
def test_each_broken_promise_is_a_named_hard_failure(
    mutate: Callable[[OutputFacts], OutputFacts], failed: str
) -> None:
    spec, facts = setup(facts=source_facts(timecode="01:00:00:00"))
    output = mutate(perfect_output(spec, facts))

    report = judge(spec, facts, output)

    assert report.verdict is Verdict.INVALID
    assert failed in [c.name for c in report.failures]


def test_a_truncated_output_is_invalid_by_frames_and_duration() -> None:
    spec, facts = setup()
    output = perfect_output(spec, facts)
    assert output.video is not None
    cut = with_video(output, frames=output.video.frames // 2, end_pts=output.video.end_pts / 2)

    names = {c.name for c in judge(spec, facts, cut).failures}

    assert {"frame_count", "video_duration"} <= names


def test_an_output_larger_than_its_reservation_is_invalid() -> None:
    spec, facts = setup()
    big = perfect_output(spec, facts, size=reservation_bytes(spec, facts) + 1)

    assert "size" in [c.name for c in judge(spec, facts, big).failures]


def test_duration_tolerance_is_one_source_and_one_output_frame() -> None:
    spec, facts = setup()
    output = perfect_output(spec, facts)
    assert output.video is not None
    tolerance = 2 / 25  # source frame + output frame at 25 fps

    inside = with_video(output, end_pts=output.video.end_pts - tolerance * 0.9)
    outside = with_video(output, end_pts=output.video.end_pts - tolerance * 1.1)

    assert "video_duration" not in [c.name for c in judge(spec, facts, inside).failures]
    assert "video_duration" in [c.name for c in judge(spec, facts, outside).failures]


def test_audio_that_drifted_out_of_sync_is_invalid() -> None:
    spec, facts = setup()
    output = perfect_output(spec, facts)
    moved = replace(
        output,
        audio=tuple(
            replace(a, first_pts=a.first_pts + 0.3, end_pts=a.end_pts + 0.3) for a in output.audio
        ),
    )

    names = {c.name for c in judge(spec, facts, moved).failures}

    assert "audio_0_sync" in names


def test_wrong_audio_format_is_invalid() -> None:
    spec, facts = setup()
    output = perfect_output(spec, facts)
    mono = replace(output, audio=tuple(replace(a, channels=1) for a in output.audio))

    assert "audio_0" in [c.name for c in judge(spec, facts, mono).failures]


# ---------------------------------------------------------------------------- condition
BASE: dict[str, object] = {
    "has_slot": True,
    "asset_present": True,
    "file_ok": True,
    "verdict": Verdict.VALID,
    "validator_version": VALIDATOR_VERSION,
    "artifact_fingerprint": "a",
    "wanted_fingerprint": "a",
    "validator_current": VALIDATOR_VERSION,
}


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, Condition.READY),
        ({"has_slot": False}, Condition.NONE),
        ({"asset_present": False}, Condition.MISSING),
        ({"file_ok": False}, Condition.MISSING),
        ({"file_ok": False, "verdict": Verdict.INVALID}, Condition.MISSING),
        ({"verdict": Verdict.INVALID}, Condition.INVALID),
        ({"verdict": Verdict.UNVERIFIABLE}, Condition.INVALID),
        ({"verdict": None}, Condition.INVALID),
        ({"validator_version": 0}, Condition.NEEDS_REVALIDATION),
        ({"wanted_fingerprint": "b"}, Condition.STALE),
        ({"verdict": Verdict.VALID_WITH_TRANSFORMS}, Condition.READY),
    ],
)
def test_condition_distinguishes_ready_stale_missing_and_invalid(
    change: dict[str, object], expected: Condition
) -> None:
    assert assess(**{**BASE, **change}) is expected  # type: ignore[arg-type]


def test_a_language_tag_lost_on_the_way_is_a_warning_not_a_failure() -> None:
    facts = source_facts(
        audio=[
            replace(audio_facts(index=1), language="eng"),
            replace(audio_facts(index=2), language="deu"),
        ]
    )
    spec, facts = setup("edit", facts)
    output = perfect_output(spec, facts)
    lost = replace(output, audio=(output.audio[0], replace(output.audio[1], language=None)))

    kept, report = judge(spec, facts, output), judge(spec, facts, lost)

    assert kept.warnings == () and kept.verdict is Verdict.VALID
    assert report.verdict is Verdict.VALID
    assert report.warnings == ("audio track 1: language 'deu' was not preserved (unset)",)
