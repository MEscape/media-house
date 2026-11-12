"""From the built timeline's facts to what Resolve should hold: ``Expected``.

Pure and deterministic. Every expected element carries the id of the timeline element it comes
from, so every Resolve operation is traceable to an internal instruction. Nothing is decided
here: where Resolve's scripting API cannot do what the timeline asks (retime, a moving crop, a
native effect, a transition), the instruction becomes a ``Gap``: kept, reported, and (where a
person can finish it in one step) turned into a marker that says exactly what to do. Nothing is
replaced by a look of its own.
"""

from dataclasses import dataclass

from media_house.core.domain import Rational
from media_house.modules.resolve_adapter.domain.capabilities import CapabilityMatrix
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.errors import TimelineNotExecutable
from media_house.modules.resolve_adapter.domain.facts import (
    BlurKey,
    ClipFact,
    MarkerFact,
    MediaFact,
    SettingsFact,
    SubtitleFact,
    TimelineFacts,
    TransitionFact,
    ZoomKey,
)
from media_house.modules.resolve_adapter.domain.values import (
    TAG_PREFIX,
    RetimePlacement,
    Strategy,
)

#: Timeline rates Resolve names with a decimal text.
_NTSC = {
    (24000, 1001): "23.976",
    (30000, 1001): "29.97",
    (48000, 1001): "47.952",
    (60000, 1001): "59.94",
    (120000, 1001): "119.88",
}

#: The range Resolve accepts for a clip's ``AudioVolume``.
_VOLUME_DB = (-100.0, 30.0)

CRITICAL_SETTINGS = ("timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight")


def frame_rate_text(rate: Rational) -> str | None:
    """The text Resolve uses for a frame rate, or ``None`` for a rate it has no name for."""
    named = _NTSC.get((rate.numerator, rate.denominator))
    if named is not None:
        return named
    if rate.denominator == 1:
        return str(rate.numerator)
    return None


def _num(value: float) -> str:
    return f"{round(value, 4):g}"


# --- the expected state ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpectedTrack:
    track_id: str
    kind: str
    index: int  # Resolve's, 1 = bottom
    name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ExpectedItem:
    element_id: str
    kind: str  # video | audio
    track_index: int
    asset_id: str
    name: str
    start: int
    duration: int
    src_start: int
    src_frames: int
    enabled: bool
    props: tuple[tuple[str, float], ...]
    link: str | None
    digest: str
    #: A moving window, carried out as a keyframed Fusion transform (not part of ``fields``:
    #: Resolve cannot read it back as a clip property).
    motion: tuple[ZoomKey, ...] = ()
    #: A keyframed blur after the framing, and a screen recording floated as a card (window).
    blur: tuple[BlurKey, ...] = ()
    card: tuple[float, float, float, float] | None = None
    #: A recording of the main picture or the cover footage (an auto grade applies to it).
    gradable: bool = False

    @property
    def fields(self) -> tuple[tuple[str, str], ...]:
        """The comparable state of the item: what is merged three ways and verified."""
        return (
            ("asset", self.asset_id),
            ("duration", str(self.duration)),
            ("enabled", "1" if self.enabled else "0"),
            *((f"prop:{k}", _num(v)) for k, v in self.props),
            ("src_start", str(self.src_start)),
            ("start", str(self.start)),
            ("track", f"{self.kind}:{self.track_index}"),
        )


@dataclass(frozen=True, slots=True)
class ExpectedMarker:
    custom: str
    frame: int
    color: str
    name: str
    note: str
    duration: int


@dataclass(frozen=True, slots=True)
class Gap:
    """A timeline instruction Resolve's scripting cannot carry out."""

    element_id: str
    operation: str  # a capability id
    strategy: Strategy
    detail: str
    alternatives: tuple[str, ...] = ()
    #: The clip itself was still placed (only the instruction is open).
    placed: bool = True
    owner: str = ""


