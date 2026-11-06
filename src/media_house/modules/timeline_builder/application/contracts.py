"""The Timeline Builder PUBLIC API for other modules (import this file only).

``timeline_builder`` turns the compiler's validated ``timeline_specification`` into the
constructed, application-independent timeline a person (or ``resolve_adapter``) opens: tracks
that hold their clips in order, one source table (original, derivative, proxy), each picture
clip's processing chain in a fixed order, transitions that join two named clips, mix instructions
for ``audio_mix_engine``, markers, sections, groups, a dependency manifest, findings, and a stable
id and provenance on every element. It decides nothing creative and names no timeline
application: an instruction it cannot carry out is kept and reported, never replaced.

    result = builder.execute(BuildTimelineCommand(spec=spec, spec_asset_id=asset.id), ctx)
    timeline = result.value.timeline          # created=False on a cache hit
    for track in timeline.tracks: track.video, track.audio
    timeline.transitions, timeline.mix, timeline.markers, timeline.dependencies, timeline.issues

A person's changes are recorded with ``TimelineEditor`` and survive the next build according to
``BuilderConfig.conflict_policy``. ``BuiltTimelineCatalog`` reads stored builds. Blocking library
I/O: run these as jobs.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.timeline_builder.application.build_timeline import (
    BuildError,
    BuildResult,
    BuildTimelineCommand,
)
from media_house.modules.timeline_builder.application.record_edits import (
    RecordEditsCommand,
    RecordError,
)
from media_house.modules.timeline_builder.domain.engine import LIMITATIONS
from media_house.modules.timeline_builder.domain.errors import (
    InvalidBuilderConfig,
    InvalidBuiltDocument,
    InvalidHumanEdit,
    InvalidSpecification,
    NothingBuilt,
    SpecificationRequired,
    TimelineBuilderError,
    TimelineNotBuildable,
)
from media_house.modules.timeline_builder.domain.human import HumanEdit
from media_house.modules.timeline_builder.domain.model import (
    AudioClip,
    BrandAssetReference,
    BuildInfo,
    BuiltSection,
    BuiltTimeline,
    BuiltTrack,
    BuiltTransition,
    ChainStep,
    ChangeSet,
    ColorSettings,
    Counts,
    CropKeyframe,
    CropPlan,
    DependencyRecord,
    EditRecord,
    EditReference,
    EffectStep,
    ExternalCheck,
    FontReference,
    Group,
    HandleCheck,
    HumanMark,
    IdentityReference,
    Issue,
    Marker,
    MaskPlan,
    MixInstruction,
    SourceMedia,
    SourceUse,
    SubtitleCue,
    SubtitleTrack,
    TimelineSettings,
    TimeMap,
    TimeSegment,
    Tombstone,
    Transform,
    Validation,
    VideoClip,
    ZoneReference,
)
from media_house.modules.timeline_builder.domain.preflight import IdentityExpectation
from media_house.modules.timeline_builder.domain.profile import (
    NATIVE_TRANSITIONS,
    BuilderConfig,
    Capabilities,
)
from media_house.modules.timeline_builder.domain.serialization import (
    timeline_from_json,
    timeline_to_json,
)
from media_house.modules.timeline_builder.domain.values import (
    BUILDER_VERSION,
    DOCUMENT_TYPE,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    BlendMode,
    BuildMode,
    BuildStatus,
    ChainStage,
    CheckKind,
    CheckState,
    ClipKind,
    ClipState,
    ConflictPolicy,
    DependencyKind,
    DependencyState,
    EditKind,
    Execution,
    FitMode,
    Provenance,
    Role,
    Severity,
    TrackKind,
    TransitionMode,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class TimelineBuilder(Protocol):
    def execute(
        self, command: BuildTimelineCommand, ctx: JobContext
    ) -> Result[BuildResult, BuildError]:
        """Build, validate and store (or reuse) the timeline of one compiled specification."""
        ...


class TimelineEditor(Protocol):
    def execute(
        self, command: RecordEditsCommand, ctx: JobContext
    ) -> Result[BuildResult, RecordError]:
        """Record a person's changes as the next version of the built timeline."""
        ...


class BuiltTimelineCatalog(Protocol):
    """Stored builds; nothing is built."""

    def latest(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> Result[tuple[BuiltTimeline, MediaAssetDto] | None, MediaError]: ...

    def history(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> list[MediaAssetDto]: ...

    def load(
        self, build_asset_id: str
    ) -> Result[BuiltTimeline, MediaError | InvalidBuiltDocument]: ...


__all__ = [
    "BUILDER_VERSION",
    "DOCUMENT_TYPE",
    "LIMITATIONS",
    "NATIVE_TRANSITIONS",
    "RESULT_OPERATION",
    "SCHEMA_VERSION",
    "AudioClip",
    "BlendMode",
    "BrandAssetReference",
    "BuildError",
    "BuildInfo",
    "BuildMode",
    "BuildResult",
    "BuildStatus",
    "BuildTimelineCommand",
    "BuilderConfig",
    "BuiltSection",
    "BuiltTimeline",
    "BuiltTimelineCatalog",
    "BuiltTrack",
    "BuiltTransition",
    "Capabilities",
    "ChainStage",
    "ChainStep",
    "ChangeSet",
    "CheckKind",
    "CheckState",
    "ClipKind",
    "ClipState",
    "ColorSettings",
    "ConflictPolicy",
    "Counts",
    "CropKeyframe",
    "CropPlan",
    "DependencyKind",
    "DependencyRecord",
    "DependencyState",
    "EditKind",
    "EditRecord",
    "EditReference",
    "EffectStep",
    "Execution",
    "ExternalCheck",
    "FitMode",
    "FontReference",
    "Group",
    "HandleCheck",
    "HumanEdit",
    "HumanMark",
    "IdentityExpectation",
    "IdentityReference",
    "InvalidBuilderConfig",
    "InvalidBuiltDocument",
    "InvalidHumanEdit",
    "InvalidSpecification",
    "Issue",
    "Marker",
    "MaskPlan",
    "MixInstruction",
    "NothingBuilt",
    "Provenance",
    "RecordEditsCommand",
    "RecordError",
    "Role",
    "Severity",
    "SourceMedia",
    "SourceUse",
    "SpecificationRequired",
    "SubtitleCue",
    "SubtitleTrack",
    "TimeMap",
    "TimeSegment",
    "TimelineBuilder",
    "TimelineBuilderError",
    "TimelineEditor",
    "TimelineNotBuildable",
    "TimelineSettings",
    "Tombstone",
    "TrackKind",
    "Transform",
    "TransitionMode",
    "Validation",
    "VideoClip",
    "ZoneReference",
    "timeline_from_json",
    "timeline_to_json",
]
