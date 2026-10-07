"""ffprobe's report -> normalised facts. Handwritten reports cover what sample files cannot."""

from typing import Any

import pytest

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import Integrity, ObservedMedia
from media_house.modules.media_inspection.domain.timing import Packet
from media_house.modules.media_inspection.domain.values import (
    ChannelClass,
    Provenance,
    Rational,
    ScanType,
)
from media_house.modules.media_inspection.infrastructure.ffprobe_normalizer import (
    normalize,
    unreadable,
)

INTEGRITY = Integrity(readable=True, decode_checked=True)


def probe(
    video: dict[str, Any] | None = None,
    audio: dict[str, Any] | None = None,
    container: dict[str, Any] | None = None,
    extra: list[dict[str, Any]] | None = None,
    packets: dict[int, list[Packet]] | None = None,
    frame_side_data: dict[int, list[dict[str, Any]]] | None = None,
) -> ObservedMedia:
    streams = []
    if video is not None:
        streams.append({"index": 0, "codec_type": "video", **video})
    if audio is not None:
        streams.append({"index": 1, "codec_type": "audio", **audio})
    streams += extra or []
    info = {
        "format": {"format_name": "mov,mp4", "duration": "3.0", **(container or {})},
        "streams": streams,
    }
    return normalize(info, packets or {}, INTEGRITY, InspectionConfig(), frame_side_data)


