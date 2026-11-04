"""The Music and Sound Design PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``music_and_sound_design``.

The owner's sound library is the ONLY source of audio: nothing is downloaded, generated or
substituted. A need the library cannot meet becomes a ``MissingAsset`` recommendation.

    # 1. inventory (idempotent: identical files are stored once; measurements are cached)
    report = importer.execute(ImportSoundLibraryCommand(Path(".../manifest.json")), ctx)

    # 2. plan (the pipeline maps the edit, or the bare recordings, into a SoundtrackTimeline)
    result = planner.execute(PlanSoundDesignCommand(timeline), ctx)
    plan = result.value.plan          # SoundDesignPlan; created is False on a cache hit
    plan.music, plan.music_free, plan.sfx, plan.no_sfx, plan.ducking, plan.crossfades
    plan.original_audio, plan.missing_assets, plan.credit_lines, plan.checks

    # 3. optional stem (WAV pcm_f32le 48 kHz, NOT mastered) for audio_improvement's AudioMixing
    stem = renderer.execute(RenderSoundStemCommand(result.value.asset.id), ctx)

Conventions: ``timeline_*`` are exact seconds from the start of the edit, ``source_*`` exact
seconds of the music/effect file, all ``Rational``; frames are computed once from the timeline's
frame rate. Levels and ducking are intent for audio_improvement (no compressor, limiter or
loudness normalisation here). Stored results are read through the catalogs.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.music_and_sound_design.application.import_library import (
    AnalyzeSoundsCommand,
    ImportedSound,
    ImportSoundLibrary,
    ImportSoundLibraryCommand,
    ImportSoundLibraryReport,
    SoundAnalysisReport,
)
from media_house.modules.music_and_sound_design.application.plan_sound_design import (
    PlanSoundDesignCommand,
    PlanSoundDesignError,
    SoundDesignResult,
)
from media_house.modules.music_and_sound_design.application.ports import (
    StemOptions,
    StemReport,
)
from media_house.modules.music_and_sound_design.application.render_stem import (
    RenderSoundStemCommand,
    RenderStemError,
    StemResult,
)
from media_house.modules.music_and_sound_design.domain.analysis import (
    EditPoint,
    LoopRegion,
    Loudness,
    MusicAnalysis,
    MusicMeasurement,
    MusicSection,
    SfxMeasurement,
    SfxProfile,
    Tempo,
    Unavailable,
)
from media_house.modules.music_and_sound_design.domain.errors import (
    InvalidManifest,
    InvalidSoundConfig,
    InvalidSoundDesignPlan,
    InvalidSoundDocument,
    NotASound,
    NothingToScore,
    SoundDesignError,
    StemRenderFailed,
    UnreadableSound,
)
from media_house.modules.music_and_sound_design.domain.library import (
    Licence,
    LicencePolicy,
    SoundAsset,
    SoundIndex,
    SoundMatch,
    SoundQuery,
    StatedMusic,
)
from media_house.modules.music_and_sound_design.domain.model import (
    AssetRef,
    Candidate,
    Credit,
    Crossfade,
    DuckingIntent,
    InputRecord,
    ItemDecision,
    MissingAsset,
    MusicCue,
    MusicFreeSpan,
    MusicSegment,
    NoSoundEffect,
    OriginalAudio,
    Provenance,
    QcIssue,
    SfxEvent,
    SoundDesignPlan,
    StageReport,
)
from media_house.modules.music_and_sound_design.domain.profile import (
    CHANNEL_IDENTITY,
    CHANNEL_IDENTITY_VERSION,
    DEFAULT_PROFILE,
    PROFILES,
    STORY_FORMAT_PROFILE,
    MixIntent,
    MusicRules,
    OriginalAudioRules,
    RoleCategories,
    RoleEnergy,
    RolePresence,
    SectionOverride,
    SfxRule,
    SfxRules,
    SilenceRules,
    SoundDesignConfig,
    SoundProfile,
)
from media_house.modules.music_and_sound_design.domain.serialization import (
    music_analysis_from_json,
    plan_from_json,
    plan_to_json,
    sfx_profile_from_json,
)
from media_house.modules.music_and_sound_design.domain.timeline import (
    AudioBridge,
    EditRef,
    OriginalSoundEvent,
    PacingCue,
    SoundtrackTimeline,
    SpeechSpan,
    SyncEvent,
    TimelineClip,
    TimelineSection,
)
from media_house.modules.music_and_sound_design.domain.values import (
    ANALYZER_VERSION,
    MUSIC_ANALYSIS,
    PLANNER_VERSION,
    PROFILE_VERSION,
    RENDERER_VERSION,
    SCHEMA_VERSION,
    SFX_PROFILE,
    SOUND_DESIGN_PLAN,
    STEM_RENDER,
    VOCABULARY_VERSION,
    Alignment,
    BridgeKind,
    ClipAudio,
    CrossfadeKind,
    Decision,
    Declined,
    DuckKey,
    EditPointKind,
    EventSource,
    InputHow,
    LicenceState,
    MusicMode,
    MusicPresence,
    Need,
    OriginalAudioMode,
    QcCategory,
    ResultState,
    SectionLabel,
    SectionRole,
    Severity,
    SfxAlign,
    SoundKind,
    StageState,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class SoundLibraryImporter(Protocol):
    """Import the owner's manifest into the Media Library and measure every sound."""

    def execute(
        self, command: ImportSoundLibraryCommand, ctx: JobContext
    ) -> Result[ImportSoundLibraryReport, MediaError | SoundDesignError]: ...


class SoundLibraryCatalog(Protocol):
    """The imported library with its measurements. Nothing is computed."""

    def sounds(self, kind: SoundKind | None = None) -> list[SoundAsset]: ...

    def get(self, asset_id: str) -> SoundAsset | None: ...

    def index(self) -> SoundIndex: ...

    def search(self, query: SoundQuery) -> list[SoundMatch]: ...


