"""The Reframing Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``reframing_intelligence``.

``ReframingAnalyzer.execute`` recommends how each shot of a recording should be framed for a target
format (``9:16``, ``16:9``, ``1:1`` or a custom shape): a crop window and, where the subject moves,
keyframes that move it; or the original framing; or an explicit statement that no crop can keep what
the shot needs. It READS the stored picture analysis (tracks, quality), the inspection, the story
plan and the continuity analysis; it recommends only. It never modifies media, renders, or touches
a timeline backend, and it does not decide the edit: ``editing_intelligence`` does. It is blocking
(library reads): run it as a job.

    command = AnalyzeReframingCommand((asset_id,), TargetFormat.of("9:16"))
    result = analyzer.execute(command, ctx)
    if isinstance(result, Ok):
        analysis = result.value.analysis      # ReframingAnalysis (created is False on a reuse)
        for rec in analysis.covering(asset_id, start, end):   # what applies to a source range
            rec.strategy, rec.feasibility, rec.executable, rec.keyframes, rec.scores
            rec.crop_at(seconds)               # the window at a source time

Conventions (see ``domain/values.py`` and ``domain/model.py``): a crop is ``Crop(x, y, w, h)``, the
top-left corner and size as fractions of the DISPLAY-oriented source picture, scaled uniformly to
the output; keyframe times are exact seconds on the recording's common clock; between keyframes
the first one's ``interpolation`` applies. Stored analyses are read through ``ReframingCatalog``.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.reframing_intelligence.application.analyze_reframing import (
    AnalyzeReframingCommand,
    AnalyzeReframingError,
    ReframingResult,
    SegmentDecision,
)
from media_house.modules.reframing_intelligence.domain.errors import (
    InvalidReframingAnalysis,
    InvalidReframingConfig,
    InvalidReframingDocument,
    MissingPrerequisite,
    NothingToFrame,
    ReframingIntelligenceError,
)
from media_house.modules.reframing_intelligence.domain.geometry import (
    Box,
    Crop,
    SourceGeometry,
    TargetFormat,
    crop_problems,
    effective_scale,
    interpolate,
    widest_crop,
)
from media_house.modules.reframing_intelligence.domain.model import (
    Alternative,
    Confidence,
    CropKeyframe,
    Element,
    Evidence,
    InputRecord,
    Issue,
    Metrics,
    PairCheck,
    Provenance,
    Recommendation,
    ReframingAnalysis,
    Resolution,
    Score,
    SourceRecord,
    StageReport,
    TrackingInterval,
    crop_at,
)
from media_house.modules.reframing_intelligence.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    ReframingConfig,
    ReframingProfile,
    SafeArea,
)
from media_house.modules.reframing_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.reframing_intelligence.domain.validation import validate_analysis
from media_house.modules.reframing_intelligence.domain.values import (
    ANALYZER_VERSION,
    ASPECT_TOLERANCE,
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    CameraMotion,
    Decision,
    Feasibility,
    Importance,
    InputHow,
    Interpolation,
    IntervalState,
    IssueKind,
    ResultState,
    ScoreBasis,
    ScoreName,
    Severity,
    StageState,
    Strategy,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ReframingAnalyzer(Protocol):
    """Recommend the framing of the shots of one or more recordings for a target format."""

    def execute(
        self, command: AnalyzeReframingCommand, ctx: JobContext
    ) -> Result[ReframingResult, AnalyzeReframingError]: ...


class ReframingCatalog(Protocol):
    """Read stored analyses. Nothing is computed."""

    def latest(
        self,
        asset_id: str,
        *,
        target: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[ReframingAnalysis, MediaAssetDto] | None, MediaError]:
        """The newest stored analysis (of one target such as ``9:16`` and profile, if given)."""
        ...

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        """Every stored analysis of the asset, newest first (earlier ones are never changed)."""
        ...

    def load(
        self, analysis_asset_id: str
    ) -> Result[ReframingAnalysis, MediaError | InvalidReframingDocument]: ...


__all__ = [
    "ANALYZER_VERSION",
    "ASPECT_TOLERANCE",
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "PROFILES",
    "SCHEMA_VERSION",
    "VOCABULARY_VERSION",
    "Alternative",
    "AnalyzeReframingCommand",
    "AnalyzeReframingError",
    "Box",
    "CameraMotion",
    "Confidence",
    "Crop",
    "CropKeyframe",
    "Decision",
    "Element",
    "Evidence",
    "Feasibility",
    "Importance",
    "InputHow",
    "InputRecord",
    "Interpolation",
    "IntervalState",
    "InvalidReframingAnalysis",
    "InvalidReframingConfig",
    "InvalidReframingDocument",
    "Issue",
    "IssueKind",
    "Metrics",
    "MissingPrerequisite",
    "NothingToFrame",
    "PairCheck",
    "Provenance",
    "Recommendation",
    "ReframingAnalysis",
    "ReframingAnalyzer",
    "ReframingCatalog",
    "ReframingConfig",
    "ReframingIntelligenceError",
    "ReframingProfile",
    "ReframingResult",
    "Resolution",
    "ResultState",
    "SafeArea",
    "Score",
    "ScoreBasis",
    "ScoreName",
    "SegmentDecision",
    "Severity",
    "SourceGeometry",
    "SourceRecord",
    "StageReport",
    "StageState",
    "Strategy",
    "TargetFormat",
    "TrackingInterval",
    "analysis_from_json",
    "analysis_to_json",
    "crop_at",
    "crop_problems",
    "effective_scale",
    "interpolate",
    "validate_analysis",
    "widest_crop",
]
