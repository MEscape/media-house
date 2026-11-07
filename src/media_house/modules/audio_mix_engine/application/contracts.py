"""The Audio Mix Engine PUBLIC API for other modules (import this file only).

``audio_mix_engine`` turns the sound of a built timeline into one coherent, loudness-checked
soundtrack: a versioned ``AudioMixSpecification`` (what is mixed and how), the stems of every bus,
the final mix, and a ``MixResult`` (what was rendered, measured and found). It decides nothing
creative (which sounds exist and where they sit come from the sound design and the timeline),
repairs no recording (``audio_improvement``), analyses nothing (``audio_intelligence``), and
knows no editing application: an editing-application adapter can import its outputs.

    made = creator.execute(CreateSpecCommand(timeline=built))           # the specification
    done = mixer.execute(RenderMixCommand(timeline=built), ctx)         # stems + mix + checks
    done.value.mix_asset, done.value.stem_assets, done.value.result.issues

Blocking: run as a job.
"""

from typing import Protocol

from media_house.modules.audio_mix_engine.application.attach_mix import (
    ATTACH_OPERATION,
    AttachMixCommand,
    AttachMixResult,
)
from media_house.modules.audio_mix_engine.application.create_spec import (
    CreateSpecCommand,
    MixConfig,
    MixError,
    SpecResult,
)
from media_house.modules.audio_mix_engine.application.render_mix import (
    MixRenderResult,
    RenderMixCommand,
)
from media_house.modules.audio_mix_engine.domain.build import MixOverrides
from media_house.modules.audio_mix_engine.domain.errors import (
    AudioMixError,
    BackendFailed,
    InvalidMixConfig,
    InvalidMixDocument,
    NothingToMix,
    SpecificationInvalid,
    TimelineNotMixable,
)
from media_house.modules.audio_mix_engine.domain.profile import (
    DELIVERIES,
    PROFILES,
    DeliveryProfile,
    MixProfile,
)
from media_house.modules.audio_mix_engine.domain.result import (
    Measurement,
    MixOutput,
    MixResult,
    OutputIssue,
    StemOutput,
)
from media_house.modules.audio_mix_engine.domain.spec import (
    AudioMixSpecification,
    MixBus,
    MixClip,
    MixTrack,
)
from media_house.modules.audio_mix_engine.domain.values import (
    ENGINE_VERSION,
    MIX_OPERATION,
    RESULT_OPERATION,
    SPEC_OPERATION,
    STEM_OPERATION,
    BusRole,
    Certainty,
    JobState,
    Severity,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class AudioMixCreator(Protocol):
    def execute(self, command: CreateSpecCommand) -> Result[SpecResult, MixError]:
        """Build (or find) the mix specification of a built timeline. Renders nothing."""
        ...


class AudioMixer(Protocol):
    def execute(
        self, command: RenderMixCommand, ctx: JobContext
    ) -> Result[MixRenderResult, MixError]:
        """Render the stems and the final mix, measure, correct the level, validate, store."""
        ...


class MixAttacher(Protocol):
    def execute(
        self, command: AttachMixCommand, ctx: JobContext
    ) -> Result[AttachMixResult, Exception]:
        """Put the final mix under a rendered picture (video copied) and measure the file."""
        ...


class MixCatalog(Protocol):
    """Stored specifications and results; nothing is rendered."""

    def latest_spec(
        self, anchor_asset_id: str, timeline_id: str | None
    ) -> Result[tuple[AudioMixSpecification, MediaAssetDto] | None, MediaError]: ...

    def latest_result(
        self, anchor_asset_id: str, timeline_id: str | None
    ) -> Result[tuple[MixResult, MediaAssetDto] | None, MediaError]: ...

    def find_spec(
        self, anchor_asset_id: str, content_hash: str
    ) -> Result[tuple[AudioMixSpecification, MediaAssetDto] | None, MediaError]:
        """The specification with this content hash: the one a result was made from (the newest
        stored specification is not necessarily the one in use, an identical one is reused)."""
        ...


__all__ = [
    "ATTACH_OPERATION",
    "DELIVERIES",
    "ENGINE_VERSION",
    "MIX_OPERATION",
    "PROFILES",
    "RESULT_OPERATION",
    "SPEC_OPERATION",
    "STEM_OPERATION",
    "AttachMixCommand",
    "AttachMixResult",
    "AudioMixCreator",
    "AudioMixError",
    "AudioMixSpecification",
    "AudioMixer",
    "BackendFailed",
    "BusRole",
    "Certainty",
    "CreateSpecCommand",
    "DeliveryProfile",
    "InvalidMixConfig",
    "InvalidMixDocument",
    "JobState",
    "Measurement",
    "MixAttacher",
    "MixBus",
    "MixCatalog",
    "MixClip",
    "MixConfig",
    "MixError",
    "MixOutput",
    "MixOverrides",
    "MixProfile",
    "MixRenderResult",
    "MixResult",
    "MixTrack",
    "NothingToMix",
    "OutputIssue",
    "RenderMixCommand",
    "Severity",
    "SpecResult",
    "SpecificationInvalid",
    "StemOutput",
    "TimelineNotMixable",
]
