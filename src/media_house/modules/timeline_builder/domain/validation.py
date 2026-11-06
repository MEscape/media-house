"""Validation of the timeline that was actually built (not of the specification).

The compiler judged its specification. These checks look at the finished elements: structure
(ids, tracks, references), timing (ranges, frames, samples, overlaps, gaps), media (sources and
derivatives), the picture (crops, zones, layering), the sound (sync, fades), the creator identity
and what the model or a later application cannot carry out. A reused element is judged exactly
like a new one, because findings are made here and never during construction.

Checks that need a rendered preview or a media analysis (black frames, missing frames, silence,
sync of the rendered result) are NOT made here: ``Validation.not_run`` says so until an
``ExternalCheck`` reports them.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from fractions import Fraction
from itertools import pairwise

from media_house.modules.timeline_builder.domain.facts import SpecFacts
from media_house.modules.timeline_builder.domain.model import (
    AudioClip,
    BuiltTimeline,
    ExternalCheck,
    Issue,
    Validation,
    VideoClip,
)
from media_house.modules.timeline_builder.domain.profile import BuilderConfig, Capabilities
from media_house.modules.timeline_builder.domain.timing import fr, frame_at, sample_at
from media_house.modules.timeline_builder.domain.values import (
    BlendMode,
    ChainStage,
    CheckKind,
    CheckState,
    ClipKind,
    ClipState,
    Execution,
    Role,
    Severity,
    TrackKind,
    TransitionMode,
)

#: Stacking order of the roles of a kind, bottom to top (what a track's index must follow).
_VIDEO_RANK = {Role.PRIMARY: 0, Role.COVER: 1, Role.GRAPHICS: 2, Role.CAPTIONS: 3, Role.EFFECTS: 4}
_CLIP_ROLE: dict[ClipKind, tuple[Role, ...]] = {
    ClipKind.PRIMARY: (Role.PRIMARY,),
    ClipKind.VFX: (Role.PRIMARY,),
    ClipKind.COVER: (Role.COVER,),
    ClipKind.GRAPHIC: (Role.GRAPHICS,),
    ClipKind.CAPTION: (Role.CAPTIONS,),
    ClipKind.TRANSITION_OVERLAY: (Role.EFFECTS,),
}
_MEDIA_CLIPS = frozenset({ClipKind.PRIMARY, ClipKind.VFX, ClipKind.COVER})
#: Source frame-rate differences smaller than this are not worth saying.
_RATE_TOLERANCE = Fraction(1, 1000)


class _Found:
    def __init__(self) -> None:
        self.items: list[Issue] = []

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        element_id: str | None = None,
        *,
        start: int | None = None,
        end: int | None = None,
        evidence: str = "",
        resolution: str = "",
    ) -> None:
        self.items.append(
            Issue(
                code=code,
                severity=severity,
                message=message,
                element_id=element_id,
                start_frame=start,
                end_frame=end,
                evidence=evidence,
                resolution=resolution,
            )
        )


type _Check = Callable[[BuiltTimeline, BuilderConfig, Capabilities, _Found], None]


def validate_structure(
    t: BuiltTimeline, config: BuilderConfig, caps: Capabilities
) -> tuple[list[Issue], tuple[str, ...]]:
    """Every check that needs only the built timeline itself."""
    found = _Found()
    names: list[str] = []
    for name, check in _CHECKS:
        check(t, config, caps, found)
        names.append(name)
    return found.items, tuple(names)


def validate_against_spec(t: BuiltTimeline, spec: SpecFacts) -> tuple[list[Issue], tuple[str, ...]]:
    """Output consistency: everything the specification asked for is in the timeline."""
    found = _Found()
    _consistency(t, spec, found)
    return found.items, ("output_consistency",)


def validation_of(
    issues: Iterable[Issue], checks_run: tuple[str, ...], external: tuple[ExternalCheck, ...]
) -> Validation:
    ran = {c.kind for c in external if c.state is not CheckState.NOT_RUN}
    return Validation(
        checks_run=checks_run,
        passed=not any(i.severity is Severity.ERROR for i in issues),
        external=external,
        not_run=tuple(k for k in CheckKind if k not in ran),
    )


def external_issues(external: tuple[ExternalCheck, ...]) -> list[Issue]:
    """A failed check of a rendered result is a finding; a passed or skipped one is not."""
    return [
        Issue(
            code=f"external.{c.kind.value}",
            severity=Severity.WARNING,
            message=f"{c.kind.value.replace('_', ' ')} check failed"
            + (f": {c.detail}" if c.detail else ""),
            element_id=c.element_id,
            start_frame=c.start_frame,
            end_frame=c.end_frame,
            evidence=c.tool,
            origin="external",
        )
        for c in external
        if c.state is CheckState.FAILED
    ]


# --- structure ------------------------------------------------------------------------------------


def _ids(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    seen: dict[str, int] = defaultdict(int)
    for element in t.element_ids():
        seen[element] += 1
    for element, n in sorted(seen.items()):
        if n > 1:
            out.add("duplicate_id", Severity.ERROR, f"the id {element} is used {n} times", element)


def _tracks(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    video = sorted((x for x in t.tracks if x.kind is TrackKind.VIDEO), key=lambda x: x.index)
    for lower, upper in pairwise(video):
        below, above = _VIDEO_RANK.get(lower.role), _VIDEO_RANK.get(upper.role)
        if below is not None and above is not None and below > above:
            out.add(
                "track_hierarchy",
                Severity.ERROR,
                f"{upper.track_id} ({upper.role.value}) is stacked below {lower.track_id} "
                f"({lower.role.value}): the layering would be wrong",
                upper.track_id,
            )
    for track in t.tracks:
        if (track.kind is TrackKind.VIDEO and track.audio) or (
            track.kind is TrackKind.AUDIO and track.video
        ):
            out.add(
                "invalid_track_assignment",
                Severity.ERROR,
                f"{track.track_id} is a {track.kind.value} track but holds the other kind",
                track.track_id,
            )
        for c in track.video:
            allowed = _CLIP_ROLE.get(c.kind)
            if allowed is not None and track.role not in allowed:
                out.add(
                    "invalid_track_assignment",
                    Severity.ERROR,
                    f"{c.kind.value} clip {c.clip_id} is on {track.track_id} ({track.role.value})",
                    c.clip_id,
                )
            if c.track_id != track.track_id:
                out.add(
                    "clip_track_mismatch",
                    Severity.ERROR,
                    f"{c.clip_id} names another track",
                    c.clip_id,
                )
        for a in track.audio:
            if a.role is not track.role:
                out.add(
                    "invalid_track_assignment",
                    Severity.ERROR,
                    f"{a.role.value} clip {a.clip_id} is on {track.track_id} ({track.role.value})",
                    a.clip_id,
                )
            if a.track_id != track.track_id:
                out.add(
                    "clip_track_mismatch",
                    Severity.ERROR,
                    f"{a.clip_id} names another track",
                    a.clip_id,
                )
    if caps.max_video_tracks is not None:
        n = sum(1 for x in t.tracks if x.kind is TrackKind.VIDEO)
        if n > caps.max_video_tracks:
            out.add(
                "too_many_video_tracks",
                Severity.UNSUPPORTED,
                f"the timeline needs {n} video tracks, the target application offers "
                f"{caps.max_video_tracks}",
            )
    if caps.max_audio_tracks is not None:
        n = sum(1 for x in t.tracks if x.kind is TrackKind.AUDIO)
        if n > caps.max_audio_tracks:
            out.add(
                "too_many_audio_tracks",
                Severity.UNSUPPORTED,
                f"the timeline needs {n} audio tracks, the target application offers "
                f"{caps.max_audio_tracks}",
            )


def _references(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    sources = {s.source_id for s in t.sources}
    video = {c.clip_id: c for c in t.video_clips()}
    audio = {c.clip_id: c for c in t.audio_clips()}
    everything = set(t.element_ids()) | set(video) | set(audio)
    both: list[VideoClip | AudioClip] = [*video.values(), *audio.values()]
    for c in both:
        if c.source is not None and c.source.source_id not in sources:
            out.add(
                "broken_source_reference",
                Severity.ERROR,
                f"{c.clip_id} points at {c.source.source_id}, which is not in the source table",
                c.clip_id,
            )
    for c in video.values():
        if c.audio_link is not None:
            partner = audio.get(c.audio_link)
            if partner is None:
                out.add(
                    "broken_link",
                    Severity.ERROR,
                    f"{c.clip_id} links to a missing sound",
                    c.clip_id,
                )
            elif partner.video_link != c.clip_id:
                out.add(
                    "broken_link",
                    Severity.ERROR,
                    f"{c.clip_id} and {partner.clip_id} are not linked both ways",
                    c.clip_id,
                )
        for ref in (c.transition_in, c.transition_out):
            if ref is not None and t.transition(ref) is None:
                out.add(
                    "broken_transition_reference",
                    Severity.ERROR,
                    f"{c.clip_id} names a missing transition {ref}",
                    c.clip_id,
                )
    for a in audio.values():
        if a.video_link is not None and a.video_link not in video:
            out.add(
                "broken_link", Severity.ERROR, f"{a.clip_id} links to a missing picture", a.clip_id
            )
        for m in a.mix_ids:
            if not any(x.mix_id == m for x in t.mix):
                out.add(
                    "broken_mix_reference",
                    Severity.ERROR,
                    f"{a.clip_id} names a missing mix instruction {m}",
                    a.clip_id,
                )
    for x in t.mix:
        for ref in x.clip_ids:
            if ref not in audio:
                out.add(
                    "broken_mix_reference",
                    Severity.ERROR,
                    f"mix instruction {x.mix_id} acts on {ref}, which is not an audio clip",
                    x.mix_id,
                )
    for tr in t.transitions:
        for end in (tr.from_clip, tr.to_clip):
            if end is not None and end not in video:
                out.add(
                    "broken_transition_reference",
                    Severity.ERROR,
                    f"transition {tr.transition_id} joins {end}, which does not exist",
                    tr.transition_id,
                )
        if (
            tr.overlay_clip is not None
            and tr.overlay_clip not in video
            and tr.mode is TransitionMode.OVERLAY
        ):
            out.add(
                "broken_transition_reference",
                Severity.ERROR,
                f"transition {tr.transition_id} names a missing overlay {tr.overlay_clip}",
                tr.transition_id,
            )
        if tr.mix_id is not None and not any(x.mix_id == tr.mix_id for x in t.mix):
            out.add(
                "broken_mix_reference",
                Severity.ERROR,
                f"transition {tr.transition_id} names a missing mix instruction",
                tr.transition_id,
            )
        if not tr.track_id or t.track(tr.track_id) is None:
            out.add(
                "broken_transition_reference",
                Severity.ERROR,
                f"transition {tr.transition_id} sits on no track",
                tr.transition_id,
            )
    for s in t.sections:
        for ref in s.clip_ids:
            if ref not in video and ref not in audio:
                out.add(
                    "broken_section_reference",
                    Severity.ERROR,
                    f"section {s.section_id} lists {ref}, which does not exist",
                    s.section_id,
                )
    for g in t.groups:
        for ref in g.members:
            if ref not in everything:
                out.add(
                    "broken_group_reference",
                    Severity.ERROR,
                    f"group {g.group_id} lists {ref}, which does not exist",
                    g.group_id,
                )


# --- timing ---------------------------------------------------------------------------------------


def _timing(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    fps = fr(t.settings.frame_rate)
    total = t.settings.duration_frames
    for c in t.video_clips():
        _video_timing(c, fps, total, out)
    for sound in t.audio_clips():
        _audio_timing(sound, t.settings.sample_rate, fps, total, out)
    for track in t.tracks:
        spans = sorted(
            [(c.start_frame, c.end_frame, c.clip_id) for c in track.video if c.enabled]
            + [(a.start_sample, a.end_sample, a.clip_id) for a in track.audio if a.enabled]
        )
        for (_s0, e0, a_id), (s1, _e1, b_id) in pairwise(spans):
            if s1 < e0:
                unit = "samples" if track.kind is TrackKind.AUDIO else "frames"
                out.add(
                    "overlap_on_track",
                    Severity.ERROR,
                    f"{a_id} and {b_id} overlap on {track.track_id} ({unit} {s1}..{e0})",
                    b_id,
                    start=s1 if unit == "frames" else None,
                    end=e0 if unit == "frames" else None,
                )
    tol = config.gap_tolerance_frames
    primary = sorted(
        (c for tr in t.tracks if tr.role is Role.PRIMARY for c in tr.video if c.enabled),
        key=lambda c: c.start_frame,
    )
    if primary:
        if primary[0].start_frame > tol:
            out.add(
                "late_start",
                Severity.WARNING,
                f"the main picture starts at frame {primary[0].start_frame}, not 0",
                primary[0].clip_id,
                start=0,
                end=primary[0].start_frame,
            )
        for before, after in pairwise(primary):
            if after.start_frame - before.end_frame > tol:
                out.add(
                    "gap_on_main_picture",
                    Severity.WARNING,
                    f"{after.start_frame - before.end_frame} frames of nothing between "
                    f"{before.clip_id} and {after.clip_id}",
                    after.clip_id,
                    start=before.end_frame,
                    end=after.start_frame,
                )
        if primary[-1].end_frame < total - tol:
            out.add(
                "early_end",
                Severity.WARNING,
                f"the main picture ends at frame {primary[-1].end_frame}, the timeline at {total}",
                primary[-1].clip_id,
                start=primary[-1].end_frame,
                end=total,
            )


def _video_timing(c: VideoClip, fps: Fraction, total: int, out: _Found) -> None:
    if c.end_frame <= c.start_frame:
        out.add("empty_clip", Severity.ERROR, f"{c.clip_id} lasts no frame", c.clip_id)
        return
    if (
        frame_at(fr(c.timeline_start), fps) != c.start_frame
        or frame_at(fr(c.timeline_end), fps) != c.end_frame
    ):
        out.add(
            "frame_mismatch",
            Severity.ERROR,
            f"{c.clip_id}: frames {c.start_frame}..{c.end_frame} do not match its exact times",
            c.clip_id,
            start=c.start_frame,
            end=c.end_frame,
        )
    if c.end_frame > total:
        out.add(
            "beyond_timeline",
            Severity.ERROR,
            f"{c.clip_id} ends at frame {c.end_frame}, after the timeline ({total})",
            c.clip_id,
            start=c.start_frame,
            end=c.end_frame,
        )
    if c.source is not None:
        s = c.source
        if fr(s.source_out) <= fr(s.source_in):
            out.add(
                "invalid_source_range",
                Severity.ERROR,
                f"{c.clip_id}: source out is not after source in",
                c.clip_id,
            )
        elif c.kind in _MEDIA_CLIPS and c.time_map is None:
            played = (fr(s.source_out) - fr(s.source_in)) / fr(c.speed) if fr(c.speed) else None
            span = fr(c.timeline_end) - fr(c.timeline_start)
            if played is None or played != span:
                out.add(
                    "duration_mismatch",
                    Severity.ERROR,
                    f"{c.clip_id}: {float(fr(s.source_out) - fr(s.source_in)):.3f}s of source at "
                    f"speed {float(fr(c.speed)):g} does not fill its {float(span):.3f}s on the "
                    "timeline",
                    c.clip_id,
                    start=c.start_frame,
                    end=c.end_frame,
                )
    if c.time_map is not None:
        _time_map(c, out)
    for e in c.effects:
        if (
            e.end_frame < e.start_frame
            or e.start_frame < c.start_frame
            or e.end_frame > c.end_frame
        ):
            out.add(
                "effect_outside_clip",
                Severity.ERROR,
                f"effect {e.effect_id} spans frames {e.start_frame}..{e.end_frame}, outside "
                f"{c.clip_id}",
                e.effect_id,
                start=e.start_frame,
                end=e.end_frame,
            )
    orders = [s.order for s in c.chain]
    if orders != sorted(orders) or len(set(orders)) != len(orders):
        out.add(
            "chain_order",
            Severity.ERROR,
            f"{c.clip_id}: the processing order is not strictly increasing",
            c.clip_id,
        )
    stages = [s.stage for s in c.chain]
    if c.chain and (
        stages[-1] is not ChainStage.COMPOSITE or stages.count(ChainStage.COMPOSITE) != 1
    ):
        out.add(
            "chain_order",
            Severity.ERROR,
            f"{c.clip_id}: composite must be the last step, once",
            c.clip_id,
        )
    if not 0.0 <= c.opacity <= 1.0:
        out.add(
            "invalid_opacity",
            Severity.ERROR,
            f"{c.clip_id}: opacity {c.opacity} is not between 0 and 1",
            c.clip_id,
        )


def _time_map(c: VideoClip, out: _Found) -> None:
    tm = c.time_map
    if tm is None or not tm.segments:
        out.add(
            "invalid_time_map",
            Severity.ERROR,
            f"{c.clip_id}: the time map has no segment",
            c.clip_id,
        )
        return
    segs = tm.segments
    if segs[0].timeline_start != c.timeline_start or segs[-1].timeline_end != c.timeline_end:
        out.add(
            "invalid_time_map",
            Severity.ERROR,
            f"{c.clip_id}: the time map does not cover exactly the clip's span",
            c.clip_id,
        )
    for a, b in pairwise(segs):
        if a.timeline_end != b.timeline_start:
            out.add(
                "invalid_time_map",
                Severity.ERROR,
                f"{c.clip_id}: a hole or overlap in the time map at "
                f"{float(fr(a.timeline_end)):.3f}s",
                c.clip_id,
            )
    for s in segs:
        rate = fr(s.rate)
        span = fr(s.timeline_end) - fr(s.timeline_start)
        if rate < 0 or span <= 0:
            out.add(
                "invalid_time_map",
                Severity.ERROR,
                f"{c.clip_id}: a segment has a negative rate or span",
                c.clip_id,
            )
        elif rate > 0 and abs(fr(s.source_end) - fr(s.source_start)) != rate * span:
            out.add(
                "invalid_time_map",
                Severity.ERROR,
                f"{c.clip_id}: a {s.kind} segment's source span is not rate x timeline span",
                c.clip_id,
            )


def _audio_timing(a: AudioClip, sample_rate: int, fps: Fraction, total: int, out: _Found) -> None:
    if a.end_sample <= a.start_sample:
        out.add("empty_clip", Severity.ERROR, f"{a.clip_id} lasts no sample", a.clip_id)
        return
    if a.sample_rate != sample_rate:
        out.add(
            "sample_rate_mismatch",
            Severity.ERROR,
            f"{a.clip_id} is positioned at {a.sample_rate} Hz, the timeline runs at {sample_rate} "
            "Hz",
            a.clip_id,
        )
        return
    if (
        sample_at(fr(a.timeline_start), a.sample_rate) != a.start_sample
        or sample_at(fr(a.timeline_end), a.sample_rate) != a.end_sample
    ):
        out.add(
            "sample_mismatch",
            Severity.ERROR,
            f"{a.clip_id}: samples do not match its exact times",
            a.clip_id,
        )
    if a.end_sample > sample_at(Fraction(total) / fps, a.sample_rate) + 1:
        out.add(
            "beyond_timeline", Severity.ERROR, f"{a.clip_id} ends after the timeline", a.clip_id
        )
    if a.source is not None and fr(a.source.source_out) <= fr(a.source.source_in):
        out.add(
            "invalid_source_range",
            Severity.ERROR,
            f"{a.clip_id}: source out is not after source in",
            a.clip_id,
        )
    length = fr(a.timeline_end) - fr(a.timeline_start)
    if fr(a.fade_in) + fr(a.fade_out) > length:
        out.add(
            "fades_exceed_clip",
            Severity.ERROR,
            f"{a.clip_id}: fade in and out ({float(fr(a.fade_in) + fr(a.fade_out)):.3f}s) are "
            "longer than the clip",
            a.clip_id,
        )


# --- transitions ----------------------------------------------------------------------------------


def _transitions(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    fps = fr(t.settings.frame_rate)
    for tr in t.transitions:
        a = t.video_clip(tr.from_clip) if tr.from_clip else None
        b = t.video_clip(tr.to_clip) if tr.to_clip else None
        if not tr.start_frame <= tr.cut_frame <= tr.end_frame:
            out.add(
                "invalid_transition_position",
                Severity.ERROR,
                f"transition {tr.transition_id}: the cut lies outside its span",
                tr.transition_id,
            )
        if tr.duration_frames != tr.end_frame - tr.start_frame:
            out.add(
                "invalid_transition_position",
                Severity.ERROR,
                f"transition {tr.transition_id}: duration is not end - start",
                tr.transition_id,
            )
        if a is not None and a.end_frame != tr.cut_frame and b is not None:
            out.add(
                "invalid_transition_position",
                Severity.ERROR,
                f"transition {tr.transition_id}: {a.clip_id} ends at frame {a.end_frame}, not at "
                f"the cut ({tr.cut_frame})",
                tr.transition_id,
                start=tr.start_frame,
                end=tr.end_frame,
            )
        if b is not None and b.start_frame != tr.cut_frame and a is not None:
            out.add(
                "invalid_transition_position",
                Severity.ERROR,
                f"transition {tr.transition_id}: {b.clip_id} starts at frame {b.start_frame}, not "
                f"at the cut ({tr.cut_frame})",
                tr.transition_id,
                start=tr.start_frame,
                end=tr.end_frame,
            )
        if not tr.supported:
            out.add(
                "unsupported_transition",
                Severity.UNSUPPORTED,
                f"transition {tr.transition_id} ({tr.kind}) cannot be carried out by the timeline "
                "model; the cut stays where it is and nothing replaces it",
                tr.transition_id,
                start=tr.start_frame,
                end=tr.end_frame,
            )
            continue
        if tr.mode in {TransitionMode.DISSOLVE, TransitionMode.FADE, TransitionMode.DIP}:
            for clip, need, name in (
                (a, tr.tail_frames, "outgoing"),
                (b, tr.head_frames, "incoming"),
            ):
                if clip is None or clip.source is None or need == 0:
                    continue
                if need > clip.frames:
                    out.add(
                        "transition_longer_than_clip",
                        Severity.UNSUPPORTED,
                        f"transition {tr.transition_id} reads {need} frames of {clip.clip_id}, "
                        f"which lasts {clip.frames}",
                        tr.transition_id,
                    )
                have = _handle(t, clip, outgoing=name == "outgoing", fps=fps)
                if have is not None and have < need:
                    out.add(
                        "insufficient_handles",
                        Severity.UNSUPPORTED,
                        f"transition {tr.transition_id} needs {need} frames of handle after/before "
                        f"{clip.clip_id}; the file holds {have}",
                        tr.transition_id,
                        start=tr.start_frame,
                        end=tr.end_frame,
                    )
            if not tr.handles.sufficient:
                out.add(
                    "insufficient_handles",
                    Severity.UNSUPPORTED,
                    f"transition {tr.transition_id}: the specification itself states insufficient "
                    "handles",
                    tr.transition_id,
                )
        if tr.mode is TransitionMode.OVERLAY and tr.overlay_clip is not None:
            overlay = t.video_clip(tr.overlay_clip)
            if overlay is not None and overlay.state is ClipState.UNRESOLVED:
                out.add(
                    "unresolved_overlay",
                    Severity.WARNING,
                    f"transition {tr.transition_id}: its overlay file is not available",
                    tr.transition_id,
                )


def _handle(t: BuiltTimeline, clip: VideoClip, *, outgoing: bool, fps: Fraction) -> int | None:
    """Frames of footage the FILE holds beyond the clip's out point (or before its in point)."""
    if clip.source is None:
        return None
    if not outgoing:
        return max(0, int(fr(clip.source.source_in) * fps))
    media = t.source(clip.source.source_id)
    if media is None or media.duration is None:
        return None
    return max(0, int((fr(media.duration) - fr(clip.source.source_out)) * fps))


