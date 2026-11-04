"""The edit as every Layer 4 task sees it: the one place that reads an ``EditPlan`` for Layer 4.

Layer 4 modules never import the edit plan (each owns a narrow input DTO). The pipeline maps
the plan ONCE into this neutral view, and ``layer4_inputs`` maps the view into each module's DTO,
so clip ids, section spans, beat functions and the frame rate are computed in one place and are
identical in every plan (continuity's and transition's findings line up on ``placement_id``).

Without an edit plan (no speech: a meme, a compilation, an uncut take; or no edit requested) the
view is built from the inspection instead (contracts.md sec. 2, NO-SPEECH PATH): one clip of the
whole recording, timeline = source (start 0, speed 1), the inspected frame rate, no sections,
beats, effects or bridges, ``project_id=None``.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.application.contracts import EditPlan, TrackKind
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.story_architecture.application.contracts import StoryPlan

#: shortcut: the frame rate assumed when the inspection states none (audio-only media); the
#: view says so (``frame_rate_assumed``) and seconds stay authoritative.
_ASSUMED_RATE = Rational(30, 1)


def fr(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def rat(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)


def seconds(value: float) -> Fraction:
    """A probe's decimal seconds as the exact decimal they were printed as (not the binary)."""
    return Fraction(repr(value))


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewClip:
    """One placed range of a recording at speed 1 (an edit placement, or the whole recording)."""

    clip_id: str
    asset_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    #: On the picture track (``False``: an audio-track placement such as a voice-over).
    picture: bool = True
    #: 0 = main, 1+ = cover.
    layer: int = 0
    #: ``source_sync`` | ``voiceover`` | ``muted`` (the edit's audio behaviour of the clip).
    audio: str = "source_sync"
    section_id: str | None = None
    beat_id: str | None = None
    beat_function: str | None = None

    @property
    def timeline_end(self) -> Rational:
        return rat(fr(self.timeline_start) + fr(self.source_end) - fr(self.source_start))


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewSection:
    section_id: str
    order: int
    label: str
    purpose: str
    #: Earliest start and latest end of the section's placements on the timeline.
    timeline_start: Rational
    timeline_end: Rational
    beat_functions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewEffect:
    """An editorial effect request (``EffectRequest``) at a TIMELINE time."""

    request_id: str
    kind: str
    intent: str
    timeline_at: Rational
    duration: Rational | None
    section_id: str | None
    priority: float
    reason: str
    decision_id: str | None = None
    #: The verbatim phrase a graphic should show (from the coverage request it came from).
    concept: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewBridge:
    """A J/L cut: the sound changes at another time than the picture."""

    bridge_id: str
    kind: str
    audio_at: Rational
    picture_at: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class EditView:
    anchor_asset_id: str
    #: ``None`` on the no-speech path.
    edit_plan_id: str | None
    plan_version: int | None
    project_id: str | None
    frame_rate: Rational
    frame_rate_assumed: bool
    duration: Rational
    story_format: str | None
    clips: tuple[ViewClip, ...]
    sections: tuple[ViewSection, ...] = ()
    effects: tuple[ViewEffect, ...] = ()
    bridges: tuple[ViewBridge, ...] = ()

    @property
    def has_edit(self) -> bool:
        return self.edit_plan_id is not None

    def picture(self) -> tuple[ViewClip, ...]:
        return tuple(c for c in self.clips if c.picture)

    def sound(self) -> tuple[ViewClip, ...]:
        """The clips whose sound the edit plays: the audio track, else the main picture."""
        audio = tuple(c for c in self.clips if not c.picture)
        return audio or tuple(c for c in self.clips if c.picture and c.layer == 0)

    def effects_of(self, *kinds: str) -> tuple[ViewEffect, ...]:
        return tuple(e for e in self.effects if e.kind in kinds)


