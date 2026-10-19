"""The pure alignment pipeline: facts in, one ``AlignmentDocument`` out. No I/O, no clock."""

from collections.abc import Mapping
from datetime import datetime

from media_house.modules.audio_visual_alignment.domain.clock import (
    Clock,
    build_corrections,
    probe_lag,
)
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.events import (
    boundary_relations,
    sound_actions,
)
from media_house.modules.audio_visual_alignment.domain.inputs import Inputs
from media_house.modules.audio_visual_alignment.domain.model import (
    AlignmentDocument,
    BoundaryRelation,
    Flag,
    InputRecord,
    Opportunity,
    Provenance,
    SoundAction,
    SpeakerLink,
    SpeechSource,
    StageReport,
    SyncAssessment,
    TextMatch,
)
from media_house.modules.audio_visual_alignment.domain.opportunities import (
    no_visible_speaker_windows,
    passage_contexts,
)
from media_house.modules.audio_visual_alignment.domain.semantic import text_matches
from media_house.modules.audio_visual_alignment.domain.speech import (
    speaker_links,
    speech_sources,
)
from media_house.modules.audio_visual_alignment.domain.values import (
    ALIGNMENT_VERSION,
    SCHEMA_VERSION,
    SCORING_VERSION,
    VOCABULARY_VERSION,
    CorrectionSource,
    FlagKind,
    ProbeState,
    Severity,
    StageState,
)

#: A video origin that cannot be checked widens the sound-to-picture window by this much.
_UNVERIFIED_ORIGIN_SLACK = 0.5
_ENGLISH = ("en", "eng")
LIMITATIONS = (
    "Mouth movement is the jaw opening of the largest face, a few samples per second: it shows "
    "whether a mouth moves while speech is heard, not lip-sync to a frame, and not which of "
    "several people speaks.",
    "The lag probe can reveal an offset of about a second or more; it does not measure drift and "
    "is never applied to any time.",
    "Speaker links need speaker labels from the audio analysis and a recognisable identity "
    "cluster from the video analysis; identities are anonymous and per video analysis.",
    "Matching speech to pictures uses exact words (on-screen text, descriptions, object labels) "
    "and, only when supplied, embedding similarity; it does not recognise demonstrated actions or "
    "contradictions.",
    "A sound that matches no seen event may come from off screen; a pair is a candidate, never "
    "proof of cause.",
    "No sound classes or musical beats are available upstream, so none are matched.",
)


