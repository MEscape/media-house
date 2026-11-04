"""Anti-corruption layer: upstream results (read through their owners' contracts) -> the
transition engine's own facts. Nothing is measured here; values are only copied and named."""

from fractions import Fraction

from media_house.modules.audio_intelligence.application.contracts import (
    AudioIntelligenceTimeline,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    ContinuityAnalysis,
    Decision,
    Owner,
    RepairKind,
)
from media_house.modules.media_inspection.application.contracts import (
    FrameRateMode,
    InspectionResult,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.pacing_intelligence.application.contracts import (
    PacingAnalysis,
    RecommendationType,
)
from media_house.modules.transition_engine.domain.facts import (
    ContinuityFact,
    EdgeFact,
    PacingCue,
    SourceFacts,
    StyleLimits,
    WordSpan,
    fr,
)
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalysis,
    edge_state,
)
from media_house.modules.visual_style_engine.application.contracts import ResolvedStyle

_PACING_CUES = (RecommendationType.ADD_SECTION_BREATH, RecommendationType.HOLD_REVEAL)


def _exact(seconds: float) -> Fraction:
    """A float time from a probe or a transcript as an exact microsecond value."""
    return Fraction(round(seconds * 1_000_000), 1_000_000)


def source_facts(
    asset_id: str,
    inspection: InspectionResult | None,
    asset: MediaAssetDto | None,
    audio: AudioIntelligenceTimeline | None,
) -> SourceFacts:
    """Length, rate and words of one recording: the stored inspection first (no re-probe),
    else the library's own duration, else unknown."""
    start, duration, basis = Fraction(0), None, "unknown"
    frame_rate: Fraction | None = None
    vfr: bool | None = None
    sample_rate: int | None = None
    if inspection is not None:
        media = inspection.inspection
        container = media.container
        if container.duration is not None:
            duration, basis = _exact(container.duration), "inspection"
            start = _exact(container.start_time or 0.0)
        video = media.primary_video
        if video is not None:
            if video.frame_rate.value is not None:
                frame_rate = fr(video.frame_rate.value)
            mode = video.timing.mode if video.timing is not None else FrameRateMode.UNKNOWN
            vfr = None if mode is FrameRateMode.UNKNOWN else mode is FrameRateMode.VARIABLE
        stream = media.audio_stream(0)
        sample_rate = stream.sample_rate if stream is not None else None
    if duration is None and asset is not None and asset.duration_seconds is not None:
        duration, basis = _exact(asset.duration_seconds), "library"
    words: tuple[WordSpan, ...] = ()
    if audio is not None:
        words = tuple(
            WordSpan(start=_exact(w.word.start), end=_exact(w.word.end))
            for w in audio.words
            if not w.suspect and w.word.end > w.word.start
        )
    return SourceFacts(
        asset_id=asset_id,
        start=start,
        duration=duration,
        duration_basis=basis,
        frame_rate=frame_rate,
        variable_frame_rate=vfr,
        sample_rate=sample_rate,
        words=words,
        words_known=audio is not None,
    )


def edge_fact(video: VideoAnalysis, seconds: float) -> EdgeFact | None:
    """The picture at one side of a cut (``None`` when no analysed shot holds the moment)."""
    state = edge_state(video, seconds)
    if state is None:
        return None
    shot = next((s for s in video.shots if s.shot_id == state.shot_id), None)
    camera = shot.camera if shot is not None and shot.camera.ok else None
    subject = state.subject
    return EdgeFact(
        shot_id=state.shot_id,
        scene_id=state.scene_id,
        environment=state.environment,
        indoor_outdoor=state.indoor_outdoor,
        camera_movement=state.camera_movement,
        camera_direction=camera.direction_degrees if camera is not None else None,
        camera_speed=camera.speed if camera is not None else None,
        subject_cx=subject.cx if subject is not None else None,
        subject_cy=subject.cy if subject is not None else None,
        subject_vx=subject.vx if subject is not None else None,
        subject_vy=subject.vy if subject is not None else None,
        has_text=bool(state.texts),
        luma=state.luma,
    )


def continuity_facts(analysis: ContinuityAnalysis) -> dict[tuple[str, str], ContinuityFact]:
    """continuity's judgement per cut, keyed by the clip ids of both sides (the pipeline maps
    the same placement ids into both sequences)."""
    pairs = {p.transition_id: p.compatibility for p in analysis.compatible_pairs}
    out: dict[tuple[str, str], ContinuityFact] = {}
    for t in analysis.transitions:
        issues = [
            i for i in analysis.issues_at(t.transition_id) if i.decision is not Decision.REJECTED
        ]
        out[(t.outgoing_clip, t.incoming_clip)] = ContinuityFact(
            outgoing_clip=t.outgoing_clip,
            incoming_clip=t.incoming_clip,
            issues=tuple(sorted(f"{i.type.value}:{i.level.value}" for i in issues)),
            audio_crossfade=any(
                r.kind is RepairKind.AUDIO_CROSSFADE and r.owner is Owner.TRANSITION
                for i in issues
                for r in i.repairs
            ),
            compatibility=pairs.get(t.transition_id),
        )
    return out


def pacing_cues(analysis: PacingAnalysis) -> tuple[PacingCue, ...]:
    return tuple(
        PacingCue(
            kind=r.type.value,
            asset_id=r.target.asset_id,
            start=fr(r.target.start),
            end=fr(r.target.end),
        )
        for r in analysis.recommendations
        if r.type in _PACING_CUES
    )


def style_limits(style: ResolvedStyle, document_id: str | None) -> StyleLimits:
    base = style.tokens.motion.intensity_ceiling
    t = style.tokens.transitions  # None under identity v1: the profile's vocabulary alone
    return StyleLimits(
        approved=t.approved if t else (),
        prohibited=t.prohibited if t else (),
        bookend_only=t.bookend_only if t else (),
        document_id=document_id,
        fingerprint=style.fingerprint,
        identity_version=style.identity_version,
        profile=style.profile,
        intensity_ceiling=base,
        section_ceilings=tuple(
            (s.section_id, s.tokens.motion.intensity_ceiling)
            for s in style.sections
            if s.tokens.motion.intensity_ceiling != base
        ),
        end_screen_transition=style.tokens.end_screen.entry_transition,
        membership_transition=style.tokens.end_screen.membership_transition,
        move_easing=style.tokens.motion.move_easing,
        width=style.output.width,
        height=style.output.height,
    )
