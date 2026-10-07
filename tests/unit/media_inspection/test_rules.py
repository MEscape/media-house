"""Findings and status: facts stay as read, conclusions state how sure they are."""

from dataclasses import replace

import pytest

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import (
    ColorInfo,
    DecodeMessage,
    Finding,
    Geometry,
    Integrity,
    MediaInspection,
    ObservedMedia,
    ProductionMetadata,
    TimecodeInfo,
)
from media_house.modules.media_inspection.domain.timecode import Timecode
from media_house.modules.media_inspection.domain.values import (
    Certainty,
    ChannelClass,
    Concern,
    FrameRateMode,
    Rational,
    ScanType,
    Severity,
    Sourced,
    Verdict,
)
from tests.support.inspection_fakes import (
    audio,
    codes,
    inspect,
    observed,
    timing,
    video,
)


def finding(media: MediaInspection, code: str) -> Finding:
    found = [f for f in media.findings if f.code == code]
    assert found, f"{code} not reported: {sorted(codes(media))}"
    return found[0]


class TestCleanMedia:
    def test_a_well_formed_file_is_valid_without_findings(self) -> None:
        result = inspect(observed((video(),), (audio(),)))

        assert result.findings == ()
        assert result.status.verdict is Verdict.VALID
        assert result.status.usable
        assert result.status.concerns == ()

    def test_facts_are_all_still_present_when_nothing_is_wrong(self) -> None:
        result = inspect(observed((video(),), (audio(),)))

        assert result.primary_video is not None
        assert result.primary_video.frame_rate.value == Rational(30, 1)
        assert result.audio_stream(0) is not None
        assert result.container.duration == 3.0


class TestUnreadable:
    def test_a_file_that_cannot_be_opened_is_a_confirmed_error(self) -> None:
        media = observed(integrity=Integrity(False, False, read_error="Invalid data"))

        result = inspect(media)

        bad = finding(result, "container.unreadable")
        assert (bad.severity, bad.certainty) == (Severity.ERROR, Certainty.CONFIRMED)
        assert not result.status.usable
        assert result.status.verdict is Verdict.ERROR
        assert Concern.REPAIR in result.status.concerns
        assert len(result.findings) == 1  # nothing else is claimed about a file never read

    def test_a_container_without_streams_is_an_error(self) -> None:
        assert not inspect(observed()).status.usable


