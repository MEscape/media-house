"""Hand-built recordings and stories for the editing-intelligence tests.

``talk`` lays sentences out in time (each word 0.3 s long, a gap before each sentence you choose)
and returns the matching content, audio and source facts, so a test states the situation ("a 2 s
pause before the third sentence") instead of building analyses. ``story`` turns groups of those
sentences into the sections of a story plan. Nothing here needs a model, a file or a library.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.engine import PlanMeta, plan_edit
from media_house.modules.editing_intelligence.domain.facts import (
    AlignmentFacts,
    AssetEvidence,
    AudioFacts,
    BeatFact,
    ConstraintFact,
    ContentFacts,
    ContextFact,
    EventFact,
    KeyInfoFact,
    LinkFact,
    OpeningFact,
    PacingFacts,
    PauseFact,
    RefFact,
    RelationFact,
    SectionFact,
    SegmentFact,
    ShotChoice,
    ShotFact,
    SourceFacts,
    SpeechFact,
    StoryFacts,
    UnitFact,
    VideoFacts,
    VisualFact,
    WordFact,
)
from media_house.modules.editing_intelligence.domain.hooks import Hooks
from media_house.modules.editing_intelligence.domain.model import (
    Decision,
    EditPlan,
    StoryRef,
)
from media_house.modules.editing_intelligence.domain.profile import EditConfig

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
WORD = 0.3
DEFAULT_GAP = 0.15
FPS = Rational(25, 1)


@dataclass(frozen=True, slots=True)
class S:
    """One sentence: its text and the silence before it (seconds)."""

    text: str
    gap: float = DEFAULT_GAP
    emphasis: float | None = None
    clean_end: bool = True
    importance: float | None = None
    removal: float | None = None
    repeats: str | None = None
    requires: tuple[int, ...] = ()
    depends_on: tuple[int, ...] = ()
    highlight: float | None = None
    #: Seconds of silence between words inside the sentence: (after word index, seconds).
    inner: tuple[tuple[int, float], ...] = ()
    #: An audio event heard in the gap before the sentence.
    event: str | None = None
    confidence: float | None = 0.95


@dataclass(slots=True)
class Recording:
    evidence: AssetEvidence
    #: Start and end (seconds) of each sentence, by index.
    spans: list[tuple[float, float]] = field(default_factory=list)

    @property
    def content(self) -> ContentFacts:
        assert self.evidence.content is not None
        return self.evidence.content

    @property
    def audio(self) -> AudioFacts:
        assert self.evidence.audio is not None
        return self.evidence.audio


def talk(
    sentences: Sequence[S],
    *,
    asset_id: str = "a1",
    forced: bool = True,
    silence: float = 0.97,
    fps: Rational | None = FPS,
    vfr: bool | None = False,
    video: bool = True,
    with_audio: bool = True,
    with_content: bool = True,
    tail: float = 1.0,
    sample_rate: int | None = 48_000,
    duration: float | None = None,
) -> Recording:
    words: list[WordFact] = []
    pauses: list[PauseFact] = []
    events: list[EventFact] = []
    units: list[UnitFact] = []
    segments: list[SegmentFact] = []
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for i, sentence in enumerate(sentences):
        gap_start = cursor
        cursor += sentence.gap
        if sentence.gap >= 0.2:
            pauses.append(
                PauseFact(
                    start=gap_start,
                    end=cursor,
                    silence_ratio=silence,
                )
            )
        if sentence.event is not None:
            events.append(
                EventFact(
                    kind=sentence.event, start=gap_start + 0.05, end=cursor - 0.05, strength=0.8
                )
            )
        start = cursor
        tokens = sentence.text.split()
        inner = dict(sentence.inner)
        for k, token in enumerate(tokens):
            words.append(
                WordFact(
                    index=len(words),
                    start=round(cursor, 6),
                    end=round(cursor + WORD, 6),
                    text=token.strip(".,?!").casefold(),
                    confidence=sentence.confidence,
                    emphasis=sentence.emphasis,
                )
            )
            cursor += WORD
            if k in inner:
                pauses.append(
                    PauseFact(
                        start=cursor,
                        end=cursor + inner[k],
                        silence_ratio=silence,
                    )
                )
                cursor += inner[k]
        spans.append((start, round(cursor, 6)))
        units.append(
            UnitFact(
                unit_id=f"u{i:03d}",
                segment_id=f"seg{i}",
                start=start,
                end=round(cursor, 6),
                time_basis="words" if forced else "interpolated",
                text=sentence.text,
                words=len(tokens),
                starts_clean=True,
                ends_clean=sentence.clean_end,
            )
        )
        segments.append(
            SegmentFact(
                segment_id=f"seg{i}",
                unit_ids=(f"u{i:03d}",),
                start=start,
                end=round(cursor, 6),
                importance=sentence.importance,
                removal=sentence.removal,
                highlight=sentence.highlight,
                hook=None,
                requires_context=tuple(f"u{j:03d}" for j in sentence.requires),
                depends_on=tuple(f"seg{j}" for j in sentence.depends_on),
                repeats=sentence.repeats,
            )
        )
    total = duration if duration is not None else round(cursor + tail, 6)
    source = SourceFacts(
        asset_id=asset_id,
        name=f"clip {asset_id}",
        duration=total,
        frame_rate=fps,
        variable_frame_rate=vfr,
        has_video=video,
        has_audio=True,
        sample_rate=sample_rate,
        usable=True,
        identity=f"src-{asset_id}-{total}",
    )
    audio = (
        AudioFacts(
            asset_id=asset_id,
            duration=total,
            alignment_method="forced_alignment" if forced else "whisper_segment",
            words=tuple(words),
            pauses=tuple(pauses),
            events=tuple(events),
            identity=f"audio-{asset_id}-{len(words)}",
        )
        if with_audio
        else None
    )
    content = (
        ContentFacts(
            asset_id=asset_id,
            units=tuple(units),
            segments=tuple(segments),
            identity=f"content-{asset_id}-{len(units)}",
        )
        if with_content
        else None
    )
    return Recording(AssetEvidence(source=source, content=content, audio=audio), spans)


def with_video(
    recording: Recording,
    shots: Sequence[tuple[float, float]],
    *,
    speaking: Sequence[int] = (),
    visibility: str = "speaking_face",
    **shot_fields: object,
) -> Recording:
    """Add a video analysis (shots by start/end) and an alignment: a face speaks in ``speaking``."""
    facts: list[ShotFact] = []
    for i, (start, end) in enumerate(shots):
        base: dict[str, object] = {
            "shot_id": f"shot_{i}",
            "index": i,
            "start": start,
            "end": end,
            "black_in": False,
            "camera": "static",
            "usability": 0.9,
            "previous_shot_id": f"shot_{i - 1}" if i else None,
            "jump_cut_likelihood": None,
            "luma_delta": None,
            "color_delta": None,
        }
        base.update(shot_fields)
        facts.append(ShotFact(**base))  # type: ignore[arg-type]
    shot_ids = tuple(f"shot_{i}" for i in speaking)
    speech = tuple(
        SpeechFact(start=a, end=b, visibility=visibility, shot_ids=shot_ids)
        for a, b in recording.spans
    )
    evidence = replace(
        recording.evidence,
        video=VideoFacts(
            asset_id=recording.evidence.source.asset_id,
            shots=tuple(facts),
            identity=f"video-{len(facts)}",
        ),
        alignment=AlignmentFacts(
            asset_id=recording.evidence.source.asset_id,
            speech=speech,
            identity="alignment-1",
        ),
    )
    return Recording(evidence, recording.spans)


@dataclass(frozen=True, slots=True)
class Sec:
    """A story section: a label, its priority and the sentences (by index) it holds."""

    label: str
    sentences: tuple[int, ...]
    priority: str = "supporting"
    function: str | None = None
    requires: tuple[str, ...] = ()
    asset: str = "a1"


def story(
    recording: Recording | Mapping[str, Recording],
    sections: Sequence[Sec],
    *,
    fmt: str = "generic",
    key_info: Sequence[tuple[str, int, tuple[int, ...]]] = (),
    constraints: Sequence[ConstraintFact] = (),
    contexts: Sequence[ContextFact] = (),
    links: Sequence[LinkFact] = (),
    visuals: Sequence[VisualFact] = (),
    openings: Sequence[OpeningFact] = (),
    recommended: str | None = None,
    order: Sequence[int] | None = None,
    order_changes: Sequence[object] = (),
    relations: Sequence[RelationFact] = (),
    target: tuple[float | None, float | None] = (None, None),
) -> StoryFacts:
    """``key_info`` entries: (priority, sentence index, qualifier sentence indexes)."""
    recordings = {"a1": recording} if isinstance(recording, Recording) else dict(recording)
    asset_ids = tuple(dict.fromkeys(sec.asset for sec in sections)) or ("a1",)
    facts: list[SectionFact] = []
    beats: list[BeatFact] = []
    for i, sec in enumerate(sections):
        section_id = f"sec{i}"
        beat_ids: list[str] = []
        asset_id = sec.asset
        spans = recordings[asset_id].spans
        sources = tuple(
            RefFact(
                asset_id=asset_id,
                segment_ids=(f"seg{j}",),
                unit_ids=(f"u{j:03d}",),
                start=spans[j][0],
                end=spans[j][1],
                exact_time=True,
            )
            for j in sec.sentences
        )
        if sec.function is not None:
            beat_id = f"beat{i}"
            beat_ids.append(beat_id)
            beats.append(
                BeatFact(
                    beat_id=beat_id,
                    section_id=section_id,
                    function=sec.function,
                    source=RefFact(
                        asset_id=asset_id,
                        segment_ids=tuple(f"seg{j}" for j in sec.sentences),
                        unit_ids=tuple(f"u{j:03d}" for j in sec.sentences),
                        start=spans[sec.sentences[0]][0],
                        end=spans[sec.sentences[-1]][1],
                        exact_time=True,
                    ),
                )
            )
        facts.append(
            SectionFact(
                section_id=section_id,
                order=order[i] if order is not None else i,
                label=sec.label,
                priority=sec.priority,
                importance=None,
                sources=sources,
                requires=sec.requires,
            )
        )
    infos = tuple(
        KeyInfoFact(
            info_id=f"info{n}",
            section_id="sec0",
            kind="claim",
            text=recordings["a1"].content.units[index].text,
            priority=priority,
            source=RefFact(
                asset_id="a1", segment_ids=(f"seg{index}",), unit_ids=(f"u{index:03d}",)
            ),
            qualified_by=tuple(f"u{q:03d}" for q in qualifiers),
        )
        for n, (priority, index, qualifiers) in enumerate(key_info)
    )
    return StoryFacts(
        story_plan_id="sp_test",
        plan_version=1,
        stored_as=None,
        target_format=fmt,
        asset_ids=asset_ids,
        sections=tuple(facts),
        beats=tuple(beats),
        key_info=infos,
        constraints=tuple(constraints),
        contexts=tuple(contexts),
        links=tuple(links),
        visuals=tuple(visuals),
        openings=tuple(openings),
        recommended_opening=recommended,
        order_changes=tuple(order_changes),  # type: ignore[arg-type]
        relations=tuple(relations),
        target_low=target[0],
        target_high=target[1],
    )


def visual(
    story_facts: StoryFacts,
    recording: Recording,
    sentence: int,
    shots: Sequence[tuple[str, str, int]] = (),
    *,
    concept: str = "the battery",
    purpose: str = "illustration",
    necessity: str = "essential",
    strength: float = 0.9,
    already_covered: bool = False,
    asset_id: str = "a1",
) -> VisualFact:
    start, end = recording.spans[sentence]
    return VisualFact(
        visual_id=f"vis{sentence}",
        section_id="sec0",
        purpose=purpose,
        necessity=necessity,
        concept=concept,
        source=RefFact(
            asset_id=asset_id,
            segment_ids=(f"seg{sentence}",),
            unit_ids=(f"u{sentence:03d}",),
            start=start,
            end=end,
            exact_time=True,
        ),
        strength=strength,
        already_covered=already_covered,
        shots=tuple(
            ShotChoice(asset_id=asset_id, shot_id=shot, grade=grade, rank=rank)
            for shot, grade, rank in shots
        ),
    )


def meta(**changes: object) -> PlanMeta:
    base = PlanMeta(
        edit_plan_id="ep_test",
        plan_version=1,
        supersedes=None,
        project_id=None,
        created_at=NOW,
        configuration_fingerprint="cfg",
        story=StoryRef(
            story_plan_id="sp_test", plan_version=1, stored_as=None, target_format="generic"
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def edit(
    story_facts: StoryFacts,
    recordings: Mapping[str, Recording] | Recording,
    config: EditConfig | None = None,
    *,
    hooks: Hooks | None = None,
    pacing: PacingFacts | None = None,
    **meta_changes: object,
) -> EditPlan:
    if isinstance(recordings, Recording):
        evidence = {recordings.evidence.source.asset_id: recordings.evidence}
    else:
        evidence = {k: v.evidence for k, v in recordings.items()}
    return plan_edit(
        story_facts, evidence, config or EditConfig(), meta(**meta_changes), hooks, pacing
    )


def decision(plan: EditPlan, decision_id: str) -> Decision:
    """The decision with this id; a test that names a missing one fails here, readably."""
    found = plan.decision(decision_id)
    assert found is not None, f"no decision {decision_id}"
    return found
