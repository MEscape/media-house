"""Render profiles are validated data; the output hash names what a file is; a finished file is
checked against what was promised."""

import pytest

from media_house.core.domain import Rational
from media_house.modules.render_manager.domain.errors import (
    InvalidRenderProfile,
    UnknownRenderProfile,
)
from media_house.modules.render_manager.domain.keys import FrameRange, job_key, output_name
from media_house.modules.render_manager.domain.profile import (
    PROFILES,
    AudioSettings,
    RenderProfile,
    ResolvedSettings,
    VideoSettings,
    get_profile,
    output_config_hash,
    profile_problems,
    resolve_settings,
)
from media_house.modules.render_manager.domain.values import (
    CheckState,
    Purpose,
    RenderType,
    Verification,
)
from media_house.modules.render_manager.domain.verify import (
    Check,
    Expectation,
    OutputFacts,
    overall,
    verify_output,
)

RATE = Rational(25, 1)


def resolved(
    profile_id: str = "youtube_16_9", w: int = 1920, h: int = 1080, rate: Rational = RATE
) -> ResolvedSettings:
    return resolve_settings(get_profile(profile_id), width=w, height=h, frame_rate=rate)


def test_every_builtin_profile_is_valid_and_has_a_unique_id() -> None:
    ids = [p.profile_id for p in PROFILES]

    assert len(ids) == len(set(ids))
    assert {"youtube_16_9", "shorts_9_16", "reels_9_16", "square_1_1", "review_fast"} <= set(ids)
    assert {"review_proxy", "intermediate", "master_hq"} <= set(ids)


def test_an_unknown_profile_names_the_known_ones() -> None:
    with pytest.raises(UnknownRenderProfile) as raised:
        get_profile("tiktok")

    assert "youtube_16_9" in raised.value.user_message


def test_a_custom_profile_is_validated() -> None:
    ok = RenderProfile(
        "my_square", 1, "Mine", Purpose.DELIVERY,
        VideoSettings(1080, 1080, Rational(30, 1), "h264", "mp4", "yuv420p", aspect=(1, 1)),
        AudioSettings("aac", 48000, 2),
    )  # fmt: skip
    assert ok.profile_id == "my_square"
    video = VideoSettings(1080, None, None, "h264", "mp4", "yuv420p")
    with pytest.raises(InvalidRenderProfile, match="together"):
        RenderProfile("bad_size", 1, "x", Purpose.PREVIEW, video, AudioSettings("aac", 48000, 2))
    with pytest.raises(InvalidRenderProfile, match="Bad Id"):
        RenderProfile(
            "Bad Id", 1, "x", Purpose.PREVIEW,
            VideoSettings(None, None, None, "h264", "mp4", "yuv420p"),
            AudioSettings("aac", 48000, 2),
        )  # fmt: skip
    with pytest.raises(InvalidRenderProfile, match="sample rate"):
        RenderProfile(
            "odd_rate", 1, "x", Purpose.PREVIEW,
            VideoSettings(None, None, None, "h264", "mp4", "yuv420p"),
            AudioSettings("aac", 12345, 2),
        )  # fmt: skip
    with pytest.raises(InvalidRenderProfile, match="quality"):
        RenderProfile(
            "odd_quality", 1, "x", Purpose.PREVIEW,
            VideoSettings(None, None, None, "h264", "mp4", "yuv420p", quality="very"),
            AudioSettings("aac", 48000, 2),
        )  # fmt: skip


def test_the_timeline_fills_in_what_the_profile_leaves_open() -> None:
    settings = resolved("youtube_16_9", 1920, 1080, Rational(30000, 1001))

    assert (settings.width, settings.height) == (1920, 1080)
    assert settings.frame_rate == Rational(30000, 1001)
    proxy = resolved("review_proxy")
    assert (proxy.width, proxy.height) == (960, 540)


def test_the_output_hash_names_the_real_output() -> None:
    base = output_config_hash(resolved())

    assert base == output_config_hash(resolved())  # deterministic
    assert base != output_config_hash(resolved(rate=Rational(30, 1)))  # another frame rate
    assert base != output_config_hash(resolved(w=3840, h=2160))  # another size
    assert base != output_config_hash(resolved("master_hq"))  # another profile
    assert base != output_config_hash(resolved("shorts_9_16", 1080, 1920))


