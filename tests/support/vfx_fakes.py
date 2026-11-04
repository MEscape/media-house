"""Builders for VFX engine tests: shots, facts, synthetic footage and its stored analysis."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from media_house.core.domain import Rational
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.vfx_engine.domain.facts import (
    Box,
    FlashFact,
    ShotFact,
    SourceFacts,
    StyleLimits,
    TrackFact,
    TrackSample,
    VfxClip,
    VfxRequest,
    VfxShots,
    rat,
)
from media_house.modules.vfx_engine.domain.planner import PlanInputs
from media_house.modules.vfx_engine.domain.profile import VfxConfig
from media_house.modules.vfx_engine.domain.values import EffectType, RequestKind
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerState,
    EntityKind,
    Evidence,
    Track,
    TrackPoint,
    VideoAnalysis,
)
from media_house.modules.video_intelligence.domain.geometry import BBox
from tests.support import vi_signals as vs
from tests.support.media_factories import ffmpeg

ALL_EFFECTS = frozenset(EffectType) - {
    EffectType.BACKGROUND_BLUR,
    EffectType.BACKGROUND_REPLACEMENT,
    EffectType.SUBJECT_ISOLATION,
    EffectType.OBJECT_REMOVAL,
    EffectType.SCREEN_REPLACEMENT,
    EffectType.LENS_FLARE,
}


def q(x: float | str) -> Rational:
    return rat(Fraction(x).limit_denominator(100000) if isinstance(x, float) else Fraction(x))


def clip(
    clip_id: str = "c1",
    asset: str = "a1",
    start: float | str = 0,
    end: float | str = 4,
    at: float | str = 0,
    **kw: object,
) -> VfxClip:
    return VfxClip(
        clip_id=clip_id,
        asset_id=asset,
        source_start=q(start if isinstance(start, str) else float(start)),
        source_end=q(end if isinstance(end, str) else float(end)),
        timeline_start=q(at if isinstance(at, str) else float(at)),
        **kw,  # type: ignore[arg-type]
    )


def request(kind: RequestKind = RequestKind.EMPHASIS, at: float = 1.0, **kw: object) -> VfxRequest:
    return VfxRequest(
        request_id=f"r-{kind.value}-{at}",
        kind=kind,
        timeline_at=q(at),
        priority=0.8,
        **kw,  # type: ignore[arg-type]
    )


def shots(*clips: VfxClip, requests: Sequence[VfxRequest] = (), **kw: object) -> VfxShots:
    base = VfxShots(
        edit=None,
        project_id=None,
        frame_rate=Rational(30, 1),
        output_width=1920,
        output_height=1080,
        clips=clips or (clip(),),
        requests=tuple(requests),
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def moving_box(t: float) -> Box:
    """The synthetic subject: 96x96 px moving right at 80 px/s on a 640x360 picture."""
    x = (100 + 80 * t) / 640
    return Box(x0=x, y0=120 / 360, x1=x + 96 / 640, y1=216 / 360)


def face_track(
    track_id: str = "f1",
    seconds: float = 4.0,
    step: float = 0.1,
    box: Callable[[float], Box] = moving_box,
    confidence: Callable[[float], float] = lambda _t: 0.9,
    kind: str = "face",
) -> TrackFact:
    times = [round(i * step, 6) for i in range(int(seconds / step) + 1)]
    return TrackFact(
        track_id=track_id,
        kind=kind,
        shot_id="s0",
        confidence=0.9,
        samples=tuple(
            TrackSample(source_time=q(t), box=box(t), confidence=confidence(t)) for t in times
        ),
    )


def facts(
    asset: str = "a1",
    *,
    width: int = 1920,
    height: int = 1080,
    shots_: Sequence[ShotFact] = (),
    tracks: Sequence[TrackFact] = (),
    flashes: Sequence[tuple[float, float]] = (),
    analysed: bool = True,
) -> SourceFacts:
    return SourceFacts(
        asset_id=asset,
        width=width,
        height=height,
        frame_rate=Rational(30, 1),
        analysed=analysed,
        analysis_id="va-1",
        shots=tuple(shots_) or (ShotFact(shot_id="s0", source_start=q(0.0), source_end=q(60.0)),),
        tracks=tuple(tracks),
        flashes=tuple(
            FlashFact(event_id=f"fl{i}", source_time=q(t), strength=s, shot_id="s0")
            for i, (t, s) in enumerate(flashes)
        ),
    )


def inputs(
    the_shots: VfxShots,
    *sources: SourceFacts,
    style: StyleLimits | None = None,
    config: VfxConfig | None = None,
    available: frozenset[EffectType] = ALL_EFFECTS,
) -> PlanInputs:
    return PlanInputs(
        shots=the_shots,
        sources={s.asset_id: s for s in sources},
        style=style,
        available=available,
        config=config or VfxConfig(),
    )


def style(ceiling: int = 2) -> StyleLimits:
    return StyleLimits(
        document_id="style-doc",
        fingerprint="f" * 64,
        identity_version=1,
        profile="generic",
        intensity_ceiling=ceiling,
        motion_character="punchy",
        palette=(("fire_glow", "#F3BC19"),),
    )


# --- real footage -----------------------------------------------------------------------------
def make_subject_video(
    path: Path, *, seconds: int = 4, flash_at: float | None = 2.0, fps: int = 30
) -> Path:
    """640x360 8-bit H.264: a detailed background, a detailed 96 px "subject" moving right at
    80 px/s (``moving_box``) and, optionally, a 3-frame white burst at ``flash_at``."""
    flash = (
        f",drawbox=x=220:y=40:w=200:h=120:c=white@0.9:t=fill:"
        f"enable='between(t,{flash_at},{flash_at + 0.09})'"
        if flash_at is not None
        else ""
    )
    graph = (
        f"testsrc2=s=640x360:r={fps}:d={seconds}[bg];"
        f"testsrc=s=96x96:r={fps}:d={seconds},hue=s=0[fg];"
        f"[bg][fg]overlay=x='100+t*80':y=120{flash},format=yuv420p[v]"
    )
    ffmpeg(
        "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-crf", "16",
        "-preset", "veryfast", "-r", str(fps), str(path),
    )  # fmt: skip
    return path


def subject_analysis(
    asset: MediaAssetDto,
    *,
    seconds: int = 4,
    flashes: Sequence[int] = (60,),
    with_track: bool = True,
    width: int = 640,
    height: int = 360,
) -> VideoAnalysis:
    """A stored-shape analysis of ``make_subject_video``: one shot, the bursts, a face track."""
    frames = seconds * 30
    base = vs.analysis(vs.shot_signals(frames, flashes=tuple(flashes)))
    tracks: tuple[Track, ...] = ()
    if with_track:
        start = base.shots[0].range.start
        timebase = start.timebase

        def at(t: float) -> object:
            return replace(start, frame=round(t * 30), pts=round(t / timebase.value))

        points = tuple(
            TrackPoint(
                time=at(i / 10),  # type: ignore[arg-type]
                box=BBox(*(lambda b: (b.x0, b.y0, b.x1, b.y1))(moving_box(i / 10))),
                confidence=0.9,
            )
            for i in range(seconds * 10)
        )
        tracks = (
            Track(
                state=AnalyzerState.OK,
                confidence=0.9,
                evidence=(Evidence(frames=(points[0].time,), metric="track"),),
                track_id="face1",
                kind=EntityKind.FACE,
                label="face",
                shot_id=base.shots[0].shot_id,
                first=points[0].time,
                last=points[-1].time,
                points=points,
                occluded_frames=0,
            ),
        )
    return replace(
        base,
        asset_id=asset.id,
        asset_checksum=asset.checksum,
        source=replace(
            base.source,
            width=width,
            height=height,
            duration_seconds=float(seconds),
            declared_frame_count=frames,
        ),
        tracks=tracks,
    )