# --- media ----------------------------------------------------------------------------------------


def _media(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    fps = fr(t.settings.frame_rate)
    slack = Fraction(1) / fps
    color = t.settings.color
    clips: list[VideoClip | AudioClip] = [*t.video_clips(), *t.audio_clips()]
    for c in clips:
        if c.source is None:
            continue
        media = t.source(c.source.source_id)
        if media is None:
            continue
        if media.original_asset_id != c.source.original_asset_id:
            out.add(
                "derivative_mismatch",
                Severity.ERROR,
                f"{c.clip_id} says {c.source.source_id} traces to {c.source.original_asset_id}, "
                f"the source table says {media.original_asset_id}",
                c.clip_id,
            )
        if media.duration is not None and fr(c.source.source_out) > fr(media.duration) + slack:
            out.add(
                "source_range_beyond_media",
                Severity.ERROR,
                f"{c.clip_id}: source out {float(fr(c.source.source_out)):.3f}s is after the end "
                "of "
                f"{c.source.source_id} ({float(fr(media.duration)):.3f}s)",
                c.clip_id,
            )
        if not media.available and (isinstance(c, AudioClip) or c.kind in _MEDIA_CLIPS):
            out.add(
                "source_unavailable",
                Severity.ERROR,
                f"{c.source.source_id} is not available for {c.clip_id}",
                c.clip_id,
            )
    for media in t.sources:
        if media.media_type == "video" or media.width:
            if media.frame_rate_variable:
                out.add(
                    "variable_frame_rate",
                    Severity.WARNING,
                    f"{media.source_id} has a variable frame rate; times are exact seconds, "
                    "conform the picture in the timeline application",
                    media.source_id,
                )
            elif media.frame_rate is not None and abs(fr(media.frame_rate) - fps) > _RATE_TOLERANCE:
                out.add(
                    "frame_rate_conversion",
                    Severity.INFO,
                    f"{media.source_id} runs at {float(fr(media.frame_rate)):g} fps, the timeline "
                    "at "
                    f"{float(fps):g} fps: the application converts it",
                    media.source_id,
                )
            tags = (
                (media.color_space, color.space),
                (media.color_primaries, color.primaries),
                (media.color_transfer, color.transfer),
                (media.color_range, color.range),
            )
            if media.color_space is not None or media.color_transfer is not None:
                differs = [f"{a} (timeline {b})" for a, b in tags if a is not None and a != b]
                if differs and media.media_type == "video" and media.relation == "original":
                    out.add(
                        "color_mismatch",
                        Severity.WARNING,
                        f"{media.source_id} is tagged {', '.join(differs)}; nothing is converted "
                        "here, the timeline application must",
                        media.source_id,
                    )
    for c in t.video_clips():
        if c.source is None or c.transform is None or c.crops:
            continue
        media = t.source(c.source.source_id)
        if media is None or not media.width or not media.height:
            continue
        if media.width * t.settings.height != media.height * t.settings.width:
            out.add(
                "fit_applied",
                Severity.WARNING if c.kind not in _MEDIA_CLIPS else Severity.INFO,
                f"{c.clip_id}: the picture is {media.width}x{media.height}, the timeline "
                f"{t.settings.width}x{t.settings.height}; fit mode {c.transform.fit.value} applies",
                c.clip_id,
            )
    for c in t.video_clips():
        if c.state is ClipState.UNRESOLVED:
            out.add(
                "unresolved_dependency",
                Severity.ERROR if config.block_on_unresolved else Severity.WARNING,
                f"{c.clip_id} ({c.kind.value}) has no usable rendered file",
                c.clip_id,
                start=c.start_frame,
                end=c.end_frame,
            )


# --- the picture ----------------------------------------------------------------------------------


def _visual(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    zones = {z.name: z for z in t.identity.zones}
    width, height = t.settings.width, t.settings.height
    for c in t.video_clips():
        for crop in c.crops:
            if not crop.keys:
                out.add(
                    "invalid_crop",
                    Severity.ERROR,
                    f"{c.clip_id}: crop {crop.crop_id} has no keyframe",
                    c.clip_id,
                )
            for k in crop.keys:
                if (
                    k.w <= 0
                    or k.h <= 0
                    or k.x < 0
                    or k.y < 0
                    or k.x + k.w > 1.0001
                    or k.y + k.h > 1.0001
                ):
                    out.add(
                        "invalid_crop",
                        Severity.ERROR,
                        f"{c.clip_id}: crop {crop.crop_id} has a window outside the picture "
                        f"({k.x:.3f}, {k.y:.3f}, {k.w:.3f}, {k.h:.3f})",
                        c.clip_id,
                    )
                    break
        if c.zone:
            zone = zones.get(c.zone)
            if zone is None:
                out.add(
                    "unknown_zone",
                    Severity.WARNING,
                    f"{c.clip_id} sits in zone {c.zone}, which the creator identity does not "
                    "define",
                    c.clip_id,
                )
            elif (
                zone.left < 0
                or zone.top < 0
                or zone.left + zone.width > width
                or zone.top + zone.height > height
            ):
                out.add(
                    "zone_outside_frame",
                    Severity.WARNING,
                    f"zone {zone.name} of {c.clip_id} leaves the {width}x{height} frame",
                    c.clip_id,
                )
            elif c.kind is ClipKind.CAPTION and zone.kind != "caption":
                out.add(
                    "zone_kind",
                    Severity.WARNING,
                    f"caption {c.clip_id} is in zone {zone.name}, a {zone.kind} zone",
                    c.clip_id,
                )
        if c.blend is BlendMode.ALPHA_OVER and c.kind in _MEDIA_CLIPS:
            out.add(
                "invalid_blend",
                Severity.ERROR,
                f"{c.clip_id}: footage cannot composite as alpha over",
                c.clip_id,
            )
    subtitles = t.subtitles
    captions = [c for c in t.video_clips() if c.kind is ClipKind.CAPTION]
    if subtitles is not None and subtitles.cues and not captions:
        out.add(
            "captions_not_placed",
            Severity.WARNING,
            "the captions exist as text cues only: no rendered caption clip is on the timeline",
        )
    if captions and (subtitles is None or not subtitles.cues):
        out.add(
            "captions_without_text",
            Severity.WARNING,
            "caption clips are placed but no subtitle text came with them",
        )


# --- the sound ------------------------------------------------------------------------------------


def _audio(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    video = {c.clip_id: c for c in t.video_clips()}
    audio = {c.clip_id: c for c in t.audio_clips()}
    for a in audio.values():
        if a.video_link is None:
            continue
        v = video.get(a.video_link)
        if v is None:
            continue
        if a.timeline_start != v.timeline_start or a.timeline_end != v.timeline_end:
            out.add(
                "av_sync",
                Severity.ERROR,
                f"{a.clip_id} "
                f"({float(fr(a.timeline_start)):.3f}-{float(fr(a.timeline_end)):.3f}s) is out "
                f"of sync with {v.clip_id} "
                f"({float(fr(v.timeline_start)):.3f}-{float(fr(v.timeline_end)):.3f}s)",
                a.clip_id,
            )
        elif (
            a.source is not None
            and v.source is not None
            and a.source.original_asset_id == v.source.original_asset_id
            and v.time_map is None
            and v.kind is not ClipKind.VFX
            and (a.source.source_in != v.source.source_in or a.speed != v.speed)
        ):
            out.add(
                "av_sync",
                Severity.ERROR,
                f"{a.clip_id} plays other source time than {v.clip_id}: they would drift apart",
                a.clip_id,
            )
    for v in video.values():
        if v.kind not in _MEDIA_CLIPS or v.audio_link is not None or v.source is None:
            continue
        media = t.source(v.source.source_id)
        original = t.source(v.source.original_asset_id)
        has_sound = any(m is not None and m.sample_rate for m in (media, original))
        wanted_silent = v.kind is ClipKind.COVER
        if has_sound and not wanted_silent:
            out.add(
                "missing_dialogue",
                Severity.WARNING,
                f"{v.clip_id} has sound in its source but no audio clip is linked to it",
                v.clip_id,
                start=v.start_frame,
                end=v.end_frame,
            )


# --- identity -------------------------------------------------------------------------------------


def _identity(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    ident = t.identity
    if not ident.identity_id or not ident.style_fingerprint:
        out.add(
            "identity_missing", Severity.ERROR, "the timeline carries no creator identity reference"
        )
        return
    presets = set(ident.presets)
    for c in t.video_clips():
        for ref in c.identity:
            if ref not in presets:
                out.add(
                    "identity_reference",
                    Severity.WARNING,
                    f"{c.clip_id} uses {ref[0]} {ref[1]!r}, which the identity "
                    f"{ident.identity_id} v{ident.identity_version} does not list",
                    c.clip_id,
                )
    for tr in t.transitions:
        if (
            tr.preset_id
            and tr.mode is not TransitionMode.CUT
            and ("transition_preset", tr.preset_id) not in presets
        ):
            out.add(
                "identity_reference",
                Severity.WARNING,
                f"transition {tr.transition_id} uses preset {tr.preset_id!r}, which the identity "
                "does not list",
                tr.transition_id,
            )
    for f in ident.fonts:
        if not f.available:
            out.add(
                "font_unavailable",
                Severity.WARNING,
                f"font {f.key} ({f.family}) is not available",
                f.key,
            )
    for b in ident.brand_assets:
        if b.required and b.asset_id is None:
            out.add(
                "brand_asset_unbound",
                Severity.WARNING,
                f"required brand slot {b.slot} has no asset",
                b.slot,
            )
    if any(a.role is Role.MUSIC for a in t.audio_clips()) and not ident.sound_identity:
        out.add(
            "sound_identity_missing",
            Severity.WARNING,
            "music is placed but no sound identity is referenced",
        )


# --- what cannot be carried out ------------------------------------------------------------------


def _execution(t: BuiltTimeline, config: BuilderConfig, caps: Capabilities, out: _Found) -> None:
    for c in t.video_clips():
        for e in c.effects:
            if e.execution is Execution.NATIVE and not e.applied:
                out.add(
                    "unsupported_effect",
                    Severity.UNSUPPORTED,
                    f"effect {e.effect_type} ({e.effect_id}) is not available in the target "
                    "application; "
                    "it is kept and not applied",
                    e.effect_id,
                    start=e.start_frame,
                    end=e.end_frame,
                )
            elif e.execution is Execution.NATIVE:
                for m in e.masks:
                    if m.track_refs:
                        out.add(
                            "tracking_unverified",
                            Severity.WARNING,
                            f"mask {m.mask_id} of {e.effect_id} follows tracking data "
                            f"{', '.join(m.track_refs)} that nothing here can verify",
                            e.effect_id,
                        )
        tm = c.time_map
        if tm is not None and tm.applied:
            if tm.has_reverse and not caps.reverse:
                out.add(
                    "unsupported_reverse",
                    Severity.UNSUPPORTED,
                    f"{c.clip_id}: reverse playback is not available in the target application",
                    c.clip_id,
                )
            # a clip that plays at more than one speed is a ramp (a freeze is not a speed)
            speeds = {s.rate for s in tm.segments if fr(s.rate) != 0}
            if len(speeds) > 1 and not caps.speed_ramps:
                out.add(
                    "unsupported_speed_ramp",
                    Severity.UNSUPPORTED,
                    f"{c.clip_id}: speed ramps are not available in the target application",
                    c.clip_id,
                )


def _consistency(t: BuiltTimeline, spec: SpecFacts, out: _Found) -> None:
    here = set(t.element_ids())
    gone = {x.element_id for x in t.removed_by_human}
    wanted = (
        [v.item_id for v in spec.video]
        + [o.item_id for o in spec.overlays]
        + [a.item_id for a in spec.audio]
        + [x.transition_id for x in spec.transitions]
        + [m.automation_id for m in spec.automation]
        + [tr.track_id for tr in spec.tracks]
    )
    for element in wanted:
        if element not in here and element not in gone:
            out.add(
                "element_missing",
                Severity.ERROR,
                f"{element} is in the specification but not in the timeline",
                element,
            )
    if t.settings.duration_frames != spec.timebase.duration_frames:
        out.add(
            "duration_changed",
            Severity.ERROR,
            "the timeline duration differs from the specification's",
        )
    if len(t.tracks) < len(spec.tracks):
        out.add(
            "tracks_missing",
            Severity.ERROR,
            f"{len(spec.tracks) - len(t.tracks)} specified tracks are missing",
        )


_CHECKS: tuple[tuple[str, _Check], ...] = (
    ("unique_ids", _ids),
    ("tracks_and_hierarchy", _tracks),
    ("references", _references),
    ("timing", _timing),
    ("transitions", _transitions),
    ("media_and_derivatives", _media),
    ("picture", _visual),
    ("sound", _audio),
    ("creator_identity", _identity),
    ("execution_support", _execution),
)
