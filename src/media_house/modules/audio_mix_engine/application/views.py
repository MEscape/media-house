"""The built timeline, read as the facts a mix needs.

The only place that knows both the timeline builder's ``BuiltTimeline`` and the engine's own
``MixFacts`` (the domain imports no other module). Times become samples of the timeline's rate
once, here, from exact values.
"""

from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.audio_mix_engine.domain.facts import (
    ClipFact,
    InstructionFact,
    MixFacts,
    SourceFact,
    TimebaseFact,
    TrackFact,
)
from media_house.modules.timeline_builder.application.contracts import (
    AudioClip,
    BuiltTimeline,
    ClipState,
    MixInstruction,
    TrackKind,
)


def _fr(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def to_facts(timeline: BuiltTimeline) -> MixFacts:
    rate = timeline.settings.sample_rate
    audio_tracks = [t for t in timeline.tracks if t.kind is TrackKind.AUDIO]
    clips = [_clip(c) for t in audio_tracks for c in t.audio]
    return MixFacts(
        timeline_id=timeline.timeline_id,
        anchor_asset_id=timeline.anchor_asset_id,
        schema_version=timeline.schema_version,
        build_version=timeline.build.build_version,
        build_hash=timeline.build.build_hash,
        format_profile=timeline.identity.format_profile,
        timebase=TimebaseFact(
            frame_rate=timeline.settings.frame_rate,
            sample_rate=rate,
            duration_samples=round(_fr(timeline.settings.duration) * rate),
        ),
        tracks=tuple(TrackFact(t.track_id, t.role.value, t.name) for t in audio_tracks),
        clips=tuple(clips),
        instructions=tuple(_instruction(m, rate) for m in timeline.mix),
        sources=tuple(
            SourceFact(
                asset_id=s.source_id,
                original_asset_id=s.original_asset_id,
                relation=s.relation,
                name=s.name,
                sample_rate=s.sample_rate,
                channels=s.channels,
                checksum=s.checksum,
                available=s.available,
            )
            for s in timeline.sources
            if s.media_type in {"audio", "video"}
        ),
    )


def _clip(c: AudioClip) -> ClipFact:
    source = c.source
    return ClipFact(
        clip_id=c.clip_id,
        track_id=c.track_id,
        role=c.role.value,
        asset_id=source.source_id if source else None,
        source_in=source.source_in if source else Rational(0, 1),
        source_out=source.source_out if source else Rational(0, 1),
        start_sample=c.start_sample,
        end_sample=c.end_sample,
        gain_db=c.gain_db,
        fade_in=c.fade_in,
        fade_out=c.fade_out,
        speed=c.speed,
        channels=c.channels,
        provenance=c.provenance.value,
        resolved=c.state is ClipState.RESOLVED and c.enabled,
        video_link=c.video_link,
    )


def _instruction(m: MixInstruction, rate: int) -> InstructionFact:
    mix_rate = m.sample_rate or rate

    def sample(t: Rational | None) -> int | None:
        return None if t is None else round(_fr(t) * mix_rate)

    return InstructionFact(
        mix_id=m.mix_id,
        kind=m.kind,
        clip_ids=m.clip_ids,
        start_sample=sample(m.timeline_start) or 0,
        end_sample=sample(m.timeline_end) or 0,
        key=m.key,
        depth_db=m.depth_db,
        attack_seconds=m.attack_seconds,
        release_seconds=m.release_seconds,
        curve=m.curve,
        at_sample=sample(m.timeline_at),
        before_seconds=float(_fr(m.before)) if m.before is not None else 0.0,
        after_seconds=float(_fr(m.after)) if m.after is not None else 0.0,
    )
