"""Transcripts and builders for the content-intelligence tests (no model, no media)."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from media_house.modules.content_intelligence.domain.config import ContentConfig, Profile
from media_house.modules.content_intelligence.domain.engine import AnalysisMeta, analyze, prepare
from media_house.modules.content_intelligence.domain.model import (
    ContentAnalysis,
    Provenance,
    SourceRecord,
)
from media_house.modules.content_intelligence.domain.similarity import SimilarityProvider
from media_house.modules.content_intelligence.domain.source import (
    SourceSegment,
    SourceTranscript,
    SourceWord,
)
from media_house.modules.content_intelligence.domain.values import SpeechTiming

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

BATTERIES = (
    "Welcome to the show.",
    "Today we talk about batteries and charging.",
    "What is a lithium battery?",
    "A lithium battery stores energy in chemical cells.",
    "The battery capacity is measured in watt hours.",
    "For example, a phone battery holds about 15 watt hours.",
    "Charging the battery too fast heats the cells.",
    "But this only matters above 45 degrees.",
    "Heat damages the battery over time.",
)
GARDEN = (
    "Now let's talk about gardening.",
    "Tomatoes need a lot of sunlight and water.",
    "The garden soil should be rich in compost.",
    "Plant the tomato seedlings after the last frost.",
    "Water the tomatoes every morning in the garden.",
    "Compost keeps the soil moist and feeds the tomato roots.",
    "Sunlight is the most important factor for ripe tomatoes.",
)
BUDGET = (
    "Finally the budget.",
    "The yearly budget covers salaries and rent.",
    "We cut the marketing budget by 10 percent.",
    "The finance team reviews the budget every quarter.",
    "Salaries are the largest part of the yearly budget.",
    "Rent is the second largest budget item.",
)
THREE_TOPICS = (*BATTERIES, *GARDEN, *BUDGET)

GERMAN = (
    "Willkommen zur Sendung.",
    "Heute sprechen wir über Batterien und das Laden.",
    "Eine Lithium-Batterie speichert Energie in chemischen Zellen.",
    "Die Kapazität der Batterie wird in Wattstunden gemessen.",
    "Zu schnelles Laden erhitzt die Zellen der Batterie.",
    "Hitze schädigt die Batterie mit der Zeit.",
    "Kommen wir nun zum Garten.",
    "Tomaten brauchen viel Sonne und Wasser im Garten.",
    "Der Boden im Garten sollte reich an Kompost sein.",
    "Pflanze die Tomatenpflanzen nach dem letzten Frost.",
    "Gieße die Tomaten jeden Morgen im Garten.",
    "Kompost hält den Boden feucht und ernährt die Wurzeln der Tomaten.",
)


def lines_to_transcript(
    lines: Sequence[str],
    *,
    language: str | None = "en",
    seconds_per_word: float = 0.4,
    gap: float = 0.3,
    pauses: dict[int, float] | None = None,
    speakers: dict[int, str] | None = None,
    timed: bool = True,
    with_words: bool = True,
    asset_id: str | None = None,
    timing: SpeechTiming = SpeechTiming.ALIGNED,
) -> SourceTranscript:
    """One upstream segment per line, with evenly spaced word times (unless ``timed=False``)."""
    clock = 0.0
    segments: list[SourceSegment] = []
    for index, line in enumerate(lines):
        clock += (pauses or {}).get(index, gap if index else 0.0)
        words: list[SourceWord] = []
        start = clock
        for token in line.split():
            words.append(SourceWord(token, clock, clock + seconds_per_word, timing))
            clock += seconds_per_word
        segments.append(
            SourceSegment(
                id=index,
                text=line,
                start=start if timed else None,
                end=clock if timed else None,
                speaker=(speakers or {}).get(index),
                words=tuple(words) if timed and with_words else (),
            )
        )
    return SourceTranscript(
        tuple(segments),
        language=language,
        duration=clock + 1.0 if timed else None,
        asset_id=asset_id,
    )


def provenance(config: ContentConfig, similarity: str = "lexical-tfidf-v1") -> Provenance:
    return Provenance(
        schema_version=1,
        processing_version=1,
        vocabulary_version=1,
        profile=config.profile.value,
        similarity=similarity,
        similarity_kind="lexical",
        engines={},
        prompt_version=None,
        configuration_fingerprint="test",
        device="cpu",
    )


def analysis_of(
    source: SourceTranscript,
    config: ContentConfig | None = None,
    *,
    visual: object | None = None,
    audio: object | None = None,
    similarity: SimilarityProvider | None = None,
) -> ContentAnalysis:
    """The deterministic analysis of ``source`` (word-overlap similarity unless one is given)."""
    cfg = config or ContentConfig(profile=Profile.FAST)
    prepared = prepare(source, cfg)
    meta = AnalysisMeta(
        analysis_id="ci-test",
        asset_id=source.asset_id,
        timestamp=NOW,
        source=SourceRecord(
            origin=source.origin,
            transcript_asset_id=None,
            transcript_fingerprint=source.fingerprint(),
            language=prepared.language,
            duration=source.duration,
            upstream_segments=len(source.segments),
        ),
        inputs=(),
        provenance=provenance(cfg),
    )
    return analyze(prepared, similarity or prepared.lexical_similarity(), cfg, meta, visual, audio)  # type: ignore[arg-type]


def edited(source: SourceTranscript, index: int, text: str) -> SourceTranscript:
    """The same transcript with one segment's text replaced (words and times dropped for it)."""
    segments = list(source.segments)
    segments[index] = replace(segments[index], text=text, words=())
    return replace(source, segments=tuple(segments))
