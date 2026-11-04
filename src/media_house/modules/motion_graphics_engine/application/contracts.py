"""The Motion Graphics Engine PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``motion_graphics_engine``.

It builds, animates, renders and validates graphics from the channel identity
(``visual_style_engine``): one shared graphic model (``GraphicRequest``), one layout engine,
one animation compiler, one renderer (HyperFrames -> ProRes 4444 MOV with true alpha).

* ``GraphicsRenderer.render(RenderGraphicsCommand, ctx)``: the EXECUTOR contract. caption_engine
  sends ``GraphicKind.CAPTION`` groups (``TimedWord``s), transition_engine sends
  ``GraphicKind.TRANSITION_OVERLAY`` requests; both get per-request library asset ids, exact
  frames, animation events and QC. Identical requests (same style, fps, renderer) are reused.
* ``MotionGraphicsPlanner.execute(PlanGraphicsCommand(brief, style, config), ctx)``: plans HOW
  the edit's graphic requests are built (+ format templates and brand bookends), renders them
  and stores a ``graphics_plan`` document of the anchor.
* ``GraphicsCatalog``: stored plans (``latest`` / ``history`` / ``load``).
* ``AnimationEvent``s (``enter_start | enter_peak | emphasis | exit_start | exit_end`` with
  ``local_at`` and ``timeline_at``) are what music_and_sound_design syncs to.

Blocking calls (rendering runs Chrome through HyperFrames): run them as jobs.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.motion_graphics_engine.application.plan_graphics import (
    LIMITATIONS,
    PlanError,
    PlanGraphicsCommand,
    PlanGraphicsResult,
)
from media_house.modules.motion_graphics_engine.application.render_graphics import (
    RenderedGraphic,
    RenderedGraphics,
    RenderError,
    RenderGraphicsCommand,
)
from media_house.modules.motion_graphics_engine.application.views import (
    price_cents,
    product_facts,
    rank_of,
)
from media_house.modules.motion_graphics_engine.domain.brief import (
    BriefSection,
    EditorialGraphic,
    GraphicsBrief,
    OnScreenText,
    ProductFacts,
    SpecFact,
    SponsoredSpan,
)
from media_house.modules.motion_graphics_engine.domain.engine import frame_span
from media_house.modules.motion_graphics_engine.domain.errors import (
    InvalidGraphicRequest,
    InvalidGraphicsConfig,
    InvalidGraphicsDocument,
    InvalidGraphicsPlan,
    MotionGraphicsError,
    NothingToRender,
    RendererNotAvailable,
    RenderFailed,
    StyleMismatch,
)
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationEvent,
    AnimationRequest,
    CompiledAnimation,
    Composition,
    DataPoint,
    Element,
    FrameSpan,
    GraphicContent,
    GraphicRequest,
    GraphicsPlan,
    Keyframe,
    MotionSpec,
    Placement,
    PlannedGraphic,
    Provenance,
    Rect,
    RenderRecord,
    SpecLine,
    SyncPoint,
    TimedEmoji,
    TimedWord,
    Timing,
    Track,
    TrackPoint,
)
from media_house.modules.motion_graphics_engine.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    STORY_FORMAT_PROFILE,
    Bookends,
    CaptionExecution,
    DecisionAction,
    GraphicDecision,
    MotionGraphicsConfig,
    MotionGraphicsProfile,
    RenderSettings,
    SectionOverride,
    Templates,
    knob_fields,
)
from media_house.modules.motion_graphics_engine.domain.profile import (
    Timing as TimingKnobs,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    DOCUMENT_TYPE,
    PROFILE_VERSION,
    RENDER_OPERATION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    Anchor,
    ChartKind,
    EditRef,
    EventKind,
    GraphicKind,
    InputHow,
    InputRecord,
    Intent,
    ItemStatus,
    Origin,
    OverlayDirection,
    OverlayKind,
    Prop,
    QcCategory,
    QcIssue,
    RenderQuality,
    ResultState,
    Severity,
    StageReport,
    StageState,
    StyleBinding,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class GraphicsRenderer(Protocol):
    def render(
        self, command: RenderGraphicsCommand, ctx: JobContext
    ) -> Result[RenderedGraphics, RenderError]:
        """Render (or reuse) every request. ``RendererNotAvailable`` names the setup command."""
        ...


class MotionGraphicsPlanner(Protocol):
    def execute(
        self, command: PlanGraphicsCommand, ctx: JobContext
    ) -> Result[PlanGraphicsResult, PlanError]:
        """Plan, render and store (or reuse) the graphics of one edit."""
        ...


class GraphicsCatalog(Protocol):
    def latest(
        self, anchor_asset_id: str, *, project_id: str | None = None
    ) -> Result[tuple[GraphicsPlan, MediaAssetDto] | None, MediaError]: ...

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]: ...

    def load(
        self, plan_asset_id: str
    ) -> Result[GraphicsPlan, MediaError | InvalidGraphicsDocument]: ...


__all__ = [
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "LIMITATIONS",
    "PROFILES",
    "PROFILE_VERSION",
    "RENDER_OPERATION",
    "RESULT_OPERATION",
    "SCHEMA_VERSION",
    "STORY_FORMAT_PROFILE",
    "Anchor",
    "AnimationEvent",
    "AnimationRequest",
    "Bookends",
    "BriefSection",
    "CaptionExecution",
    "ChartKind",
    "CompiledAnimation",
    "Composition",
    "DataPoint",
    "DecisionAction",
    "EditRef",
    "EditorialGraphic",
    "Element",
    "EventKind",
    "FrameSpan",
    "GraphicContent",
    "GraphicDecision",
    "GraphicKind",
    "GraphicRequest",
    "GraphicsBrief",
    "GraphicsCatalog",
    "GraphicsPlan",
    "GraphicsRenderer",
    "InputHow",
    "InputRecord",
    "Intent",
    "InvalidGraphicRequest",
    "InvalidGraphicsConfig",
    "InvalidGraphicsDocument",
    "InvalidGraphicsPlan",
    "ItemStatus",
    "Keyframe",
    "MotionGraphicsConfig",
    "MotionGraphicsError",
    "MotionGraphicsPlanner",
    "MotionGraphicsProfile",
    "MotionSpec",
    "NothingToRender",
    "OnScreenText",
    "Origin",
    "OverlayDirection",
    "OverlayKind",
    "Placement",
    "PlanError",
    "PlanGraphicsCommand",
    "PlanGraphicsResult",
    "PlannedGraphic",
    "ProductFacts",
    "Prop",
    "Provenance",
    "QcCategory",
    "QcIssue",
    "Rect",
    "RenderError",
    "RenderFailed",
    "RenderGraphicsCommand",
    "RenderQuality",
    "RenderRecord",
    "RenderSettings",
    "RenderedGraphic",
    "RenderedGraphics",
    "RendererNotAvailable",
    "ResultState",
    "SectionOverride",
    "Severity",
    "SpecFact",
    "SpecLine",
    "SponsoredSpan",
    "StageReport",
    "StageState",
    "StyleBinding",
    "StyleMismatch",
    "SyncPoint",
    "Templates",
    "TimedEmoji",
    "TimedWord",
    "Timing",
    "TimingKnobs",
    "Track",
    "TrackPoint",
    "frame_span",
    "knob_fields",
    "price_cents",
    "product_facts",
    "rank_of",
]