class MusicAnalysisCatalog(Protocol):
    """Stored ``music_analysis`` documents. Nothing is computed."""

    def latest(
        self, asset_id: str
    ) -> Result[tuple[MusicAnalysis, MediaAssetDto] | None, MediaError]: ...

    def load(
        self, document_asset_id: str
    ) -> Result[MusicAnalysis, MediaError | InvalidSoundDocument]: ...


class SfxProfileCatalog(Protocol):
    """Stored ``sfx_profile`` documents. Nothing is computed."""

    def latest(
        self, asset_id: str
    ) -> Result[tuple[SfxProfile, MediaAssetDto] | None, MediaError]: ...

    def load(
        self, document_asset_id: str
    ) -> Result[SfxProfile, MediaError | InvalidSoundDocument]: ...


class SoundDesignPlanner(Protocol):
    """Plan music, effects, silence and mix intent for one edit (or the bare recordings)."""

    def execute(
        self, command: PlanSoundDesignCommand, ctx: JobContext
    ) -> Result[SoundDesignResult, PlanSoundDesignError]: ...


class SoundDesignCatalog(Protocol):
    """Stored ``sound_design_plan`` documents. Nothing is computed."""

    def latest(
        self,
        asset_id: str,
        *,
        edit_plan_id: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[SoundDesignPlan, MediaAssetDto] | None, MediaError]:
        """Newest plan of the anchor (for one edit: its id, or ``"none"`` on the no-speech path)."""
        ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored plan of the anchor, newest first (earlier ones are never changed)."""
        ...

    def load(
        self, plan_asset_id: str
    ) -> Result[SoundDesignPlan, MediaError | InvalidSoundDocument]: ...


class SoundStemRenderer(Protocol):
    """Render a stored plan's music and effects into one WAV stem (no mastering)."""

    def execute(
        self, command: RenderSoundStemCommand, ctx: JobContext
    ) -> Result[StemResult, RenderStemError]: ...


__all__ = [
    "ANALYZER_VERSION",
    "CHANNEL_IDENTITY",
    "CHANNEL_IDENTITY_VERSION",
    "DEFAULT_PROFILE",
    "MUSIC_ANALYSIS",
    "PLANNER_VERSION",
    "PROFILES",
    "PROFILE_VERSION",
    "RENDERER_VERSION",
    "SCHEMA_VERSION",
    "SFX_PROFILE",
    "SOUND_DESIGN_PLAN",
    "STEM_RENDER",
    "STORY_FORMAT_PROFILE",
    "VOCABULARY_VERSION",
    "Alignment",
    "AnalyzeSoundsCommand",
    "AssetRef",
    "AudioBridge",
    "BridgeKind",
    "Candidate",
    "ClipAudio",
    "Credit",
    "Crossfade",
    "CrossfadeKind",
    "Decision",
    "Declined",
    "DuckKey",
    "DuckingIntent",
    "EditPoint",
    "EditPointKind",
    "EditRef",
    "EventSource",
    "ImportSoundLibrary",
    "ImportSoundLibraryCommand",
    "ImportSoundLibraryReport",
    "ImportedSound",
    "InputHow",
    "InputRecord",
    "InvalidManifest",
    "InvalidSoundConfig",
    "InvalidSoundDesignPlan",
    "InvalidSoundDocument",
    "ItemDecision",
    "Licence",
    "LicencePolicy",
    "LicenceState",
    "LoopRegion",
    "Loudness",
    "MissingAsset",
    "MixIntent",
    "MusicAnalysis",
    "MusicAnalysisCatalog",
    "MusicCue",
    "MusicFreeSpan",
    "MusicMeasurement",
    "MusicMode",
    "MusicPresence",
    "MusicRules",
    "MusicSection",
    "MusicSegment",
    "Need",
    "NoSoundEffect",
    "NotASound",
    "NothingToScore",
    "OriginalAudio",
    "OriginalAudioMode",
    "OriginalAudioRules",
    "OriginalSoundEvent",
    "PacingCue",
    "PlanSoundDesignCommand",
    "PlanSoundDesignError",
    "Provenance",
    "QcCategory",
    "QcIssue",
    "RenderSoundStemCommand",
    "RenderStemError",
    "ResultState",
    "RoleCategories",
    "RoleEnergy",
    "RolePresence",
    "SectionLabel",
    "SectionOverride",
    "SectionRole",
    "Severity",
    "SfxAlign",
    "SfxEvent",
    "SfxMeasurement",
    "SfxProfile",
    "SfxProfileCatalog",
    "SfxRule",
    "SfxRules",
    "SilenceRules",
    "SoundAnalysisReport",
    "SoundAsset",
    "SoundDesignCatalog",
    "SoundDesignConfig",
    "SoundDesignError",
    "SoundDesignPlan",
    "SoundDesignPlanner",
    "SoundDesignResult",
    "SoundIndex",
    "SoundKind",
    "SoundLibraryCatalog",
    "SoundLibraryImporter",
    "SoundMatch",
    "SoundProfile",
    "SoundQuery",
    "SoundStemRenderer",
    "SoundtrackTimeline",
    "SpeechSpan",
    "StageReport",
    "StageState",
    "StatedMusic",
    "StemOptions",
    "StemRenderFailed",
    "StemReport",
    "StemResult",
    "SyncEvent",
    "Tempo",
    "TimelineClip",
    "TimelineSection",
    "Unavailable",
    "UnreadableSound",
    "music_analysis_from_json",
    "plan_from_json",
    "plan_to_json",
    "sfx_profile_from_json",
]