@dataclass(frozen=True, slots=True)
class Expected:
    tracks: tuple[ExpectedTrack, ...]
    items: tuple[ExpectedItem, ...]
    markers: tuple[ExpectedMarker, ...]
    gaps: tuple[Gap, ...]
    settings: tuple[tuple[str, str], ...]
    subtitles: SubtitleFact | None

    def item(self, element_id: str) -> ExpectedItem | None:
        return next((i for i in self.items if i.element_id == element_id), None)

    def track_count(self, kind: str) -> int:
        return max((t.index for t in self.tracks if t.kind == kind), default=0)


# --- translation ----------------------------------------------------------------------------------


def translate(
    facts: TimelineFacts, config: ResolveAdapterConfig, matrix: CapabilityMatrix
) -> Expected:
    problems = _problems(facts)
    if problems:
        raise TimelineNotExecutable(problems)
    tracks, index_of = _tracks(facts)
    gaps: list[Gap] = []
    items: list[ExpectedItem] = []
    markers: list[ExpectedMarker] = []
    for clip in sorted(facts.clips, key=lambda c: (c.start_frame, c.clip_id)):
        item = _item(clip, facts, index_of, config, matrix, gaps, markers)
        if item is not None:
            items.append(item)
    for transition in sorted(facts.transitions, key=lambda t: (t.cut_frame, t.transition_id)):
        _transition(transition, config, gaps, markers)
    for marker in facts.markers:
        markers.append(_marker(marker))
    settings = _settings(facts.settings, gaps)
    subtitles = facts.subtitles
    if subtitles is not None and config.caption_strategy.value != "rendered_with_editable_cues":
        subtitles = None
    return Expected(
        tracks=tracks,
        items=tuple(items),
        markers=tuple(sorted(markers, key=lambda m: (m.frame, m.custom))),
        gaps=tuple(gaps),
        settings=settings,
        subtitles=subtitles,
    )


def _problems(facts: TimelineFacts) -> list[str]:
    problems: list[str] = []
    if frame_rate_text(facts.settings.frame_rate) is None:
        problems.append(f"Resolve has no name for the frame rate {facts.settings.frame_rate}")
    if facts.settings.width <= 0 or facts.settings.height <= 0:
        problems.append("the timeline has no size")
    known = {t.track_id for t in facts.tracks}
    for clip in facts.clips:
        if clip.track_id not in known:
            problems.append(f"{clip.clip_id} is on a track the timeline does not have")
        if clip.end_frame <= clip.start_frame:
            problems.append(f"{clip.clip_id} has no length")
    return problems


def _tracks(facts: TimelineFacts) -> tuple[tuple[ExpectedTrack, ...], dict[str, int]]:
    """Resolve's tracks, bottom to top: the builder's stacking order, then its lane."""
    found: list[ExpectedTrack] = []
    index_of: dict[str, int] = {}
    for kind in ("video", "audio"):
        ordered = sorted(
            (t for t in facts.tracks if t.kind == kind), key=lambda t: (t.index, t.lane, t.track_id)
        )
        for position, track in enumerate(ordered, start=1):
            index_of[track.track_id] = position
            found.append(ExpectedTrack(track.track_id, kind, position, track.name, track.enabled))
    return tuple(found), index_of


def _settings(settings: SettingsFact, gaps: list[Gap]) -> tuple[tuple[str, str], ...]:
    rate = frame_rate_text(settings.frame_rate) or ""
    values = {
        "timelineFrameRate": rate,
        "timelinePlaybackFrameRate": rate,
        "timelineResolutionWidth": str(settings.width),
        "timelineResolutionHeight": str(settings.height),
    }
    if settings.color_profile != "rec709":
        gaps.append(
            Gap(
                element_id="timeline",
                operation="project.settings",
                strategy=Strategy.NOT_APPLIED,
                detail=(
                    f"colour profile {settings.color_profile} needs Resolve's colour-managed "
                    "settings; they are not set here and the project keeps its colour science"
                ),
                alternatives=("set the colour management in Resolve to match the timeline",),
            )
        )
    return tuple(sorted(values.items()))


