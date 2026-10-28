"""Hand-built recordings, pictures and stories for the pacing-intelligence tests.

``speak`` lays sentences out in time (a gap before each, words of a chosen length) and returns the
matching audio, content and source facts. ``shots`` adds a video analysis, ``pulse`` a rhythm
window, ``tell`` a story plan. Nothing needs a model, a file or a library.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from media_house.core.domain import Rational
from media_house.modules.pacing_intelligence.domain.engine import PlanMeta, analyse_pacing
from media_house.modules.pacing_intelligence.domain.facts import (
    AssetEvidence,
    AudioFacts,
    BeatFact,
    ContentFacts,
    EventFact,
    PauseFact,
    RefFact,
    RhythmFact,
    SectionFact,
    SegmentFact,
    ShotFact,
    SourceFacts,
    StoryFacts,
    UnitFact,
    VideoFacts,
    VisualFact,
    WordFact,
)
from media_house.modules.pacing_intelligence.domain.model import PacingAnalysis
from media_house.modules.pacing_intelligence.domain.profile import PacingConfig

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
FPS = Rational(25, 1)


@dataclass(frozen=True, slots=True)
class T:
    """One sentence: text, the silence before it, and what content analysis said about it."""

    text: str
    gap: float = 0.2
    seconds_per_word: float = 0.3
    density: float | None = None
    repeats: str | None = None
    requires: int = 0
    emphasis: float | None = None
    speaker: str | None = None
    event: str | None = None
    question: bool = False
    #: Silence between words inside the sentence: (after word index, seconds).
    inner: tuple[tuple[int, float], ...] = ()


def speak(
    sentences: Sequence[T],
    *,
    asset_id: str = "a1",
    forced: bool = True,
    silence: float = 0.97,
    tail: float = 1.0,
    with_content: bool = True,
    with_audio: bool = True,
    fps: Rational | None = FPS,
    video: bool = True,
) -> AssetEvidence:
    words: list[WordFact] = []
    pauses: list[PauseFact] = []
    events: list[EventFact] = []
    units: list[UnitFact] = []
    segments: list[SegmentFact] = []
    cursor = 0.0
    for i, s in enumerate(sentences):
        before = cursor
        cursor += s.gap
        if s.gap >= 0.25:
            pauses.append(
                PauseFact(
                    start=before,
                    end=cursor,
                    silence_ratio=silence,
                    before_word=words[-1].index if words else None,
                    after_word=len(words),
                    at_segment_boundary=True,
                )
            )
        if s.event is not None:
            events.append(
                EventFact(kind=s.event, start=before + 0.05, end=cursor - 0.05, strength=0.8)
            )
        start = cursor
        tokens = s.text.rstrip("?.!").split()
        inner = dict(s.inner)
        for k, token in enumerate(tokens):
            words.append(
                WordFact(
                    index=len(words),
                    start=round(cursor, 6),
                    end=round(cursor + s.seconds_per_word, 6),
                    text=token.casefold(),
                    confidence=0.95,
                    speaker=s.speaker,
                    emphasis=s.emphasis if k == 0 else None,
                )
            )
            cursor += s.seconds_per_word
            if k in inner:
                pauses.append(
                    PauseFact(
                        start=round(cursor, 6),
                        end=round(cursor + inner[k], 6),
                        silence_ratio=silence,
                        before_word=len(words) - 1,
                        after_word=len(words),
                        at_segment_boundary=False,
                    )
                )
                cursor += inner[k]
        text = s.text if not s.question else s.text.rstrip("?") + "?"
        units.append(
            UnitFact(
                unit_id=f"u{i:03d}",
                segment_id=f"seg{i}",
                start=start,
                end=round(cursor, 6),
                time_basis="words" if forced else "interpolated",
                text=text,
                words=len(tokens),
            )
        )
        segments.append(
            SegmentFact(
                segment_id=f"seg{i}",
                unit_ids=(f"u{i:03d}",),
                start=start,
                end=round(cursor, 6),
                importance=None,
                density=s.density,
                repeats=s.repeats,
                requires_context=s.requires,
                depends_on=(),
                key_items=0,
                topic_ids=(),
            )
        )
    total = round(cursor + tail, 6)
    source = SourceFacts(
        asset_id=asset_id,
        name=f"clip {asset_id}",
        duration=total,
        frame_rate=fps,
        variable_frame_rate=False if fps else None,
        has_video=video,
        has_audio=True,
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
            rhythm=(),
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
            identity=f"content-{asset_id}",
        )
        if with_content
        else None
    )
    return AssetEvidence(source=source, audio=audio, content=content)


def shots(
    evidence: AssetEvidence,
    cuts: Sequence[tuple[float, float]],
    *,
    static: float | None = 0.95,
    camera: str | None = "static",
) -> AssetEvidence:
    """A video analysis: one shot per ``(start, end)``, each with the same static fraction."""
    facts = tuple(
        ShotFact(
            shot_id=f"shot_{i}",
            index=i,
            start=a,
            end=b,
            start_frame=round(a * 25),
            end_frame=round(b * 25),
            camera=camera,
            static_fraction=static,
            motion_energy=None if static is None else 1.0 - static,
            visual_interest=0.5,
        )
        for i, (a, b) in enumerate(cuts)
    )
    return replace(
        evidence,
        video=VideoFacts(
            asset_id=evidence.source.asset_id,
            frame_rate=evidence.source.frame_rate,
            shots=facts,
            identity=f"video-{len(facts)}",
        ),
    )


def pulse(
    evidence: AssetEvidence, start: float, end: float, bpm: float, strength: float = 0.8
) -> AssetEvidence:
    """A rhythm window with a regular beat grid."""
    assert evidence.audio is not None
    period = 60.0 / bpm
    beats = []
    t = start
    while t < end:
        beats.append(round(t, 4))
        t += period
    window = RhythmFact(start=start, end=end, tempo_bpm=bpm, strength=strength, beats=tuple(beats))
    return replace(evidence, audio=replace(evidence.audio, rhythm=(*evidence.audio.rhythm, window)))


@dataclass(frozen=True, slots=True)
class Sec:
    label: str
    sentences: tuple[int, ...]
    priority: str = "supporting"
    functions: tuple[str, ...] = ()
    seconds: float | None = None
    asset: str = "a1"


def tell(
    evidence: dict[str, AssetEvidence] | AssetEvidence,
    sections: Sequence[Sec],
    *,
    fmt: str = "generic",
    visuals: Sequence[tuple[int, str, str, str]] = (),
) -> StoryFacts:
    """A story plan over sentences. ``visuals``: (sentence, purpose, necessity, concept)."""
    by_asset = (
        {evidence.source.asset_id: evidence} if isinstance(evidence, AssetEvidence) else evidence
    )

    def span(asset: str, index: int) -> RefFact:
        content = by_asset[asset].content
        assert content is not None
        unit = content.units[index]
        return RefFact(asset_id=asset, unit_ids=(unit.unit_id,), start=unit.start, end=unit.end)

    facts: list[SectionFact] = []
    beats: list[BeatFact] = []
    for n, sec in enumerate(sections):
        refs = tuple(span(sec.asset, i) for i in sec.sentences)
        facts.append(
            SectionFact(
                section_id=f"sec{n}",
                order=n,
                label=sec.label,
                priority=sec.priority,
                sources=refs,
                duration_seconds=sec.seconds,
            )
        )
        for k, function in enumerate(sec.functions):
            index = sec.sentences[min(k, len(sec.sentences) - 1)]
            beats.append(
                BeatFact(
                    beat_id=f"beat{n}_{k}",
                    section_id=f"sec{n}",
                    function=function,
                    source=span(sec.asset, index),
                )
            )
    vis = tuple(
        VisualFact(
            visual_id=f"vis{i}",
            section_id="sec0",
            purpose=purpose,
            necessity=necessity,
            concept=concept,
            source=span(next(iter(by_asset)), sentence),
        )
        for i, (sentence, purpose, necessity, concept) in enumerate(visuals)
    )
    return StoryFacts(
        story_plan_id="sp_test",
        plan_version=1,
        stored_as=None,
        target_format=fmt,
        asset_ids=tuple(dict.fromkeys(sec.asset for sec in sections)),
        sections=tuple(facts),
        beats=tuple(beats),
        visuals=vis,
    )


def meta(**changes: object) -> PlanMeta:
    base = PlanMeta(
        pacing_analysis_id="pa_test",
        analysis_version=1,
        supersedes=None,
        project_id=None,
        created_at=NOW,
        configuration_fingerprint="cfg",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def analyse(
    evidence: AssetEvidence | dict[str, AssetEvidence],
    config: PacingConfig | None = None,
    *,
    story: StoryFacts | None = None,
) -> PacingAnalysis:
    by_asset = (
        {evidence.source.asset_id: evidence} if isinstance(evidence, AssetEvidence) else evidence
    )
    return analyse_pacing(story, by_asset, tuple(by_asset), config or PacingConfig(), meta())
