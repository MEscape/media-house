"""Stream facts are taken from an inspection only when it really supplies them."""

from dataclasses import replace
from typing import cast

import pytest

from media_house.modules.audio_improvement.application.ports import SourceAudioFacts
from media_house.modules.audio_improvement.application.prior_inspection import known_audio_facts
from media_house.modules.media_inspection.application.contracts import (
    InspectionConfig,
    InspectionResult,
)
from media_house.modules.media_inspection.domain.model import Integrity, MediaInspection
from media_house.modules.media_inspection.domain.values import ChannelClass
from media_house.modules.media_library.application.contracts import MediaAssetDto
from tests.support.inspection_fakes import audio, inspect, observed, video


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


def known(inspection: MediaInspection | None) -> SourceAudioFacts | None:
    return known_audio_facts(Catalog(inspection), "asset-1")


def test_without_a_catalog_or_an_inspection_nothing_is_known() -> None:
    assert known_audio_facts(None, "asset-1") is None
    assert known(None) is None


def test_facts_of_the_first_audio_stream_mean_what_the_probe_means() -> None:
    stream = audio(start_time=0.5, sample_rate=44_100, channels=1, channel_class=ChannelClass.MONO)
    inspection = inspect(observed((video(),), (stream, audio(index=2, sample_rate=22_050))))

    facts = known(inspection)

    assert facts is not None
    assert (facts.sample_rate, facts.channels, facts.duration) == (44_100, 1, 3.0)
    assert facts.start_offset == pytest.approx(0.5)


def test_the_offset_is_counted_from_the_container_start_and_never_negative() -> None:
    media = observed((video(),), (audio(start_time=10.2),))
    media = replace(media, container=replace(media.container, start_time=10.0))
    early = observed((video(),), (audio(start_time=-0.02),))

    assert known(inspect(media)).start_offset == pytest.approx(0.2)  # type: ignore[union-attr]
    assert known(inspect(early)).start_offset == 0.0  # type: ignore[union-attr]


def test_no_audio_stream_or_no_format_means_probe_instead() -> None:
    assert known(inspect(observed((video(),)))) is None
    incomplete = audio(sample_rate=None, channels=None, channel_class=ChannelClass.UNKNOWN)
    assert known(inspect(observed(audios=(incomplete,)))) is None


def test_an_unreadable_file_or_unknown_duration_means_probe_instead() -> None:
    unreadable = observed(integrity=Integrity(False, False, read_error="bad"))
    no_length = observed((video(),), (audio(duration=None),))
    no_length = replace(no_length, container=replace(no_length.container, duration=None))

    assert known(inspect(unreadable)) is None
    assert known(inspect(no_length)) is None


def test_the_catalog_is_asked_about_the_right_asset() -> None:
    catalog = Catalog(None)

    known_audio_facts(catalog, "the-source")

    assert catalog.asked == ["the-source"]