def _item(
    clip: ClipFact,
    facts: TimelineFacts,
    index_of: dict[str, int],
    config: ResolveAdapterConfig,
    matrix: CapabilityMatrix,
    gaps: list[Gap],
    markers: list[ExpectedMarker],
) -> ExpectedItem | None:
    if not clip.resolved or clip.asset_id is None:
        gaps.append(
            Gap(
                clip.clip_id,
                "clip.place",
                Strategy.NOT_APPLIED,
                "the file this clip plays is not available, so the clip was not placed",
                placed=False,
            )
        )
        return None
    media = facts.media_of(clip.asset_id)
    src_frames = clip.source_frames
    props: dict[str, float] = {}
    motion: tuple[ZoomKey, ...] = ()
    blur: tuple[BlurKey, ...] = ()
    card: tuple[float, float, float, float] | None = None

    if clip.retime is not None and clip.retime.applied_by_application:
        placed = config.retime_placement is RetimePlacement.PLACE_WITH_MARKER
        gap = _gap(
            clip,
            "clip.retime",
            matrix,
            f"{clip.retime.kind} retime ({clip.retime.detail or 'see the specification'}) "
            "must be applied in Resolve",
            placed=placed,
        )
        gaps.append(gap)
        if not placed:
            return None
        src_frames = clip.frames  # real speed: the clip keeps its place and length
        _instruct(clip, gap, config, markers)

    if clip.kind == "video":
        if clip.crop is not None and media is not None:
            if (
                clip.crop.card is None
                and _reshapes(media, facts.settings)
                and matrix.usable("clip.crop_static")
            ):
                # shortcut: a window that changes the aspect (16:9 into 9:16) is held still (at the
                # clip's middle); a Fusion zoom keeps the source aspect, so following a moving one
                # needs a crop-to-frame node first. Upgrade when a reframe drifts visibly.
                props.update(_crop_props(clip, media, facts.settings))
                if matrix.usable("clip.crop_fusion") and any(
                    abs(k.size - 1.0) > 1e-6 for k in clip.crop.motion
                ):
                    motion = clip.crop.motion  # a camera move inside the held window
            elif clip.crop.moves and clip.crop.motion and matrix.usable("clip.crop_fusion"):
                motion, card = clip.crop.motion, clip.crop.card
            elif clip.crop.moves:
                gap = _gap(
                    clip,
                    "clip.crop_keyframed",
                    matrix,
                    f"the reframing window moves ({clip.crop.keys} keys); "
                    "Resolve's scripting has no keyframes",
                )
                gaps.append(gap)
                _instruct(clip, gap, config, markers)
            elif matrix.usable("clip.crop_static"):
                props.update(_crop_props(clip, media, facts.settings))
            else:
                gaps.append(
                    _gap(clip, "clip.crop_static", matrix, "the reframing window cannot be set")
                )
        elif media is not None:
            props.update(_fit_props(clip.fit, media, facts.settings))
        if clip.blur and matrix.usable("clip.crop_fusion"):
            blur = clip.blur
        if clip.opacity != 1.0:
            props["Opacity"] = clip.opacity * 100.0
        if clip.native_effects:
            gap = _gap(
                clip,
                "effect.native",
                matrix,
                "effects to apply in Resolve: " + ", ".join(clip.native_effects),
            )
            gaps.append(gap)
            _instruct(clip, gap, config, markers)
    else:
        volume = clip.gain_db is not None and matrix.usable("audio.clip_volume")
        if clip.gain_db is not None and volume:
            props["AudioVolume"] = round(max(_VOLUME_DB[0], min(_VOLUME_DB[1], clip.gain_db)), 1)
        elif clip.gain_db is not None:
            gaps.append(
                Gap(
                    clip.clip_id,
                    "audio.clip_volume",
                    Strategy.NOT_APPLIED,
                    "the clip plays at 0 dB, not at its mix level",
                    alternatives=matrix.get("audio.clip_volume").alternatives,
                )
            )
        if clip.mix_work:
            gaps.append(
                Gap(
                    clip.clip_id,
                    "audio.gain_fades",
                    Strategy.NOT_APPLIED,
                    "fades and ducking are mix instructions for audio_mix_engine"
                    + (" (the clip's level is set)" if volume else ""),
                    alternatives=matrix.get("audio.gain_fades").alternatives,
                    owner="audio_mix_engine",
                )
            )
    return ExpectedItem(
        element_id=clip.clip_id,
        kind=clip.kind,
        track_index=index_of[clip.track_id],
        asset_id=clip.asset_id,
        name=clip.name,
        start=clip.start_frame,
        duration=clip.frames,
        src_start=clip.source_in_frame,
        src_frames=src_frames,
        enabled=clip.enabled,
        props=tuple(sorted(props.items())),
        link=clip.link,
        digest=clip.digest,
        motion=motion,
        blur=blur,
        card=card,
        gradable=clip.kind == "video" and clip.clip_kind in {"primary", "cover"} and card is None,
    )