class TestVideoFindings:
    def test_variable_frame_rate_is_measured_and_asks_for_a_transcode(self) -> None:
        stream = video(timing=timing(mode=FrameRateMode.VARIABLE, irregular_intervals=40))

        result = inspect(observed((stream,)))

        found = finding(result, "video.variable_frame_rate")
        assert found.certainty is Certainty.MEASURED
        assert found.concern is Concern.TRANSCODE
        assert result.status.verdict is Verdict.WARNING
        assert result.status.usable

    def test_a_declared_rate_that_the_timestamps_contradict_is_only_a_possible_problem(
        self,
    ) -> None:
        stream = video(timing=timing(packet_count=75))  # 75 frames in 3 s = 25 fps, declared 30

        found = finding(inspect(observed((stream,))), "video.frame_rate_mismatch")

        assert found.certainty is Certainty.POSSIBLE
        assert found.evidence["declared"] == "30/1"

    def test_a_rate_that_is_off_because_of_holes_is_explained_by_them(self) -> None:
        stream = video(timing=timing(packet_count=75, discontinuity_count=1, dropped_frames=0))

        assert "video.frame_rate_mismatch" not in codes(inspect(observed((stream,))))

    def test_dropped_frames_and_discontinuities_ask_for_a_sync_check(self) -> None:
        stream = video(
            timing=timing(dropped_frames=3, discontinuity_count=1, discontinuity_positions=(1.5,))
        )

        result = inspect(observed((stream,)))

        assert finding(result, "video.dropped_frames").certainty is Certainty.POSSIBLE
        assert finding(result, "video.timestamp_discontinuity").evidence["at_seconds"] == [1.5]
        assert Concern.SYNC_CHECK in result.status.concerns

    def test_duplicate_and_missing_timestamps_ask_for_repair(self) -> None:
        stream = video(timing=timing(duplicate_timestamps=2, missing_timestamps=1))

        result = inspect(observed((stream,)))

        assert {"video.duplicate_timestamps", "video.missing_timestamps"} <= codes(result)
        assert Concern.REPAIR in result.status.concerns

    @pytest.mark.parametrize(
        ("rate", "unusual"),
        [
            (Rational(30000, 1001), False),
            (Rational(24000, 1001), False),
            (Rational(25, 1), False),
            (Rational(60, 1), False),
            (Rational(15, 1), True),
            (Rational(2997, 100), False),  # 29.97, close enough to 30000/1001
            (Rational(2900, 100), True),
        ],
    )
    def test_only_unusual_frame_rates_are_noted(self, rate: Rational, unusual: bool) -> None:
        stream = video(frame_rate=Sourced.declared(rate), average_frame_rate=Sourced.declared(rate))

        assert ("video.unusual_frame_rate" in codes(inspect(observed((stream,))))) is unusual

    def test_interlaced_rotated_and_non_square_pixels_are_informational(self) -> None:
        geometry = Geometry(
            1440,
            1080,
            Sourced.declared(Rational(4, 3)),
            Sourced.declared(Rational(16, 9)),
            Sourced.declared(90),
        )
        stream = video(geometry=geometry, scan_type=Sourced.declared(ScanType.INTERLACED))

        result = inspect(observed((stream,)))

        assert {"video.interlaced", "video.rotated", "video.non_square_pixels"} <= codes(result)
        assert all(
            f.severity is Severity.INFO
            for f in result.findings
            if f.code in {"video.interlaced", "video.rotated", "video.non_square_pixels"}
        )
        assert result.status.verdict is Verdict.VALID

    def test_odd_dimensions_with_chroma_subsampling_ask_for_a_transcode(self) -> None:
        geometry = replace(video().geometry, width=1919)

        found = finding(inspect(observed((video(geometry=geometry),))), "video.odd_dimensions")

        assert found.concern is Concern.TRANSCODE

    def test_starved_encodings_are_flagged_as_possible_quality_problems(self) -> None:
        starved = video(bit_rate=Sourced.declared(100_000))

        found = finding(inspect(observed((starved,))), "video.low_bitrate")

        assert found.certainty is Certainty.POSSIBLE

    def test_the_bitrate_threshold_is_configurable(self) -> None:
        stream = video(bit_rate=Sourced.declared(100_000))

        result = inspect(observed((stream,)), InspectionConfig(low_bits_per_pixel=0.0001))

        assert "video.low_bitrate" not in codes(result)

    def test_several_video_streams_that_disagree_are_a_warning(self) -> None:
        other = video(
            index=2,
            geometry=Geometry(
                1280,
                720,
                Sourced.declared(Rational(1, 1)),
                Sourced.declared(Rational(16, 9)),
                Sourced.inferred(0),
            ),
        )

        found = finding(inspect(observed((video(), other))), "video.multiple_streams")

        assert found.severity is Severity.WARNING


class TestColorFindings:
    def test_undeclared_color_is_reported_not_assumed(self) -> None:
        unknown = ColorInfo(
            Sourced.unknown(),
            Sourced.unknown(),
            Sourced.unknown(),
            Sourced.declared("tv"),
            Sourced.unknown(),
        )

        result = inspect(observed((video(color=unknown),)))

        found = finding(result, "color.undeclared")
        assert found.evidence["missing"] == ["primaries", "transfer", "space"]
        assert found.concern is Concern.COLOR_CHECK
        assert found.severity is Severity.INFO

    def hdr(self, **changes: object) -> ColorInfo:
        base = ColorInfo(
            space=Sourced.declared("bt2020nc"),
            primaries=Sourced.declared("bt2020"),
            transfer=Sourced.declared("smpte2084"),
            range=Sourced.declared("tv"),
            dynamic_range=Sourced.inferred("hdr10"),
            mastering_display=True,
            content_light_level=True,
        )
        return replace(base, **changes)  # type: ignore[arg-type]

    def test_complete_hdr_has_nothing_to_report(self) -> None:
        stream = video(color=self.hdr(), bit_depth=Sourced.declared(10))

        assert codes(inspect(observed((stream,), (audio(),)))) == set()

    def test_hdr_without_metadata_needs_a_color_check(self) -> None:
        color = self.hdr(mastering_display=False, content_light_level=False)

        result = inspect(observed((video(color=color, bit_depth=Sourced.declared(10)),)))

        assert finding(result, "color.hdr_metadata_missing").concern is Concern.COLOR_CHECK

    def test_hdr_transfer_with_sdr_properties_is_a_possible_inconsistency(self) -> None:
        color = self.hdr(primaries=Sourced.declared("bt709"))

        result = inspect(observed((video(color=color, bit_depth=Sourced.declared(10)),)))

        assert finding(result, "color.inconsistent").certainty is Certainty.POSSIBLE

    def test_hdr_at_eight_bits_is_a_possible_inconsistency(self) -> None:
        result = inspect(observed((video(color=self.hdr(), bit_depth=Sourced.declared(8)),)))

        assert "color.inconsistent" in codes(result)


