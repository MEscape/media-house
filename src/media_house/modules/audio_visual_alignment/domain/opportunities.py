"""Intervals a downstream stage may want to look at. They state evidence; they choose nothing.

* ``NO_VISIBLE_SPEAKER_WINDOW``: speech is heard while the picture POSITIVELY shows no moving
  mouth (a quiet face, or no face found by an analyser that ran). Unknown, unmeasured, ambiguous
  and insufficient never create one. Voice-over, a listening face and a reaction shot look the
  same to this rule, and it does not say a cutaway is wanted.
* ``CONTENT_PASSAGE_CONTEXT``: for each place content intelligence marks as inviting a visual,
  what the picture is doing with the speaker meanwhile. Content intelligence's own verdict on
  whether the idea is already shown is passed through unchanged, not re-derived.
"""

from collections.abc import Sequence

from media_house.modules.audio_visual_alignment.domain.clock import Clock
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.inputs import ContentFacts
from media_house.modules.audio_visual_alignment.domain.model import (
    Opportunity,
    Scores,
    SpeechSource,
)
from media_house.modules.audio_visual_alignment.domain.scoring import overlap
from media_house.modules.audio_visual_alignment.domain.values import OpportunityKind, Visibility

_POSITIVE_ABSENCE = (Visibility.QUIET_FACE, Visibility.NO_FACE_DETECTED)
_LIMITS_WINDOW = (
    "voice-over, a listening face and a reaction shot look identical to this rule",
    "the mouth signal is a few samples per second of the largest face only",
)
_LIMITS_CONTEXT = (
    "whether a visual is wanted is not decided here",
    "coverage comes from content intelligence's text matching, not from the picture itself",
)


def no_visible_speaker_windows(
    sources: Sequence[SpeechSource], clock: Clock, profile: AlignmentProfile
) -> tuple[Opportunity, ...]:
    out: list[Opportunity] = []
    margin = profile.window_boundary_margin_seconds
    for source in sources:
        for part in source.shots:
            if part.visibility not in _POSITIVE_ABSENCE:
                continue
            shot_start, shot_end = clock.video_interval(part.video_range)
            start = max(source.start, shot_start) + margin
            end = min(source.end, shot_end) - margin
            if end - start < profile.window_min_seconds:
                continue
            reason = (
                "a face is on screen and its mouth is still"
                if part.visibility is Visibility.QUIET_FACE
                else "the face analyser ran and found no face"
            )
            out.append(
                Opportunity(
                    opportunity_id=f"op_window_{source.segment_id}_{part.shot_id}",
                    kind=OpportunityKind.NO_VISIBLE_SPEAKER_WINDOW,
                    start=start,
                    end=end,
                    shot_ids=(part.shot_id,),
                    reasons=(reason, f"speech from segment {source.segment_id}"),
                    scores=Scores(overlap_seconds=end - start),
                    limitations=_LIMITS_WINDOW,
                )
            )
    return tuple(out)


def passage_contexts(
    content: ContentFacts, sources: Sequence[SpeechSource], clock: Clock
) -> tuple[Opportunity, ...]:
    out: list[Opportunity] = []
    for fact in content.opportunities:
        start, end = clock.audio(fact.start), clock.audio(fact.end)
        speaking = 0.0
        shots: list[str] = []
        for source in sources:
            if overlap(start, end, source.start, source.end) <= 0:
                continue
            for part in source.shots:
                shot_start, shot_end = clock.video_interval(part.video_range)
                shared = overlap(
                    start, end, max(source.start, shot_start), min(source.end, shot_end)
                )
                if shared <= 0:
                    continue
                if part.shot_id not in shots:
                    shots.append(part.shot_id)
                if part.visibility is Visibility.SPEAKING_FACE:
                    speaking += shared
        length = end - start
        reasons = [f"content intelligence: {fact.kind} for '{fact.concept}'"]
        reasons.append(
            f"a speaking face is on screen for {speaking:.1f}s of {length:.1f}s"
            if speaking
            else "no speaking face is on screen during the passage"
        )
        out.append(
            Opportunity(
                opportunity_id=f"op_context_{fact.opportunity_id}",
                kind=OpportunityKind.CONTENT_PASSAGE_CONTEXT,
                start=start,
                end=end,
                shot_ids=tuple(sorted(shots)),
                passage_id=fact.passage_id,
                content_opportunity_id=fact.opportunity_id,
                already_covered=fact.already_covered,
                visible_speaking_seconds=speaking,
                reasons=tuple(reasons),
                scores=Scores(overlap_seconds=length),
                limitations=_LIMITS_CONTEXT,
            )
        )
    return tuple(out)
