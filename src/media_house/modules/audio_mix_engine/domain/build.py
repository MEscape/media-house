"""Build the audio mix specification from a timeline's sound and a mix + delivery profile.

Pure and deterministic: the same timeline, profiles and overrides give the same specification and
the same content hash (no clock, no run id in the hash). Nothing creative is decided here: which
sounds exist, where they sit and the intended ducking, fades and crossfades come from the
timeline. The profiles add only technical behaviour (bus levels, EQ, dynamics, loudness).
Where the sound design gave no ducking, the profile may derive one from where the voice is
(provenance ``automatic``); where it gave ducking, that is used and nothing is added.
"""

from dataclasses import dataclass, replace
from fractions import Fraction

from media_house.core.domain import Rational, json_codec
from media_house.modules.audio_mix_engine.domain.facts import (
    ClipFact,
    InstructionFact,
    MixFacts,
)
from media_house.modules.audio_mix_engine.domain.profile import (
    DeliveryProfile,
    Limiter,
    MixProfile,
)
from media_house.modules.audio_mix_engine.domain.spec import (
    AudioMixSpecification,
    Crossfade,
    Dependency,
    Duck,
    Loudness,
    MixBus,
    MixClip,
    MixTrack,
    RenderConfig,
    SourceRef,
    SpecIssue,
    SpecProvenance,
    StemSpec,
    Timebase,
    ValidationRequirements,
)
from media_house.modules.audio_mix_engine.domain.timebase import fr, rat
from media_house.modules.audio_mix_engine.domain.validate import validate_spec
from media_house.modules.audio_mix_engine.domain.values import (
    ENGINE_VERSION,
    MASTER,
    BusRole,
    DependencyState,
    Provenance,
    Severity,
)

#: The checks the finished mix is held to (``validate.py`` / ``output.py``).
CHECKS = (
    "clipping",
    "true_peak",
    "integrated_loudness",
    "loudness_range",
    "format",
    "duration",
    "dialogue_over_music",
    "silence",
    "stems_aligned",
    "recombination",
)


@dataclass(frozen=True, slots=True)
class MixOverrides:
    """Changes a person recorded: ``(bus role or id, dB)`` and ``(clip id, dB)``."""

    bus_gain_db: tuple[tuple[str, float], ...] = ()
    clip_gain_db: tuple[tuple[str, float], ...] = ()

    def labels(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                [f"bus:{k}={v:g}" for k, v in self.bus_gain_db]
                + [f"clip:{k}={v:g}" for k, v in self.clip_gain_db]
            )
        )


@dataclass(frozen=True, slots=True)
class BuiltSpec:
    spec: AudioMixSpecification | None
    issues: tuple[SpecIssue, ...]

    @property
    def errors(self) -> tuple[SpecIssue, ...]:
        return tuple(i for i in self.issues if i.severity in {Severity.ERROR, Severity.CRITICAL})


