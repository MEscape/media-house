"""Builders for caption engine tests: German transcripts (real audio_intelligence fusion and
content's commerce facts through the module's views), timelines, style facts and plans."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.audio_intelligence.domain.analysis.config import AudioIntelligenceConfig
from media_house.modules.audio_intelligence.domain.analysis.fusion import build_timeline
from media_house.modules.audio_intelligence.domain.analysis.scoring import HeuristicScorer
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_intelligence.domain.builder import BuildContext, build_transcript
from media_house.modules.caption_engine.application.views import transcript_facts
from media_house.modules.caption_engine.domain.engine import PlanMeta, plan_captions
from media_house.modules.caption_engine.domain.facts import (
    CaptionClip,
    CaptionInputs,
    CaptionTimeline,
    CoveredSpan,
    Rect,
    StyleFacts,
    SubjectBox,
    TranscriptFacts,
    ZoneFacts,
)
from media_house.modules.caption_engine.domain.model import CaptionDecision, CaptionPlan
from media_house.modules.caption_engine.domain.profile import CaptionConfig, JsonValue
from media_house.modules.caption_engine.domain.values import EditRef, StyleBinding
from tests.support.analysis_fakes import measurements, raw_segments, speech_track
from tests.support.audio_fakes import CREATED

type Word = tuple[str, float, float]

EDIT = EditRef(edit_plan_id="ep1", plan_version=1)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def said(text: str, start: float = 0.0, word: float = 0.3, gap: float = 0.05) -> list[list[Word]]:
    """Sentences of ``text`` as timed word groups (one transcript segment per sentence)."""
    groups: list[list[Word]] = []
    t = start
    for sentence in _SENTENCE.split(text.strip()):
        group: list[Word] = []
        for w in sentence.split():
            group.append((w, round(t, 3), round(t + word, 3)))
            t += word + gap
        groups.append(group)
        t += 0.3
    return groups


def audio_timeline(
    groups: Sequence[Sequence[Word]],
    *,
    asset_id: str = "clip",
    language: str = "de",
    confidences: Mapping[str, float] | None = None,
) -> AudioIntelligenceTimeline:
    flat = [w for g in groups for w in g]
    total = flat[-1][2] + 1.0 if flat else 1.0
    transcript = build_transcript(
        raw_segments(groups, language=language, confidences=confidences),
        BuildContext(asset_id, "aud", total, 0.0, 16_000, 1, 1, CREATED),
    )
    config = AudioIntelligenceConfig()
    return build_timeline(
        transcript=transcript,
        measurements=measurements(speech_track(flat, seconds=total)),
        config=config,
        scorer=HeuristicScorer(config.scoring),
        audio_offset=0.0,
        created_at=CREATED,
    )


def transcript(
    text: str,
    *,
    asset_id: str = "clip",
    start: float = 0.0,
    language: str = "de",
    word: float = 0.3,
    confidences: Mapping[str, float] | None = None,
) -> TranscriptFacts:
    timeline = audio_timeline(
        said(text, start, word), asset_id=asset_id, language=language, confidences=confidences
    )
    return transcript_facts(timeline, asset_id, f"audio-{asset_id}", None, "de")


def clip(
    asset_id: str = "clip",
    source: tuple[float, float] = (0.0, 120.0),
    at: float = 0.0,
    clip_id: str | None = None,
    **changes: object,
) -> CaptionClip:
    built = CaptionClip(
        clip_id=clip_id or f"{asset_id}-{source[0]}",
        asset_id=asset_id,
        source_start=_r(source[0]),
        source_end=_r(source[1]),
        timeline_start=_r(at),
    )
    return replace(built, **changes)  # type: ignore[arg-type]


def _r(value: float) -> Rational:
    f = Fraction(value).limit_denominator(1000)
    return Rational(f.numerator, f.denominator)


def zone(name: str, left: int, top: int, width: int, height: int, **kw: bool) -> ZoneFacts:
    return ZoneFacts(
        name=name,
        rect=Rect(left=left, top=top, width=width, height=height),
        takes_captions=kw.get("captions", False),
        keep_out=kw.get("keep_out", False),
    )


VERTICAL_ZONES = (
    zone("platform_top", 0, 0, 1080, 134, keep_out=True),
    zone("platform_right", 929, 730, 151, 883, keep_out=True),
    zone("platform_bottom", 0, 1613, 1080, 307, keep_out=True),
    zone("title_band", 54, 269, 972, 307, captions=True),
    zone("caption_band", 54, 1114, 853, 249, captions=True),
)
WIDE_ZONES = (
    zone("title_band", 422, 54, 1076, 184, captions=True),
    zone("caption_band", 192, 886, 1536, 140, captions=True),
)


def style(vertical: bool = True, **changes: object) -> StyleFacts:
    """The MEsc word-group look (Anton caps, 22 chars x 2 lines, yellow keyword)."""
    zones = VERTICAL_ZONES if vertical else WIDE_ZONES
    built = StyleFacts(
        binding=StyleBinding(
            document_id="style-doc", fingerprint="f" * 64, identity_version=1, profile="generic"
        ),
        profile="shorts_haul" if vertical else "generic",
        width=1080 if vertical else 1920,
        height=1920 if vertical else 1080,
        look="word_group",
        case="upper",
        size_px=84 if vertical else 72,
        line_px=92 if vertical else 79,
        stroke_px=7,
        max_chars=22,
        max_lines=2,
        strip_trailing_period=True,
        highlight_hex="#F4DE19",
        zone=next(z for z in zones if z.name == "caption_band"),
        zones=zones,
        min_text_px=28,
        entry_pattern="pop_in",
        exit_pattern="pop_out",
        intensity_ceiling=4 if vertical else 2,
        calm=False,
    )
    return replace(built, **changes)  # type: ignore[arg-type]


def inputs(
    *transcripts: TranscriptFacts,
    clips: Sequence[CaptionClip] | None = None,
    vertical: bool = True,
    edit: EditRef | None = EDIT,
    subjects: Sequence[SubjectBox] = (),
    covered: Sequence[CoveredSpan] = (),
    style_facts: StyleFacts | None = None,
) -> CaptionInputs:
    facts = style_facts or style(vertical)
    return CaptionInputs(
        timeline=CaptionTimeline(
            edit=edit,
            project_id="p1",
            frame_rate=Rational(30, 1),
            width=facts.width,
            height=facts.height,
            clips=tuple(clips) if clips is not None else (clip(),),
        ),
        transcripts=transcripts,
        style=facts,
        subjects=tuple(subjects),
        covered=tuple(covered),
    )


def plan(
    given: CaptionInputs,
    profile: str = "shorts_haul",
    overrides: Mapping[str, JsonValue] | None = None,
    decisions: Sequence[CaptionDecision] = (),
    config: CaptionConfig | None = None,
) -> CaptionPlan:
    config = config or CaptionConfig(profile=profile, overrides=dict(overrides or {}))
    resolved = config.resolve()
    return plan_captions(
        given,
        config,
        resolved,
        tuple(decisions),
        now=CREATED,
        meta=PlanMeta(
            plan_id="captions:test",
            plan_version=1,
            supersedes=None,
            style=given.style.binding,
            configuration_fingerprint="cfg",
            inputs=(),
        ),
    )


def texts(p: CaptionPlan) -> list[str]:
    return [s.text for s in p.segments]
