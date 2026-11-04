"""Plan the captions of one edit: hearing -> selection -> segmentation -> expression -> layout
-> animation request -> subtitles -> QC. Pure: the application supplies facts and the clock.

The three decision areas stay separate: selection decides WHICH words (mode per section),
segmentation/layout decide HOW they are grouped and placed, expression decides WHAT is
highlighted or decorated. Rendering is the application's call to motion graphics afterwards.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.caption_engine.domain.cues import WordCues, analyse
from media_house.modules.caption_engine.domain.expression import (
    EmojiPick,
    GroupView,
    choose_emojis,
    emphasise,
)
from media_house.modules.caption_engine.domain.facts import (
    BurnedInSpan,
    CaptionInputs,
    StyleFacts,
    frac,
    rat,
)
from media_house.modules.caption_engine.domain.hearing import Heard, HeardWord, hear
from media_house.modules.caption_engine.domain.layout import place, subjects_during
from media_house.modules.caption_engine.domain.model import (
    AnimationIntent,
    CaptionDecision,
    CaptionPlan,
    CaptionSegment,
    CaptionWord,
    EmojiDecision,
    Provenance,
    RenderRef,
    ReprocessRequest,
    SubtitleCue,
    SubtitleExport,
    TranscriptRef,
)
from media_house.modules.caption_engine.domain.profile import CaptionConfig, CaptionProfile
from media_house.modules.caption_engine.domain.qc import check_segments, issue
from media_house.modules.caption_engine.domain.segmentation import Group, Limits, Token, segment
from media_house.modules.caption_engine.domain.selection import select
from media_house.modules.caption_engine.domain.subtitles import to_srt, to_vtt
from media_house.modules.caption_engine.domain.text import bare, ends_sentence, strip_full_stop
from media_house.modules.caption_engine.domain.values import (
    PLANNER_VERSION,
    PROFILE_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Action,
    CaptionMode,
    EmphasisLevel,
    InputRecord,
    QcCategory,
    QcIssue,
    RenderStatus,
    ResultState,
    Severity,
    StageReport,
    StyleBinding,
    WordFate,
)

EMOJI_NOT_RENDERED = (
    "emoji are drawn with their caption group only under a style that bundles an emoji face"
    " (visual identity v2+); under an identity v1 style they stay planned (an installed emoji"
    " font is never relied on)"
)


def digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key(
    inputs: CaptionInputs,
    configuration: Mapping[str, object],
    decisions: Sequence[CaptionDecision],
    *,
    render: bool,
    renderer: str,
) -> dict[str, object]:
    """Everything that changes the plan and nothing else (digests only: 64 KiB cap)."""
    t = inputs.timeline
    return {
        "edit": asdict(t.edit) if t.edit else None,
        "timeline": digest(asdict(t)),
        "transcripts": {
            x.asset_id: {"document": x.document_id, "content": digest(asdict(x))}
            for x in inputs.transcripts
        },
        "subjects": digest([asdict(s) for s in inputs.subjects]),
        "covered": digest([asdict(c) for c in inputs.covered]),
        "burned_in": digest([asdict(b) for b in inputs.burned_in]),
        "style": inputs.style.binding.fingerprint,
        "style_layout": digest(asdict(inputs.style)),
        "configuration": digest(configuration),
        "decisions": sorted((d.item_id, d.action.value, d.text) for d in decisions),
        "render": render,
        "renderer": renderer,
        "versions": {
            "planner": PLANNER_VERSION,
            "schema": SCHEMA_VERSION,
            "vocabulary": VOCABULARY_VERSION,
            "profile": PROFILE_VERSION,
        },
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanMeta:
    plan_id: str
    plan_version: int
    supersedes: str | None
    style: StyleBinding | None
    configuration_fingerprint: str
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...] = ()
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    engines: tuple[tuple[str, str], ...] = ()


def _frames(t: Fraction, fps: Fraction) -> int:
    return round(t * fps)


def _segment_id(words: Sequence[HeardWord]) -> str:
    return "cap:" + hashlib.sha256("|".join(w.word_id for w in words).encode()).hexdigest()[:16]


def _tokens(
    words: Sequence[HeardWord], cues: Mapping[int, WordCues] | None, strip: bool = False
) -> list[Token]:
    return [
        Token(
            key=w.key,
            text=_display(w, strip),
            start=w.start,
            end=w.end,
            speaker=w.word.speaker,
            clip_id=w.clip.clip_id,
            sentence_id=w.word.sentence_id,
            sentence_end=w.word.sentence_end or ends_sentence(w.text),
            atom=cues[w.key].atom if cues is not None else w.key,
        )
        for w in words
    ]


def _limits(profile: CaptionProfile, inputs: CaptionInputs) -> Limits:
    s, style = profile.segmentation, inputs.style
    return Limits(
        max_chars=s.max_chars_per_line or style.max_chars,
        max_lines=s.max_lines or style.max_lines,
        case=style.case,
    )


def _display(w: HeardWord, strip: bool) -> str:
    """The look drops a sentence's final full stop (a sentence end always ends a caption)."""
    if strip and (w.word.sentence_end or ends_sentence(w.text)):
        return strip_full_stop(w.text) or w.text
    return w.text