def build_spec(
    facts: MixFacts,
    profile: MixProfile,
    delivery: DeliveryProfile,
    *,
    created_at: str,
    overrides: MixOverrides | None = None,
    speech_spans: tuple[tuple[int, int], ...] | None = None,
) -> BuiltSpec:
    overrides = overrides or MixOverrides()
    issues: list[SpecIssue] = []
    deps: list[Dependency] = []
    rate = facts.timebase.sample_rate
    tracks_by_id = {t.track_id: t for t in facts.tracks}
    clip_overrides = dict(overrides.clip_gain_db)

    clips: list[MixClip] = []
    used_sources: dict[str, SourceRef] = {}
    for fact in sorted(facts.clips, key=lambda c: (c.start_sample, c.clip_id)):
        made = _clip(fact, facts, clip_overrides, issues, deps)
        if made is None:
            continue
        clip, source = made
        clips.append(clip)
        used_sources[source.source_id] = source
    if not clips:
        issues.append(
            SpecIssue(
                "nothing_to_mix", Severity.ERROR, "the timeline has no sound that can be mixed"
            )
        )
        return BuiltSpec(None, tuple(issues))

    clips = _apply_instructions(clips, facts, rate, issues)
    crossfades = _crossfades(facts)
    roles_in_use = {tracks_by_id[c.track_id].role for c in clips}
    track_ids = {c.track_id for c in clips}
    tracks = tuple(
        MixTrack(t.track_id, t.name, BusRole(t.role), BusRole(t.role).value)
        for t in sorted(facts.tracks, key=lambda t: t.track_id)
        if t.track_id in track_ids
    )
    bus_gain = dict(overrides.bus_gain_db)
    buses = (
        *(
            _bus(role, profile, bus_gain)
            for role in sorted(roles_in_use, key=lambda r: list(BusRole).index(BusRole(r)))
        ),
        _master(profile, delivery, bus_gain),
    )
    ducks = _ducks(facts, clips, tracks, profile, rate, speech_spans)
    for ins in facts.instructions:
        if ins.kind == "duck" and ins.depth_db < 0:
            issues.append(
                SpecIssue(
                    "duck_depth_sign",
                    Severity.INFO,
                    f"ducking {ins.mix_id} states its depth as {ins.depth_db:g} dB; "
                    "a depth is a reduction, so its size is used",
                    ins.mix_id,
                )
            )

    bus_ids = [b.bus_id for b in buses if b.bus_id != MASTER]
    spec = AudioMixSpecification(
        specification_id="",
        project_id=None,
        timeline_id=facts.timeline_id,
        timeline_version=facts.build_version,
        created_at=created_at,
        timebase=Timebase(facts.timebase.frame_rate, rate, facts.timebase.duration_samples),
        sources=tuple(sorted(used_sources.values(), key=lambda s: s.source_id)),
        tracks=tracks,
        buses=buses,
        clips=tuple(clips),
        automation=(),
        ducking=tuple(ducks),
        crossfades=tuple(crossfades),
        loudness=Loudness(
            delivery.key,
            delivery.integrated_lufs,
            delivery.tolerance_lu,
            delivery.max_loudness_range_lu,
            delivery.true_peak_dbtp,
        ),
        render=RenderConfig(
            delivery.sample_rate,
            delivery.bit_depth,
            delivery.channels,
            delivery.container,
            delivery.codec,
        ),
        stems=tuple(StemSpec(f"stem:{b}", b, "post_bus") for b in bus_ids),
        validation=ValidationRequirements(
            CHECKS, profile.min_dialogue_over_music_lu, -60.0, 1.0, -60.0
        ),
        dependencies=tuple(sorted(deps, key=lambda d: (d.kind, d.reference))),
        warnings=(),
        provenance=SpecProvenance(
            timeline_id=facts.timeline_id,
            timeline_version=facts.build_version,
            timeline_build_hash=facts.build_hash,
            anchor_asset_id=facts.anchor_asset_id,
            profile=profile.key,
            profile_version=profile.version,
            delivery=delivery.key,
            engine_version=ENGINE_VERSION,
            overrides=overrides.labels(),
        ),
        content_hash="",
    )
    found = tuple(issues) + validate_spec(spec)
    spec = replace(spec, warnings=found)
    digest = json_codec.digest(replace(spec, specification_id="", created_at="", content_hash=""))
    spec = replace(spec, content_hash=digest, specification_id=f"mixspec-{digest[:16]}")
    return BuiltSpec(spec, found)


# --- pieces ---------------------------------------------------------------------------------------


def _clip(
    fact: ClipFact,
    facts: MixFacts,
    clip_overrides: dict[str, float],
    issues: list[SpecIssue],
    deps: list[Dependency],
) -> tuple[MixClip, SourceRef] | None:
    source = facts.source(fact.asset_id) if fact.asset_id else None
    if not fact.resolved or fact.asset_id is None or source is None or not source.available:
        reference = fact.asset_id or fact.clip_id
        deps.append(
            Dependency("audio_source", reference, DependencyState.MISSING, (fact.clip_id,),
                       "the sound this clip plays is not available")
        )  # fmt: skip
        issues.append(
            SpecIssue(
                "clip_unavailable",
                Severity.WARNING,
                f"{fact.clip_id} is left out of the mix: its sound is not available",
                fact.clip_id,
            )
        )
        return None
    extra = clip_overrides.get(fact.clip_id)
    ref = SourceRef(
        source_id=source.asset_id,
        asset_id=source.asset_id,
        original_asset_id=source.original_asset_id,
        relation=source.relation,
        name=source.name,
        sample_rate=source.sample_rate,
        channels=source.channels,
        checksum=source.checksum,
        available=source.available,
    )
    clip = MixClip(
        clip_id=fact.clip_id,
        track_id=fact.track_id,
        source_id=source.asset_id,
        source_in=fact.source_in,
        source_out=fact.source_out,
        start_sample=fact.start_sample,
        end_sample=fact.end_sample,
        gain_db=fact.gain_db + (extra or 0.0),
        fade_in=fact.fade_in,
        fade_out=fact.fade_out,
        fade_curve="linear",
        speed=fact.speed,
        channels=fact.channels,
        pan=0.0,
        provenance=Provenance.HUMAN if extra is not None else Provenance.COMPILED,
        origin_ref=fact.clip_id,
    )
    return clip, ref


def _bus(role: str, profile: MixProfile, overrides: dict[str, float]) -> MixBus:
    r = BusRole(role)
    p = profile.bus(r)
    extra = overrides.get(r.value, 0.0)
    return MixBus(r.value, r, r.value.title(), MASTER, p.gain_db + extra, p.eq, p.compressor, p.pan)


