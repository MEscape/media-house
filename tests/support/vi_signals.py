"""Hand-made signals and analyses for Video Intelligence unit tests (no media involved)."""

from collections.abc import Callable, Mapping, Sequence

from media_house.core.domain import Rational
from media_house.modules.video_intelligence.domain.derive import Derived, derive
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.profiles import (
    ProcessingProfile,
    get_profile,
)
from media_house.modules.video_intelligence.domain.result import (
    AnalyzerReport,
    Provenance,
    VideoAnalysis,
)
from media_house.modules.video_intelligence.domain.signals import (
    DetectionRow,
    DetectionSignals,
    EmbeddingSignals,
    FaceRow,
    FaceSignals,
    MotionSignals,
    QualitySignals,
    ShotSignals,
)
from media_house.modules.video_intelligence.domain.source import (
    InputUse,
    ProcessingHistory,
    SourceInfo,
)
from media_house.modules.video_intelligence.domain.values import (
    PROCESSING_VERSION,
    SCHEMA_VERSION,
    AnalyzerId,
    AnalyzerState,
    CacheOutcome,
    CostTier,
    EntityKind,
    InputSource,
    MeasuredOn,
    Stabilization,
)
from media_house.modules.video_intelligence.domain.vocabulary import label_prompts

TIMEBASE = Rational(1, 30000)
TICKS = 1000  # one frame at 30 fps
QUIET = 0.003
CUT_DIFF = 0.2

type Pairs = Sequence[tuple[int, float]]


def shot_signals(
    frames: int = 120,
    *,
    cuts: Sequence[int] = (),
    flashes: Sequence[int] = (),
    luma: Callable[[int], float] | None = None,
    spread: Callable[[int], float] | None = None,
    steps: Callable[[int], float] | None = None,
    long_gap: int = 12,
) -> ShotSignals:
    """Per-frame signals of a clean 30 fps clip with cuts and single-frame flashes at known frames.

    ``steps(i)`` is the quiet frame-to-frame change (default: compression noise only).
    ``luma(i)`` / ``spread(i)`` set brightness and contrast per frame.
    """
    quiet = steps or (lambda _i: QUIET)
    diff1 = [0.0] + [quiet(i) for i in range(1, frames)]
    diff2 = [0.0, 0.0] + [quiet(i) + quiet(i - 1) for i in range(2, frames)]
    hist = [0.0] * frames
    for cut in cuts:
        diff1[cut] = CUT_DIFF
        hist[cut] = 0.3
        diff2[cut] = CUT_DIFF
        if cut + 1 < frames:
            diff2[cut + 1] = CUT_DIFF
    for flash in flashes:
        diff1[flash] = diff1[flash + 1] = 0.3
        hist[flash] = hist[flash + 1] = 0.3
        diff2[flash] = diff2[flash + 2] = 0.3
    brightness = luma or (lambda _i: 0.5)
    contrast = spread or (lambda _i: 0.2)
    # the long-gap difference follows from the steps and the cuts inside the window
    long_diff = [
        min(0.5, sum(diff1[max(1, i - long_gap + 1) : i + 1])) if i >= long_gap else 0.0
        for i in range(frames)
    ]
    return ShotSignals(
        timebase=TIMEBASE,
        pts=tuple(i * TICKS for i in range(frames)),
        end_pts=frames * TICKS,
        estimated_timestamps=0,
        long_gap=long_gap,
        luma_mean=tuple(brightness(i) for i in range(frames)),
        luma_std=tuple(contrast(i) for i in range(frames)),
        diff1=tuple(diff1),
        diff2=tuple(diff2),
        diff_long=tuple(long_diff),
        hist1=tuple(hist),
    )


def motion_signals(
    pairs: int = 20,
    *,
    first: int = 0,
    gap: int = 2,
    tx: Callable[[int], float] = lambda _k: 0.0,
    ty: Callable[[int], float] = lambda _k: 0.0,
    scale: Callable[[int], float] = lambda _k: 0.0,
    residual: Callable[[int], float] = lambda _k: 0.001,
    confidence: Callable[[int], float] = lambda _k: 0.9,
) -> MotionSignals:
    """``pairs`` estimates between sample frames ``gap`` frames apart, starting at ``first``."""
    later = tuple(first + (k + 1) * gap for k in range(pairs))
    earlier = tuple(first + k * gap for k in range(pairs))
    return MotionSignals(
        stride=gap,
        frames=later,
        prev_frames=earlier,
        tx=tuple(tx(k) for k in range(pairs)),
        ty=tuple(ty(k) for k in range(pairs)),
        log_scale=tuple(scale(k) for k in range(pairs)),
        rotation=(0.0,) * pairs,
        residual=tuple(residual(k) for k in range(pairs)),
        confidence=tuple(confidence(k) for k in range(pairs)),
    )


def quality_signals(
    frames: Sequence[int],
    *,
    p1: float = 0.05,
    p50: float = 0.5,
    p99: float = 0.95,
    clipped: float = 0.0,
    crushed: float = 0.0,
    sharpness: float = 0.5,
    noise: float = 0.005,
) -> QualitySignals:
    n = len(frames)
    return QualitySignals(
        stride=2,
        frames=tuple(frames),
        luma_p1=(p1,) * n,
        luma_p50=(p50,) * n,
        luma_p99=(p99,) * n,
        clipped=(clipped,) * n,
        crushed=(crushed,) * n,
        sharpness=(sharpness,) * n,
        noise_sigma=(noise,) * n,
    )


