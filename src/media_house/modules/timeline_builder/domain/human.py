"""A person's changes to a built timeline, recorded without losing what the AI built.

The changes arrive as plain records (``HumanEdit``: which element, which named fields, which new
values), typically from a later synchronisation with a timeline application. They are applied to
the stored build, the element is marked ``human_modified`` together with the digest of the AI
version it was changed from, a deleted element leaves a tombstone, and a created one is marked
``human_created``. The next build then recognises these and applies the conflict policy instead
of overwriting them.

Only picture and sound clips can be changed; a change that leaves the timeline invalid (an
overlap, a clip beyond the timeline, a broken transition) is refused as a whole.
"""

from dataclasses import dataclass, replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.timeline_builder.domain.digest import digest
from media_house.modules.timeline_builder.domain.engine import (
    BuildMeta,
    content_hash,
    revalidate,
)
from media_house.modules.timeline_builder.domain.errors import InvalidHumanEdit
from media_house.modules.timeline_builder.domain.model import (
    AudioClip,
    BuiltTimeline,
    BuiltTrack,
    ChangeSet,
    EditRecord,
    HumanMark,
    SourceUse,
    Tombstone,
    VideoClip,
)
from media_house.modules.timeline_builder.domain.profile import BuilderConfig, Capabilities
from media_house.modules.timeline_builder.domain.reconcile import heal
from media_house.modules.timeline_builder.domain.timing import (
    fr,
    parse_rational,
    rat,
    time_of_frame,
    time_of_sample,
)
from media_house.modules.timeline_builder.domain.values import (
    BuildMode,
    EditKind,
    Provenance,
    TrackKind,
)


@dataclass(frozen=True, slots=True)
class HumanEdit:
    """One change by a person. ``changes`` maps field names to values written as text
    (``"120"``, ``"3/2"``, ``"0.5"``, ``"true"``); ``clip`` is the whole new clip for a
    ``create``."""

    kind: EditKind
    element_id: str
    changes: tuple[tuple[str, str], ...] = ()
    clip: VideoClip | AudioClip | None = None
    editor: str = ""
    note: str = ""


VIDEO_FIELDS = (
    "start_frame",
    "end_frame",
    "source_in",
    "source_out",
    "opacity",
    "enabled",
    "name",
    "track_id",
)
AUDIO_FIELDS = (
    "start_sample",
    "end_sample",
    "source_in",
    "source_out",
    "gain_db",
    "fade_in",
    "fade_out",
    "enabled",
    "name",
    "track_id",
)


