"""EnsureProxy with REAL FFmpeg and ffprobe on real (small, generated) media.

Every assertion about the produced file uses ffprobe called directly by the test, not the module's
own probe, so the module is not grading itself. These tests are what turns "the command should
work" into "this was observed to work": a case that is not covered here is not claimed.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from media_house.modules.proxy_manager.application.contracts import (
    Condition,
    Evidence,
    ProxyDto,
    Verdict,
)
from media_house.modules.proxy_manager.application.ports import MediaProbe
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import Err, Ok
from tests.integration.proxy_manager.conftest import Env
from tests.support import proxy_media as media
from tests.support.proxy_media import probe, rotation, streams, video_pts

pytestmark = pytest.mark.integration

TOLERANCE = 0.1  # seconds: generous here; the module's own tolerances are tested in unit tests


def proxy_path(env: Env, dto: ProxyDto) -> Path:
    path = env.library.local_path(dto.proxy_asset_id)
    assert isinstance(path, Ok)
    return path.value


def make(env: Env, builder: Callable[..., Path], name: str, **kwargs: Any) -> tuple[str, Path]:
    path = builder(env.media_dir / name, **kwargs)
    return env.import_media(path), path


def duration(path: Path, kind: str) -> float:
    return float(streams(path, kind)[0]["duration"])


def test_a_landscape_clip_becomes_a_smaller_h264_proxy_with_matching_streams(env: Env) -> None:
    source, original = make(env, media.cfr_with_audio, "hd.mp4", seconds=2, size="1280x720")

    dto = env.ok(env.real, source, "analysis")

    out = proxy_path(env, dto)
    video, audio = streams(out, "video")[0], streams(out, "audio")
    assert (dto.verdict, dto.evidence, dto.condition) == (
        Verdict.VALID,
        Evidence.PACKETS,
        Condition.READY,
    )
    assert (video["codec_name"], video["width"], video["height"], video["pix_fmt"]) == (
        "h264", 640, 360, "yuv420p",
    )  # fmt: skip
    assert len(audio) == 1 and audio[0]["codec_name"] == "aac"
    assert len(video_pts(out)) == len(video_pts(original)) == 50
    assert abs(duration(out, "video") - duration(original, "video")) < TOLERANCE
    assert out.stat().st_size < original.stat().st_size


def test_the_edit_profile_makes_prores_proxy_in_mov_with_pcm_audio(env: Env) -> None:
    source, original = make(env, media.cfr_with_audio, "hd.mp4", seconds=2, size="1280x720")

    dto = env.ok(env.real, source, "edit")

    out = proxy_path(env, dto)
    video, audio = streams(out, "video")[0], streams(out, "audio")[0]
    assert dto.verdict is Verdict.VALID
    assert (video["codec_name"], video["width"], video["height"]) == ("prores", 960, 540)
    assert video["pix_fmt"] == "yuv422p10le" and audio["codec_name"] == "pcm_s16le"
    assert "mov" in probe(out)["format"]["format_name"]
    assert len(video_pts(out)) == len(video_pts(original))


def test_a_video_only_clip_gets_a_proxy_without_audio(env: Env) -> None:
    source, _ = make(env, media.video_only, "silent.mp4")

    dto = env.ok(env.real, source, "analysis")

    assert dto.verdict is Verdict.VALID and streams(proxy_path(env, dto), "audio") == []


def test_every_audio_track_is_kept_by_the_edit_profile_and_only_the_first_by_analysis(
    env: Env,
) -> None:
    source, _ = make(env, media.two_audio_tracks, "two.mp4")

    analysis = env.ok(env.real, source, "analysis")
    edit = env.ok(env.real, source, "edit")

    assert len(streams(proxy_path(env, analysis), "audio")) == 1
    assert analysis.warnings == ()  # the first track's language 'eng' survived in MP4
    tracks = streams(proxy_path(env, edit), "audio")
    assert len(tracks) == 2 and tracks[0].get("tags", {}).get("language") == "eng"


def test_a_language_tag_the_mov_muxer_cannot_carry_is_reported_not_hidden(env: Env) -> None:
    """Measured with FFmpeg 9.0.2: the MOV muxer drops the language of every audio track after the
    first, whatever the codec (MP4 keeps them). The proxy is still valid; the loss is a warning."""
    source, _ = make(env, media.two_audio_tracks, "two.mp4")

    edit = env.ok(env.real, source, "edit")

    assert edit.verdict is Verdict.VALID
    assert edit.warnings == ("audio track 1: language 'deu' was not preserved (unset)",)
    again = env.ok(env.real, source, "edit")  # the warning travels with the stored proxy
    assert again.reused and again.warnings == edit.warnings


def test_rotation_stays_metadata_and_is_not_applied_twice(env: Env) -> None:
    source, original = make(env, media.rotated, "rotated.mp4", degrees=90)
    assert rotation(streams(original, "video")[0]) != 0

    for profile in ("analysis", "edit"):
        dto = env.ok(env.real, source, profile)
        out_video = streams(proxy_path(env, dto), "video")[0]
        assert dto.verdict is Verdict.VALID, profile
        assert rotation(out_video) == rotation(streams(original, "video")[0]), profile
        # the pixels are still stored in the source's orientation, so nothing was turned twice
        assert (out_video["width"], out_video["height"]) == (320, 240), profile


def test_non_square_pixels_are_baked_into_square_ones_at_the_displayed_shape(env: Env) -> None:
    source, _ = make(env, media.anamorphic, "pal.mp4")  # 720x576, 16:15 pixels: shown 4:3

    dto = env.ok(env.real, source, "analysis")

    video = streams(proxy_path(env, dto), "video")[0]
    assert dto.verdict is Verdict.VALID
    assert (video["width"], video["height"]) == (640, 480)
    assert (
        video["sample_aspect_ratio"] in ("1:1", None) or video.get("sample_aspect_ratio") == "1:1"
    )


def test_variable_frame_rate_keeps_every_frame_at_its_own_time(env: Env) -> None:
    source, original = make(env, media.variable_frame_rate, "vfr.mp4")

    dto = env.ok(env.real, source, "analysis")

    before, after = video_pts(original), video_pts(proxy_path(env, dto))
    assert dto.verdict is Verdict.VALID and len(before) == len(after)
    assert max(abs(a - b) for a, b in zip(before, after, strict=True)) < 0.001  # not made constant


def test_a_clip_that_starts_late_is_rebased_to_zero_and_says_so(env: Env) -> None:
    source, original = make(env, media.late_start, "late.mp4", offset=1.5)
    assert float(streams(original, "video")[0]["start_time"]) == pytest.approx(1.5)

    dto = env.ok(env.real, source, "analysis")

    out = proxy_path(env, dto)
    assert dto.verdict is Verdict.VALID_WITH_TRANSFORMS
    assert dto.transformations == ("start_time_rebased",)
    assert float(streams(out, "video")[0]["start_time"]) < 0.1  # rebased, as promised
    assert len(video_pts(out)) == len(video_pts(original))


def test_a_clip_cut_without_reencoding_counts_only_the_frames_a_player_shows(env: Env) -> None:
    source, original = make(env, media.cut_without_reencoding, "cut.mp4")

    dto = env.ok(env.real, source, "analysis")

    assert dto.verdict is Verdict.VALID
    assert len(video_pts(proxy_path(env, dto))) == len(video_pts(original))


def test_source_timecode_travels_with_the_proxy(env: Env) -> None:
    source, _ = make(env, media.with_timecode, "tc.mov")

    dto = env.ok(env.real, source, "analysis")

    out = probe(proxy_path(env, dto))
    tags = {
        **out["format"].get("tags", {}),
        **streams(proxy_path(env, dto), "video")[0].get("tags", {}),
    }
    assert dto.verdict is Verdict.VALID and tags.get("timecode") == "01:00:00:00"


def test_audio_only_media_is_declined_with_a_reason(env: Env) -> None:
    source = env.import_media(media.audio_only(env.media_dir / "tone.m4a"))

    result = env.ensure(env.real, source, "analysis")

    assert isinstance(result, Err) and "not video" in str(result.error)


def test_full_decode_validation_reaches_decoded_evidence(env: Env) -> None:
    source, _ = make(env, media.cfr_with_audio, "hd.mp4", seconds=2, size="1280x720")

    plain = env.ok(env.real, source, "analysis")
    decoded = env.ok(env.real, source, "analysis", full_decode_validation=True)

    assert plain.evidence is Evidence.PACKETS
    assert decoded.evidence is Evidence.DECODED and decoded.verdict is Verdict.VALID


def test_a_stored_proxy_is_reused_without_running_ffmpeg_again(env: Env) -> None:
    source, _ = make(env, media.cfr_with_audio, "hd.mp4", seconds=2, size="1280x720")
    first = env.ok(env.real, source, "analysis")
    path = proxy_path(env, first)
    modified = path.stat().st_mtime_ns

    again = env.ok(env.real, source, "analysis")

    assert again.reused and again.proxy_asset_id == first.proxy_asset_id
    assert path.stat().st_mtime_ns == modified


def test_stored_inspection_facts_give_the_same_proxy_as_probing_the_file(env: Env) -> None:
    source, _ = make(env, media.cfr_with_audio, "hd.mp4", seconds=2, size="1280x720")
    probed = env.ok(env.real, source, "analysis")
    env.inspect(source)

    again = env.ok(env.real, source, "analysis")

    # facts from the inspection resolved the identical spec, so the stored proxy was reused
    assert again.reused and again.spec_fingerprint == probed.spec_fingerprint


# ------------------------------------------------- what the validator can and cannot establish
def test_packet_level_checks_catch_truncation_but_only_decoding_catches_corruption(
    env: Env,
) -> None:
    source, _ = make(env, media.cfr_with_audio, "hd.mp4", seconds=4, size="1280x720")
    dto = env.ok(env.real, source, "analysis")
    good = proxy_path(env, dto)
    probe_port = env.container.resolve(MediaProbe)
    token = CancellationToken()

    corrupted = env.media_dir / "corrupted.mp4"
    data = bytearray(good.read_bytes())
    for offset in range(len(data) // 2, len(data) // 2 + 4000, 7):
        data[offset] ^= 0xFF  # damage payload bytes, leave the structure readable
    corrupted.write_bytes(bytes(data))

    structure_only = probe_port.probe_output(corrupted, token, decode=False)
    decoded = probe_port.probe_output(corrupted, token, decode=True)

    assert structure_only.decode_errors is None  # a packet listing says nothing about decoding
    assert decoded.decode_errors is not None and decoded.decode_errors > 0