def align(
    inputs: Inputs,
    profile: AlignmentProfile,
    *,
    alignment_id: str,
    created_at: datetime,
    records: tuple[InputRecord, ...],
    engines: Mapping[str, str],
    warnings: tuple[str, ...] = (),
) -> AlignmentDocument:
    audio, video = inputs.audio, inputs.video
    corrections = build_corrections(inputs.clock, video, inputs.audio_shift_seconds, profile)
    clock = Clock.of(corrections)
    flags: list[Flag] = []
    stages: list[StageReport] = []

    video_fix = next(c for c in corrections if c.stream == "video")
    unverified = video_fix.source is CorrectionSource.NONE
    if unverified:
        flags.append(
            Flag(
                kind=FlagKind.VIDEO_ORIGIN_UNVERIFIED,
                severity=Severity.WARNING,
                subject="video",
                detail="no stored inspection: the video's time origin was taken as reported",
            )
        )
    elif video_fix.seconds != 0.0:
        flags.append(
            Flag(
                kind=FlagKind.VIDEO_ORIGIN_CORRECTED,
                severity=Severity.INFO,
                subject="video",
                detail=(
                    f"video seconds shifted by {video_fix.seconds:+.4f}s to the container timeline"
                ),
            )
        )

    probe = probe_lag(audio.activity, video.mouth, clock, profile)
    if probe.state is ProbeState.OFFSET_INDICATED:
        flags.append(
            Flag(
                kind=FlagKind.SYNC_OFFSET_INDICATED,
                severity=Severity.WARNING,
                subject="clock",
                detail=(
                    f"mouth movement follows speech with a lag near {probe.lag_seconds:+.1f}s "
                    f"(resolution about {probe.resolution_seconds:.1f}s); not applied"
                ),
            )
        )
    elif probe.state in (ProbeState.AMBIGUOUS, ProbeState.INSUFFICIENT):
        flags.append(
            Flag(
                kind=FlagKind.SYNC_PROBE_WEAK,
                severity=Severity.INFO,
                subject="clock",
                detail=probe.reason or f"the lag probe is {probe.state.value}",
            )
        )
    difference = None if video.duration is None else abs(audio.duration - video.duration)
    if difference is not None and difference > profile.duration_tolerance_seconds:
        flags.append(
            Flag(
                kind=FlagKind.DURATION_MISMATCH,
                severity=Severity.WARNING,
                subject="clock",
                detail=f"audio and video lengths differ by {difference:.2f}s",
            )
        )
    sync = SyncAssessment(
        basis="container",
        corrections=corrections,
        probe=probe,
        audio_duration=audio.duration,
        video_duration=video.duration,
    )

    sources: tuple[SpeechSource, ...] = ()
    links: tuple[SpeakerLink, ...] = ()
    matches: tuple[TextMatch, ...] = ()
    opportunities: list[Opportunity] = []
    if profile.runs("speech"):
        sources = speech_sources(audio, video, clock, profile)
        found_links, link_flags = speaker_links(sources, profile)
        links = found_links
        flags.extend(link_flags)
        flags.append(
            Flag(
                kind=FlagKind.MOUTH_SIGNAL_COARSE,
                severity=Severity.INFO,
                subject="video",
                detail="the mouth signal is a few samples per second of the largest face",
            )
        )
        stages.append(StageReport(stage="speech", state=StageState.OK))
    else:
        stages.append(
            StageReport(stage="speech", state=StageState.SKIPPED, reason="not in profile")
        )

    if profile.runs("text"):
        if inputs.content is None:
            stages.append(
                StageReport(
                    stage="text",
                    state=StageState.NOT_AVAILABLE,
                    reason="no content analysis was available",
                )
            )
        else:
            matches = text_matches(inputs.content, video.shots, clock, inputs.similarities, profile)
            stages.append(StageReport(stage="text", state=StageState.OK))
            language = (audio.language or "").casefold()
            if (
                language
                and language not in _ENGLISH
                and any(s.objects or s.description for s in video.shots)
            ):
                flags.append(
                    Flag(
                        kind=FlagKind.LANGUAGE_MISMATCH,
                        severity=Severity.INFO,
                        subject="text",
                        detail=(
                            f"speech is '{language}' but object labels and descriptions are "
                            "English: exact-word matching can only succeed through on-screen text"
                            + (
                                " or the supplied embedding similarity"
                                if inputs.similarities
                                else ""
                            )
                        ),
                    )
                )

    sounds: tuple[SoundAction, ...] = ()
    if profile.runs("sound"):
        slack = _UNVERIFIED_ORIGIN_SLACK if unverified else 0.0
        sounds = sound_actions(audio, video, clock, profile, slack)
        stages.append(StageReport(stage="sound", state=StageState.OK))

    boundaries: tuple[BoundaryRelation, ...] = ()
    if profile.runs("boundaries") and sources:
        boundaries = boundary_relations(sources, clock, profile)
        stages.append(StageReport(stage="boundaries", state=StageState.OK))

    if profile.runs("opportunities") and sources:
        opportunities.extend(no_visible_speaker_windows(sources, clock, profile))
        if inputs.content is not None:
            opportunities.extend(passage_contexts(inputs.content, sources, clock))
        stages.append(StageReport(stage="opportunities", state=StageState.OK))

    return AlignmentDocument(
        alignment_id=alignment_id,
        asset_id=video.asset_id,
        created_at=created_at,
        inputs=records,
        sync=sync,
        speech_sources=sources,
        speaker_links=links,
        text_matches=matches,
        sound_actions=sounds,
        boundaries=boundaries,
        opportunities=tuple(opportunities),
        flags=tuple(flags),
        stages=tuple(stages),
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            alignment_version=ALIGNMENT_VERSION,
            scoring_version=SCORING_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile_name=profile.name,
            profile_version=profile.version,
            engines=dict(engines),
        ),
        warnings=warnings,
        limitations=LIMITATIONS,
    )


__all__ = ["LIMITATIONS", "align"]
