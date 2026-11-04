"""Anti-corruption: upstream contract DTOs -> the VFX engine's own facts.

The only place that knows the shapes of the video analysis, the reframing analysis, the stored
inspection and the resolved style. Times become exact source seconds (``pts x time base``).
"""

from dataclasses import replace
from fractions import Fraction
from math import gcd

from media_house.core.domain import FrameTime, Rational
from media_house.modules.audio_intelligence.application.contracts import (
    IMPULSE,
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentDocument
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.reframing_intelligence.application.contracts import ReframingAnalysis
from media_house.modules.vfx_engine.application.ports import SourceMedia
from media_house.modules.vfx_engine.domain.facts import (
    BangFact,
    Box,
    CropFact,
    FlashFact,
    ShotFact,
    SourceFacts,
    StyleLimits,
    TrackFact,
    TrackSample,
    fr,
    rat,
)
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerState,
    EventKind,
    VideoAnalysis,
)
from media_house.modules.visual_style_engine.application.contracts import ResolvedStyle


def seconds(time: FrameTime) -> Fraction:
    base = time.timebase
    return Fraction(time.pts) * Fraction(base.numerator, base.denominator)


def _r(value: Fraction) -> Rational:
    return rat(max(value, Fraction(0)))


def aspect_name(width: int, height: int) -> str:
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def source_facts(
    asset_id: str,
    video: VideoAnalysis | None,
    video_id: str | None,
    inspection: InspectionResult | None,
    reframing: ReframingAnalysis | None,
    reframing_id: str | None,
) -> SourceFacts | None:
    """``None`` when nothing at all is known about the recording."""
    width = height = 0
    rate: Rational | None = None
    vfr: bool | None = None
    depth: int | None = None
    pix: str | None = None
    if inspection is not None:
        stream = inspection.inspection.primary_video
        if stream is not None:
            width, height = stream.geometry.display_size
            rate = stream.frame_rate.value
            depth = stream.bit_depth.value
            pix = stream.pixel_format
            if stream.timing is not None:
                vfr = stream.timing.mode.value == "variable"
    if video is not None and (width <= 0 or height <= 0):
        width, height = video.source.width, video.source.height
        rate = rate or video.source.frame_rate
        vfr = video.source.variable_frame_rate if vfr is None else vfr
    if width <= 0 or height <= 0:
        return None
    facts = SourceFacts(
        asset_id=asset_id,
        width=width,
        height=height,
        frame_rate=rate,
        variable_frame_rate=vfr,
        bit_depth=depth,
        pixel_format=pix,
    )
    if video is not None:
        facts = _with_video(facts, video, video_id)
    if reframing is not None:
        crops = tuple(
            CropFact(
                recommendation_id=r.recommendation_id,
                source_start=r.start,
                source_end=r.end,
                keyframes=tuple(
                    (
                        k.time,
                        Box(
                            x0=k.crop.x, y0=k.crop.y, x1=k.crop.x + k.crop.w, y1=k.crop.y + k.crop.h
                        ),
                    )
                    for k in r.keyframes
                ),
            )
            for r in reframing.recommendations
            if r.asset_id == asset_id and r.executable and r.keyframes
        )

        facts = replace(facts, crops=crops, reframing_id=reframing_id)
    return facts