def view_of(plan: EditPlan, story: StoryPlan | None) -> EditView:
    """The edit plan as Layer 4 needs it. ``story`` adds beat functions and section labels;
    without it sections keep their ids and are ordered by time."""
    beats = {b.beat_id: b.function.value for b in story.beats} if story else {}
    clips = tuple(
        sorted(
            (
                ViewClip(
                    clip_id=p.placement_id,
                    asset_id=p.source.asset_id,
                    source_start=p.source.start,
                    source_end=p.source.end,
                    timeline_start=p.timeline_start,
                    picture=p.track is TrackKind.VIDEO,
                    layer=p.layer,
                    audio=p.audio.value,
                    section_id=p.section_id,
                    beat_id=p.beat_id,
                    beat_function=beats.get(p.beat_id) if p.beat_id else None,
                )
                for p in plan.placements
            ),
            key=lambda c: (fr(c.timeline_start), not c.picture, c.layer, c.clip_id),
        )
    )
    concepts = {c.request_id: c.concept for c in plan.coverage}
    anchor = plan.sources[0].asset_id if plan.sources else clips[0].asset_id
    return EditView(
        anchor_asset_id=anchor,
        edit_plan_id=plan.edit_plan_id,
        plan_version=plan.plan_version,
        project_id=plan.project_id,
        frame_rate=plan.timebase.frame_rate,
        frame_rate_assumed=plan.timebase.assumed,
        duration=plan.duration.estimated,
        story_format=plan.target_format,
        clips=clips,
        sections=_sections(clips, story),
        effects=tuple(
            ViewEffect(
                request_id=e.request_id,
                kind=e.kind.value,
                intent=e.intent.value,
                timeline_at=e.at,
                duration=e.duration,
                section_id=e.section_id,
                priority=e.priority,
                reason=e.reason,
                decision_id=e.decision_id,
                concept=concepts.get(e.decision_id or "", ""),
            )
            for e in plan.effect_requests
        ),
        bridges=tuple(
            ViewBridge(
                bridge_id=b.bridge_id,
                kind=b.kind.value,
                audio_at=b.audio_changes_at,
                picture_at=b.picture_changes_at,
            )
            for b in plan.audio_bridges
        ),
    )


def _sections(clips: tuple[ViewClip, ...], story: StoryPlan | None) -> tuple[ViewSection, ...]:
    spans: dict[str, tuple[Fraction, Fraction]] = {}
    for c in clips:
        if c.section_id is None:
            continue
        start, end = fr(c.timeline_start), fr(c.timeline_end)
        low, high = spans.get(c.section_id, (start, end))
        spans[c.section_id] = (min(low, start), max(high, end))
    found = []
    for position, (section_id, (start, end)) in enumerate(
        sorted(spans.items(), key=lambda item: (item[1][0], item[0]))
    ):
        section = story.section(section_id) if story else None
        found.append(
            ViewSection(
                section_id=section_id,
                order=section.order if section else position,
                label=section.label if section else section_id,
                purpose=section.purpose.text if section else "",
                timeline_start=rat(start),
                timeline_end=rat(end),
                beat_functions=tuple(b.function.value for b in story.beats_of(section_id))
                if story
                else (),
            )
        )
    return tuple(found)


def no_speech_view(
    asset_id: str, inspection: InspectionResult, story_format: str | None = None
) -> EditView | None:
    """The whole recording as one clip, from its stored inspection (nothing is probed).
    ``None`` when the inspection states no duration (nothing can be placed)."""
    media = inspection.inspection
    video = media.primary_video
    audio = media.audio_stream()
    duration = next(
        (
            d
            for d in (
                media.container.duration,
                video.duration if video else None,
                audio.duration if audio else None,
            )
            if d
        ),
        None,
    )
    if duration is None or duration <= 0:
        return None
    rate = None
    if video is not None:
        rate = video.frame_rate.value or video.average_frame_rate.value
    variable = (
        video is not None and video.timing is not None and video.timing.mode.value == "variable"
    )
    length = rat(seconds(duration))
    return EditView(
        anchor_asset_id=asset_id,
        edit_plan_id=None,
        plan_version=None,
        project_id=None,
        frame_rate=rate or _ASSUMED_RATE,
        frame_rate_assumed=rate is None or variable,
        duration=length,
        story_format=story_format,
        clips=(
            ViewClip(
                clip_id=f"clip:{asset_id}",
                asset_id=asset_id,
                source_start=Rational(0, 1),
                source_end=length,
                timeline_start=Rational(0, 1),
                picture=video is not None,
            ),
        ),
    )
