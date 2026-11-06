"""The Edit Plan Compiler PUBLIC API for other modules (import this file only).

It is the bridge between creative planning and timeline construction. It turns the edit and the
stored Layer 4 plans (channel style, graphics, captions, transitions, VFX, sound design) into ONE
validated, versioned ``TimelineSpecification``: exact times and frames, tracks with explicit
stacking, every file traced to its recording, transitions with their handles, effects, reframing
crops, audio items and instructions, a dependency manifest and every finding. It decides nothing
creative: a contradiction between plans is reported (``creative_conflict``) and both instructions
are kept; a technical fix that cannot change the intent is made deterministically and reported
(``auto_resolved``); anything that makes the timeline unsafe blocks it (``critical``, nothing is
stored). The specification names no timeline application: ``timeline_builder`` consumes it.

    source = TimelineSource(edit=EditRef(...), project_id=..., anchor_asset_id=..., frame_rate=...,
                            frame_rate_assumed=False, duration=..., story_format=..., clips=(...))
    result = compiler.execute(CompileTimelineCommand(source, style=style, graphics=gp, captions=cp,
                                                     transitions=tp, vfx=vp, sound=sp), ctx)
    spec = result.value.spec          # created=False on a cache hit
    spec.video, spec.overlays, spec.audio, spec.transitions, spec.effects, spec.automation
    spec.issues, spec.dependencies, spec.creator, spec.compilation

``TimelineCatalog`` reads stored specifications. Blocking library I/O: run it as a job.
"""

from typing import Protocol

from media_house.modules.edit_plan_compiler.application.compile_timeline import (
    CompileError,
    CompileTimelineCommand,
    TimelineResult,
)
from media_house.modules.edit_plan_compiler.domain.engine import LIMITATIONS
from media_house.modules.edit_plan_compiler.domain.errors import (
    EditPlanCompilerError,
    InvalidCompilerConfig,
    InvalidCompileSource,
    InvalidTimelineDocument,
    NothingToCompile,
    StyleRequired,
    TimelineBlocked,
)
from media_house.modules.edit_plan_compiler.domain.facts import (
    EditRef,
    FramingRef,
    SourceBridge,
    SourceClip,
    SourceSection,
    TimelineSource,
)
from media_house.modules.edit_plan_compiler.domain.model import (
    AssetUse,
    AudioAutomation,
    AudioItem,
    ChangeSummary,
    ColorManagement,
    ColorTag,
    Compilation,
    CreatorIdentity,
    DependencyEntry,
    EffectSpec,
    Handles,
    InputRef,
    Issue,
    OverlayItem,
    Reframe,
    Retime,
    SectionSpec,
    SubtitleSidecar,
    Timebase,
    TimelineSpecification,
    Track,
    TransitionSpec,
    VideoItem,
)
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.serialization import spec_from_json, spec_to_json
from media_house.modules.edit_plan_compiler.domain.values import (
    COMPILER_VERSION,
    DOCUMENT_TYPE,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    AssetRelation,
    ColorProfile,
    Composite,
    DependencyKind,
    DependencyState,
    DerivativePolicy,
    Execution,
    IssueClass,
    ItemOrigin,
    ResultState,
    TrackKind,
    TrackRole,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class TimelineCompiler(Protocol):
    def execute(
        self, command: CompileTimelineCommand, ctx: JobContext
    ) -> Result[TimelineResult, CompileError]:
        """Compile, validate and store (or reuse) the timeline specification of one edit."""
        ...


class TimelineCatalog(Protocol):
    """Stored specifications; nothing is compiled."""

    def latest(
        self, anchor_asset_id: str, *, edit_plan_id: str | None = None
    ) -> Result[tuple[TimelineSpecification, MediaAssetDto] | None, MediaError]: ...

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]: ...

    def load(
        self, spec_asset_id: str
    ) -> Result[TimelineSpecification, MediaError | InvalidTimelineDocument]: ...


__all__ = [
    "COMPILER_VERSION",
    "DOCUMENT_TYPE",
    "LIMITATIONS",
    "RESULT_OPERATION",
    "SCHEMA_VERSION",
    "AssetRelation",
    "AssetUse",
    "AudioAutomation",
    "AudioItem",
    "ChangeSummary",
    "ColorManagement",
    "ColorProfile",
    "ColorTag",
    "Compilation",
    "CompileError",
    "CompileTimelineCommand",
    "CompilerConfig",
    "Composite",
    "CreatorIdentity",
    "DependencyEntry",
    "DependencyKind",
    "DependencyState",
    "DerivativePolicy",
    "EditPlanCompilerError",
    "EditRef",
    "EffectSpec",
    "Execution",
    "FramingRef",
    "Handles",
    "InputRef",
    "InvalidCompileSource",
    "InvalidCompilerConfig",
    "InvalidTimelineDocument",
    "Issue",
    "IssueClass",
    "ItemOrigin",
    "NothingToCompile",
    "OverlayItem",
    "Reframe",
    "ResultState",
    "Retime",
    "SectionSpec",
    "SourceBridge",
    "SourceClip",
    "SourceSection",
    "StyleRequired",
    "SubtitleSidecar",
    "Timebase",
    "TimelineBlocked",
    "TimelineCatalog",
    "TimelineCompiler",
    "TimelineResult",
    "TimelineSource",
    "TimelineSpecification",
    "Track",
    "TrackKind",
    "TrackRole",
    "TransitionSpec",
    "VideoItem",
    "spec_from_json",
    "spec_to_json",
]
