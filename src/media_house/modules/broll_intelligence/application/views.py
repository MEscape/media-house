"""Upstream results -> the plain facts the domain reads. The only place that knows their shapes.

Each function states what it takes from which owner and what it leaves out; a value an owner could
not state becomes ``None``. Names are read from the TEXT of things (what the passage says, what the
footage shows) with the content analysis's own ``designations``: nothing here decides what a
product is called.
"""

import hashlib
from collections.abc import Iterable, Sequence
from fractions import Fraction

from media_house.modules.asset_relationships.application.contracts import (
    RelationshipPage,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentDocument,
    ProbeState,
    Visibility,
)
from media_house.modules.broll_intelligence.domain.facts import (
    EventFact,
    MainPicture,
    NameFact,
    ReferenceFact,
    RelationFact,
    ShotFact,
    SoundActionFact,
    SourceAudioFact,
    StoryFacts,
    TargetFact,
)
from media_house.modules.broll_intelligence.domain.text import reduce
from media_house.modules.broll_intelligence.domain.values import AssetVia, ClockBasis, TargetOrigin
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    Designation,
    Reference,
    ReferenceState,
    Unit,
    designations,
    resolve_references,
    stance_of,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.pacing_intelligence.application.contracts import PacingAnalysis
from media_house.modules.story_architecture.application.contracts import StoryPlan
from media_house.modules.video_intelligence.application.contracts import (
    IndexedAsset,
    IndexedShot,
    VideoAnalysis,
)

_CONTEXT_STATEMENTS = 3
_PASSAGE_PAD = 0.0
_SPEECH_GAP = Fraction(3, 5)
_EMPHATIC = 0.65
_JUMP_CUT_MIN = 0.7
_JUMP_WINDOW = (1.0, 2.0)
_OVERLAP_SKIP = 0.5
_SPEAKING_MIN = 0.3