def plan_captions(
    inputs: CaptionInputs,
    config: CaptionConfig,
    profile: CaptionProfile,
    decisions: Sequence[CaptionDecision],
    *,
    now: datetime,
    meta: PlanMeta,
) -> CaptionPlan:
    style, timeline = inputs.style, inputs.timeline
    fps = frac(timeline.frame_rate)
    corrections = {d.item_id: d.text for d in decisions if d.action is Action.CORRECT}
    by_action = {(d.item_id, d.action) for d in decisions}
    heard = hear(inputs, profile.transcript, corrections)
    burnable = heard.burnable
    cues = analyse(burnable, inputs, profile)

    def profile_for(section_id: str | None) -> CaptionProfile:
        return config.for_section(profile, section_id)

    checks: list[QcIssue] = []
    selected = select(burnable, cues, inputs, profile_for)
    for s in selected.suppressed:
        checks.append(
            issue(
                "selection.covered_by_graphic",
                Severity.INFO,
                s.reason,
                start=s.words[0].start,
                end=s.words[-1].end,
                category=QcCategory.CREATIVE,
            )
        )
    limits = _limits(profile, inputs)
    rules = profile.segmentation
    strip = style.strip_trailing_period and rules.sentence_boundaries
    runs = [_tokens(r.words, cues, strip) for r in selected.runs]
    animated = profile.animation.reveal != "static"
    duration = max((frac(c.timeline_end) for c in timeline.clips), default=Fraction(0))
    segmented = segment(
        runs, rules, limits, style.exit_seconds if animated else Fraction(0), duration or None
    )
    by_key = {w.key: w for w in burnable}
    run_of = {w.key: r for r in selected.runs for w in r.words}
    for note in segmented.notes:
        checks.append(
            issue(note.code, Severity.WARNING, note.message, start=note.start, end=note.end)
        )
    shown_groups: list[tuple[str, Group]] = []
    for g in segmented.groups:
        gid = _segment_id([by_key[t.key] for t in g.tokens])
        if (gid, Action.REJECT) in by_action:
            checks.append(
                issue(
                    "decision.rejected",
                    Severity.INFO,
                    "rejected by a person: not shown",
                    gid,
                    start=g.start,
                    end=g.end,
                )
            )
            continue
        shown_groups.append((gid, g))
    views = [
        GroupView(
            group_id=gid,
            words=tuple(by_key[t.key] for t in g.tokens),
            profile=profile_for(by_key[g.tokens[0].key].clip.section_id),
            start=g.start,
            end=g.end,
            no_emphasis=(gid, Action.NO_EMPHASIS) in by_action,
            no_emoji=(gid, Action.NO_EMOJI) in by_action,
        )
        for gid, g in shown_groups
    ]
    levels = emphasise(views, cues)
    kinds = {
        (s.asset_id, s.sentence_id): s.key_kinds for t in inputs.transcripts for s in t.sentences
    }

    def caveat(w: HeardWord) -> bool:
        return "caveat" in kinds.get((w.word.asset_id, w.word.sentence_id), ())

    emojis = {p.group_id: p for p in choose_emojis(views, cues, caveat, duration)}
    segments: list[CaptionSegment] = []
    margin = profile.layout.subject_margin
    for view, (gid, g) in zip(views, shown_groups, strict=True):
        p = view.profile
        words = []
        for w in view.words:
            end = min(w.end, g.end)
            words.append(
                CaptionWord(
                    word_id=w.word_id,
                    asset_id=w.clip.asset_id,
                    transcript_index=w.word.index,
                    text=w.text,
                    display=_display(w, strip),
                    source_start=rat(w.source_start),
                    source_end=rat(w.source_end - (w.end - end)),
                    timeline_start=rat(w.start),
                    timeline_end=rat(end),
                    local_start=rat(w.start - g.start),
                    local_end=rat(end - g.start),
                    speaker=w.word.speaker,
                    confidence=w.word.confidence,
                    clipped=w.clipped,
                    corrected=w.corrected,
                    emphasis=levels.get(w.key, EmphasisLevel.NORMAL),
                    cues=cues[w.key].cues,
                )
            )
        subjects = (
            subjects_during(g.start, g.end, timeline.clips, inputs.subjects, style, margin)
            if p.layout.avoid_subjects
            else []
        )
        placed = place(g.lines, subjects, style, p.layout)
        if placed.covered:
            checks.append(
                issue(
                    "layout.covers_subject",
                    Severity.WARNING,
                    f"the caption zone covers {', '.join(placed.covered)} at every anchor",
                    gid,
                    start=g.start,
                    end=g.end,
                    category=QcCategory.CREATIVE,
                )
            )
        if not style.zone.takes_captions or style.zone.keep_out:
            checks.append(
                issue(
                    "zone.wrong_kind", Severity.ERROR, f"{style.zone.name} takes no captions", gid
                )
            )
        if placed.keep_outs:
            checks.append(
                issue(
                    "zone.keep_out",
                    Severity.ERROR,
                    f"the caption reaches the platform keep-out {', '.join(placed.keep_outs)}",
                    gid,
                )
            )
        if placed.outside_zone:
            checks.append(
                issue(
                    "zone.overflow",
                    Severity.WARNING,
                    f"{len(g.lines)} lines are taller than {style.zone.name}; motion graphics"
                    " shrinks the text to fit",
                    gid,
                )
            )
        emphasis_at = tuple(w.local_start for w in words if w.emphasis is not EmphasisLevel.NORMAL)
        segments.append(
            CaptionSegment(
                segment_id=gid,
                clip_ids=tuple(dict.fromkeys(w.clip.clip_id for w in view.words)),
                section_id=view.words[0].clip.section_id,
                speaker=view.words[0].word.speaker,
                mode=run_of[view.words[0].key].mode,
                reasons=tuple(dict.fromkeys(r for w in view.words for r in run_of[w.key].reasons)),
                text=" ".join(w.text for w in view.words),
                words=tuple(words),
                timeline_start=rat(g.start),
                timeline_end=rat(g.end),
                start_frame=_frames(g.start, fps),
                end_frame=_frames(g.end, fps),
                emojis=_emoji(emojis.get(gid), g, style.calm),
                layout=placed.layout,
                animation=_animation(p, style, emphasis_at, room=g.end - g.tokens[-1].end),
                render=RenderRef(status=RenderStatus.NOT_REQUESTED),
            )
        )
    checks += check_segments(
        segments,
        rules,
        style,
        limits.max_chars,
        Fraction(rules.tolerance).limit_denominator(1000),
    )
    unusable = {c.item_id for c in checks if c.severity is Severity.ERROR and c.item_id}
    segments = [
        replace(s, render=RenderRef(status=RenderStatus.FAILED, detail="QC error"))
        if s.segment_id in unusable
        else s
        for s in segments
    ]
    transcripts, reprocess, transcript_checks = _transcripts(inputs, heard, segments, profile)
    checks += transcript_checks
    mode, mode_reason = _mode(profile, config, burnable, inputs)
    subtitles = _subtitles(heard, profile, inputs, duration or None)
    errors = any(c.severity is Severity.ERROR for c in checks)
    warnings = list(meta.warnings)
    if not timeline.clips or not any(c.audio for c in timeline.clips):
        warnings.append("no clip of the edit is heard: nothing to caption")
    return CaptionPlan(
        plan_id=meta.plan_id,
        plan_version=meta.plan_version,
        supersedes=meta.supersedes,
        project_id=timeline.project_id,
        edit=timeline.edit,
        style=meta.style,
        created_at=now,
        state=ResultState.PARTIAL if errors else ResultState.OK,
        anchor_asset_id=timeline.anchor_asset_id,
        width=timeline.width,
        height=timeline.height,
        fps=timeline.frame_rate,
        fps_assumed=timeline.frame_rate_assumed,
        mode=mode,
        mode_reason=mode_reason,
        transcripts=transcripts,
        segments=tuple(segments) if mode is not CaptionMode.DISABLED else (),
        subtitles=subtitles,
        decisions=tuple(decisions),
        reprocess=reprocess,
        checks=tuple(checks),
        inputs=meta.inputs,
        stages=meta.stages,
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            planner_version=PLANNER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=profile.name,
            profile_version=PROFILE_VERSION,
            profile_evidence=profile.evidence,
            configuration_fingerprint=meta.configuration_fingerprint,
            engines=meta.engines,
        ),
        warnings=tuple(dict.fromkeys(warnings)),
        limitations=meta.limitations,
        schema_version=SCHEMA_VERSION,
    )