class TestAudioFindings:
    def test_a_video_without_audio_is_noted(self) -> None:
        assert "audio.missing" in codes(inspect(observed((video(),))))

    def test_an_audio_only_file_is_not_missing_audio(self) -> None:
        assert "audio.missing" not in codes(inspect(observed(audios=(audio(),))))

    def test_nonstandard_sample_rate_asks_for_an_audio_check(self) -> None:
        result = inspect(observed(audios=(audio(sample_rate=47_950),)))

        assert finding(result, "audio.unusual_sample_rate").concern is Concern.AUDIO_CHECK

    def test_different_sample_rates_across_streams(self) -> None:
        result = inspect(observed(audios=(audio(), audio(index=2, sample_rate=44_100))))

        found = finding(result, "audio.sample_rate_mismatch")
        assert found.evidence["sample_rates"] == [44_100, 48_000]

    def test_layout_that_contradicts_the_channel_count_is_confirmed_wrong(self) -> None:
        stream = audio(channels=6, channel_class=ChannelClass.MULTICHANNEL)  # layout says stereo

        found = finding(inspect(observed(audios=(stream,))), "audio.channel_layout_mismatch")

        assert found.certainty is Certainty.CONFIRMED

    def test_four_unlabelled_channels_are_unusual(self) -> None:
        stream = audio(
            channels=4, channel_class=ChannelClass.MULTICHANNEL, channel_layout=Sourced.unknown()
        )

        assert "audio.unusual_channel_layout" in codes(inspect(observed(audios=(stream,))))

    def test_five_one_is_not_unusual(self) -> None:
        stream = audio(
            channels=6,
            channel_class=ChannelClass.MULTICHANNEL,
            channel_layout=Sourced.declared("5.1(side)"),
        )

        assert codes(inspect(observed(audios=(stream,)))) == set()

    def test_an_audio_stream_without_a_format_is_an_error(self) -> None:
        stream = audio(channels=None, sample_rate=None, channel_class=ChannelClass.UNKNOWN)

        result = inspect(observed(audios=(stream,)))

        assert finding(result, "audio.unsupported").severity is Severity.ERROR
        assert not result.status.usable

    def test_holes_in_the_audio_timestamps_ask_for_a_sync_check(self) -> None:
        bad = audio().timing
        assert bad is not None
        stream = audio(timing=replace(bad, gap_count=2, total_gap_seconds=0.2))

        result = inspect(observed(audios=(stream,)))

        assert finding(result, "audio.timestamp_gaps").concern is Concern.SYNC_CHECK


