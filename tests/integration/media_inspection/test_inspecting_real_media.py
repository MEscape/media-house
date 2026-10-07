"""What the inspector reports for real files whose technical properties are known."""

from typing import Any, cast

import pytest

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.values import (
    Certainty,
    ChannelClass,
    Concern,
    Depth,
    FrameRateMode,
    Provenance,
    Rational,
    ScanType,
    Severity,
    Verdict,
)
from media_house.modules.media_inspection.infrastructure.ffprobe_prober import FfprobeProber
from media_house.shared.concurrency import CancellationToken
from tests.integration.media_inspection.conftest import Env

pytestmark = pytest.mark.integration


class TestOrdinaryVideo:
    def test_exact_frame_rate_timebase_codec_and_pixel_format(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.cfr())

        video = inspection.primary_video
        assert video is not None
        assert video.codec == "h264"
        assert video.frame_rate.value == Rational(30000, 1001)  # 29.97, not "30"
        assert video.average_frame_rate.value == Rational(30000, 1001)
        assert video.time_base == Rational(1, 30000)
        assert (video.geometry.width, video.geometry.height) == (160, 120)
        assert video.pixel_format == "yuv420p"
        assert video.bit_depth.value == 8
        assert video.chroma_subsampling.value == "420"
        assert video.scan_type.value is ScanType.PROGRESSIVE
        assert video.timing is not None
        assert video.timing.mode is FrameRateMode.CONSTANT
        assert video.timing.packet_count == 59  # 2 s at 29.97 fps
        assert video.timing.reordered  # B-frames: decode order differs from display order

    def test_audio_stream_facts_and_the_stream_map(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.cfr())

        audio = inspection.audio_stream(0)
        assert audio is not None
        assert (audio.codec, audio.sample_rate, audio.channels) == ("aac", 48_000, 2)
        assert audio.channel_class is ChannelClass.STEREO
        assert audio.channel_layout.value == "stereo"
        assert audio.duration == pytest.approx(2.0, abs=0.05)
        assert inspection.container.stream_count == 3  # video, audio and the timecode track
        assert "mp4" in inspection.container.format_names

    def test_drop_frame_timecode_is_read_and_placed_on_the_clock(self, env: Env) -> None:
        timecode = env.inspection_of(env.samples.cfr()).observed.timecode

        assert timecode is not None
        assert str(timecode.start) == "01:02:03;04"
        assert timecode.start.drop_frame
        assert timecode.start_seconds == pytest.approx(3723.12, abs=0.05)

    def test_non_drop_timecode_at_25_fps(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.cfr_25())

        assert str(inspection.observed.timecode.start) == "10:00:00:00"  # type: ignore[union-attr]
        assert inspection.primary_video is not None
        assert inspection.primary_video.codec == "mpeg4"
        assert inspection.primary_video.frame_rate.value == Rational(25, 1)

    def test_a_healthy_file_decodes_without_complaints(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.cfr())

        assert inspection.observed.integrity.decode_checked
        assert inspection.observed.integrity.decode_message_count == 0
        assert inspection.status.usable
        assert Concern.REPAIR not in inspection.status.concerns

    def test_raw_report_is_kept_beside_the_normalised_facts(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.cfr())

        raw: Any = inspection.observed.raw_metadata
        assert raw["format"]["format_name"].startswith("mov")
        assert len(raw["streams"]) == 3


class TestFrameTiming:
    def test_variable_frame_rate_is_detected_from_the_timestamps(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.vfr())

        video = inspection.primary_video
        assert video is not None and video.timing is not None
        assert video.timing.mode is FrameRateMode.VARIABLE
        assert video.frame_rate.value == Rational(30, 1)  # what the container CLAIMS
        finding = inspection.findings_with("video.variable_frame_rate")[0]
        assert finding.certainty is Certainty.MEASURED
        assert finding.concern is Concern.TRANSCODE

    def test_dropped_frames_are_a_possible_problem_not_a_fact(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.dropped_frames())

        finding = inspection.findings_with("video.dropped_frames")[0]
        assert finding.certainty is Certainty.POSSIBLE
        assert finding.evidence["estimated_frames"] == 2
        assert inspection.primary_video.timing.mode is FrameRateMode.CONSTANT  # type: ignore[union-attr]

    def test_a_missing_stretch_is_a_discontinuity_with_its_position(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.hole())

        finding = inspection.findings_with("video.timestamp_discontinuity")[0]
        assert finding.evidence["count"] == 1
        positions = cast("list[float]", finding.evidence["at_seconds"])
        assert positions == pytest.approx([0.967], abs=0.01)
        assert Concern.SYNC_CHECK in inspection.status.concerns