def _mode(
    profile: CaptionProfile,
    config: CaptionConfig,
    burnable: Sequence[HeardWord],
    inputs: CaptionInputs,
) -> tuple[CaptionMode, str]:
    sections = {c.section_id for c in inputs.timeline.clips}
    modes = {config.for_section(profile, s).mode for s in sections} | {profile.mode}
    source = (
        "project override"
        if "selection.mode" in config.overrides
        else f"profile {profile.name}: {profile.evidence}"
    )
    if modes == {CaptionMode.DISABLED}:
        return CaptionMode.DISABLED, f"disabled by {source}"
    if not burnable:
        return CaptionMode.DISABLED, "no reliable speech is heard in the edit (no-speech path)"
    window = Fraction(profile.selection.already_captioned_window).limit_denominator(1000)

    def on_screen(w: HeardWord, b: BurnedInSpan) -> bool:
        if b.asset_id != w.clip.asset_id:
            return False
        if b.caption:
            return b.start <= w.source_start <= b.end
        return b.start - window <= w.source_start <= b.end + window and bare(w.text) in {
            bare(t) for t in b.text.split()
        }

    shown = sum(1 for w in burnable if any(on_screen(w, b) for b in inputs.burned_in))
    if shown and shown >= profile.selection.already_captioned_share * len(burnable):
        return CaptionMode.DISABLED, (
            f"the picture already shows burned-in captions for {shown} of {len(burnable)} heard"
            " words (video analysis): none are added on top"
        )
    return profile.mode, source