class TestVideo:
    def test_exact_rates_and_time_base_are_kept_as_fractions(self) -> None:
        media = probe(
            {
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
                "avg_frame_rate": "30000/1001",
                "time_base": "1/30000",
            }
        )

        video = media.video_streams[0]
        assert video.frame_rate.value == Rational(30000, 1001)
        assert video.frame_rate.provenance is Provenance.DECLARED
        assert video.average_frame_rate.value == Rational(30000, 1001)
        assert video.time_base == Rational(1, 30000)

    def test_unstated_values_are_unknown_not_guessed(self) -> None:
        media = probe({"codec_name": "h264", "width": 640, "height": 360, "r_frame_rate": "0/0"})

        video = media.video_streams[0]
        assert not video.frame_rate.known
        assert not video.color.primaries.known
        assert not video.color.transfer.known
        assert video.color.dynamic_range.provenance is Provenance.UNKNOWN
        assert video.scan_type.value is None
        assert video.pixel_format is None
        assert not video.bit_depth.known

    @pytest.mark.parametrize(
        ("pix_fmt", "depth", "subsampling", "alpha"),
        [
            ("yuv420p", 8, "420", False),
            ("yuv422p10le", 10, "422", False),
            ("yuv444p12le", 12, "444", False),
            ("yuva420p", 8, "420", True),
            ("nv12", 8, "420", False),
            ("p010le", 10, "420", False),
            ("rgba", 8, "444", True),
            ("gbrp10le", 10, "444", False),
        ],
    )
    def test_pixel_format_facts_are_inferred_and_labelled_so(
        self, pix_fmt: str, depth: int, subsampling: str, alpha: bool
    ) -> None:
        media = probe({"codec_name": "x", "width": 4, "height": 4, "pix_fmt": pix_fmt})

        video = media.video_streams[0]
        assert video.bit_depth.value == depth
        assert video.bit_depth.provenance is Provenance.INFERRED
        assert video.chroma_subsampling.value == subsampling
        assert video.has_alpha is alpha

    def test_a_declared_bit_depth_wins_over_the_inferred_one(self) -> None:
        media = probe(
            {
                "codec_name": "x",
                "width": 4,
                "height": 4,
                "pix_fmt": "yuv420p",
                "bits_per_raw_sample": "10",
            }
        )

        assert media.video_streams[0].bit_depth.provenance is Provenance.DECLARED
        assert media.video_streams[0].bit_depth.value == 10

    @pytest.mark.parametrize(
        ("field_order", "scan"),
        [
            ("progressive", ScanType.PROGRESSIVE),
            ("tt", ScanType.INTERLACED),
            ("bb", ScanType.INTERLACED),
            ("unknown", None),
        ],
    )
    def test_scan_type_comes_from_the_field_order_only(
        self, field_order: str, scan: ScanType | None
    ) -> None:
        media = probe({"codec_name": "x", "width": 4, "height": 4, "field_order": field_order})

        assert media.video_streams[0].scan_type.value is scan

    def test_storage_pixel_shape_display_shape_and_rotation_stay_separate(self) -> None:
        media = probe(
            {
                "codec_name": "x",
                "width": 1440,
                "height": 1080,
                "sample_aspect_ratio": "4:3",
                "display_aspect_ratio": "16:9",
                "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
            }
        )

        geometry = media.video_streams[0].geometry
        assert (geometry.width, geometry.height) == (1440, 1080)
        assert geometry.sample_aspect_ratio.value == Rational(4, 3)
        assert geometry.display_aspect_ratio.value == Rational(16, 9)
        assert geometry.rotation.value == 90  # clockwise: ffprobe reports the matrix the other way
        assert not geometry.square_pixels
        assert geometry.display_size == (1080, 1920)

    def test_missing_aspect_ratios_are_inferred_and_labelled_so(self) -> None:
        media = probe(
            {"codec_name": "x", "width": 1280, "height": 720, "sample_aspect_ratio": "N/A"}
        )

        geometry = media.video_streams[0].geometry
        assert geometry.sample_aspect_ratio.value == Rational(1, 1)
        assert geometry.sample_aspect_ratio.provenance is Provenance.INFERRED
        assert geometry.display_aspect_ratio.value == Rational(16, 9)
        assert geometry.display_aspect_ratio.provenance is Provenance.INFERRED

    def test_legacy_rotate_tag_is_understood(self) -> None:
        media = probe({"codec_name": "x", "width": 4, "height": 4, "tags": {"rotate": "270"}})

        assert media.video_streams[0].geometry.rotation.value == 270

    def test_hdr_is_inferred_from_the_declared_transfer_and_metadata_comes_from_the_first_frame(
        self,
    ) -> None:
        media = probe(
            {
                "codec_name": "hevc",
                "width": 4,
                "height": 4,
                "pix_fmt": "yuv420p10le",
                "color_space": "bt2020nc",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_range": "tv",
            },
            frame_side_data={
                0: [
                    {"side_data_type": "Mastering display metadata"},
                    {"side_data_type": "Content light level metadata"},
                ]
            },
        )

        color = media.video_streams[0].color
        assert color.transfer.provenance is Provenance.DECLARED
        assert (color.dynamic_range.value, color.dynamic_range.provenance) == (
            "hdr10",
            Provenance.INFERRED,
        )
        assert color.mastering_display and color.content_light_level
        assert "first_frame_side_data" in media.raw_metadata

    def test_standard_dynamic_range_and_hlg(self) -> None:
        sdr = probe({"codec_name": "x", "width": 4, "height": 4, "color_transfer": "bt709"})
        hlg = probe({"codec_name": "x", "width": 4, "height": 4, "color_transfer": "arib-std-b67"})

        assert sdr.video_streams[0].color.dynamic_range.value == "sdr"
        assert hlg.video_streams[0].color.dynamic_range.value == "hlg"

    def test_levels_are_written_the_way_people_read_them(self) -> None:
        h264 = probe({"codec_name": "h264", "width": 4, "height": 4, "level": 41})
        hevc = probe({"codec_name": "hevc", "width": 4, "height": 4, "level": 120})

        assert h264.video_streams[0].level == "4.1"
        assert hevc.video_streams[0].level == "4.0"

    def test_cover_art_is_not_a_video_stream(self) -> None:
        media = probe(
            audio={"codec_name": "mp3", "channels": 2, "sample_rate": "44100"},
            extra=[
                {
                    "index": 2,
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "width": 500,
                    "height": 500,
                    "disposition": {"attached_pic": 1},
                }
            ],
        )

        assert media.video_streams == ()
        assert media.other_streams[0].kind == "attached_picture"

    def test_matroska_keeps_duration_and_frame_count_in_tags(self) -> None:
        media = probe(
            {
                "codec_name": "h264",
                "width": 4,
                "height": 4,
                "tags": {"DURATION": "00:00:12.500000000", "NUMBER_OF_FRAMES": "300"},
            }
        )

        assert media.video_streams[0].duration == pytest.approx(12.5)
        assert media.video_streams[0].declared_frame_count == 300

    def test_bit_rate_is_declared_or_else_measured_from_packets(self) -> None:
        packets = {0: [Packet(i / 30, i / 30, 1 / 30, 1000, keyframe=i == 0) for i in range(60)]}

        declared = probe(
            {"codec_name": "x", "width": 4, "height": 4, "bit_rate": "5000"}, packets=packets
        )
        measured = probe({"codec_name": "x", "width": 4, "height": 4}, packets=packets)

        assert declared.video_streams[0].bit_rate.provenance is Provenance.DECLARED
        assert measured.video_streams[0].bit_rate.provenance is Provenance.DETECTED
        assert measured.video_streams[0].bit_rate.value == pytest.approx(240_000, rel=0.02)

    def test_timing_is_measured_when_packets_are_available(self) -> None:
        packets = {0: [Packet(i / 30, i / 30, 1 / 30, 100, keyframe=False) for i in range(30)]}

        media = probe(
            {"codec_name": "x", "width": 4, "height": 4, "time_base": "1/90000"}, packets=packets
        )

        timing = media.video_streams[0].timing
        assert timing is not None
        assert timing.packet_count == 30
        assert media.video_streams[0].has_b_frames.provenance is Provenance.DETECTED


