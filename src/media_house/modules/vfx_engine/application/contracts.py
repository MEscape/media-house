"""The VFX Engine PUBLIC API for other modules (import this file only).

Shot-level footage effects: punch-in zooms, slow motion / freezes / reverse (exact time maps),
privacy concealment on tracked regions, looks (vignette, grain, glow, ...), footage composites -
planned with restraint and rendered as derived ProRes 422 HQ 10-bit clips. It does NOT denoise,
sharpen, grade or stabilise (video_improvement), draw graphics (motion_graphics_engine), make
transitions (transition_engine) or cut stills (visual_asset_generation).

    shots = VfxShots(edit=EditRef(edit_plan_id=..., plan_version=1), project_id=..., frame_rate=
                     Rational(30, 1), output_width=1920, output_height=1080, clips=(VfxClip(...),),
                     requests=(VfxRequest(request_id=..., kind=RequestKind.EMPHASIS, ...),))
    result = planner.execute(PlanVfxCommand(shots, style=style_ref,
                                            config=VfxConfig(profile="fireworks_showcase"),
                                            render=True), ctx)
    plan = result.value.plan          # treatments: effects, time maps, masks, outputs, render QC

The no-speech path: ``VfxShots.no_speech(((asset_id, duration),), frame_rate=..., width=...,
height=...)``. Stored plans are read with ``VfxCatalog``.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.vfx_engine.application.plan_vfx import (
    PlanVfxCommand,
    PlanVfxError,
    VfxResult,
)
from media_house.modules.vfx_engine.application.ports import (
    EffectRenderer,
    Inpainter,
    SourceMedia,
    SubjectMatter,
)
from media_house.modules.vfx_engine.domain.errors import (
    InvalidVfxConfig,
    InvalidVfxDocument,
    InvalidVfxPlan,
    InvalidVfxShots,
    NothingToTreat,
    VfxEngineError,
)
from media_house.modules.vfx_engine.domain.facts import (
    Box,
    EditRef,
    SourceSpan,
    VfxClip,
    VfxRequest,
    VfxShots,
)
from media_house.modules.vfx_engine.domain.model import (
    Capability,
    Effect,
    InputRecord,
    Mask,
    MaskKeyframe,
    OutputRef,
    Param,
    Provenance,
    QcIssue,
    RenderQc,
    StageReport,
    StyleBinding,
    TimeMap,
    TimeSegment,
    Treatment,
    VfxPlan,
)
from media_house.modules.vfx_engine.domain.profile import (
    DEFAULT_PROFILE,
    FORMAT_PROFILE,
    PROFILES,
    EffectDecision,
    SectionOverride,
    VfxConfig,
    VfxProfile,
    knob_fields,
)
from media_house.modules.vfx_engine.domain.serialization import plan_from_json, plan_to_json
from media_house.modules.vfx_engine.domain.values import (
    DOCUMENT_TYPE,
    PROFILE_VERSION,
    RENDER_OPERATION,
    RENDER_PIX_FMT,
    SCHEMA_VERSION,
    AudioIntent,
    Backend,
    ClipAudio,
    Decision,
    EffectStatus,
    EffectType,
    Interpolation,
    MaskKind,
    QcCategory,
    RequestKind,
    ResultState,
    SegmentKind,
    Severity,
    TreatmentStatus,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class VfxPlanner(Protocol):
    def execute(self, command: PlanVfxCommand, ctx: JobContext) -> Result[VfxResult, PlanVfxError]:
        """Plan (and optionally render) the effects; stored or reused. Blocking: run in a job."""
        ...


class VfxCatalog(Protocol):
    """Stored plans; nothing is planned or rendered here."""

    def latest(
        self,
        asset_id: str,
        *,
        edit_plan_id: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[VfxPlan, MediaAssetDto] | None, MediaError]: ...

    def history(self, asset_id: str) -> list[MediaAssetDto]: ...

    def load(self, plan_asset_id: str) -> Result[VfxPlan, MediaError | InvalidVfxDocument]: ...


__all__ = [
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "FORMAT_PROFILE",
    "PROFILES",
    "PROFILE_VERSION",
    "RENDER_OPERATION",
    "RENDER_PIX_FMT",
    "SCHEMA_VERSION",
    "AudioIntent",
    "Backend",
    "Box",
    "Capability",
    "ClipAudio",
    "Decision",
    "EditRef",
    "Effect",
    "EffectDecision",
    "EffectRenderer",
    "EffectStatus",
    "EffectType",
    "Inpainter",
    "InputRecord",
    "Interpolation",
    "InvalidVfxConfig",
    "InvalidVfxDocument",
    "InvalidVfxPlan",
    "InvalidVfxShots",
    "Mask",
    "MaskKeyframe",
    "MaskKind",
    "NothingToTreat",
    "OutputRef",
    "Param",
    "PlanVfxCommand",
    "PlanVfxError",
    "Provenance",
    "QcCategory",
    "QcIssue",
    "RenderQc",
    "RequestKind",
    "ResultState",
    "SectionOverride",
    "SegmentKind",
    "Severity",
    "SourceMedia",
    "SourceSpan",
    "StageReport",
    "StyleBinding",
    "SubjectMatter",
    "TimeMap",
    "TimeSegment",
    "Treatment",
    "TreatmentStatus",
    "VfxCatalog",
    "VfxClip",
    "VfxConfig",
    "VfxEngineError",
    "VfxPlan",
    "VfxPlanner",
    "VfxProfile",
    "VfxRequest",
    "VfxResult",
    "VfxShots",
    "knob_fields",
    "plan_from_json",
    "plan_to_json",
]