class TestSynchronization:
    def streams(
        self, audio_start: float, audio_end: float = 3.0, video_end: float = 3.0
    ) -> ObservedMedia:
        a = audio(start_time=audio_start, duration=audio_end - audio_start)
        assert a.timing is not None
        a = replace(a, timing=replace(a.timing, end_pts=audio_end))
        v = video(duration=video_end, timing=timing(end_pts=video_end))
        return observed((v,), (a,))

    def test_audio_starting_with_the_video_is_in_sync(self) -> None:
        assert not {c for c in codes(inspect(self.streams(0.0))) if c.startswith("sync.")}

    def test_a_late_audio_start_is_a_possible_problem_with_the_measured_offset(self) -> None:
        result = inspect(self.streams(0.5, audio_end=3.5, video_end=3.5))

        found = finding(result, "sync.audio_offset")
        assert found.certainty is Certainty.POSSIBLE
        assert found.evidence["offset_seconds"] == pytest.approx(0.5)
        assert result.synchronization.offsets[0].start_offset == pytest.approx(0.5)
        assert Concern.SYNC_CHECK in result.status.concerns

    def test_offsets_within_the_tolerance_are_not_reported(self) -> None:
        assert "sync.audio_offset" not in codes(inspect(self.streams(0.03)))

    def test_the_tolerance_is_configurable(self) -> None:
        strict = InspectionConfig(sync_tolerance_seconds=0.01)

        assert "sync.audio_offset" in codes(inspect(self.streams(0.03), strict))

    def test_audio_that_ends_much_later_than_the_video_drifts(self) -> None:
        result = inspect(self.streams(0.0, audio_end=3.4, video_end=3.0))

        found = finding(result, "sync.drift")
        assert found.evidence["drift_seconds"] == pytest.approx(0.4)

    def test_one_packet_of_slack_at_the_end_is_not_drift(self) -> None:
        assert "sync.drift" not in codes(inspect(self.streams(0.0, audio_end=3.03)))

    def test_clearly_different_lengths_are_reported(self) -> None:
        result = inspect(self.streams(0.0, audio_end=3.0, video_end=4.0))

        assert "sync.duration_mismatch" in codes(result)


class TestTimecodeAndIntegrity:
    def test_a_timecode_that_cannot_exist_at_the_frame_rate_is_confirmed_wrong(self) -> None:
        bad = TimecodeInfo(Timecode(1, 0, 0, 30), "video_stream")  # frames run 0-29 at 30 fps

        found = finding(
            inspect(observed((video(),), timecode=bad)), "timecode.invalid_for_frame_rate"
        )

        assert found.certainty is Certainty.CONFIRMED

    def test_a_valid_timecode_is_silent(self) -> None:
        good = TimecodeInfo(Timecode(1, 0, 0, 10), "video_stream")

        assert "timecode.invalid_for_frame_rate" not in codes(
            inspect(observed((video(),), timecode=good))
        )

    def test_decode_errors_make_the_file_unusable_and_ask_for_repair(self) -> None:
        integrity = Integrity(
            True, True, (DecodeMessage("error while decoding MB", 5),), decode_message_count=5
        )

        result = inspect(observed((video(),), integrity=integrity))

        found = finding(result, "integrity.decode_errors")
        assert found.certainty is Certainty.CONFIRMED
        assert found.evidence["messages"] == 5
        assert not result.status.usable
        assert result.status.concerns == (Concern.REPAIR,)

    def test_an_unchecked_decode_is_said_so(self) -> None:
        integrity = Integrity(True, False)

        assert "integrity.not_decoded" in codes(inspect(observed((video(),), integrity=integrity)))

    def test_a_frame_count_that_contradicts_the_header_is_confirmed(self) -> None:
        stream = video(declared_frame_count=120)  # the timing holds 90 packets

        assert (
            finding(inspect(observed((stream,))), "integrity.frame_count_mismatch").certainty
            is Certainty.CONFIRMED
        )

    def test_a_declared_duration_longer_than_the_data_is_a_possible_truncation(self) -> None:
        media = observed((video(),), (audio(),))
        media = replace(media, container=replace(media.container, duration=10.0))

        found = finding(inspect(media), "integrity.duration_mismatch")

        assert found.certainty is Certainty.POSSIBLE

    def test_missing_production_metadata_is_informational(self) -> None:
        media = observed((video(),), production=ProductionMetadata())

        found = finding(inspect(media), "metadata.production_missing")

        assert found.severity is Severity.INFO
        assert inspect(media).status.verdict is Verdict.VALID


class TestStatus:
    def test_the_worst_finding_decides_and_concerns_are_collected_once(self) -> None:
        stream = video(
            timing=timing(mode=FrameRateMode.VARIABLE, dropped_frames=2, duplicate_timestamps=1)
        )

        status = inspect(observed((stream,))).status

        assert status.verdict is Verdict.WARNING
        assert status.usable
        assert status.warning_count == 3
        assert status.concerns == tuple(sorted(status.concerns, key=lambda c: c.value))
        assert len(set(status.concerns)) == len(status.concerns)