def _emoji(pick: EmojiPick | None, g: Group, calm: bool) -> tuple[EmojiDecision, ...]:
    if pick is None:
        return ()
    w = pick.word
    lead = Fraction(1, 10)
    start = max(g.start, w.start - lead)
    return (
        EmojiDecision(
            emoji_id=f"emoji:{w.word_id}",
            emoji=pick.rule.emoji,
            meaning=pick.rule.meaning,
            trigger=pick.rule.trigger,
            word_id=w.word_id,
            timeline_start=rat(start),
            timeline_end=rat(g.end),
            local_start=rat(start - g.start),
            animation="fade_in" if calm else "pop_in",
            position="after_word",
            render=RenderStatus.NOT_REQUESTED,
        ),
    )


def _animation(
    profile: CaptionProfile,
    style: StyleFacts,
    emphasis_at: tuple[Rational, ...],
    *,
    room: Fraction,
) -> AnimationIntent:
    """From the style's motion tokens. A caption followed straight by the next one (no room for
    the exit after its last word) cuts out, so no word disappears while it is spoken."""
    a = profile.animation
    ceiling = 1 if style.calm else style.intensity_ceiling
    if a.reveal == "static":
        return AnimationIntent(
            reveal="static", entry="cut", exit="cut", active_word=False, intensity=0
        )
    return AnimationIntent(
        reveal=a.reveal,
        entry=style.entry_pattern,
        exit=style.exit_pattern if room >= style.exit_seconds else "cut",
        active_word=a.active_word,
        intensity=min(a.intensity, ceiling),
        emphasis_at=emphasis_at,
    )