def source(**changes: object) -> SourceInfo:
    base = SourceInfo(
        width=320,
        height=180,
        frame_rate=Rational(30, 1),
        duration_seconds=4.0,
        declared_frame_count=120,
        rotation=0,
        variable_frame_rate=False,
        color_transfer="bt709",
        color_range="tv",
    )
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


ORIGINAL = ProcessingHistory(MeasuredOn.ORIGINAL, Stabilization.NO)
IMPROVED = ProcessingHistory(MeasuredOn.IMPROVED, Stabilization.NO, ("color",), "parent")


def derived(
    timeline: ShotSignals,
    motion: MotionSignals | None = None,
    quality: QualitySignals | None = None,
    *,
    signals: Mapping[AnalyzerId, object] | None = None,
    profile: ProcessingProfile | None = None,
    info: SourceInfo | None = None,
    history: ProcessingHistory = ORIGINAL,
) -> Derived:
    """Derive from hand-made signals: motion and quality by name, anything else in ``signals``."""
    available: dict[AnalyzerId, object] = dict(signals or {})
    if motion is not None:
        available[AnalyzerId.MOTION] = motion
    if quality is not None:
        available[AnalyzerId.QUALITY] = quality
    return derive(
        timeline, available, profile or get_profile("standard"), info or source(), history
    )


def analysis(
    timeline: ShotSignals,
    motion: MotionSignals | None = None,
    quality: QualitySignals | None = None,
    *,
    signals: Mapping[AnalyzerId, object] | None = None,
    history: ProcessingHistory = ORIGINAL,
) -> VideoAnalysis:
    """A complete, valid analysis assembled from signals."""
    profile = get_profile("standard")
    result = derived(timeline, motion, quality, signals=signals, profile=profile, history=history)
    return VideoAnalysis(
        asset_id="asset-1",
        asset_checksum="abc",
        source=source(),
        history=history,
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            processing_version=PROCESSING_VERSION,
            profile_name=profile.name,
            profile_version=profile.version,
            analyzer_versions={"shots": 1},
            derivation_versions={"shots": 1},
            engines={"numpy": "x"},
            device_requested="auto",
            device_used="cpu",
            rubric_version=1,
            vocabulary_version=1,
        ),
        inputs_used=(InputUse("media_inspection", InputSource.REUSED, "inspection-1", "v1"),),
        analyzers=(
            AnalyzerReport(
                analyzer=AnalyzerId.SHOTS,
                version=1,
                cost=CostTier.CHEAP,
                state=AnalyzerState.OK,
                cache=CacheOutcome.COMPUTED,
                depends_on=(),
            ),
        ),
        shots=result.shots,
        curves=result.curves,
        warnings=result.warnings,
        scenes=result.scenes,
        tracks=result.tracks,
        identities=result.identities,
        retakes=result.retakes,
        overlays=result.overlays,
        events=result.events,
        embeddings=result.embeddings,
    )


# --- model-based signals -----------------------------------------------------------------------
def person(
    frame: int,
    box: tuple[float, float, float, float] = (0.3, 0.1, 0.7, 0.9),
    confidence: float = 0.9,
    label: str = "person",
    kind: EntityKind = EntityKind.PERSON,
) -> DetectionRow:
    return DetectionRow(frame, kind, label, confidence, BBox(*box))


def detections(frames: Sequence[int], rows: Sequence[DetectionRow]) -> DetectionSignals:
    return DetectionSignals(model="fake-detector", frames=tuple(frames), rows=tuple(rows))


def face(frame: int, **overrides: float) -> FaceRow:
    """A face looking into the camera, eyes open, neutral mouth."""
    values: dict[str, float] = {
        "confidence": 0.95,
        "yaw": 0.0,
        "pitch": 0.0,
        "roll": 0.0,
        "gaze_x": 0.0,
        "gaze_y": 0.0,
        "eye_open_left": 0.95,
        "eye_open_right": 0.95,
        "smile": 0.1,
        "mouth_open": 0.05,
        "sharpness": 0.4,
    }
    values.update(overrides)
    return FaceRow(frame=frame, box=BBox(0.4, 0.15, 0.6, 0.45), **values)


def faces(frames: Sequence[int], rows: Sequence[FaceRow]) -> FaceSignals:
    return FaceSignals(model="fake-faces", frames=tuple(frames), rows=tuple(rows))


DIM = 32


def basis(index: int) -> tuple[float, ...]:
    """The ``index``-th unit vector: orthogonal to every other, so cosine is exactly 0 or 1."""
    return tuple(1.0 if i == index else 0.0 for i in range(DIM))


def embeddings(frames: Sequence[int], vectors: Sequence[tuple[float, ...]]) -> EmbeddingSignals:
    """Frame embeddings plus the real vocabulary, each label an orthogonal unit vector."""
    names = tuple(name for name, _ in label_prompts())
    return EmbeddingSignals(
        model="fake-clip",
        dim=DIM,
        vocabulary_version=1,
        frames=tuple(frames),
        vectors=tuple(vectors),
        label_names=names,
        label_vectors=tuple(basis(i) for i in range(len(names))),
    )


def label_vector(name: str) -> tuple[float, ...]:
    """The embedding of a vocabulary label (``content:talking_head``) in the fake model."""
    names = [n for n, _ in label_prompts()]
    return basis(names.index(name))
