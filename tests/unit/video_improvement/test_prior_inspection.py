"""Facts are taken from an inspection only when it really supplies them."""

from dataclasses import replace
from typing import cast

import pytest

from media_house.modules.media_inspection.application.contracts import (
    InspectionConfig,
    InspectionResult,
)
from media_house.modules.media_inspection.domain.model import (
    ColorInfo,
    Integrity,
    MediaInspection,
    ProductionMetadata,
    TimecodeInfo,
)
from media_house.modules.media_inspection.domain.timecode import Timecode
from media_house.modules.media_inspection.domain.values import (
    FrameRateMode,
    Rational,
    ScanType,
    Sourced,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.video_improvement.application.prior_inspection import known_video_facts
from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.domain.source import VideoFacts, resolve_source_profile
from media_house.modules.video_improvement.domain.values import FactsSource, FrameRate
from tests.support.inspection_fakes import audio, inspect, observed, timing, video


class Catalog:
    def __init__(self, inspection: MediaInspection | None) -> None:
        self._inspection = inspection
        self.asked: list[str] = []

    def find(
        self, asset_id: str, config: InspectionConfig | None = None
    ) -> InspectionResult | None:
        self.asked.append(asset_id)
        if self._inspection is None:
            return None
        return InspectionResult(self._inspection, cast("MediaAssetDto", None), created=False)


def known(inspection: MediaInspection | None) -> VideoFacts | None:
    return known_video_facts(Catalog(inspection), "asset-1")


def test_without_a_catalog_or_an_inspection_nothing_is_known() -> None:
    assert known_video_facts(None, "asset-1") is None
    assert known(None) is None


def test_the_catalog_is_asked_about_the_right_asset() -> None:
    catalog = Catalog(None)

    known_video_facts(catalog, "the-source")

    assert catalog.asked == ["the-source"]


def test_a_stream_becomes_the_facts_the_processing_needs() -> None:
    stream = video(frame_rate=Sourced.declared(Rational(30000, 1001)), duration=3.0)
    inspection = inspect(observed((stream,), (audio(), audio(index=2))))

    facts = known(inspection)

    assert facts is not None
    assert facts.source is FactsSource.INSPECTION
    assert (facts.width, facts.height) == (1920, 1080)
    assert facts.frame_rate == FrameRate(30000, 1001)  # exact, not rounded
    assert facts.duration == 3.0
    assert facts.frame_count == 90  # the packets that were counted
    assert facts.pixel_format == "yuv420p"
    assert facts.bit_depth == 8
    assert facts.audio_stream_count == 2
    assert facts.color == ColorSpec(Transfer.BT709, Primaries.BT709)
    assert (facts.color_range, facts.color_matrix) == ("tv", "bt709")
    assert not facts.interlaced
    assert not facts.variable_frame_rate
    assert facts.rotation == 0


def test_hidden_pre_roll_frames_are_not_counted_as_frames() -> None:
    # a clip cut without re-encoding: 29 packets before time zero are never shown
    stream = video(timing=timing(packet_count=119, first_pts=-0.967, median_interval=1 / 30))

    facts = known(inspect(observed((stream,))))

    assert facts is not None
    assert facts.frame_count == 90


def test_the_length_is_the_video_streams_not_a_longer_data_track() -> None:
    media = observed((video(duration=20.0),))
    media = replace(media, container=replace(media.container, duration=20.95))

    facts = known(inspect(media))

    assert facts is not None
    assert facts.duration == 20.0


def test_undeclared_colour_stays_unknown() -> None:
    unknown = ColorInfo(
        Sourced.unknown(),
        Sourced.unknown(),
        Sourced.unknown(),
        Sourced.unknown(),
        Sourced.unknown(),
    )

    facts = known(inspect(observed((video(color=unknown),))))

    assert facts is not None
    assert not facts.color.known
    assert facts.color_range is None


def test_interlacing_rotation_and_variable_rate_are_carried_over() -> None:
    geometry = replace(video().geometry, rotation=Sourced.declared(90))
    stream = video(
        geometry=geometry,
        scan_type=Sourced.declared(ScanType.INTERLACED),
        timing=timing(mode=FrameRateMode.VARIABLE),
    )

    facts = known(inspect(observed((stream,))))

    assert facts is not None
    assert facts.interlaced
    assert facts.rotation == 90
    assert facts.variable_frame_rate


def test_camera_metadata_and_timecode_reach_the_profile_detection() -> None:
    media = observed(
        (video(tags={"Handler_Name": "GoPro AVC"}),),
        production=ProductionMetadata(camera_make="GoPro", camera_model="HERO9 Black"),
        timecode=TimecodeInfo(Timecode(1, 2, 3, 4, drop_frame=True), "video_stream"),
    )
    media = replace(media, container=replace(media.container, tags={"firmware": "HD9.01.01.60.00"}))

    facts = known(inspect(media))

    assert facts is not None
    assert facts.timecode == "01:02:03;04"
    assert (facts.camera_make, facts.camera_model) == ("GoPro", "HERO9 Black")
    assert facts.tags["firmware"] == "HD9.01.01.60.00"
    assert facts.tags["handler_name"] == "GoPro AVC"  # keys are lower-cased
    resolved = resolve_source_profile(None, facts)
    assert resolved.profile.name == "gopro_hero_9"
    assert resolved.origin.value == "inspection"


@pytest.mark.parametrize("case", ["unreadable", "no_video", "no_duration", "no_frame_rate"])
def test_an_inspection_that_cannot_supply_the_facts_means_probe_instead(case: str) -> None:
    if case == "unreadable":
        inspection = inspect(observed(integrity=Integrity(False, False, read_error="bad")))
    elif case == "no_video":
        inspection = inspect(observed(audios=(audio(),)))
    elif case == "no_duration":
        media = observed((video(duration=None),))
        inspection = inspect(replace(media, container=replace(media.container, duration=None)))
    else:
        unknown_rate = Sourced[Rational].unknown()
        inspection = inspect(
            observed((video(frame_rate=unknown_rate, average_frame_rate=unknown_rate),))
        )

    assert known(inspection) is None