def _transcripts(
    inputs: CaptionInputs,
    heard: Heard,
    segments: Sequence[CaptionSegment],
    profile: CaptionProfile,
) -> tuple[tuple[TranscriptRef, ...], tuple[ReprocessRequest, ...], list[QcIssue]]:
    refs: list[TranscriptRef] = []
    reprocess: list[ReprocessRequest] = []
    checks: list[QcIssue] = []
    shown = Counter(w.asset_id for s in segments for w in s.words)
    heard_by = Counter(w.clip.asset_id for w in heard.words)
    audible = list(dict.fromkeys(c.asset_id for c in inputs.timeline.clips if c.audio))
    for asset_id in audible:
        t = inputs.transcript(asset_id)
        if t is None:
            reprocess.append(
                ReprocessRequest(
                    owner="audio_intelligence",
                    asset_id=asset_id,
                    reason="no transcript is stored for a heard recording: transcribe it",
                )
            )
            continue
        counts = heard.counts.get(asset_id, Counter())
        total = heard_by[asset_id]
        bad = counts[WordFate.SUSPECT] + counts[WordFate.LOW_CONFIDENCE]
        refs.append(
            TranscriptRef(
                asset_id=asset_id,
                document_id=t.document_id,
                language=t.language,
                alignment=t.alignment,
                words_heard=total,
                words_shown=shown[asset_id],
                suspect=counts[WordFate.SUSPECT],
                low_confidence=counts[WordFate.LOW_CONFIDENCE],
                split_by_cut=counts[WordFate.SPLIT_BY_CUT],
            )
        )
        if counts[WordFate.SUSPECT]:
            checks.append(
                issue(
                    "word.suspect_dropped",
                    Severity.INFO,
                    f"{counts[WordFate.SUSPECT]} word(s) audio_intelligence marked suspect (a"
                    " bang may have been heard as speech) are not shown",
                    asset_id,
                )
            )
        if counts[WordFate.LOW_CONFIDENCE]:
            checks.append(
                issue(
                    "word.low_confidence",
                    Severity.WARNING,
                    f"{counts[WordFate.LOW_CONFIDENCE]} word(s) under the confidence floor"
                    f" {profile.transcript.min_confidence} are not burned in",
                    asset_id,
                )
            )
        if counts[WordFate.SPLIT_BY_CUT]:
            checks.append(
                issue(
                    "word.split_by_cut",
                    Severity.INFO,
                    f"{counts[WordFate.SPLIT_BY_CUT]} word(s) a cut splits are not shown",
                    asset_id,
                )
            )
        if counts[WordFate.WRONG_LANGUAGE]:
            checks.append(
                issue(
                    "language.mismatch",
                    Severity.WARNING,
                    f"the transcript is {t.language!r}, captions expect"
                    f" {profile.transcript.language!r}: nothing is burned in",
                    asset_id,
                )
            )
            reprocess.append(
                ReprocessRequest(
                    owner="audio_intelligence",
                    asset_id=asset_id,
                    reason=f"transcribe again with language {profile.transcript.language!r}"
                    f" (stored: {t.language!r})",
                )
            )
        elif total and bad / total > profile.transcript.unreliable_share:
            reprocess.append(
                ReprocessRequest(
                    owner="audio_intelligence",
                    asset_id=asset_id,
                    reason=f"{bad} of {total} heard words are suspect or uncertain",
                )
            )
        if t.alignment != "forced_alignment" and total:
            checks.append(
                issue(
                    "transcript.word_timing",
                    Severity.WARNING,
                    f"word times are estimated ({t.alignment}): word-by-word highlights may drift",
                    asset_id,
                )
            )
            reprocess.append(
                ReprocessRequest(
                    owner="audio_intelligence",
                    asset_id=asset_id,
                    reason="no forced word alignment: align the transcript",
                )
            )
    return tuple(refs), tuple(reprocess), checks


def _subtitles(
    heard: Heard, profile: CaptionProfile, inputs: CaptionInputs, until: Fraction | None
) -> SubtitleExport | None:
    rules = profile.subtitles
    words = heard.subtitle_words
    if not rules.enabled or not words:
        return None
    limits = Limits(
        max_chars=rules.segmentation.max_chars_per_line,
        max_lines=rules.segmentation.max_lines,
        case="as_written",
    )
    segmented = segment([_tokens(words, None)], rules.segmentation, limits, until=until)
    by_key = {w.key: w for w in words}
    cues = tuple(
        SubtitleCue(
            index=i,
            timeline_start=rat(g.start),
            timeline_end=rat(g.end),
            lines=g.lines,
            speaker=by_key[g.tokens[0].key].word.speaker,
        )
        for i, g in enumerate(segmented.groups, start=1)
    )
    languages = [t.language for t in inputs.transcripts if t.language]
    language = languages[0] if languages else profile.transcript.language
    return SubtitleExport(
        language=language,
        cues=cues,
        srt=to_srt(cues),
        vtt=to_vtt(cues, speaker_labels=rules.speaker_labels),
    )