class TestAudio:
    def test_the_stream_map_numbers_and_languages(self) -> None:
        media = probe(
            audio={
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "channel_layout": "stereo",
                "start_time": "0.021333",
                "tags": {"language": "deu", "title": "Dialog"},
                "disposition": {"default": 1},
            }
        )

        audio = media.audio_streams[0]
        assert (audio.index, audio.sample_rate, audio.channels) == (1, 48_000, 2)
        assert audio.channel_layout.value == "stereo"
        assert audio.channel_class is ChannelClass.STEREO
        assert (audio.language, audio.title, audio.is_default) == ("deu", "Dialog", True)
        assert audio.start_time == pytest.approx(0.021333)

    def test_an_undetermined_language_is_not_a_language(self) -> None:
        media = probe(audio={"codec_name": "aac", "channels": 1, "tags": {"language": "und"}})

        assert media.audio_streams[0].language is None

    @pytest.mark.parametrize(
        ("codec", "depth", "provenance"),
        [
            ("pcm_s24le", 24, Provenance.INFERRED),
            ("pcm_s16le", 16, Provenance.INFERRED),
            ("pcm_f32le", 32, Provenance.INFERRED),
            ("aac", None, Provenance.UNKNOWN),
        ],
    )
    def test_bit_depth_only_for_formats_that_have_one(
        self, codec: str, depth: int | None, provenance: Provenance
    ) -> None:
        media = probe(audio={"codec_name": codec, "channels": 2, "sample_rate": "48000"})

        assert media.audio_streams[0].bit_depth.value == depth
        assert media.audio_streams[0].bit_depth.provenance is provenance

    def test_a_declared_bit_depth_is_used(self) -> None:
        media = probe(audio={"codec_name": "flac", "channels": 2, "bits_per_raw_sample": "24"})

        assert media.audio_streams[0].bit_depth.provenance is Provenance.DECLARED

    def test_a_layout_that_only_repeats_the_channel_count_is_not_a_layout(self) -> None:
        media = probe(
            audio={"codec_name": "pcm_s16le", "channels": 4, "channel_layout": "4 channels"}
        )

        audio = media.audio_streams[0]
        assert not audio.channel_layout.known
        assert audio.channel_class is ChannelClass.MULTICHANNEL


class TestContainerAndTimecode:
    def test_container_facts(self) -> None:
        media = probe(
            {"codec_name": "x", "width": 4, "height": 4},
            container={
                "format_name": "mov,mp4,m4a",
                "duration": "10.5",
                "start_time": "0.000000",
                "bit_rate": "1000000",
                "size": "1312500",
                "probe_score": 100,
            },
        )

        container = media.container
        assert container.format_names == ("m4a", "mov", "mp4")
        assert (container.duration, container.bit_rate, container.size_bytes) == (
            10.5,
            1_000_000,
            1_312_500,
        )
        assert container.probe_score == 100

    def test_timecode_from_the_video_stream_with_its_clock_position(self) -> None:
        media = probe(
            {
                "codec_name": "x",
                "width": 4,
                "height": 4,
                "r_frame_rate": "25/1",
                "tags": {"timecode": "10:00:00:00"},
            }
        )

        timecode = media.timecode
        assert timecode is not None
        assert str(timecode.start) == "10:00:00:00"
        assert timecode.source == "video_stream"
        assert timecode.start_seconds == pytest.approx(36_000.0)

    def test_timecode_track_is_read_when_the_stream_has_none(self) -> None:
        media = probe(
            {"codec_name": "x", "width": 4, "height": 4, "r_frame_rate": "25/1"},
            extra=[
                {
                    "index": 2,
                    "codec_type": "data",
                    "codec_tag_string": "tmcd",
                    "tags": {"timecode": "01:00:00;00"},
                }
            ],
        )

        assert media.timecode is not None
        assert media.timecode.source == "timecode_track"
        assert media.timecode.start.drop_frame

    def test_no_timecode_is_none(self) -> None:
        assert probe({"codec_name": "x", "width": 4, "height": 4}).timecode is None

    def test_production_fields_are_found_under_vendor_names_and_kept_as_tags(self) -> None:
        media = probe(
            {"codec_name": "x", "width": 4, "height": 4},
            container={
                "tags": {
                    "com.apple.quicktime.make": "Apple",
                    "com.apple.quicktime.model": "iPhone 15",
                    "creation_time": "2026-03-04T05:06:07.000000Z",
                    "Custom": "kept",
                }
            },
        )

        production = media.production
        assert (production.camera_make, production.camera_model) == ("Apple", "iPhone 15")
        assert production.creation_time is not None and production.creation_time.year == 2026
        assert production.lens is None
        assert production.tags["Custom"] == "kept"

    def test_the_raw_report_is_kept_untouched(self) -> None:
        media = probe({"codec_name": "x", "width": 4, "height": 4, "vendor_field": "keep me"})

        raw_streams: Any = media.raw_metadata["streams"]
        assert raw_streams[0]["vendor_field"] == "keep me"


class TestUnreadable:
    def test_nothing_is_known_except_why(self) -> None:
        media = unreadable("Invalid data found")

        assert not media.integrity.readable
        assert media.integrity.read_error == "Invalid data found"
        assert media.video_streams == ()
        assert media.audio_streams == ()
