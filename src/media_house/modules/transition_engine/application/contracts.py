"""The Transition Engine PUBLIC API for other modules (import this file only).

It decides the transition between adjacent clips of an edit: whether one is needed at all (a
hard cut is the default and always available), which kind, how intense (0-4, bounded by the
channel style's ceiling and the profile's budget), how long, how it sits on the cut, whether the
sources hold the handles, and the fallback. It plans the AUDIO transition of every cut
separately (J/L cuts kept, speech-safe equal-power crossfades, head and tail fades). It does NOT
process audio (audio_improvement), render anything but asks motion_graphics_engine to render
graphic transition overlays, or place cuts (editing_intelligence).

    sequence = TransitionSequence(edit=EditRef(edit_plan_id=..., plan_version=2),
                                  project_id=..., frame_rate=Rational(30, 1),
                                  clips=(TransitionClip(...), ...), sections=(...),
                                  requests=(...), bridges=(...), bookends=(...), format="top_n")
    result = planner.execute(PlanTransitionsCommand(sequence, style=style_ref,
                                                    config=TransitionConfig(), render=True), ctx)
    plan = result.value.plan          # one Transition per boundary + TransitionEvents + QC
    plan.between(out_clip_id, in_clip_id).kind

The no-speech path: ``TransitionSequence.no_speech(((asset_id, duration),), frame_rate=...)``.
Stored plans are read with ``TransitionCatalog``; music_and_sound_design syncs to
``TransitionPlan.events`` (``TransitionEvent.strength`` = intensity / 4).
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.transition_engine.application.plan_transitions import (
    PlanTransitionsCommand,
    PlanTransitionsError,
    TransitionsResult,
)
from media_house.modules.transition_engine.domain.engine import LIMITATIONS
from media_house.modules.transition_engine.domain.errors import (
    InvalidTransitionConfig,
    InvalidTransitionDocument,
    InvalidTransitionPlan,
    InvalidTransitionSequence,
    NothingToTransition,
    TransitionEngineError,
)
from media_house.modules.transition_engine.domain.facts import (
    AudioBridgeMark,
    Bookend,
    EditRef,
    SectionMark,
    TransitionClip,
    TransitionRequest,
    TransitionSequence,
)
from media_house.modules.transition_engine.domain.model import (
    AudioTransition,
    Fallback,
    Handles,
    InputRecord,
    Metrics,
    Overlay,
    Provenance,
    QcIssue,
    Rejection,
    StageReport,
    StyleBinding,
    Transition,
    TransitionEvent,
    TransitionPlan,
)
from media_house.modules.transition_engine.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    STORY_FORMAT_PROFILE,
    STYLE_PROFILE,
    SectionOverride,
    TransitionConfig,
    TransitionDecision,
    TransitionProfile,
    knob_fields,
)
from media_house.modules.transition_engine.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.transition_engine.domain.values import (
    DOCUMENT_TYPE,
    LIBRARY,
    PLANNER_VERSION,
    PROFILE_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    Alignment,
    AudioKind,
    BookendKind,
    BoundaryKind,
    Context,
    Curve,
    DecisionAction,
    DecisionStatus,
    Family,
    InputHow,
    KindSpec,
    QcCategory,
    RenderState,
    ResultState,
    Severity,
    StageState,
    TransitionKind,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class TransitionPlanner(Protocol):
    def execute(
        self, command: PlanTransitionsCommand, ctx: JobContext
    ) -> Result[TransitionsResult, PlanTransitionsError]:
        """Plan, render (when asked) and store (or reuse) the transitions of one edit.
        Blocking (library reads, renders): run it as a job."""
        ...


class TransitionCatalog(Protocol):
    """Stored plans; nothing is computed."""

    def latest(
        self,
        anchor_asset_id: str,
        *,
        edit_plan_id: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[TransitionPlan, MediaAssetDto] | None, MediaError]: ...

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]: ...

    def load(
        self, plan_asset_id: str
    ) -> Result[TransitionPlan, MediaError | InvalidTransitionDocument]: ...


__all__ = [
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "LIBRARY",
    "LIMITATIONS",
    "PLANNER_VERSION",
    "PROFILES",
    "PROFILE_VERSION",
    "RESULT_OPERATION",
    "SCHEMA_VERSION",
    "STORY_FORMAT_PROFILE",
    "STYLE_PROFILE",
    "Alignment",
    "AudioBridgeMark",
    "AudioKind",
    "AudioTransition",
    "Bookend",
    "BookendKind",
    "BoundaryKind",
    "Context",
    "Curve",
    "DecisionAction",
    "DecisionStatus",
    "EditRef",
    "Fallback",
    "Family",
    "Handles",
    "InputHow",
    "InputRecord",
    "InvalidTransitionConfig",
    "InvalidTransitionDocument",
    "InvalidTransitionPlan",
    "InvalidTransitionSequence",
    "KindSpec",
    "Metrics",
    "NothingToTransition",
    "Overlay",
    "PlanTransitionsCommand",
    "PlanTransitionsError",
    "Provenance",
    "QcCategory",
    "QcIssue",
    "Rejection",
    "RenderState",
    "ResultState",
    "SectionMark",
    "SectionOverride",
    "Severity",
    "StageReport",
    "StageState",
    "StyleBinding",
    "Transition",
    "TransitionCatalog",
    "TransitionClip",
    "TransitionConfig",
    "TransitionDecision",
    "TransitionEngineError",
    "TransitionEvent",
    "TransitionKind",
    "TransitionPlan",
    "TransitionPlanner",
    "TransitionProfile",
    "TransitionRequest",
    "TransitionSequence",
    "TransitionsResult",
    "knob_fields",
    "plan_from_json",
    "plan_to_json",
]
