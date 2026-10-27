"""Section durations, pacing profiles and narrative-level pacing advice.

Pacing here is about the shape of the story: how dense a section is, whether the kind of content
varies, whether a long stretch has a break. It never names a cut point or a frame; advice is one of
a few narrative actions (add an example, recap, break up) for the editing stage to act on or not.
Durations are as recorded: the span between the first and last timed word, pauses included.
"""

from media_house.modules.story_architecture.domain.model import (
    Beat,
    CompletenessIssue,
    DurationEstimate,
    PacingNote,
    PacingProfile,
    PlanDuration,
    Section,
)
from media_house.modules.story_architecture.domain.profile import FormatProfile, Thresholds
from media_house.modules.story_architecture.domain.sections import Draft, mean, mode_of
from media_house.modules.story_architecture.domain.values import (
    BeatFunction,
    ContentMode,
    DurationBasis,
    IssueKind,
    PacingAction,
    PacingKind,
    Priority,
)

_OBSERVED_CONFIDENCE = 0.9
_MIXED_CONFIDENCE = 0.5
_ESTIMATED_CONFIDENCE = 0.35
_INTENSITY = frozenset({"emphasis", "surprise", "direction_change", "strong_conclusion"})
_BREAKS = frozenset(
    {BeatFunction.EXAMPLE, BeatFunction.DEMONSTRATION, BeatFunction.RECAP, BeatFunction.TRANSITION}
)


def estimate_duration(draft: Draft, profile: FormatProfile) -> DurationEstimate:
    """Observed from the timed words where all are timed, else counted from the words."""
    timed = [s for s in draft.segments if s.start is not None and s.end is not None]
    untimed_words = sum(s.words for s in draft.segments if s not in timed)
    if len(timed) == len(draft.segments):
        start = min(s.start for s in timed if s.start is not None)
        end = max(s.end for s in timed if s.end is not None)
        return DurationEstimate(
            seconds=round(max(end - start, 0.0), 3),
            basis=DurationBasis.OBSERVED,
            confidence=_OBSERVED_CONFIDENCE,
        )
    words = sum(s.words for s in draft.segments)
    if words == 0:
        return DurationEstimate(seconds=None, basis=DurationBasis.UNKNOWN, confidence=None)
    spoken = sum(max((s.end or 0.0) - (s.start or 0.0), 0.0) for s in timed)
    seconds = spoken + untimed_words / profile.words_per_minute * 60.0
    return DurationEstimate(
        seconds=round(seconds, 3),
        basis=DurationBasis.ESTIMATED_FROM_WORDS,
        confidence=_MIXED_CONFIDENCE if timed else _ESTIMATED_CONFIDENCE,
    )


def pacing_profile(
    draft: Draft, beats: tuple[Beat, ...], duration: DurationEstimate
) -> PacingProfile:
    words = sum(s.words for s in draft.segments)
    rate = (
        round(words / duration.seconds * 60.0, 1)
        if duration.basis is DurationBasis.OBSERVED and duration.seconds
        else None
    )
    intense = sum(1 for s in draft.segments if _INTENSITY & set(s.rhetoric))
    return PacingProfile(
        words=words,
        speech_rate_wpm=rate,
        density=mean([s.density for s in draft.segments]),
        rhetorical_intensity=round(intense / len(draft.segments), 4),
        mode=mode_of(beats),
    )


def plan_duration(
    sections: tuple[Section, ...], profile: FormatProfile, thresholds: Thresholds
) -> PlanDuration:
    ordered = sorted(sections, key=lambda s: s.order)

    def total(pick: list[Section]) -> DurationEstimate:
        known = [s.duration for s in pick if s.duration.seconds is not None]
        if not known:
            return DurationEstimate(seconds=None, basis=DurationBasis.UNKNOWN, confidence=None)
        seconds = round(sum(d.seconds or 0.0 for d in known), 3)
        confidences = [d.confidence or 0.0 for d in known]
        all_observed = len(known) == len(pick) and all(
            d.basis is DurationBasis.OBSERVED for d in known
        )
        return DurationEstimate(
            seconds=seconds,
            basis=DurationBasis.OBSERVED if all_observed else DurationBasis.ESTIMATED_FROM_WORDS,
            confidence=round(min(confidences), 4),
        )

    whole = total(ordered)
    core = total([s for s in ordered if s.priority is Priority.ESSENTIAL])
    low, high = profile.target_seconds if profile.target_seconds else (None, None)
    within: bool | None = None
    if whole.seconds is not None and low is not None and high is not None:
        within = (
            low * (1 - thresholds.duration_tolerance)
            <= whole.seconds
            <= high * (1 + thresholds.duration_tolerance)
        )
    return PlanDuration(
        total=whole, core=core, target_low=low, target_high=high, within_target=within
    )


