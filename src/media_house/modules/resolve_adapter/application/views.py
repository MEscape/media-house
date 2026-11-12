"""The built timeline, read as the facts the adapter executes.

This is the only place that knows both the builder's ``BuiltTimeline`` and the adapter's own
``TimelineFacts`` (the domain imports no other module). It derives frames in the FILE's own rate
for the part each clip plays, and nothing else: no creative value is chosen here.
"""

from collections.abc import Mapping
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.resolve_adapter.domain.facts import (
    BlurKey,
    ClipFact,
    CropFact,
    CueFact,
    FontFact,
    IdentityFact,
    MarkerFact,
    MediaFact,
    RetimeFact,
    SettingsFact,
    SubtitleFact,
    TimelineFacts,
    TrackFact,
    TransitionFact,
    ZoomKey,
)
from media_house.modules.timeline_builder.application.contracts import (
    AudioClip,
    BuiltTimeline,
    ClipState,
    CropPlan,
    Execution,
    SourceMedia,
    TimeMap,
    VideoClip,
)


def _fr(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def to_facts(
    timeline: BuiltTimeline, *, name: str, audio_gains: Mapping[str, float] | None = None
) -> TimelineFacts:
    """``audio_gains``: the static volume (dB) the mix gives each sound clip, by clip id."""
    s = timeline.settings
    gains = audio_gains or {}
    fps = _fr(s.frame_rate)
    sources = {m.source_id: m for m in timeline.sources}
    roles: dict[str, set[str]] = {}
    clips: list[ClipFact] = []
    for track in timeline.tracks:
        for v in track.video:
            clips.append(_video(v, fps, sources))
            if v.source is not None:
                roles.setdefault(v.source.source_id, set()).add(track.role.value)
        for a in track.audio:
            clips.append(_audio(a, fps, sources, gains.get(a.clip_id)))
            if a.source is not None:
                roles.setdefault(a.source.source_id, set()).add(track.role.value)
    identity = timeline.identity
    return TimelineFacts(
        timeline_id=timeline.timeline_id,
        anchor_asset_id=timeline.anchor_asset_id,
        project_id=timeline.project_id,
        name=name,
        schema_version=timeline.schema_version,
        build_version=timeline.build.build_version,
        build_hash=timeline.build.build_hash,
        settings=SettingsFact(
            width=s.width,
            height=s.height,
            frame_rate=s.frame_rate,
            sample_rate=s.sample_rate,
            audio_channels=s.audio_channels,
            duration_frames=s.duration_frames,
            color_profile=s.color.profile,
            fit=s.fit_mode.value,
        ),
        identity=IdentityFact(
            identity_id=identity.identity_id,
            identity_version=identity.identity_version,
            style_fingerprint=identity.style_fingerprint,
            format_profile=identity.format_profile,
            fonts=tuple(FontFact(f.family, f.available) for f in identity.fonts),
            presets=identity.presets,
        ),
        media=tuple(_media(m, roles.get(m.source_id, set())) for m in timeline.sources),
        tracks=tuple(
            TrackFact(t.track_id, t.kind.value, t.role.value, t.index, t.lane, t.name, t.enabled)
            for t in timeline.tracks
        ),
        clips=tuple(clips),
        transitions=tuple(
            TransitionFact(
                transition_id=t.transition_id,
                track_id=t.track_id,
                kind=t.kind,
                mode=t.mode.value,
                cut_frame=t.cut_frame,
                start_frame=t.start_frame,
                end_frame=t.end_frame,
                duration_frames=t.duration_frames,
                from_clip=t.from_clip,
                to_clip=t.to_clip,
                tail_frames=t.tail_frames,
                head_frames=t.head_frames,
                alignment=t.alignment,
                preset_id=t.preset_id,
                supported=t.supported,
                reasons=t.reasons,
            )
            for t in timeline.transitions
        ),
        markers=tuple(
            MarkerFact(
                m.marker_id,
                m.kind,
                m.frame,
                m.duration_frames,
                m.name,
                m.note,
                m.severity.value if m.severity else None,
            )
            for m in timeline.markers
        ),
        subtitles=(
            SubtitleFact(
                language=timeline.subtitles.language,
                cues=tuple(
                    CueFact(c.index, c.start_frame, c.end_frame, c.text)
                    for c in timeline.subtitles.cues
                ),
                srt=timeline.subtitles.srt,
            )
            if timeline.subtitles is not None and timeline.subtitles.cues
            else None
        ),
        unresolved=tuple(d.reference for d in timeline.unresolved()),
    )


def _media(m: SourceMedia, roles: set[str]) -> MediaFact:
    return MediaFact(
        asset_id=m.source_id,
        name=m.name,
        media_type=m.media_type,
        relation=m.relation,
        original_asset_id=m.original_asset_id,
        frame_rate=m.frame_rate,
        width=m.width,
        height=m.height,
        proxy_asset_id=m.proxy_asset_id,
        checksum=m.checksum,
        available=m.available,
        roles=tuple(sorted(roles)),
    )


def _file_fps(
    asset_id: str, timeline_fps: Fraction, sources: Mapping[str, SourceMedia]
) -> Fraction:
    media = sources.get(asset_id)
    if media is not None and media.frame_rate is not None:
        return _fr(media.frame_rate)
    return timeline_fps


def _video(v: VideoClip, fps: Fraction, sources: Mapping[str, SourceMedia]) -> ClipFact:
    source = v.source
    asset = source.source_id if source else None
    in_frame, played = 0, v.frames
    if source is not None:
        file_fps = _file_fps(source.source_id, fps, sources)
        in_frame = (
            source.source_in_frame
            if source.source_in_frame is not None
            else round(_fr(source.source_in) * file_fps)
        )
        played = max(
            1, round(_fr(source.source_out) * file_fps) - round(_fr(source.source_in) * file_fps)
        )
        if v.speed == Rational(1, 1) and v.time_map is None:
            # at normal speed the clip fills its slot: in and out rounded apart can leave the
            # slot one frame short, which plays as a black frame
            played = max(1, round(Fraction(v.end_frame - v.start_frame) * file_fps / fps))
    crop = _crop(v)
    return ClipFact(
        clip_id=v.clip_id,
        track_id=v.track_id,
        kind="video",
        clip_kind=v.kind.value,
        name=v.name,
        asset_id=asset,
        start_frame=v.start_frame,
        end_frame=v.end_frame,
        source_in_frame=in_frame,
        source_frames=played,
        opacity=v.opacity,
        enabled=v.enabled,
        fit=v.transform.fit.value if v.transform else "contain",
        crop=crop,
        retime=_retime(v.time_map, v.speed),
        blur=_blur(v),
        native_effects=tuple(
            e.effect_type for e in v.effects if e.applied and e.execution is Execution.NATIVE
        ),
        mix_work=False,
        link=v.audio_link,
        provenance=v.provenance.value,
        digest=v.source_digest,
        resolved=v.state is ClipState.RESOLVED,
    )


def _audio(
    a: AudioClip, fps: Fraction, sources: Mapping[str, SourceMedia], gain_db: float | None = None
) -> ClipFact:
    source = a.source
    asset = source.source_id if source else None
    start = round(_fr(a.timeline_start) * fps)
    end = max(start + 1, round(_fr(a.timeline_end) * fps))
    in_frame, played = 0, end - start
    if source is not None:
        file_fps = _file_fps(source.source_id, fps, sources)
        in_frame = round(_fr(source.source_in) * file_fps)
        played = max(1, round(_fr(source.source_out) * file_fps) - in_frame)
    return ClipFact(
        clip_id=a.clip_id,
        track_id=a.track_id,
        kind="audio",
        clip_kind="sound",
        name=a.name,
        asset_id=asset,
        start_frame=start,
        end_frame=end,
        source_in_frame=in_frame,
        source_frames=played,
        opacity=1.0,
        enabled=a.enabled,
        fit="contain",
        crop=None,
        retime=(
            RetimeFact("constant", True, f"{a.speed.numerator}/{a.speed.denominator}x")
            if a.speed != Rational(1, 1)
            else None
        ),
        native_effects=(),
        mix_work=a.gain_db != 0.0 or a.fade_in.numerator > 0 or a.fade_out.numerator > 0,
        link=a.video_link,
        provenance=a.provenance.value,
        digest=a.source_digest,
        resolved=a.state is ClipState.RESOLVED,
        gain_db=gain_db,
    )


def _crop(v: VideoClip) -> CropFact | None:
    plans = [p for p in v.crops if p.keys]
    # a camera move works on the source picture (punch-ins, beats); a window reshapes it
    camera = next((p for p in plans if p.target == "source"), None)
    window = next((p for p in plans if p is not camera), None)
    plan = window or camera
    if plan is None:
        return None
    if plan.strategy == "screen_card":
        return _card(v, plan)
    middle = (v.start_frame + v.end_frame) // 2
    if camera is not None and window is not None:
        # a reframed format: the window is held, the camera move zooms inside it
        return CropFact(*_window_at(window, middle), True, len(camera.keys), _motion(v, camera))
    first = plan.keys[0]
    moves = any((k.x, k.y, k.w, k.h) != (first.x, first.y, first.w, first.h) for k in plan.keys[1:])
    held = _window_at(plan, middle)
    return CropFact(*held, moves, len(plan.keys), _motion(v, plan) if moves else ())


def _motion(v: VideoClip, plan: CropPlan) -> tuple[ZoomKey, ...]:
    return tuple(
        ZoomKey(
            frame=max(0, k.timeline_frame - v.start_frame),
            size=1.0 / max(k.w, k.h),
            pivot_x=_pivot(k.x, k.w),
            pivot_y=_pivot(k.y, k.h),
            curve=k.interpolation or "linear",
        )
        for k in plan.keys
        if k.timeline_frame is not None
    )


def _window_at(plan: CropPlan, frame: int) -> tuple[float, float, float, float]:
    """The window at a timeline frame (linear between keys): the analysis keys the whole take,
    so the first key may lie long before this clip."""
    timed = sorted(
        ((k.timeline_frame, k) for k in plan.keys if k.timeline_frame is not None),
        key=lambda pair: pair[0],
    )
    if not timed:
        k = plan.keys[0]
        return k.x, k.y, k.w, k.h
    before = [(f, k) for f, k in timed if f <= frame]
    after = [(f, k) for f, k in timed if f > frame]
    if not before or not after:
        k = (before or after)[-1 if before else 0][1]
        return k.x, k.y, k.w, k.h
    (fa, a), (fb, b) = before[-1], after[0]
    t = (frame - fa) / (fb - fa)
    return tuple(  # type: ignore[return-value]
        p + (q - p) * t for p, q in zip((a.x, a.y, a.w, a.h), (b.x, b.y, b.w, b.h), strict=True)
    )


def _card(v: VideoClip, plan: CropPlan) -> CropFact:
    """A floating page: the window stays, the card grows by the push named in the analysis
    (``screen_card:<push>``) from the first key to the last."""
    first, last = plan.keys[0], plan.keys[-1]
    try:
        push = float(plan.analysis_id.rpartition(":")[2])
    except ValueError:
        push = 1.0
    frames = [
        max(0, k.timeline_frame - v.start_frame)
        for k in (first, last)
        if k.timeline_frame is not None
    ]
    motion = (
        (
            ZoomKey(frame=frames[0], size=1.0, pivot_x=0.5, pivot_y=0.5, curve="hold"),
            ZoomKey(frame=frames[1], size=push, pivot_x=0.5, pivot_y=0.5, curve=last.interpolation),
        )
        if len(frames) == 2 and frames[1] > frames[0]
        else (ZoomKey(frame=0, size=1.0, pivot_x=0.5, pivot_y=0.5, curve="hold"),)
    )
    window = (first.x, first.y, first.w, first.h)
    return CropFact(*window, True, len(plan.keys), motion, window)


def _blur(v: VideoClip) -> tuple[BlurKey, ...]:
    return tuple(
        BlurKey(frame=max(0, k.timeline_frame - v.start_frame), size=k.size, curve=k.interpolation)
        for k in v.blur
        if k.timeline_frame is not None
    )


def _pivot(start: float, side: float) -> float:
    """The point a window of ``side`` starting at ``start`` grows from (zoom about it)."""
    return 0.5 if side >= 1.0 else min(max(start / (1.0 - side), 0.0), 1.0)


def _retime(time_map: TimeMap | None, speed: Rational) -> RetimeFact | None:
    if time_map is None or not time_map.segments:
        if speed != Rational(1, 1):
            return RetimeFact("constant", True, f"{speed.numerator}/{speed.denominator}x")
        return None
    rates = {s.rate for s in time_map.segments}
    if rates == {Rational(1, 1)}:
        return None
    if time_map.has_reverse:
        kind = "reverse"
    elif Rational(0, 1) in rates:
        kind = "freeze"
    elif len({r for r in rates if r.numerator > 0}) > 1:
        kind = "ramp"
    else:
        kind = "constant"
    detail = ", ".join(sorted(f"{r.numerator}/{r.denominator}x" for r in rates))
    return RetimeFact(kind=kind, applied_by_application=time_map.applied, detail=detail)