def _with_video(facts: SourceFacts, video: VideoAnalysis, video_id: str | None) -> SourceFacts:

    shots = []
    for s in video.shots:
        metrics = s.quality.metrics
        shots.append(
            ShotFact(
                shot_id=s.shot_id,
                source_start=_r(seconds(s.range.start)),
                source_end=_r(seconds(s.range.end)),
                luma_p50=metrics.luma_p50,
                luma_p99=metrics.luma_p99,
                clipped=metrics.clipped,
                crushed=metrics.crushed,
                noise_sigma=metrics.noise_sigma,
                sharpness=metrics.sharpness,
                camera_speed=s.camera.speed if s.camera.state is AnalyzerState.OK else None,
                motion_energy=s.motion.mean_energy,
                main_subject_track_id=s.entities.main_subject_track_id,
            )
        )
    main = {s.entities.main_subject_track_id for s in video.shots} - {None}
    tracks = tuple(
        TrackFact(
            track_id=t.track_id,
            kind=t.kind.value,
            shot_id=t.shot_id,
            confidence=t.confidence if t.confidence is not None else 0.0,
            samples=tuple(
                TrackSample(
                    source_time=_r(seconds(p.time)),
                    box=Box(x0=p.box.x0, y0=p.box.y0, x1=p.box.x1, y1=p.box.y1),
                    confidence=p.confidence,
                )
                for p in sorted(t.points, key=lambda p: p.time.pts)
            ),
            occluded_frames=t.occluded_frames,
            is_main_subject=t.track_id in main,
        )
        for t in video.tracks
        if t.state is AnalyzerState.OK and t.points
    )
    flashes = tuple(
        FlashFact(
            event_id=e.event_id,
            source_time=_r(seconds(e.time)),
            strength=e.strength if e.strength is not None else (e.confidence or 0.0),
            shot_id=e.shot_id,
        )
        for e in video.events
        if e.kind is EventKind.FLASH
    )
    luma = tuple(
        (_r(Fraction(p) * Fraction(c.timebase.numerator, c.timebase.denominator)), v)
        for c in video.curves
        if c.name == "luma_median"
        for p, v in zip(c.pts, c.values, strict=True)
    )
    return replace(
        facts,
        analysed=True,
        analysis_id=video_id,
        shots=tuple(shots),
        tracks=tracks,
        flashes=flashes,
        luma=tuple(sorted(luma, key=lambda s: fr(s[0]))),
    )


def with_bangs(
    facts: SourceFacts, audio: AudioIntelligenceTimeline, alignment: AlignmentDocument | None
) -> SourceFacts:
    """The stored ``impulse`` events on the picture's clock: ``video = audio + audio correction
    - video correction`` (audio_visual_alignment, metadata-derived); without a stored alignment
    both clocks are taken as equal and the plan says so."""
    shift = Fraction(0)
    if alignment is not None:
        by_stream = {c.stream: c.seconds for c in alignment.sync.corrections}
        shift = _exact(by_stream.get("audio", 0.0)) - _exact(by_stream.get("video", 0.0))
    bangs = tuple(
        BangFact(
            event_id=f"impulse@{e.start:.3f}",
            source_time=_r(_exact(e.start) + shift),
            strength=e.strength,
            confidence=e.confidence,
        )
        for e in audio.events
        if e.kind == IMPULSE
    )
    return replace(
        facts, bangs=bangs, bang_clock="alignment" if alignment is not None else "assumed"
    )


def _exact(value: float) -> Fraction:
    """A float second as an exact microsecond fraction (upstream audio times are floats)."""
    return Fraction(round(value * 1_000_000), 1_000_000)


def source_media(inspection: InspectionResult | None) -> SourceMedia | None:
    if inspection is None:
        return None
    stream = inspection.inspection.primary_video
    rate = None if stream is None else stream.frame_rate.value
    if stream is None or rate is None or stream.pixel_format is None:
        return None
    width, height = stream.geometry.width, stream.geometry.height
    timecode = inspection.inspection.observed.timecode
    color = stream.color
    return SourceMedia(
        width=width,
        height=height,
        frame_rate=Fraction(rate.numerator, rate.denominator),
        pixel_format=stream.pixel_format,
        color_space=color.space.value,
        color_primaries=color.primaries.value,
        color_transfer=color.transfer.value,
        color_range=color.range.value,
        timecode=None if timecode is None else str(timecode.start),
        from_inspection=True,
    )


def style_limits(style: ResolvedStyle, document_id: str | None) -> StyleLimits:
    """Only what bounds footage effects: intensity, motion character, palette for a tint."""
    section = style.tokens
    return StyleLimits(
        document_id=document_id,
        fingerprint=style.fingerprint,
        identity_version=style.identity_version,
        profile=style.profile,
        intensity_ceiling=section.motion.intensity_ceiling,
        motion_character=section.motion.character,
        palette=tuple((c.name, c.hex) for c in style.palette()),
    )