def _gap(
    clip: ClipFact, operation: str, matrix: CapabilityMatrix, detail: str, *, placed: bool = True
) -> Gap:
    capability = matrix.get(operation)
    return Gap(
        element_id=clip.clip_id,
        operation=operation,
        strategy=Strategy.MANUAL_MARKER if placed else Strategy.NOT_APPLIED,
        detail=detail,
        alternatives=capability.alternatives,
        placed=placed,
        owner=capability.owner,
    )


def _instruct(
    clip: ClipFact, gap: Gap, config: ResolveAdapterConfig, markers: list[ExpectedMarker]
) -> None:
    """A marker where a person finishes what scripting cannot (one step in Resolve)."""
    if not config.instruction_markers or gap.strategy is not Strategy.MANUAL_MARKER:
        return
    markers.append(
        ExpectedMarker(
            custom=f"{TAG_PREFIX}gap:{clip.clip_id}:{gap.operation}",
            frame=clip.start_frame,
            color="Red",
            name=f"TODO {gap.operation.split('.')[-1]} · {clip.name}"[:60],
            note=gap.detail,
            duration=max(1, min(clip.frames, 24)),
        )
    )


def _transition(
    t: TransitionFact,
    config: ResolveAdapterConfig,
    gaps: list[Gap],
    markers: list[ExpectedMarker],
) -> None:
    """Scripting cannot add a transition. The handles are in place; a marker says what to add."""
    if t.mode in {"cut", "overlay"}:
        return  # a cut needs nothing; an overlay is a clip, placed as one
    detail = (
        f"{t.mode} of {t.duration_frames} frames at frame {t.cut_frame}"
        f" ({t.alignment or 'centered'}); {t.tail_frames} frames read past the outgoing clip and"
        f" {t.head_frames} before the incoming one"
    )
    if not t.supported:
        detail = "not in the vocabulary (" + "; ".join(t.reasons or (t.kind,)) + "): " + detail
    gap = Gap(
        element_id=t.transition_id,
        operation="transition.insert",
        strategy=Strategy.MANUAL_MARKER if config.instruction_markers else Strategy.NOT_APPLIED,
        detail=detail,
        alternatives=(
            "a timeline interchange file (FCPXML / OTIO) that carries the transition",
            "select the cut and add the transition in Resolve",
        ),
    )
    gaps.append(gap)
    if config.instruction_markers:
        markers.append(
            ExpectedMarker(
                custom=f"{TAG_PREFIX}gap:{t.transition_id}:transition.insert",
                frame=t.cut_frame,
                color="Yellow",
                name=f"TRANSITION {t.mode} {t.duration_frames}f"[:60],
                note=detail,
                duration=1,
            )
        )