def apply_edits(
    timeline: BuiltTimeline,
    edits: tuple[HumanEdit, ...],
    *,
    meta: BuildMeta,
    config: BuilderConfig,
    caps: Capabilities | None = None,
) -> BuiltTimeline:
    """The timeline with the edits applied, judged again. Raises ``InvalidHumanEdit`` (and
    changes nothing) when an edit is malformed or leaves the timeline invalid."""
    if not edits:
        raise InvalidHumanEdit("no change was given")
    video = {c.clip_id: c for c in timeline.video_clips()}
    audio = {c.clip_id: c for c in timeline.audio_clips()}
    tracks = {t.track_id: t for t in timeline.tracks}
    tombs = {t.element_id: t for t in timeline.removed_by_human}
    log = list(timeline.edit_log)
    deleted: set[str] = set()
    fps = fr(timeline.settings.frame_rate)
    touched: list[str] = []

    for e in edits:
        names = tuple(k for k, _ in e.changes)
        if e.kind is EditKind.MODIFY:
            if e.element_id in video:
                video[e.element_id] = _modify_video(video[e.element_id], e, tracks, fps)
            elif e.element_id in audio:
                audio[e.element_id] = _modify_audio(
                    audio[e.element_id], e, tracks, timeline.settings.sample_rate
                )
            else:
                raise InvalidHumanEdit(f"{e.element_id} is not a clip of this timeline")
        elif e.kind is EditKind.DELETE:
            gone_v = video.pop(e.element_id, None)
            gone_a = audio.pop(e.element_id, None)
            clip: VideoClip | AudioClip | None = gone_v or gone_a
            if clip is None:
                raise InvalidHumanEdit(f"{e.element_id} is not a clip of this timeline")
            deleted.add(e.element_id)
            if clip.provenance is not Provenance.HUMAN_CREATED:
                base = clip.human.base_digest if clip.human else clip.source_digest
                tombs[e.element_id] = Tombstone(
                    element_id=e.element_id, base_digest=base, editor=e.editor, note=e.note
                )
        else:
            _create(e, video, audio, tracks)
        touched.append(e.element_id)
        log.append(
            EditRecord(
                sequence=len(log) + 1,
                kind=e.kind,
                element_id=e.element_id,
                fields=names,
                editor=e.editor,
                note=e.note,
                at=meta.built_at,
            )
        )

    # a deleted clip takes the transitions that join it, and the references to them, with it
    dropped_t = {
        t.transition_id
        for t in timeline.transitions
        if {t.from_clip, t.to_clip, t.overlay_clip} & deleted
    }
    dropped_m = {m.mix_id for m in timeline.mix if m.clip_ids and set(m.clip_ids) <= deleted}
    v_list, a_list = heal(list(video.values()), list(audio.values()), dropped_t, dropped_m, deleted)
    mixes = tuple(
        replace(m, clip_ids=tuple(i for i in m.clip_ids if i not in deleted))
        for m in timeline.mix
        if m.mix_id not in dropped_m
    )
    new_tracks = tuple(
        replace(
            t,
            video=tuple(sorted((c for c in v_list if c.track_id == t.track_id), key=_vkey)),
            audio=tuple(sorted((c for c in a_list if c.track_id == t.track_id), key=_akey)),
        )
        for t in timeline.tracks
    )
    alive = {c.clip_id for c in v_list} | {c.clip_id for c in a_list}
    alive |= {t.transition_id for t in timeline.transitions if t.transition_id not in dropped_t}
    alive |= {m.mix_id for m in mixes}
    groups = tuple(
        g
        for g in (
            replace(g, members=tuple(m for m in g.members if m in alive)) for g in timeline.groups
        )
        if len(g.members) > 1
    )
    sections = tuple(
        replace(s, clip_ids=tuple(i for i in s.clip_ids if i not in deleted))
        for s in timeline.sections
    )
    edited = replace(
        timeline,
        tracks=new_tracks,
        groups=groups,
        sections=sections,
        transitions=tuple(t for t in timeline.transitions if t.transition_id not in dropped_t),
        mix=mixes,
        removed_by_human=tuple(sorted(tombs.values(), key=lambda t: t.element_id)),
        edit_log=tuple(log),
    )
    judged, errors = revalidate(edited, config, caps)
    if errors:
        raise InvalidHumanEdit("; ".join(i.message for i in errors[:3]))
    return replace(
        judged,
        build=replace(
            timeline.build,
            build_version=meta.build_version,
            supersedes=meta.supersedes,
            mode=BuildMode.INCREMENTAL,
            reason="a person's change was recorded",
            built_at=meta.built_at,
            build_hash=content_hash(judged),
            change=ChangeSet(changed=tuple(sorted(set(touched)))),
        ),
    )


def _with_source(source: SourceUse, name: str, value: Rational) -> SourceUse:
    return (
        replace(source, source_in=value)
        if name == "source_in"
        else replace(source, source_out=value)
    )


def _vkey(c: VideoClip) -> tuple[int, str]:
    return (c.start_frame, c.clip_id)


def _akey(c: AudioClip) -> tuple[int, str]:
    return (c.start_sample, c.clip_id)


def _marked(
    clip_provenance: Provenance,
    mark: HumanMark | None,
    base: str,
    fields: tuple[str, ...],
    e: HumanEdit,
) -> tuple[Provenance, HumanMark]:
    provenance = (
        Provenance.HUMAN_CREATED
        if clip_provenance is Provenance.HUMAN_CREATED
        else Provenance.HUMAN_MODIFIED
    )
    done = tuple(sorted({*(mark.fields if mark else ()), *fields}))
    return provenance, HumanMark(
        base_digest=mark.base_digest if mark else base,
        fields=done,
        editor=e.editor or (mark.editor if mark else ""),
        note=e.note or (mark.note if mark else ""),
        pending_digest=mark.pending_digest if mark else None,
    )


def _int(name: str, value: str, minimum: int = 0) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise InvalidHumanEdit(f"{name} must be a whole number, not {value!r}") from exc
    if n < minimum:
        raise InvalidHumanEdit(f"{name} must be at least {minimum}")
    return n


def _float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise InvalidHumanEdit(f"{name} must be a number, not {value!r}") from exc


def _time(name: str, value: str) -> Fraction:
    try:
        return fr(parse_rational(value))
    except ValueError as exc:
        raise InvalidHumanEdit(f"{name}: {exc}") from exc