def _master(profile: MixProfile, delivery: DeliveryProfile, overrides: dict[str, float]) -> MixBus:
    ceiling = min(profile.master_limiter.ceiling_db, delivery.true_peak_dbtp - 0.5)
    limiter = Limiter(ceiling, profile.master_limiter.attack_ms, profile.master_limiter.release_ms)
    return MixBus(
        MASTER, BusRole.MASTER, "Master", None, overrides.get(MASTER, 0.0), profile.master_eq,
        profile.master_compressor, 0.0, limiter,
    )  # fmt: skip


def _seconds(samples: int, rate: int) -> Fraction:
    return Fraction(max(samples, 0), rate)


def _apply_instructions(
    clips: list[MixClip], facts: MixFacts, rate: int, issues: list[SpecIssue]
) -> list[MixClip]:
    """Crossfades and music tails become fades on the clips they act on (the longer one wins)."""
    by_id = {c.clip_id: c for c in clips}
    for ins in sorted(facts.instructions, key=lambda i: i.mix_id):
        if ins.kind == "crossfade" and ins.at_sample is not None:
            lead = round(ins.before_seconds * rate)
            tail = round(ins.after_seconds * rate)
            window_start, window_end = ins.at_sample - lead, ins.at_sample + tail
            ordered = sorted(
                (by_id[i] for i in ins.clip_ids if i in by_id), key=lambda c: c.start_sample
            )
            if len(ordered) == 2:
                out, inc = ordered
                by_id[out.clip_id] = replace(
                    out,
                    fade_out=_longer(out.fade_out, _seconds(out.end_sample - window_start, rate)),
                    fade_curve="equal_power",
                )
                by_id[inc.clip_id] = replace(
                    inc,
                    fade_in=_longer(inc.fade_in, _seconds(window_end - inc.start_sample, rate)),
                    fade_curve="equal_power",
                )
        elif ins.kind == "music_tail":
            for clip_id in ins.clip_ids:
                clip = by_id.get(clip_id)
                if clip is not None and clip.start_sample < ins.start_sample < clip.end_sample:
                    by_id[clip_id] = replace(
                        clip,
                        fade_out=_longer(
                            clip.fade_out, _seconds(clip.end_sample - ins.start_sample, rate)
                        ),
                    )
        elif ins.kind not in {"duck", "crossfade", "music_tail"}:
            issues.append(
                SpecIssue(
                    "instruction_unknown",
                    Severity.WARNING,
                    f"mix instruction {ins.mix_id} ({ins.kind}) is not one this engine carries out",
                    ins.mix_id,
                )
            )
    return [by_id[c.clip_id] for c in clips]


def _longer(a: Rational, b: Fraction) -> Rational:
    return rat(max(fr(a), b))


def _crossfades(facts: MixFacts) -> list[Crossfade]:
    return [
        Crossfade(
            f"x:{i.mix_id}", i.clip_ids, i.at_sample or 0, i.before_seconds, i.after_seconds,
            "equal_power", i.mix_id,
        )
        for i in sorted(facts.instructions, key=lambda i: i.mix_id)
        if i.kind == "crossfade" and i.at_sample is not None
    ]  # fmt: skip


def _ducks(
    facts: MixFacts,
    clips: list[MixClip],
    tracks: tuple[MixTrack, ...],
    profile: MixProfile,
    rate: int,
    speech_spans: tuple[tuple[int, int], ...] | None,
) -> list[Duck]:
    known = {c.clip_id for c in clips}
    compiled: list[InstructionFact] = [i for i in facts.instructions if i.kind == "duck"]
    if compiled:
        return [
            Duck(
                f"d:{i.mix_id}", tuple(c for c in i.clip_ids if c in known), i.key or "speech",
                i.start_sample, i.end_sample, abs(i.depth_db), i.attack_seconds, i.release_seconds,
                Provenance.COMPILED, i.mix_id,
            )
            for i in sorted(compiled, key=lambda i: i.mix_id)
            if any(c in known for c in i.clip_ids)
        ]  # fmt: skip
    if not profile.auto_duck:
        return []
    role_of = {t.track_id: t.role for t in tracks}
    music = [c for c in clips if role_of.get(c.track_id) is BusRole.MUSIC]
    if not music:
        return []
    spans = speech_spans
    if spans is None:
        spans = tuple(
            (c.start_sample, c.end_sample)
            for c in clips
            if role_of.get(c.track_id) is BusRole.DIALOGUE
        )
    hold = int(profile.ducking.hold_seconds * rate)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] <= hold:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    ducks = []
    for n, (start, end) in enumerate(merged, start=1):
        targets = tuple(c.clip_id for c in music if c.start_sample < end and start < c.end_sample)
        if targets:
            ducks.append(
                Duck(
                    f"d:auto:{n}", targets, "speech", start, end, profile.ducking.depth_db,
                    profile.ducking.attack_seconds, profile.ducking.release_seconds,
                    Provenance.AUTOMATIC, "profile:ducking",
                )
            )  # fmt: skip
    return ducks