def _marker(m: MarkerFact) -> ExpectedMarker:
    if m.kind == "section":
        color = "Blue"
    elif m.severity in {"error", "unsupported"}:
        color = "Red"
    else:
        color = "Yellow"
    return ExpectedMarker(
        custom=f"{TAG_PREFIX}marker:{m.marker_id}",
        frame=m.frame,
        color=color,
        name=m.name[:60],
        note=m.note,
        duration=max(1, m.duration_frames),
    )


# --- picture geometry -----------------------------------------------------------------------------
#
# Resolve scales a file whose shape differs from the project to fit by default ("scale entire image
# to fit"), so ZoomX / ZoomY are relative to that fitted picture. These formulas assume that default
# (a person can see and change it in the project settings); a clip that matches the project needs
# nothing.


def _fit_props(fit: str, media: MediaFact, settings: SettingsFact) -> dict[str, float]:
    if not media.width or not media.height:
        return {}
    tw, th, sw, sh = settings.width, settings.height, media.width, media.height
    if tw * sh == th * sw and (fit == "contain" or tw == sw):
        return {}
    contain = min(tw / sw, th / sh)
    if fit == "contain":
        return {}
    if fit == "cover":
        zoom = max(tw / sw, th / sh) / contain
        return {} if abs(zoom - 1.0) < 1e-6 else {"ZoomX": zoom, "ZoomY": zoom}
    if fit == "stretch":
        zx, zy = (tw / sw) / contain, (th / sh) / contain
        return {} if abs(zx - 1.0) + abs(zy - 1.0) < 1e-6 else {"ZoomX": zx, "ZoomY": zy}
    zoom = 1.0 / contain  # none: pixels 1:1
    return {} if abs(zoom - 1.0) < 1e-6 else {"ZoomX": zoom, "ZoomY": zoom}


def _reshapes(media: MediaFact, settings: SettingsFact) -> bool:
    """The source picture has another aspect than the timeline (a reframed format)."""
    if not media.width or not media.height:
        return False
    return abs(media.width / media.height - settings.width / settings.height) >= 0.01


def _crop_props(clip: ClipFact, media: MediaFact, settings: SettingsFact) -> dict[str, float]:
    """The window as zoom and pan: the window fills the frame width, centred on the window."""
    crop = clip.crop
    if crop is None or not media.width or not media.height or crop.w <= 0:
        return {}
    tw, th, sw, sh = settings.width, settings.height, media.width, media.height
    contain = min(tw / sw, th / sh)
    scale = tw / (crop.w * sw)  # pixels of the timeline per source pixel
    zoom = scale / contain
    centre_x = crop.x + crop.w / 2
    centre_y = crop.y + crop.h / 2
    pan = (0.5 - centre_x) * sw * scale
    tilt = (centre_y - 0.5) * sh * scale  # positive tilt moves the picture up
    found = {"ZoomX": zoom, "ZoomY": zoom}
    if abs(pan) > 1e-6:
        found["Pan"] = pan
    if abs(tilt) > 1e-6:
        found["Tilt"] = tilt
    return found


# --- pool folders ---------------------------------------------------------------------------------


def folder_for(media: MediaFact, root: str) -> tuple[str, ...]:
    """The media pool folder a file belongs in, from what it is and what plays it."""
    roles = set(media.roles)
    if media.relation == "original":
        return (root, "Original audio" if media.media_type == "audio" else "Original footage")
    if media.relation in {"improved_video", "improved_audio"}:
        return (root, "Processed media")
    if media.relation == "proxy":
        return (root, "Proxies")
    if "music" in roles:
        return (root, "Music")
    if "sfx" in roles:
        return (root, "Sound effects")
    if "ambience" in roles:
        return (root, "Ambience")
    if "captions" in roles:
        return (root, "Captions")
    if "graphics" in roles:
        return (root, "Graphics")
    if media.relation == "rendered":
        return (root, "Generated")
    return (root, "Library")
