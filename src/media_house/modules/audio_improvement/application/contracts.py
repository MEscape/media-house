"""The Audio Improvement PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``audio_improvement``.

``execute`` is blocking (FFmpeg, signal analysis, file I/O): run it as a job. The original
asset is never modified: the result is a NEW asset derived from it, carrying its processing
provenance (``result.provenance``) in the asset metadata, readable by anyone through
``read_provenance(asset.metadata)`` without calling this module.

Typical use::

    result = improver.execute(ImproveAudioCommand(voiceover_asset_id, profile="youtube"), ctx)
    if isinstance(result, Ok):
        improved = result.value.asset            # derived asset, same timeline as the source
        result.value.provenance.noise_reduced    # facts, not assumptions
"""

from collections.abc import Mapping
from typing import Protocol

from media_house.modules.audio_improvement.application.improve_audio import (
    ImproveAudioCommand,
    ImproveError,
    ImprovementResult,
)
from media_house.modules.audio_improvement.application.mix_audio import (
    MixAudioCommand,
    MixResult,
)
from media_house.modules.audio_improvement.domain.errors import (
    AudioImprovementError,
    InvalidProfile,
    UnreadableSource,
)
from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.profiles import (
    DEFAULT_PROFILE,
    profile_names,
    resolve_profile,
)
from media_house.modules.audio_improvement.domain.provenance import (
    ProcessingProvenance,
    StageRecord,
)
from media_house.modules.audio_improvement.domain.settings import AudioProfile
from media_house.modules.audio_improvement.domain.values import (
    METADATA_KEY,
    JsonValue,
    ProcessingStage,
    StageStatus,
)
from media_house.modules.audio_improvement.domain.verification import Check
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class AudioImprover(Protocol):
    """Improve one audio/video asset into a new, cleaner and consistently mastered asset."""

    def execute(
        self,
        command: ImproveAudioCommand,
        ctx: JobContext,
    ) -> Result[ImprovementResult, ImproveError]: ...


class AudioMixing(Protocol):
    """Mix a voice asset with a music asset into one mastered asset."""

    def execute(
        self,
        command: MixAudioCommand,
        ctx: JobContext,
    ) -> Result[MixResult, ImproveError]: ...


def read_provenance(metadata: Mapping[str, JsonValue]) -> ProcessingProvenance | None:
    """What was done to an asset, from its metadata; ``None`` if it carries no valid record.

    The way for any module to learn about prior processing. ``None`` means "unknown": never
    assume processing that this function does not confirm.
    """
    return ProcessingProvenance.from_metadata(metadata)


__all__ = [
    "DEFAULT_PROFILE",
    "METADATA_KEY",
    "AudioImprovementError",
    "AudioImprover",
    "AudioMixing",
    "AudioProfile",
    "Check",
    "ImproveAudioCommand",
    "ImproveError",
    "ImprovementResult",
    "InvalidProfile",
    "MixAudioCommand",
    "MixResult",
    "ProcessingProvenance",
    "ProcessingStage",
    "QualityMeasurements",
    "StageRecord",
    "StageStatus",
    "UnreadableSource",
    "profile_names",
    "read_provenance",
    "resolve_profile",
]