class TestColorAndPicture:
    def test_hdr10_properties_are_declared_and_metadata_is_found(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.hdr())

        video = inspection.primary_video
        assert video is not None
        assert video.codec == "hevc"
        assert (video.bit_depth.value, video.pixel_format) == (10, "yuv420p10le")
        color = video.color
        assert (color.space.value, color.primaries.value, color.transfer.value) == (
            "bt2020nc",
            "bt2020",
            "smpte2084",
        )
        assert color.transfer.provenance is Provenance.DECLARED
        assert (color.dynamic_range.value, color.dynamic_range.provenance) == (
            "hdr10",
            Provenance.INFERRED,
        )
        assert color.mastering_display and color.content_light_level
        assert "color.hdr_metadata_missing" not in {f.code for f in inspection.findings}

    def test_sdr_without_color_tags_is_reported_as_undeclared_not_assumed(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.cfr())

        color = inspection.primary_video.color  # type: ignore[union-attr]
        assert not color.primaries.known and not color.transfer.known
        assert color.dynamic_range.provenance is Provenance.UNKNOWN
        finding = inspection.findings_with("color.undeclared")[0]
        assert finding.concern is Concern.COLOR_CHECK
        assert finding.severity is Severity.INFO

    def test_display_rotation_is_separate_from_the_stored_size(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.rotated())

        geometry = inspection.primary_video.geometry  # type: ignore[union-attr]
        assert (geometry.width, geometry.height) == (160, 120)  # storage is unchanged
        assert geometry.rotation.value == 270  # clockwise
        assert geometry.display_size == (120, 160)
        assert inspection.findings_with("video.rotated")

    def test_professional_codec_with_high_bit_depth_and_pcm_audio(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.prores())

        video = inspection.primary_video
        assert video is not None
        assert video.codec == "prores"
        assert (video.bit_depth.value, video.chroma_subsampling.value) == (10, "422")
        audio = inspection.audio_stream(0)
        assert audio is not None
        assert audio.codec.startswith("pcm_s24")
        assert audio.bit_depth.value == 24
        assert audio.bit_depth.provenance is Provenance.DECLARED  # PCM states its sample size


class TestAudioAndSynchronization:
    def test_audio_only_files_have_no_video_and_no_missing_video_complaint(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.audio_only())

        assert inspection.video_streams == ()
        audio = inspection.audio_stream(0)
        assert audio is not None
        assert (audio.codec, audio.sample_rate, audio.channels) == ("pcm_s16le", 48_000, 2)
        assert audio.bit_depth.value == 16
        assert inspection.synchronization.offsets == ()
        assert inspection.status.verdict is not Verdict.ERROR

    def test_video_only_files_are_reported_without_audio(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.silent_video())

        assert inspection.audio_streams == ()
        assert inspection.findings_with("audio.missing")[0].severity is Severity.INFO
        assert inspection.status.usable

    def test_several_audio_streams_are_listed_in_order_with_their_languages(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.multi_audio())

        first, second = inspection.audio_streams
        assert (first.sample_rate, second.sample_rate) == (44_100, 22_050)
        assert (first.language, second.language) == ("eng", "deu")
        assert inspection.audio_stream(1) == second
        assert inspection.findings_with("audio.sample_rate_mismatch")
        assert Concern.AUDIO_CHECK in inspection.status.concerns

    def test_audio_that_starts_late_is_measured(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.late_audio())

        offset = inspection.synchronization.offsets[0]
        assert offset.start_offset == pytest.approx(0.5, abs=0.05)
        finding = inspection.findings_with("sync.audio_offset")[0]
        assert finding.certainty is Certainty.POSSIBLE  # a measured fact, maybe intentional
        assert Concern.SYNC_CHECK in inspection.status.concerns

    def test_audio_that_starts_with_the_video_is_not_flagged(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.synced())

        assert not inspection.findings_with("sync.audio_offset")
        assert not inspection.findings_with("sync.drift")


class TestProductionMetadata:
    def test_camera_and_clip_tags(self, env: Env) -> None:
        production = env.inspection_of(env.samples.tagged()).observed.production

        assert (production.camera_make, production.camera_model) == ("ACME", "Cam One")
        assert (production.scene, production.take) == ("12A", "3")
        assert production.creation_time is not None
        assert production.creation_time.isoformat().startswith("2026-03-04T05:06:07")
        assert production.lens is None and production.iso is None  # absent stays absent

    def test_missing_production_metadata_is_stated_not_invented(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.cfr())

        assert inspection.observed.production.camera_make is None
        assert inspection.findings_with("metadata.production_missing")


class TestDamagedMedia:
    def test_a_file_that_ends_early_is_reported_not_repaired(self, env: Env) -> None:
        before = env.samples.truncated_mkv().read_bytes()

        inspection = env.inspection_of(env.samples.truncated_mkv())

        assert not inspection.status.usable
        assert (
            inspection.findings_with("integrity.decode_errors")[0].certainty is Certainty.CONFIRMED
        )
        assert inspection.findings_with("integrity.duration_mismatch")
        assert Concern.REPAIR in inspection.status.concerns
        assert env.samples.truncated_mkv().read_bytes() == before

    def test_corrupted_data_is_found_by_decoding(self, env: Env) -> None:
        inspection = env.inspection_of(env.samples.damaged_mkv())

        assert inspection.observed.integrity.decode_message_count > 0
        assert inspection.status.verdict is Verdict.ERROR

    def test_the_cheaper_probe_depth_does_not_claim_the_streams_are_sound(self, env: Env) -> None:
        inspection = env.inspection_of(
            env.samples.damaged_mkv(), InspectionConfig(depth=Depth.PROBE)
        )

        assert not inspection.observed.integrity.decode_checked
        assert inspection.findings_with("integrity.not_decoded")
        assert inspection.observed.integrity.decode_message_count == 0  # nothing was decoded

    def test_a_container_that_cannot_be_opened_is_a_result_not_an_exception(self, env: Env) -> None:
        # the library refuses unreadable media at import, so the prober is asked directly
        broken = env.samples.truncated_mp4()

        observed = FfprobeProber(env.runner).probe(broken, InspectionConfig(), CancellationToken())

        assert not observed.integrity.readable
        assert observed.integrity.read_error
        assert str(broken) not in observed.integrity.read_error  # no path in the message