def digest(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def name_fact(d: Designation) -> NameFact:
    return NameFact(family=d.family, value=d.value, text=d.text)


def fraction(value: object) -> Fraction:
    return Fraction(value.numerator, value.denominator)  # type: ignore[attr-defined]


# --- the story ---------------------------------------------------------------------------------
def story_facts(plan: StoryPlan, stored_as: str | None) -> StoryFacts:
    return StoryFacts(
        story_plan_id=plan.story_plan_id,
        plan_version=plan.plan_version,
        stored_as=stored_as,
        target_format=plan.target_format,
    )


# --- passages ----------------------------------------------------------------------------------
class TargetBuilder:
    """Builds the passages of ONE recording from the story, the content analysis, the pacing
    analysis and the recording's own picture; higher sources win where passages overlap."""

    def __init__(
        self,
        asset_id: str,
        content: ContentAnalysis | None,
        story: StoryPlan | None,
        video: VideoAnalysis | None,
        audio: AudioIntelligenceTimeline | None,
        alignment: AlignmentDocument | None,
        pacing: PacingAnalysis | None,
        correction: float = 0.0,
    ) -> None:
        self._asset = asset_id
        self._content = content
        self._story = story
        self._video = video
        self._audio = audio
        self._alignment = alignment
        self._pacing = pacing
        self._correction = correction
        self._units = list(content.units) if content is not None else []
        self._refs: dict[str, list[Reference]] = {}
        if content is not None:
            for ref in resolve_references(content):
                self._refs.setdefault(ref.unit_id, []).append(ref)

    def build(self) -> list[TargetFact]:
        out: list[TargetFact] = []
        for candidate in (
            *self._from_story(),
            *self._from_content(),
            *self._from_pacing(),
            *self._jump_cuts(),
        ):
            # Passages of one source never hide each other; a weaker source yields to a stronger.
            if not any(
                kept.origin is not candidate.origin and _overlap(candidate, kept) >= _OVERLAP_SKIP
                for kept in out
            ):
                out.append(candidate)
        return sorted(out, key=lambda t: (t.start, t.target_id))

    # --- sources of passages -------------------------------------------------------------------
    def _from_story(self) -> Iterable[TargetFact]:
        plan = self._story
        if plan is None:
            return
        importance = {s.section_id: s.importance for s in plan.sections}
        beats = list(plan.beats)
        for visual in plan.visual_opportunities:
            ref = visual.source
            if ref.asset_id != self._asset or ref.start is None or ref.end is None:
                continue
            units = self._units_of(ref.unit_ids, ref.start, ref.end)
            start, end = ref.start, ref.end
            narrowed = self._narrow(units, visual.concept)
            if narrowed is not None:
                units = [narrowed]
                start = narrowed.span.start if narrowed.span.start is not None else start
                end = narrowed.span.end if narrowed.span.end is not None else end
            beat = next(
                (
                    b
                    for b in beats
                    if b.section_id == visual.section_id
                    and set(b.source.unit_ids) & set(ref.unit_ids)
                ),
                next((b for b in beats if b.section_id == visual.section_id), None),
            )
            yield self._target(
                f"bt:{visual.visual_id}",
                TargetOrigin.STORY_VISUAL,
                start,
                end,
                units,
                concept=visual.concept,
                purpose=visual.purpose.value,
                necessity=visual.necessity.value,
                strength=visual.strength,
                already_covered=visual.already_covered,
                section_id=visual.section_id,
                beat_id=beat.beat_id if beat else None,
                beat_function=beat.function.value if beat else "",
                story_visual_id=visual.visual_id,
                importance=importance.get(visual.section_id),
            )

    @staticmethod
    def _narrow(units: Sequence[Unit], concept: str) -> Unit | None:
        """The one statement a visual is about, when its source spans several.

        The story may give a whole section as the visual's source; its ``concept`` is the verbatim
        phrase to show, so the statement sharing most of its words is the passage. No shared word
        keeps the whole source (nothing is guessed)."""
        if len(units) < 2 or not concept:
            return None
        wanted = set(reduce(concept))
        scored = [(len(wanted & set(reduce(u.text))), u) for u in units]
        best = max(scored, key=lambda item: item[0])
        return best[1] if best[0] > 0 else None

    def _from_content(self) -> Iterable[TargetFact]:
        if self._content is None:
            return
        for segment in self._content.segments:
            for opp in segment.broll:
                if opp.span.start is None or opp.span.end is None:
                    continue
                units = self._units_of(opp.unit_ids, opp.span.start, opp.span.end)
                yield self._target(
                    f"bt:{opp.opportunity_id}",
                    TargetOrigin.CONTENT_OPPORTUNITY,
                    opp.span.start,
                    opp.span.end,
                    units,
                    concept=opp.concept,
                    category=opp.category,
                    strength=opp.strength,
                    already_covered=opp.already_covered,
                    segment_id=segment.segment_id,
                )

    def _from_pacing(self) -> Iterable[TargetFact]:
        if self._pacing is None:
            return
        for rec in self._pacing.recommendations:
            if rec.type.value != "add_visual_change" or rec.target.asset_id != self._asset:
                continue
            start, end = rec.target.start.value, rec.target.end.value
            yield self._target(
                f"bt:{rec.recommendation_id}",
                TargetOrigin.PACING,
                start,
                end,
                self._units_of((), start, end),
                reason=rec.observed or rec.proposal,
                section_id=rec.section_id,
                beat_id=rec.beat_id,
            )

    def _jump_cuts(self) -> Iterable[TargetFact]:
        if self._video is None:
            return
        for shot in self._video.shots:
            cont = shot.continuity
            if not cont.ok or cont.jump_cut_likelihood is None:
                continue
            if cont.jump_cut_likelihood < _JUMP_CUT_MIN or shot.index == 0:
                continue
            at = shot.range.start.seconds + self._correction
            start, end = max(0.0, at - _JUMP_WINDOW[0]), at + _JUMP_WINDOW[1]
            units = self._units_of((), start, end)
            if not units:
                continue  # nothing is said across the cut: nothing to put footage under
            yield self._target(
                f"bt:cut:{shot.shot_id}",
                TargetOrigin.JUMP_CUT,
                start,
                end,
                units,
                reason=f"jump cut likelihood {cont.jump_cut_likelihood:.2f} into {shot.shot_id}",
            )

    # --- one passage ---------------------------------------------------------------------------
    def _target(
        self,
        target_id: str,
        origin: TargetOrigin,
        start: float,
        end: float,
        units: Sequence[Unit],
        *,
        concept: str = "",
        purpose: str = "",
        category: str = "",
        necessity: str = "",
        strength: float | None = None,
        already_covered: bool = False,
        section_id: str | None = None,
        beat_id: str | None = None,
        beat_function: str = "",
        story_visual_id: str | None = None,
        segment_id: str | None = None,
        reason: str = "",
        importance: float | None = None,
    ) -> TargetFact:
        text = " ".join(u.text for u in units)
        named: dict[str, NameFact] = {}
        for d in (*designations(text), *designations(concept)):
            named.setdefault(d.key, name_fact(d))
        references: list[ReferenceFact] = []
        for u in units:
            for ref in self._refs.get(u.unit_id, ()):
                resolved = ref.state is ReferenceState.RESOLVED and ref.designation is not None
                if resolved and ref.designation is not None:
                    named.setdefault(ref.designation.key, name_fact(ref.designation))
                references.append(
                    ReferenceFact(
                        phrase=ref.phrase,
                        state=ref.state.value,
                        name=name_fact(ref.designation) if ref.designation else None,
                        reason=ref.reason,
                        about_activity=ref.about_activity,
                    )
                )
        first = self._units.index(units[0]) if units else 0
        context = tuple(u.text for u in self._units[max(0, first - _CONTEXT_STATEMENTS) : first])
        stance = stance_of(text)
        return TargetFact(
            target_id=target_id,
            origin=origin,
            asset_id=self._asset,
            start=start,
            end=end,
            text=text,
            context=context,
            concept=concept,
            purpose=purpose,
            category=category,
            necessity=necessity,
            strength=strength,
            already_covered=already_covered,
            section_id=section_id,
            beat_id=beat_id,
            beat_function=beat_function,
            story_visual_id=story_visual_id,
            segment_id=segment_id,
            names=tuple(named.values()),
            references=tuple(references),
            negated=stance.negated or stance.hypothetical,
            main=self._main(start, end),
            emphatic=self._emphatic(start, end),
            reason=reason,
            importance=importance,
        )

    def _units_of(self, unit_ids: Sequence[str], start: float, end: float) -> list[Unit]:
        if unit_ids:
            wanted = set(unit_ids)
            found = [u for u in self._units if u.unit_id in wanted]
            if found:
                return found
        return [
            u
            for u in self._units
            if u.span.start is not None
            and u.span.end is not None
            and u.span.start < end
            and u.span.end > start
        ]

    def _main(self, start: float, end: float) -> MainPicture | None:
        if self._video is None:
            return None
        middle = (start + end) / 2 - self._correction
        shot = next(
            (s for s in self._video.shots if s.range.start.seconds <= middle < s.range.end.seconds),
            None,
        )
        if shot is None:
            return None
        speaking: bool | None = None
        if shot.faces.ok and shot.faces.visual_speaking is not None:
            speaking = shot.faces.visual_speaking >= _SPEAKING_MIN
        if self._alignment is not None:
            visible = [
                s.visibility
                for s in self._alignment.speech_sources
                if s.start < end and s.end > start
            ]
            if visible:
                speaking = Visibility.SPEAKING_FACE in visible
        meaning = shot.meaning
        return MainPicture(
            shot_id=shot.shot_id,
            speaking_face=speaking,
            jump_cut_likelihood=shot.continuity.jump_cut_likelihood if shot.continuity.ok else None,
            environment=meaning.environment if meaning.ok else None,
            indoor_outdoor=meaning.indoor_outdoor if meaning.ok else None,
            framing=shot.framing.framing.value
            if shot.framing.ok and shot.framing.framing
            else None,
            shot_seconds=shot.range.seconds,
        )

    def _emphatic(self, start: float, end: float) -> bool | None:
        if self._audio is None:
            return None
        values = [
            w.emphasis_score
            for w in self._audio.words_between(start, end)
            if w.emphasis_score is not None
        ]
        laughter = any(e.kind == "laughter" for e in self._audio.events_between(start, end))
        if not values and not laughter:
            return None
        return laughter or (max(values) if values else 0.0) >= _EMPHATIC


def _overlap(a: TargetFact, b: TargetFact) -> float:
    if a.asset_id != b.asset_id:
        return 0.0
    shared = min(a.end, b.end) - max(a.start, b.start)
    length = max(1e-9, min(a.end - a.start, b.end - b.start))
    return max(0.0, shared) / length


# --- footage -----------------------------------------------------------------------------------
def shot_facts(
    indexed: IndexedAsset,
    analysed: MediaAssetDto,
    original: MediaAssetDto,
    via: AssetVia,
) -> list[ShotFact]:
    """The shots of one analysed version, attributed to the ORIGINAL asset."""
    index = indexed.index
    duration = (
        Fraction(index.duration_seconds).limit_denominator(1000)
        if index.duration_seconds
        else (
            Fraction(original.duration_seconds).limit_denominator(1000)
            if original.duration_seconds
            else None
        )
    )
    meta_names = [
        NameFact(family=d.family, value=d.value, text=d.text)
        for d in designations(" ".join((original.display_name, *original.tags)))
    ]
    identity = digest(
        indexed.asset.id, indexed.index.analysis_identity, indexed.index.index_version
    )
    return [_shot(s, original, analysed, via, duration, meta_names, identity) for s in index.shots]


def _shot(
    s: IndexedShot,
    original: MediaAssetDto,
    analysed: MediaAssetDto,
    via: AssetVia,
    duration: Fraction | None,
    meta_names: list[NameFact],
    identity: str,
) -> ShotFact:
    names: list[tuple[NameFact, str]] = []
    for text in s.texts:
        names += [(name_fact(d), "text") for d in designations(text)]
    names += [(n, "metadata") for n in meta_names]
    if s.description:
        names += [(name_fact(d), "description") for d in designations(s.description)]
    return ShotFact(
        asset_id=original.id,
        analyzed_asset_id=analysed.id,
        via=via,
        asset_name=original.display_name,
        shot_id=s.shot_id,
        index=s.index,
        start=fraction(s.start),
        end=fraction(s.end),
        clock=ClockBasis.VIDEO_ANALYSIS,
        asset_duration=duration,
        tags=tuple(original.tags),
        scene_id=s.scene_id,
        objects=s.objects,
        entity_labels=s.entity_labels,
        description=s.description,
        environment=s.environment,
        indoor_outdoor=s.indoor_outdoor,
        content_label=s.content_label,
        texts=s.texts,
        screen_kind=s.screen_kind,
        names=tuple(dict.fromkeys(names)),
        scores=tuple(sorted(s.scores.items())),
        sharpness=s.sharpness,
        motion_energy=s.motion_energy,
        static_fraction=s.static_fraction,
        camera_movement=s.camera_movement,
        framing=s.framing,
        face_fraction=s.face_fraction,
        visual_speaking=s.visual_speaking,
        indicators=s.indicators,
        head_seconds=s.head_seconds,
        tail_seconds=s.tail_seconds,
        duplicate_group=s.duplicate_group,
        events=tuple(
            EventFact(
                kind=e.kind,
                start=fraction(e.start),
                end=fraction(e.end) if e.end is not None else None,
                strength=e.strength,
            )
            for e in s.events
        ),
        analysis_identity=identity,
    )


def audio_fact(timeline: AudioIntelligenceTimeline) -> SourceAudioFact:
    """Where the footage's own sound has speech (words closer than 0.6 s join) and other events."""
    speech: list[list[Fraction]] = []
    for word in timeline.words:
        start, end = (
            Fraction(word.start).limit_denominator(1000),
            Fraction(word.end).limit_denominator(1000),
        )
        if speech and start - speech[-1][1] <= _SPEECH_GAP:
            speech[-1][1] = max(speech[-1][1], end)
        else:
            speech.append([start, end])
    events = tuple(
        (e.kind, Fraction(e.start).limit_denominator(1000), Fraction(e.end).limit_denominator(1000))
        for e in timeline.events
    )
    return SourceAudioFact(
        speech=tuple((a, b) for a, b in speech),
        events=events,
        identity=digest(timeline.metadata.duration, len(timeline.words), len(timeline.events)),
    )


def alignment_parts(
    document: AlignmentDocument,
) -> tuple[Fraction, tuple[SoundActionFact, ...], bool | None]:
    """The recording's clock correction, its sound-action pairings and whether its sync holds."""
    correction = Fraction(document.video_correction).limit_denominator(1_000_000)
    actions = []
    for a in document.sound_actions:
        strength = a.scores.strength
        grade = (
            "corroborated"
            if strength is not None and strength >= 0.7 and a.chance_hits < 0.5
            else "indicated"
            if strength is not None and strength >= 0.4
            else "suggestive"
        )
        time = Fraction(a.video_time.pts) * Fraction(
            a.video_time.timebase.numerator, a.video_time.timebase.denominator
        )
        actions.append(
            SoundActionFact(
                video_time=time + correction,
                audio_kind=a.audio_kind,
                delta=a.delta_seconds,
                grade=grade,
            )
        )
    state = document.sync.probe.state
    established = (
        True
        if state is ProbeState.CONSISTENT
        else None
        if state is ProbeState.INSUFFICIENT
        else False
    )
    return correction, tuple(actions), established


def relation_facts(page: RelationshipPage, asset_id: str) -> tuple[RelationFact, ...]:
    kinds = {
        RelationshipType.ALTERNATE_TAKE_OF,
        RelationshipType.TEMPORALLY_OVERLAPS_WITH,
        RelationshipType.EXACT_DUPLICATE_OF,
    }
    out = []
    for r in page.items:
        if r.type not in kinds or r.status in (
            RelationshipStatus.REJECTED,
            RelationshipStatus.STALE,
        ):
            continue
        other = r.target_asset_id if r.source_asset_id == asset_id else r.source_asset_id
        certainty = (
            "recorded"
            if r.status is None
            else "confirmed"
            if r.status is RelationshipStatus.CONFIRMED
            else "detected"
        )
        out.append(RelationFact(other_asset_id=other, kind=r.type.value, certainty=certainty))
    return tuple(out)