def _bool(name: str, value: str) -> bool:
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise InvalidHumanEdit(f"{name} must be true or false")


def _name(value: str) -> str:
    if not value.strip():
        raise InvalidHumanEdit("name must not be empty")
    return value.strip()


def _track(name: str, value: str, tracks: dict[str, BuiltTrack], kind: TrackKind) -> str:
    found = tracks.get(value)
    if found is None or found.kind is not kind:
        raise InvalidHumanEdit(f"{name}: {value!r} is not a {kind.value} track of this timeline")
    return value


def _modify_video(
    clip: VideoClip, e: HumanEdit, tracks: dict[str, BuiltTrack], fps: Fraction
) -> VideoClip:
    out = clip
    for name, value in e.changes:
        if name == "start_frame":
            n = _int(name, value)
            out = replace(out, start_frame=n, timeline_start=time_of_frame(n, fps))
        elif name == "end_frame":
            n = _int(name, value, 1)
            out = replace(out, end_frame=n, timeline_end=time_of_frame(n, fps))
        elif name in {"source_in", "source_out"} and out.source is not None:
            out = replace(out, source=_with_source(out.source, name, rat(_time(name, value))))
        elif name == "opacity":
            out = replace(out, opacity=_float(name, value))
        elif name == "enabled":
            out = replace(out, enabled=_bool(name, value))
        elif name == "name":
            out = replace(out, name=_name(value))
        elif name == "track_id":
            out = replace(out, track_id=_track(name, value, tracks, TrackKind.VIDEO))
        else:
            raise InvalidHumanEdit(
                f"{name!r} cannot be changed on a picture clip ({', '.join(VIDEO_FIELDS)})"
            )
    provenance, mark = _marked(
        clip.provenance, clip.human, clip.source_digest, tuple(k for k, _ in e.changes), e
    )
    return replace(out, provenance=provenance, human=mark)


def _modify_audio(
    clip: AudioClip, e: HumanEdit, tracks: dict[str, BuiltTrack], sample_rate: int
) -> AudioClip:
    out = clip
    for name, value in e.changes:
        if name == "start_sample":
            n = _int(name, value)
            out = replace(out, start_sample=n, timeline_start=time_of_sample(n, sample_rate))
        elif name == "end_sample":
            n = _int(name, value, 1)
            out = replace(out, end_sample=n, timeline_end=time_of_sample(n, sample_rate))
        elif name in {"source_in", "source_out"} and out.source is not None:
            out = replace(out, source=_with_source(out.source, name, rat(_time(name, value))))
        elif name == "gain_db":
            out = replace(out, gain_db=_float(name, value))
        elif name in {"fade_in", "fade_out"}:
            fade = rat(_time(name, value))
            out = replace(out, fade_in=fade) if name == "fade_in" else replace(out, fade_out=fade)
        elif name == "enabled":
            out = replace(out, enabled=_bool(name, value))
        elif name == "name":
            out = replace(out, name=_name(value))
        elif name == "track_id":
            out = replace(out, track_id=_track(name, value, tracks, TrackKind.AUDIO))
        else:
            raise InvalidHumanEdit(
                f"{name!r} cannot be changed on a sound clip ({', '.join(AUDIO_FIELDS)})"
            )
    provenance, mark = _marked(
        clip.provenance, clip.human, clip.source_digest, tuple(k for k, _ in e.changes), e
    )
    return replace(out, provenance=provenance, human=mark)


def _create(
    e: HumanEdit,
    video: dict[str, VideoClip],
    audio: dict[str, AudioClip],
    tracks: dict[str, BuiltTrack],
) -> None:
    clip = e.clip
    if clip is None:
        raise InvalidHumanEdit("a created clip needs the clip")
    if clip.clip_id in video or clip.clip_id in audio:
        raise InvalidHumanEdit(f"{clip.clip_id} already exists")
    if clip.clip_id != e.element_id:
        raise InvalidHumanEdit("the clip's id and the edit's element id differ")
    mark = HumanMark(base_digest="", editor=e.editor, note=e.note)
    if isinstance(clip, VideoClip):
        _track("track_id", clip.track_id, tracks, TrackKind.VIDEO)
        video[clip.clip_id] = replace(
            clip, provenance=Provenance.HUMAN_CREATED, human=mark, source_digest=digest(clip)
        )
    else:
        _track("track_id", clip.track_id, tracks, TrackKind.AUDIO)
        audio[clip.clip_id] = replace(
            clip, provenance=Provenance.HUMAN_CREATED, human=mark, source_digest=digest(clip)
        )
