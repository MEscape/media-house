"""Who is heard, and what the picture says about where the voice comes from.

Five different facts are kept apart (``Visibility``): a speaking face, a quiet face, no face
found, several people, and "not measured". The mouth signal is a few samples per second of the
largest face's jaw opening, so this reads "the mouth moves while speech is heard", not lip-sync.
A voice is never called off screen here: a quiet or absent face is a fact about the picture only.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from media_house.modules.audio_visual_alignment.domain.clock import Clock
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.inputs import (
    AudioFacts,
    MouthSample,
    ShotFacts,
    SpeechSegment,
    VideoFacts,
)
from media_house.modules.audio_visual_alignment.domain.model import (
    Evidence,
    Flag,
    Scores,
    ShotSpeech,
    SpeakerLink,
    SpeechSource,
)
from media_house.modules.audio_visual_alignment.domain.scoring import (
    combine,
    coverage,
    overlap,
    spread,
)
from media_house.modules.audio_visual_alignment.domain.values import (
    EvidenceKind,
    FlagKind,
    LinkState,
    Severity,
    Visibility,
)

_AUDIO = "audio_intelligence"
_VIDEO = "video_intelligence"
#: Samples per mouth-spread window that make the cue fully dense (3x the minimum).
_DENSITY_FACTOR = 3


class MouthSeries:
    """Jaw-opening samples on the common clock, sorted, with window statistics."""

    def __init__(self, samples: Sequence[MouthSample], clock: Clock) -> None:
        ordered = sorted(samples, key=lambda s: s.seconds)
        self.times = [clock.video(s.seconds) for s in ordered]
        self.jaws = [s.jaw for s in ordered]

    def window(self, start: float, end: float) -> list[float]:
        return self.jaws[bisect_left(self.times, start) : bisect_right(self.times, end)]


def judge_shot(
    segment: SpeechSegment,
    shot: ShotFacts,
    audio: AudioFacts,
    mouth: MouthSeries,
    clock: Clock,
    shot_span: tuple[float, float],
    profile: AlignmentProfile,
) -> ShotSpeech | None:
    seg_start, seg_end = clock.audio(segment.start), clock.audio(segment.end)
    start, end = max(seg_start, shot_span[0]), min(seg_end, shot_span[1])
    shared = overlap(seg_start, seg_end, *shot_span)
    if shared < profile.min_speech_seconds:
        return None
    fraction = (
        audio.activity.fraction(start - clock.audio_shift, end - clock.audio_shift)
        if audio.activity
        else None
    )
    speech_seconds = shared * (1.0 if fraction is None else fraction)

    def make(visibility: Visibility, samples: int = 0, moved: float | None = None) -> ShotSpeech:
        return ShotSpeech(
            shot_id=shot.shot_id,
            video_range=shot.range,
            overlap_seconds=shared,
            speech_seconds=speech_seconds,
            visibility=visibility,
            mouth_samples=samples,
            mouth_spread=moved,
            identity_id=shot.identity_id,
            track_id=shot.main_track_id,
        )

    if not shot.faces_measured:
        return make(Visibility.VISUAL_UNAVAILABLE)
    if shot.face_frames == 0:
        return make(Visibility.NO_FACE_DETECTED)
    jaws = mouth.window(start, end)
    moved = spread(jaws) if len(jaws) >= profile.min_mouth_samples else None
    if moved is None:
        visibility = Visibility.INSUFFICIENT
    elif shot.people is not None and shot.people > 1:
        visibility = Visibility.AMBIGUOUS
    elif moved >= profile.speaking_spread:
        visibility = Visibility.SPEAKING_FACE
    elif moved <= profile.quiet_spread:
        visibility = Visibility.QUIET_FACE
    else:
        visibility = Visibility.INSUFFICIENT
    return make(visibility, len(jaws), moved)


def speech_sources(
    audio: AudioFacts,
    video: VideoFacts,
    clock: Clock,
    profile: AlignmentProfile,
) -> tuple[SpeechSource, ...]:
    mouth = MouthSeries(video.mouth, clock)
    spans = [(shot, clock.video_interval(shot.range)) for shot in video.shots]
    out: list[SpeechSource] = []
    for segment in audio.segments:
        seg_start, seg_end = clock.audio(segment.start), clock.audio(segment.end)
        parts = [
            judged
            for shot, span in spans
            if span[1] > seg_start and span[0] < seg_end
            if (judged := judge_shot(segment, shot, audio, mouth, clock, span, profile))
        ]
        out.append(_source(segment, seg_start, seg_end, parts, profile))
    return tuple(out)


def _source(
    segment: SpeechSegment,
    start: float,
    end: float,
    parts: list[ShotSpeech],
    profile: AlignmentProfile,
) -> SpeechSource:
    seconds: dict[Visibility, float] = defaultdict(float)
    for part in parts:
        seconds[part.visibility] += part.speech_seconds
    # ties resolve in enum order so the result never depends on dictionary order
    visibility = (
        max(Visibility, key=lambda v: (seconds.get(v, 0.0), -list(Visibility).index(v)))
        if seconds
        else Visibility.VISUAL_UNAVAILABLE
    )
    shared = sum(p.overlap_seconds for p in parts)
    chosen = [p for p in parts if p.visibility is visibility]
    strength = None
    evidence: list[Evidence] = []
    if visibility in (Visibility.SPEAKING_FACE, Visibility.QUIET_FACE) and chosen:
        top = max(chosen, key=lambda p: p.speech_seconds)
        moved = top.mouth_spread or 0.0
        cue = (
            min(1.0, moved / (2 * profile.speaking_spread))
            if visibility is Visibility.SPEAKING_FACE
            else max(0.0, 1.0 - moved / profile.speaking_spread)
        )
        density = min(1.0, top.mouth_samples / (profile.min_mouth_samples * _DENSITY_FACTOR))
        strength = combine([cue, density])
        evidence.append(
            Evidence(
                kind=EvidenceKind.MOUTH_SAMPLES,
                source=_VIDEO,
                ref=top.shot_id,
                value=moved,
                detail=f"{top.mouth_samples} jaw-opening samples, spread {moved:.4f}",
            )
        )
    evidence.append(
        Evidence(
            kind=EvidenceKind.TEMPORAL_OVERLAP,
            source=_AUDIO,
            ref=segment.segment_id,
            value=shared,
            detail=f"{len(parts)} shot(s) on screen during the segment",
        )
    )
    return SpeechSource(
        source_id=f"ss_{segment.segment_id}",
        segment_id=segment.segment_id,
        start=start,
        end=end,
        speaker=segment.speaker,
        visibility=visibility,
        shots=tuple(parts),
        scores=Scores(
            overlap_seconds=shared,
            coverage_audio=coverage(shared, end - start),
            strength=strength,
        ),
        evidence=tuple(evidence),
    )


# --- speaker to person ----------------------------------------------------------------------------
def speaker_links(
    sources: Sequence[SpeechSource], profile: AlignmentProfile
) -> tuple[tuple[SpeakerLink, ...], tuple[Flag, ...]]:
    """Accumulate, per diarized speaker, which anonymous identity is seen speaking.

    Only a speaking face with an identity counts for a link, only a quiet face counts against
    it, and everything ambiguous or unmeasured counts for neither.
    """
    support: dict[tuple[str, str], float] = defaultdict(float)
    against: dict[tuple[str, str], float] = defaultdict(float)
    shots: dict[tuple[str, str], set[str]] = defaultdict(set)
    for source in sources:
        if source.speaker is None:
            continue
        for part in source.shots:
            if part.identity_id is None:
                continue
            key = (source.speaker, part.identity_id)
            if part.visibility is Visibility.SPEAKING_FACE:
                support[key] += part.speech_seconds
                shots[key].add(part.shot_id)
            elif part.visibility is Visibility.QUIET_FACE:
                against[key] += part.speech_seconds
    if not any(s.speaker for s in sources):
        flags: tuple[Flag, ...] = ()
        if sources:
            flags = (
                Flag(
                    kind=FlagKind.NO_SPEAKER_LABELS,
                    severity=Severity.INFO,
                    subject="audio",
                    detail="the audio analysis has no speaker labels, so no speaker can be linked",
                ),
            )
        return (), flags

    totals: dict[str, float] = defaultdict(float)
    for (speaker, _), seconds in support.items():
        totals[speaker] += seconds
    links: list[SpeakerLink] = []
    flags_out: list[Flag] = []
    for key in sorted(set(support) | set(against)):
        speaker, identity = key
        seconds, contra = support.get(key, 0.0), against.get(key, 0.0)
        share = seconds / totals[speaker] if totals.get(speaker) else 0.0
        enough = seconds >= profile.link_min_support_seconds and (
            len(shots[key]) >= profile.link_min_shots
            or seconds >= profile.link_solo_support_seconds
        )
        rivals = [
            other
            for (sp, other), s in support.items()
            if sp == speaker
            and other != identity
            and s / totals[speaker] >= profile.link_conflict_share
        ]
        if not enough:
            state = LinkState.CANDIDATE
        elif rivals and share >= profile.link_conflict_share:
            state = LinkState.CONFLICT
        elif share >= profile.link_min_share and contra <= seconds:
            state = LinkState.SUPPORTED
        else:
            state = LinkState.CANDIDATE
        total_evidence = seconds + contra
        links.append(
            SpeakerLink(
                link_id=f"link_{speaker}_{identity}",
                speaker=speaker,
                identity_id=identity,
                state=state,
                supporting_seconds=seconds,
                contradicting_seconds=contra,
                shot_ids=tuple(sorted(shots[key])),
                scores=Scores(
                    identity_support=min(1.0, share),
                    strength=combine(
                        [
                            min(
                                1.0, seconds / (profile.link_min_support_seconds * _DENSITY_FACTOR)
                            ),
                            min(1.0, share),
                        ],
                        contradiction=contra / total_evidence if total_evidence else 0.0,
                    ),
                ),
                evidence=(
                    Evidence(
                        kind=EvidenceKind.SPEAKER_TURN,
                        source=_AUDIO,
                        ref=speaker,
                        value=seconds,
                        detail=(
                            f"{seconds:.1f}s with a speaking face, {contra:.1f}s with a quiet one"
                        ),
                    ),
                ),
            )
        )
        if state is LinkState.CONFLICT:
            flags_out.append(
                Flag(
                    kind=FlagKind.SPEAKER_LINK_CONFLICT,
                    severity=Severity.WARNING,
                    subject=speaker,
                    detail=f"{speaker} is seen speaking as more than one identity",
                )
            )
        if contra > seconds > 0:
            flags_out.append(
                Flag(
                    kind=FlagKind.SPEAKER_CONTRADICTED,
                    severity=Severity.WARNING,
                    subject=f"{speaker}/{identity}",
                    detail=(
                        "this identity's mouth is still during more of the speaker's speech "
                        "than it moves"
                    ),
                )
            )
    return _mark_shared_identities(links, flags_out)


def _mark_shared_identities(
    links: list[SpeakerLink], flags: list[Flag]
) -> tuple[tuple[SpeakerLink, ...], tuple[Flag, ...]]:
    """One identity supported for several speakers: none of those links can be trusted alone."""
    by_identity: dict[str, list[SpeakerLink]] = defaultdict(list)
    for link in links:
        if link.state is LinkState.SUPPORTED:
            by_identity[link.identity_id].append(link)
    clashing = {link.link_id for group in by_identity.values() if len(group) > 1 for link in group}
    if not clashing:
        return tuple(links), tuple(flags)
    changed = [
        replace(link, state=LinkState.CONFLICT) if link.link_id in clashing else link
        for link in links
    ]
    for identity, group in sorted(by_identity.items()):
        if len(group) > 1:
            flags.append(
                Flag(
                    kind=FlagKind.SPEAKER_LINK_CONFLICT,
                    severity=Severity.WARNING,
                    subject=identity,
                    detail="several speakers are seen speaking as the same identity",
                )
            )
    return tuple(changed), tuple(flags)
