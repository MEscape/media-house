"""Reuse of processing that another module has already performed on an asset.

Audio Intelligence works on any audio, so it never ASSUMES prior processing. It only skips work
when the asset's processing provenance (published by Audio Improvement through its contract)
proves, by MEASUREMENT, that the result is already what this analysis would produce.
"""

from dataclasses import replace

from media_house.modules.audio_improvement.application.contracts import read_provenance
from media_house.modules.audio_intelligence.domain.values import (
    LOUDNESS_TARGET_LUFS,
    LOUDNESS_TRUE_PEAK_DBTP,
    PreparationConfig,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: The measured loudness of the source may differ this much (LU) from the preparation target.
LOUDNESS_TOLERANCE_LU = 1.0

LOUDNESS_NORMALIZATION = "loudness_normalization"


def effective_preparation(
    source: MediaAssetDto,
    requested: PreparationConfig,
) -> tuple[PreparationConfig, tuple[str, ...]]:
    """The preparation to really run and the names of the operations skipped as already done."""
    if not requested.loudness_normalization:
        return requested, ()
    provenance = read_provenance(source.metadata)
    if provenance is not None and provenance.meets_loudness(
        LOUDNESS_TARGET_LUFS, LOUDNESS_TOLERANCE_LU, LOUDNESS_TRUE_PEAK_DBTP
    ):
        _log.info(
            "Loudness normalisation skipped: the source is already measured on target",
            asset_id=source.id,
            lufs=provenance.output_integrated_lufs,
        )
        return replace(requested, loudness_normalization=False), (LOUDNESS_NORMALIZATION,)
    return requested, ()
