"""Heard events against seen events, and speech that runs across a cut.

A sound and a movement that coincide are a CANDIDATE pair, never a proven cause: many sounds come
from off screen, and a busy picture produces movement peaks all the time. Every pair therefore
carries how many movement events fall in its window and how many would be expected by chance, and
its strength falls with the second. Silence of the picture is not evidence that nothing happened.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from itertools import pairwise

from media_house.modules.audio_visual_alignment.domain.clock import Clock
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.inputs import AudioFacts, VideoFacts
from media_house.modules.audio_visual_alignment.domain.model import (
    BoundaryRelation,
    Evidence,
    Scores,
    SoundAction,
    SpeechSource,
)
from media_house.modules.audio_visual_alignment.domain.scoring import combine
from media_house.modules.audio_visual_alignment.domain.values import BoundaryKind, EvidenceKind


def sound_actions(
    audio: AudioFacts,
    video: VideoFacts,
    clock: Clock,
    profile: AlignmentProfile,
    extra_tolerance: float = 0.0,
) -> tuple[SoundAction, ...]:
    """Pair each strong heard event with the seen events of the configured kinds near its onset."""
    tolerance = profile.event_tolerance_seconds + extra_tolerance
    heard = [
        e
        for e in audio.events
        if e.kind in profile.audio_event_kinds and e.strength >= profile.min_event_strength
    ]
    seen = [
        (event, clock.video(event.time.seconds))
        for event in video.events
        if event.kind in profile.video_event_kinds
    ]
    duration = video.duration or max((t for _, t in seen), default=0.0)
    totals: dict[str, int] = defaultdict(int)
    for event, _ in seen:
        totals[event.kind] += 1
    pairs: list[SoundAction] = []
    for sound in heard:
        onset = clock.audio(sound.start)
        near = sorted(
            (
                (abs(t - onset), t - onset, event)
                for event, t in seen
                if abs(t - onset) <= tolerance
            ),
            key=lambda item: (item[0], item[2].event_id),
        )
        for distance, delta, event in near[: profile.max_candidates]:
            chance = totals[event.kind] * 2 * tolerance / duration if duration > 0 else 0.0
            rarity = math.exp(-chance)
            proximity = 1.0 - distance / tolerance if tolerance > 0 else 1.0
            pairs.append(
                SoundAction(
                    pair_id=f"sa_{sound.event_id}_{event.event_id}",
                    audio_event_id=sound.event_id,
                    audio_kind=sound.kind,
                    audio_start=onset,
                    audio_end=clock.audio(sound.end),
                    video_event_id=event.event_id,
                    video_kind=event.kind,
                    shot_id=event.shot_id,
                    video_time=event.time,
                    delta_seconds=delta,
                    tolerance_seconds=tolerance,
                    chance_hits=chance,
                    candidates_in_window=len(near),
                    scores=Scores(strength=combine([proximity, rarity])),
                    evidence=(
                        Evidence(
                            kind=EvidenceKind.EVENT_COINCIDENCE,
                            source="audio_intelligence",
                            ref=sound.event_id,
                            value=delta,
                            detail=(
                                f"{event.kind} {delta:+.2f}s from the {sound.kind} onset; "
                                f"{len(near)} in window, {chance:.2f} expected by chance"
                            ),
                        ),
                    ),
                )
            )
    return tuple(pairs)


def boundary_relations(
    sources: Sequence[SpeechSource], clock: Clock, profile: AlignmentProfile
) -> tuple[BoundaryRelation, ...]:
    """For speech that spans two consecutive shots: how much is heard on each side of the cut."""
    out: list[BoundaryRelation] = []
    for source in sources:
        parts = sorted(source.shots, key=lambda p: p.video_range.start.frame)
        for before, after in pairwise(parts):
            if min(before.speech_seconds, after.speech_seconds) < profile.min_speech_seconds:
                continue
            out.append(
                BoundaryRelation(
                    relation_id=f"br_{source.segment_id}_{before.shot_id}_{after.shot_id}",
                    kind=BoundaryKind.SPEECH_CROSSES_CUT,
                    segment_id=source.segment_id,
                    boundary_at=clock.video(after.video_range.start.seconds),
                    from_shot_id=before.shot_id,
                    to_shot_id=after.shot_id,
                    speech_before_seconds=before.speech_seconds,
                    speech_after_seconds=after.speech_seconds,
                    visibility_before=before.visibility,
                    visibility_after=after.visibility,
                )
            )
    return tuple(out)