def duration_issue(
    duration: PlanDuration,
) -> tuple[IssueKind, str] | None:
    low, high = duration.target_low, duration.target_high
    if duration.within_target is not False or duration.total.seconds is None:
        return None
    if low is None or high is None:
        return None
    if duration.total.seconds > high:
        return (
            IssueKind.OVER_TARGET_DURATION,
            f"The material runs about {duration.total.seconds:.0f} s; the format aims for "
            f"{low:.0f}-{high:.0f} s"
            f" (essential sections alone: {_secs(duration.core)})",
        )
    return (
        IssueKind.UNDER_TARGET_DURATION,
        f"The material runs about {duration.total.seconds:.0f} s; the format aims for "
        f"{low:.0f}-{high:.0f} s",
    )


def _secs(d: DurationEstimate) -> str:
    return "unknown" if d.seconds is None else f"{d.seconds:.0f} s"


def pacing_notes(
    sections: tuple[Section, ...],
    beats_by_section: dict[str, tuple[Beat, ...]],
    issues: tuple[CompletenessIssue, ...],
    thresholds: Thresholds,
) -> tuple[PacingNote, ...]:
    """Advice per section and per run of sections, in the recommended order."""
    notes: list[PacingNote] = []
    ordered = sorted(sections, key=lambda s: s.order)
    repeated = {
        i.section_id for i in issues if i.kind is IssueKind.REDUNDANT_MATERIAL and i.section_id
    }

    def add(section: Section, kind: PacingKind, action: PacingAction, detail: str) -> None:
        notes.append(
            PacingNote(
                note_id=f"pn_{len(notes) + 1:03d}",
                section_id=section.section_id,
                kind=kind,
                action=action,
                detail=detail,
            )
        )

    for section in ordered:
        beats = beats_by_section[section.section_id]
        functions = {b.function for b in beats}
        density = section.pacing.density
        seconds = section.duration.seconds
        if density is not None and density >= thresholds.dense:
            if not functions & {BeatFunction.EXAMPLE, BeatFunction.DEMONSTRATION}:
                add(
                    section,
                    PacingKind.DENSE,
                    PacingAction.ADD_EXAMPLE,
                    "Many new terms and no example or demonstration to ground them",
                )
            if BeatFunction.EXPLANATION not in functions and BeatFunction.CONTEXT not in functions:
                add(
                    section,
                    PacingKind.UNDER_EXPLAINED,
                    PacingAction.ADD_CONTEXT,
                    "Dense material with no passage that explains it",
                )
        elif density is not None and density <= thresholds.slow and (seconds or 0) > 10:
            add(
                section,
                PacingKind.SLOW,
                PacingAction.TIGHTEN,
                "Little new information for the time it takes",
            )
        if section.section_id in repeated:
            add(
                section,
                PacingKind.REPETITIVE,
                PacingAction.TIGHTEN,
                "Repeats material that comes earlier",
            )
        if (seconds or 0) > thresholds.long_section_seconds and not functions & _BREAKS:
            add(
                section,
                PacingKind.LONG_WITHOUT_BREAK,
                PacingAction.BREAK_UP if not section.visual_ids else PacingAction.VISUAL_BREAK,
                f"Runs about {seconds:.0f} s with no example, demonstration, recap or transition",
            )

    run: list[Section] = []
    tail: list[Section | None] = [*ordered, None]
    for entry in tail:
        if run and (entry is None or entry.pacing.mode is not run[-1].pacing.mode):
            if len(run) >= thresholds.monotone_run and run[-1].pacing.mode not in (
                ContentMode.UNKNOWN,
                ContentMode.FRAMING,
            ):
                action = (
                    PacingAction.ADD_DEMONSTRATION
                    if run[-1].pacing.mode is ContentMode.EXPLAINING
                    else PacingAction.ADD_RECAP
                )
                add(
                    run[-1],
                    PacingKind.MONOTONE_RUN,
                    action,
                    f"{len(run)} sections in a row are all {run[-1].pacing.mode.value}",
                )
            run = []
        if entry is not None:
            run.append(entry)
    return tuple(notes)