def test_a_profile_that_does_not_fit_the_timeline_says_why() -> None:
    portrait = get_profile("shorts_9_16")

    assert profile_problems(portrait, width=1920, height=1080)
    assert not profile_problems(portrait, width=1080, height=1920)
    assert not profile_problems(get_profile("review_fast"), width=1920, height=1080)


def test_the_job_key_changes_with_the_content_and_not_with_a_rebuild() -> None:
    def key(content: str = "h1", rng: FrameRange | None = None, backend: str = "resolve") -> str:
        return job_key(
            timeline_id="tl", content_hash=content, render_type=RenderType.REVIEW,
            profile_id="review_fast", profile_version=1, config_hash="c", frames=rng,
            backend=backend,
        )  # fmt: skip

    assert key() == key()
    assert key() != key("h2")  # the video changed
    assert key() != key(rng=FrameRange(0, 50))
    assert key() != key(backend="other")


def test_an_output_name_is_unique_versioned_and_safe() -> None:
    name = output_name(
        "{timeline}_v{version}_{type}_{profile}_{n:03d}",
        timeline="tl:ab/c", version=3, render_type="review", profile="review_fast", n=2,
    )  # fmt: skip

    assert name == "tl_ab_c_v3_review_review_fast_002"


def facts(**changes: object) -> OutputFacts:
    base = OutputFacts(
        exists=True, readable=True, size_bytes=400_000, extension=".mp4", duration=8.0,
        has_video=True, has_audio=True, width=1920, height=1080, frame_rate=RATE,
        video_codec="h264", pixel_format="yuv420p", audio_codec="aac", audio_duration=8.0,
        sample_rate=48000, channels=2,
    )  # fmt: skip
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


def expectation(**changes: object) -> Expectation:
    from dataclasses import replace

    base = Expectation(
        width=1920, height=1080, frame_rate=RATE, duration=8.0, video_codec="h264",
        container="mp4", pixel_format="yuv420p", audio_required=True, audio_codec="aac",
        sample_rate=48000, channels=2,
    )  # fmt: skip
    return replace(base, **changes)  # type: ignore[arg-type]


def states(checks: tuple[Check, ...], name: str) -> CheckState:
    return next(c.state for c in checks if c.name == name)


def test_a_file_that_is_what_was_promised_verifies() -> None:
    checks = verify_output(facts(), expectation())

    assert overall(checks) is Verification.VERIFIED
    assert all(c.state is CheckState.PASSED for c in checks)


@pytest.mark.parametrize(
    ("changes", "check"),
    [
        ({"exists": False}, "output exists"),
        ({"size_bytes": 100}, "size plausible"),
        ({"readable": False}, "output readable"),
        ({"inspection_error": True}, "output readable"),
        ({"has_video": False}, "video stream"),
        ({"has_audio": False}, "audio stream"),
        ({"duration": 5.0}, "duration"),
        ({"audio_duration": 6.0}, "audio duration"),
        ({"width": 1280, "height": 720}, "resolution"),
        ({"frame_rate": Rational(30, 1)}, "frame rate"),
        ({"video_codec": "hevc"}, "video codec"),
        ({"extension": ".mov"}, "container"),
    ],
)
def test_a_file_that_is_not_what_was_promised_fails_the_matching_check(
    changes: dict[str, object], check: str
) -> None:
    checks = verify_output(facts(**changes), expectation())

    assert states(checks, check) is CheckState.FAILED
    assert overall(checks) is Verification.FAILED


def test_an_unusual_pixel_format_or_audio_codec_is_a_warning_not_a_failure() -> None:
    checks = verify_output(facts(pixel_format="yuv444p", audio_codec="opus"), expectation())

    assert states(checks, "pixel format") is CheckState.WARNING
    assert states(checks, "audio codec") is CheckState.WARNING
    assert overall(checks) is Verification.WITH_WARNINGS


def test_a_setting_the_backend_cannot_apply_is_not_held_against_the_file() -> None:
    checks = verify_output(facts(video_codec="hevc"), expectation(not_applied=("codec",)))

    assert states(checks, "video codec") is CheckState.WARNING


def test_a_missing_duration_is_not_checked_not_passed() -> None:
    checks = verify_output(facts(duration=None), expectation())

    assert states(checks, "duration") is CheckState.NOT_CHECKED
    assert overall(checks) is Verification.WITH_WARNINGS


def test_a_silent_timeline_needs_no_audio_stream() -> None:
    checks = verify_output(facts(has_audio=False), expectation(audio_required=False))

    assert overall(checks) is Verification.VERIFIED
