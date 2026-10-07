"""Source timing is taken from an inspection only when it really supplies it."""

from dataclasses import replace
from typing import cast

import pytest

from media_house.modules.audio_intelligence.application.ports import KnownSourceTiming
from media_house.modules.audio_intelligence.application.prior_inspection import known_source_timing
from media_house.modules.media_inspection.application.contracts import (
    InspectionConfig,
    InspectionResult,
)
from media_house.modules.media_inspection.domain.model import Integrity, MediaInspection
from media_house.modules.media_library.application.contracts import MediaAssetDto
from tests.support.inspection_fakes import audio, inspect, observed, video


class Catalog:
    def __init__(self, inspection: MediaInspection | None) -> None:
        self._inspection = inspection

    def find(
        self, asset_id: str, config: InspectionConfig | None = None
    ) -> InspectionResult | None:
        if self._inspection is None:
            return None
        return InspectionResult(self._inspection, cast("MediaAssetDto", None), created=False)


def known(inspection: MediaInspection | None, stream: int = 0) -> KnownSourceTiming | None:
    return known_source_timing(Catalog(inspection), "asset-1", stream)


def test_without_a_catalog_or_an_inspection_nothing_is_known() -> None:
    assert known_source_timing(None, "asset-1", 0) is None
    assert known(None) is None


def test_duration_is_the_containers_and_the_offset_is_the_streams_start() -> None:
    inspection = inspect(observed((video(),), (audio(start_time=0.5),)))

    timing = known(inspection)
    assert timing is not None
    assert timing.duration == 3.0
    assert timing.audio_offset == pytest.approx(0.5)


def test_the_requested_audio_stream_is_the_one_measured() -> None:
    inspection = inspect(
        observed((video(),), (audio(start_time=0.0), audio(index=2, start_time=1.0)))
    )

    assert known(inspection, 0).audio_offset == 0.0  # type: ignore[union-attr]
    assert known(inspection, 1).audio_offset == pytest.approx(1.0)  # type: ignore[union-attr]


def test_a_stream_that_is_not_there_means_the_module_must_report_it_itself() -> None:
    assert known(inspect(observed((video(),), (audio(),))), 3) is None
    assert known(inspect(observed((video(),)))) is None


def test_unreadable_files_and_unknown_length_mean_probe_instead() -> None:
    unreadable = observed(integrity=Integrity(False, False, read_error="bad"))
    no_length = observed((video(),), (audio(duration=None),))
    no_length = replace(no_length, container=replace(no_length.container, duration=None))

    assert known(inspect(unreadable)) is None
    assert known(inspect(no_length)) is None
